"""TechDeva's own sales team (Part 22).

Operators log in to TechDeva's own business (the "home" tenant) with their
PIN and work their leads there:

  operator   /sales/*            their own leads, contacts, follow-ups and
                                 meetings, and their progress against target
  owner      /sales/*            the same for the whole team (full access)
  founder    /techdeva/sales*    every operator's numbers, targets, salary
                                 and cost per client landed

Salaries are read only by the founder routes. Days are Pakistan days.
"""
import datetime as _dt

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .tdwork import _admin, _home, _need_home, _rec, _text, _who

TZ = "Asia/Karachi"
SOURCES = ("whatsapp", "call", "walk_in", "referral", "facebook", "instagram", "website", "other")
STATUSES = ("new", "contacted", "meeting", "proposal", "won", "lost")
CHANNELS = ("whatsapp", "call", "visit", "meeting", "email", "other")
MODES = ("in_person", "online")
MEETING_STATUSES = ("booked", "done", "no_show", "cancelled")
TARGETS = ("contacts_per_day", "leads_per_month", "meetings_per_month", "clients_per_month")

LEAD_COLS = ("l.id, l.owner_user_id, l.business_name, l.contact_name, l.phone, l.city, l.business_type, "
             "l.source, l.status, l.plan_interest, l.next_followup_at, l.client_tenant_id, l.client_name, "
             "l.won_at, l.notes, l.created_at, l.updated_at, "
             "coalesce(nullif(u.name, ''), u.email) AS owner_name, "
             "(SELECT max(c.at) FROM sales_contacts c WHERE c.lead_id = l.id) AS last_contact_at")
MEET_COLS = ("m.id, m.lead_id, m.user_id, m.starts_at, m.mode, m.place, m.meet_link, m.status, m.notes, "
             "l.business_name, l.contact_name, l.phone, coalesce(nullif(u.name, ''), u.email) AS owner_name")


def _when(v, what):
    """An ISO time from the browser (with its offset). None when empty."""
    if v in (None, ""):
        return None
    try:
        t = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "%s must be a date and time." % what)
    if t.tzinfo is None:
        raise HTTPException(400, "%s needs a time zone." % what)
    return t


def _pick(v, allowed, what):
    v = str(v or "").strip().lower()
    if v not in allowed:
        raise HTTPException(400, "%s must be one of: %s." % (what, ", ".join(allowed)))
    return v


def _phone(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit() or ch == "+")[:20]


def _team_member(tid, role):
    if not tid or role not in ("owner", "operator"):
        raise HTTPException(403, "The sales page is for TechDeva's team.")


def _is_home(cur):
    cur.execute("SELECT EXISTS (SELECT 1 FROM td_catalog) AS home")
    return cur.fetchone()["home"]


def _counts(cur, tenant_id, user_id=None):
    """Contacts, new leads, meetings and clients won: today, last 7 and 30
    days and this month, per operator (or one operator). Pakistan days."""
    who = "AND x.u = %s" if user_id else ""
    args = [tenant_id] * 4 + ([user_id] if user_id else [])
    cur.execute("""
      WITH d AS (SELECT (now() AT TIME ZONE %%s)::date AS today),
      x AS (
        SELECT user_id AS u, 'contacts' AS k, (at AT TIME ZONE %%s)::date AS day FROM sales_contacts WHERE tenant_id = %%s
        UNION ALL SELECT owner_user_id, 'leads', (created_at AT TIME ZONE %%s)::date FROM sales_leads WHERE tenant_id = %%s
        UNION ALL SELECT user_id, 'meetings', (created_at AT TIME ZONE %%s)::date FROM sales_meetings
                   WHERE tenant_id = %%s AND status <> 'cancelled' 
        UNION ALL SELECT owner_user_id, 'won', (won_at AT TIME ZONE %%s)::date FROM sales_leads
                   WHERE tenant_id = %%s AND status = 'won' AND won_at IS NOT NULL)
      SELECT x.u::text AS u, x.k,
             count(*) FILTER (WHERE x.day = d.today) AS today,
             count(*) FILTER (WHERE x.day > d.today - 7 AND x.day <= d.today) AS d7,
             count(*) FILTER (WHERE x.day > d.today - 30 AND x.day <= d.today) AS d30,
             count(*) FILTER (WHERE date_trunc('month', x.day) = date_trunc('month', d.today)
                              AND x.day <= d.today) AS month
        FROM x, d WHERE x.u IS NOT NULL %s GROUP BY 1, 2""" % who,
                [TZ, TZ, args[0], TZ, args[1], TZ, args[2], TZ, args[3]] + args[4:])
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["u"], {})[r["k"]] = {k: int(r[k]) for k in ("today", "d7", "d30", "month")}
    return out


def _daily(cur, tenant_id, days=14):
    """Contacts per operator per day for the last `days` Pakistan days."""
    cur.execute("""SELECT user_id::text AS u, (at AT TIME ZONE %s)::date AS day, count(*) AS n
                     FROM sales_contacts
                    WHERE tenant_id = %s AND at > now() - make_interval(days => %s)
                    GROUP BY 1, 2""", (TZ, tenant_id, days + 1))
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["u"], {})[r["day"].isoformat()] = int(r["n"])
    return out


def _lead_or_404(cur, lead_id, role, uid):
    cur.execute("SELECT id, owner_user_id, status, business_name, won_at FROM sales_leads WHERE id::text = %s",
                (str(lead_id),))
    lead = cur.fetchone()
    if not lead:
        raise HTTPException(404, "That lead is not there.")
    if role != "owner" and str(lead["owner_user_id"] or "") != str(uid or ""):
        raise HTTPException(403, "That lead belongs to someone else.")
    return lead


def _lead_fields(body, partial):
    f = {}
    for k, n in (("business_name", 160), ("contact_name", 120), ("city", 80), ("business_type", 80),
                 ("plan_interest", 40), ("notes", 2000)):
        if k in body or (not partial and k == "business_name"):
            f[k] = _text(body.get(k), n, "Business name", required=(k == "business_name"))
    if "phone" in body:
        f["phone"] = _phone(body.get("phone"))
    if "source" in body:
        f["source"] = _pick(body.get("source") or "other", SOURCES, "Source")
    if "status" in body:
        f["status"] = _pick(body.get("status"), STATUSES, "Status")
    if "next_followup_at" in body:
        f["next_followup_at"] = _when(body.get("next_followup_at"), "Follow-up")
    return f


def make_sales_router():
    router = APIRouter(tags=["sales"])

    # ------------------------------------------------------------ operators
    @router.get("/sales/me")
    def me(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """An operator's page: their leads, what is due, their meetings and
        their numbers against target. The owner sees the whole team."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if not tid or role not in ("owner", "operator"):
            return {"home": False}
        mine = "" if role == "owner" else " AND l.owner_user_id = %s"
        margs = [] if role == "owner" else [uid]
        with tenant_session(tid, role) as cur:
            if not _is_home(cur):
                return {"home": False}
            cur.execute("SELECT " + LEAD_COLS + " FROM sales_leads l LEFT JOIN users u ON u.id = l.owner_user_id "
                        "WHERE true" + mine + " ORDER BY (l.status IN ('won','lost')), "
                        "l.next_followup_at NULLS LAST, l.updated_at DESC LIMIT 500", margs)
            leads = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT " + MEET_COLS + " FROM sales_meetings m JOIN sales_leads l ON l.id = m.lead_id "
                        "LEFT JOIN users u ON u.id = m.user_id WHERE m.status = 'booked' "
                        "AND m.starts_at > now() - interval '12 hours'" +
                        ("" if role == "owner" else " AND m.user_id = %s") +
                        " ORDER BY m.starts_at LIMIT 100", margs)
            meetings = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT c.id, c.lead_id, c.channel, c.outcome, c.note, c.at, l.business_name "
                        "FROM sales_contacts c JOIN sales_leads l ON l.id = c.lead_id WHERE true" +
                        ("" if role == "owner" else " AND c.user_id = %s") +
                        " ORDER BY c.at DESC LIMIT 40", margs)
            contacts = [dict(r) for r in cur.fetchall()]
            counts = _counts(cur, tid, None if role == "owner" else uid)
            cur.execute("SELECT " + ", ".join(TARGETS) + " FROM sales_people WHERE user_id = %s", (uid,))
            t = cur.fetchone()
            cur.execute("SELECT u.id, coalesce(nullif(u.name, ''), s.name, u.email) AS name FROM users u "
                        "LEFT JOIN staff s ON s.id = u.staff_id WHERE s.active IS NOT false ORDER BY 2")
            team = [{"id": str(r["id"]), "name": r["name"]} for r in cur.fetchall()]
        if role == "owner":                     # the whole team's totals
            tot = {}
            for per in counts.values():
                for k, v in per.items():
                    for p, n in v.items():
                        tot.setdefault(k, {}).setdefault(p, 0)
                        tot[k][p] += n
            stats = tot
        else:
            stats = counts.get(str(uid), {})
        return {"home": True, "me": str(uid) if uid else None, "role": role, "leads": leads,
                "meetings": meetings, "contacts": contacts, "stats": stats,
                "targets": dict(t) if t else {k: 0 for k in TARGETS}, "team": team}

    @router.post("/sales/leads")
    def add_lead(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _team_member(tid, role)
        f = _lead_fields(body, partial=False)
        f.pop("status", None)
        owner = uid
        if role == "owner" and body.get("owner_user_id"):
            owner = str(body["owner_user_id"])
        with tenant_session(tid, role) as cur:
            if not _is_home(cur):
                raise HTTPException(403, "The sales page is for TechDeva's team.")
            cur.execute("SELECT id FROM users WHERE id::text = %s", (str(owner),))
            if not cur.fetchone():
                raise HTTPException(404, "That person is not on the team.")
            if f.get("phone"):
                cur.execute("SELECT l.business_name, coalesce(nullif(u.name, ''), u.email) AS who FROM sales_leads l "
                            "LEFT JOIN users u ON u.id = l.owner_user_id "
                            "WHERE right(regexp_replace(l.phone, '\\D', '', 'g'), 10) = right(regexp_replace(%s, '\\D', '', 'g'), 10) "
                            "AND length(regexp_replace(l.phone, '\\D', '', 'g')) >= 7 "
                            "AND l.status NOT IN ('lost') LIMIT 1", (f["phone"],))
                dup = cur.fetchone()
                if dup:
                    raise HTTPException(409, "%s (this number) is already a lead of %s." % (dup["business_name"], dup["who"] or "the team"))
            cols = ["tenant_id", "owner_user_id"] + list(f)
            cur.execute("INSERT INTO sales_leads (%s) VALUES (%s) RETURNING id" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                        [tid, owner] + list(f.values()))
            lid = str(cur.fetchone()["id"])
            record(cur, action="sales.lead_add", resource="sales_leads", resource_id=lid, actor=str(uid),
                   actor_type="human", status="ok", detail={"business": f["business_name"], "owner": str(owner)}, error=None)
        return {"id": lid}

    @router.put("/sales/leads/{lead_id}")
    def edit_lead(lead_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _team_member(tid, role)
        f = _lead_fields(body, partial=True)
        if role == "owner" and body.get("owner_user_id"):
            f["owner_user_id"] = str(body["owner_user_id"])
        if not f:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            lead = _lead_or_404(cur, lead_id, role, uid)
            if f.get("owner_user_id"):
                cur.execute("SELECT id FROM users WHERE id::text = %s", (f["owner_user_id"],))
                if not cur.fetchone():
                    raise HTTPException(404, "That person is not on the team.")
            sets = ["%s = %%s" % k for k in f]
            vals = list(f.values())
            if f.get("status") == "won" and not lead["won_at"]:
                sets.append("won_at = now()")
            elif "status" in f and f["status"] != "won":
                sets.append("won_at = NULL")
            cur.execute("UPDATE sales_leads SET %s, updated_at = now() WHERE id = %%s" % ", ".join(sets),
                        vals + [lead["id"]])
            record(cur, action="sales.lead_edit", resource="sales_leads", resource_id=lead_id, actor=str(uid),
                   actor_type="human", status="ok",
                   detail={k: (str(v) if v is not None else None) for k, v in f.items() if k != "notes"}, error=None)
        return {"ok": True}

    @router.post("/sales/leads/{lead_id}/contacts")
    def log_contact(lead_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One contact with a lead. A new lead becomes 'contacted'; a next
        follow-up date given here goes on the lead."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _team_member(tid, role)
        channel = _pick(body.get("channel"), CHANNELS, "Channel")
        outcome = _text(body.get("outcome"), 120, "Outcome")
        note = _text(body.get("note"), 1000, "Note")
        nxt = _when(body.get("next_followup_at"), "Next follow-up")
        with tenant_session(tid, role) as cur:
            lead = _lead_or_404(cur, lead_id, role, uid)
            cur.execute("INSERT INTO sales_contacts (tenant_id, lead_id, user_id, channel, outcome, note) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (tid, lead["id"], uid, channel, outcome, note))
            cid = str(cur.fetchone()["id"])
            cur.execute("UPDATE sales_leads SET status = CASE WHEN status = 'new' THEN 'contacted' ELSE status END, "
                        "next_followup_at = CASE WHEN %s THEN %s ELSE next_followup_at END, updated_at = now() "
                        "WHERE id = %s", (body.get("next_followup_at") is not None, nxt, lead["id"]))
            record(cur, action="sales.contact", resource="sales_contacts", resource_id=cid, actor=str(uid),
                   actor_type="human", status="ok",
                   detail={"lead": lead["business_name"], "channel": channel, "outcome": outcome}, error=None)
        return {"id": cid}

    @router.post("/sales/meetings")
    def add_meeting(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _team_member(tid, role)
        at = _when(body.get("starts_at"), "Meeting time")
        if not at:
            raise HTTPException(400, "Pick the meeting date and time.")
        mode = _pick(body.get("mode") or "in_person", MODES, "Meeting type")
        place = _text(body.get("place"), 200, "Place")
        link = _text(body.get("meet_link"), 300, "Meeting link")
        if link and not link.startswith("https://"):
            raise HTTPException(400, "The meeting link must start with https://")
        notes = _text(body.get("notes"), 1000, "Notes")
        with tenant_session(tid, role) as cur:
            lead = _lead_or_404(cur, body.get("lead_id"), role, uid)
            cur.execute("SELECT now() - interval '1 day' > %s AS past", (at,))
            if cur.fetchone()["past"]:
                raise HTTPException(400, "That time is in the past.")
            cur.execute("INSERT INTO sales_meetings (tenant_id, lead_id, user_id, starts_at, mode, place, meet_link, notes) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (tid, lead["id"], lead["owner_user_id"] or uid, at, mode, place, link, notes))
            mid = str(cur.fetchone()["id"])
            cur.execute("UPDATE sales_leads SET status = CASE WHEN status IN ('new','contacted') THEN 'meeting' ELSE status END, "
                        "updated_at = now() WHERE id = %s", (lead["id"],))
            record(cur, action="sales.meeting_add", resource="sales_meetings", resource_id=mid, actor=str(uid),
                   actor_type="human", status="ok",
                   detail={"lead": lead["business_name"], "at": at.isoformat(), "mode": mode}, error=None)
        return {"id": mid}

    @router.put("/sales/meetings/{meeting_id}")
    def edit_meeting(meeting_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Mark a meeting done / no-show / cancelled, or move it. A meeting
        marked done also counts as a contact."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _team_member(tid, role)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, lead_id, user_id, status FROM sales_meetings WHERE id::text = %s", (meeting_id,))
            m = cur.fetchone()
            if not m:
                raise HTTPException(404, "That meeting is not there.")
            if role != "owner" and str(m["user_id"] or "") != str(uid or ""):
                raise HTTPException(403, "That meeting belongs to someone else.")
            sets, vals = [], []
            st = None
            if "status" in body:
                st = _pick(body.get("status"), MEETING_STATUSES, "Status")
                sets.append("status = %s"); vals.append(st)
            if body.get("starts_at"):
                sets.append("starts_at = %s"); vals.append(_when(body.get("starts_at"), "Meeting time"))
            if "notes" in body:
                sets.append("notes = %s"); vals.append(_text(body.get("notes"), 1000, "Notes"))
            if not sets:
                raise HTTPException(400, "Nothing to change.")
            cur.execute("UPDATE sales_meetings SET %s, updated_at = now() WHERE id = %%s" % ", ".join(sets), vals + [m["id"]])
            if st == "done" and m["status"] != "done":
                cur.execute("INSERT INTO sales_contacts (tenant_id, lead_id, user_id, channel, outcome) "
                            "VALUES (%s, %s, %s, 'meeting', 'met')", (tid, m["lead_id"], m["user_id"] or uid))
            record(cur, action="sales.meeting_edit", resource="sales_meetings", resource_id=meeting_id, actor=str(uid),
                   actor_type="human", status="ok", detail={"status": st}, error=None)
        return {"ok": True}

    # ------------------------------------------------------------ founder
    @router.get("/techdeva/sales")
    def team_progress(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Every operator: today / 7 / 30 days / this month against target,
        open leads, overdue follow-ups, salary and cost per client landed."""
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            if not h:
                return {"home": None, "people": []}
            hid = h["id"]
            cur.execute("""SELECT u.id::text AS user_id, coalesce(nullif(u.name, ''), s.name, u.email) AS name,
                                  u.role::text AS access, s.id AS staff_id, coalesce(s.role, '') AS job,
                                  s.salary, coalesce(s.active, true) AS active, u.last_login_at,
                                  p.contacts_per_day, p.leads_per_month, p.meetings_per_month, p.clients_per_month,
                                  (SELECT count(*) FROM sales_leads l WHERE l.owner_user_id = u.id
                                     AND l.status NOT IN ('won','lost')) AS open_leads,
                                  (SELECT count(*) FROM sales_leads l WHERE l.owner_user_id = u.id
                                     AND l.status NOT IN ('won','lost') AND l.next_followup_at < now()) AS overdue,
                                  (SELECT count(*) FROM sales_meetings m WHERE m.user_id = u.id
                                     AND m.status = 'booked' AND m.starts_at > now()) AS upcoming_meetings
                             FROM users u LEFT JOIN staff s ON s.id = u.staff_id
                             LEFT JOIN sales_people p ON p.user_id = u.id
                            WHERE u.tenant_id = %s ORDER BY coalesce(s.active, true) DESC, 2""", (hid,))
            people = [dict(r) for r in cur.fetchall()]
            counts = _counts(cur, hid)
            daily = _daily(cur, hid)
            cur.execute("SELECT (now() AT TIME ZONE %s)::date AS today", (TZ,))
            today = cur.fetchone()["today"]
            cur.execute("SELECT status, count(*) AS n FROM sales_leads WHERE tenant_id = %s GROUP BY 1", (hid,))
            pipeline = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        days = [(today - _dt.timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        for p in people:
            c = counts.get(p["user_id"], {})
            p["stats"] = c
            p["daily"] = [daily.get(p["user_id"], {}).get(d, 0) for d in days]
            won_month = (c.get("won") or {}).get("month", 0)
            sal = float(p["salary"]) if p["salary"] is not None else None
            p["salary"] = sal
            p["cost_per_client"] = round(sal / won_month) if sal and won_month else None
            for k in TARGETS:
                p[k] = int(p[k] or 0)
        return {"home": dict(h), "today": today.isoformat(), "days": days, "people": people, "pipeline": pipeline}

    @router.get("/techdeva/sales/people/{user_id}")
    def person(user_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("SELECT id FROM users WHERE id::text = %s AND tenant_id = %s", (user_id, h["id"]))
            if not cur.fetchone():
                raise HTTPException(404, "That person is not on TechDeva's team.")
            cur.execute("SELECT " + LEAD_COLS + " FROM sales_leads l LEFT JOIN users u ON u.id = l.owner_user_id "
                        "WHERE l.tenant_id = %s AND l.owner_user_id::text = %s "
                        "ORDER BY (l.status IN ('won','lost')), l.updated_at DESC LIMIT 300", (h["id"], user_id))
            leads = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT c.id, c.channel, c.outcome, c.note, c.at, l.business_name FROM sales_contacts c "
                        "JOIN sales_leads l ON l.id = c.lead_id WHERE c.tenant_id = %s AND c.user_id::text = %s "
                        "ORDER BY c.at DESC LIMIT 150", (h["id"], user_id))
            contacts = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT " + MEET_COLS + " FROM sales_meetings m JOIN sales_leads l ON l.id = m.lead_id "
                        "LEFT JOIN users u ON u.id = m.user_id WHERE m.tenant_id = %s AND m.user_id::text = %s "
                        "ORDER BY m.starts_at DESC LIMIT 100", (h["id"], user_id))
            meetings = [dict(r) for r in cur.fetchall()]
        return {"leads": leads, "contacts": contacts, "meetings": meetings}

    @router.put("/techdeva/sales/people/{user_id}")
    def set_targets(user_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        vals = {}
        for k in TARGETS:
            if k in body:
                try:
                    n = int(str(body.get(k) or 0).replace(",", ""))
                except ValueError:
                    raise HTTPException(400, "Targets are whole numbers.")
                if n < 0 or n > 10000:
                    raise HTTPException(400, "A target must be between 0 and 10,000.")
                vals[k] = n
        if not vals:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("SELECT id FROM users WHERE id::text = %s AND tenant_id = %s", (user_id, h["id"]))
            u = cur.fetchone()
            if not u:
                raise HTTPException(404, "That person is not on TechDeva's team.")
            cols = list(vals)
            cur.execute("INSERT INTO sales_people (tenant_id, user_id, %s) VALUES (%%s, %%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET %s, updated_at = now()"
                        % (", ".join(cols), ", ".join(["%s"] * len(cols)),
                           ", ".join("%s = EXCLUDED.%s" % (c, c) for c in cols)),
                        [h["id"], u["id"]] + list(vals.values()))
            _rec(cur, action="techdeva.sales_targets", resource="sales_people", resource_id=user_id,
                 actor=str(uid), actor_type="human", status="ok", detail=vals, error=None)
        return {"ok": True}

    @router.put("/techdeva/sales/leads/{lead_id}/client")
    def link_client(lead_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Say which platform client a won lead became (or clear it)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("SELECT id, business_name FROM sales_leads WHERE id::text = %s AND tenant_id = %s", (lead_id, h["id"]))
            lead = cur.fetchone()
            if not lead:
                raise HTTPException(404, "That lead is not there.")
            cid, cname = None, ""
            if body.get("client_tenant_id"):
                cur.execute("SELECT id, name FROM tenants WHERE id::text = %s AND id <> %s",
                            (str(body["client_tenant_id"]), h["id"]))
                c = cur.fetchone()
                if not c:
                    raise HTTPException(404, "That client is not on the platform.")
                cid, cname = c["id"], c["name"]
            cur.execute("UPDATE sales_leads SET client_tenant_id = %s, client_name = %s, "
                        "status = CASE WHEN %s THEN 'won' ELSE status END, "
                        "won_at = CASE WHEN %s AND won_at IS NULL THEN now() ELSE won_at END, updated_at = now() "
                        "WHERE id = %s", (cid, cname, cid is not None, cid is not None, lead["id"]))
            _rec(cur, action="techdeva.sales_client_link", resource="sales_leads", resource_id=lead_id,
                 actor=str(uid), actor_type="human", status="ok",
                 detail={"lead": lead["business_name"], "client": cname or None}, error=None)
        return {"ok": True, "client_name": cname}

    return router
