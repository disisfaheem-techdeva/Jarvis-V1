"""The CRM, upgraded (Faheem, 24 Oct 2026; docs/CRM-UPGRADE.md, db/77).

Built on the existing leads, customers, appointments and conversations; nothing
is duplicated. What this adds:

  /crm/leads              server-side search, filters, sorting and pages
  /crm/leads/{id}         one lead with everything linked to it: follow-ups and
                          notes, its changes (the audit trail), WhatsApp
                          conversations, appointments, the customer it became,
                          and possible duplicates
  /crm/leads/{id}/convert lead -> customer, in one transaction, reusing a
                          matching customer instead of making a second one;
                          pressing it twice changes nothing
  /crm/activities         follow-ups, calls, meetings, visits and notes a PERSON
                          does (leads.followup_at stays the AI agent's)
  /crm/duplicates         possible duplicates, shown for review; never merged
                          automatically
  /crm/team               who a lead or task can be given to

Every read and write goes through row-level security; a link to a lead,
customer or person is also checked in this business (db/77 refuses it in the
database too).
"""
from __future__ import annotations

import datetime as _dt
import re
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

KINDS = ("task", "call", "meeting", "visit", "whatsapp", "email", "note")
CONTACT_KINDS = ("call", "meeting", "visit", "whatsapp", "email")      # doing one counts as contacting the lead
PRIORITIES = ("low", "normal", "high")
CLOSED = ("sold", "lost", "fake")
SORTS = {"newest": "l.created_at DESC", "oldest": "l.created_at ASC", "name": "lower(l.name) ASC, l.created_at DESC",
         "value": "l.est_revenue DESC NULLS LAST, l.created_at DESC", "updated": "l.updated_at DESC",
         "followup": "n.due_at ASC NULLS LAST, l.created_at DESC"}
VIEWS = ("mine", "all", "today", "upcoming", "overdue", "done")
SOURCE_TO_CUSTOMER = {"hermes": "whatsapp", "phone_ai": "phone", "facebook": "facebook", "instagram": "instagram"}


def phone_key(p):
    """One number written different ways (+92 300..., 0300..., 300-...) gives the same key."""
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 7 else None


def _uuid(v, what="record"):
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError):
        raise HTTPException(404, "That %s was not found." % what)


def _is_person(uid):
    try:
        uuid.UUID(str(uid))
        return True
    except (ValueError, TypeError):
        return False


def _when(v, what="date"):
    if v in (None, ""):
        return None
    try:
        t = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "The %s is not a valid date and time." % what)
    if t.tzinfo is None:
        from zoneinfo import ZoneInfo
        t = t.replace(tzinfo=ZoneInfo("Asia/Karachi"))
    return t


def _tz(cur):
    from .dashboard import _tz as tz
    return tz(cur)


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _iso(r):
    return {k: (v.isoformat() if isinstance(v, (_dt.datetime, _dt.date)) else (str(v) if isinstance(v, uuid.UUID) else v))
            for k, v in dict(r).items()}


def duplicates_for(cur, phone=None, email=None, exclude_lead=None, exclude_customer=None):
    """Leads and customers in this business with the same phone (any spelling) or email."""
    key, mail = phone_key(phone), (str(email or "").strip().lower() or None)
    if not key and not mail:
        return []
    out = []
    cur.execute("""SELECT 'lead' AS type, id, name, phone, email, status::text AS status, created_at FROM leads
                    WHERE (%s::text IS NOT NULL AND right(regexp_replace(coalesce(phone,''), '\\D', '', 'g'), 10) = %s)
                       OR (%s::text IS NOT NULL AND lower(email) = %s)
                    ORDER BY created_at LIMIT 20""", (key, key, mail, mail))
    out += [r for r in cur.fetchall() if str(r["id"]) != str(exclude_lead)]
    cur.execute("""SELECT 'customer' AS type, id, name, phone, email, status, created_at FROM customers
                    WHERE (%s::text IS NOT NULL AND right(regexp_replace(coalesce(phone,''), '\\D', '', 'g'), 10) = %s)
                       OR (%s::text IS NOT NULL AND lower(email) = %s)
                    ORDER BY created_at LIMIT 20""", (key, key, mail, mail))
    out += [r for r in cur.fetchall() if str(r["id"]) != str(exclude_customer)]
    for r in out:
        why = []
        if key and phone_key(r["phone"]) == key:
            why.append("same phone number")
        if mail and (r["email"] or "").lower() == mail:
            why.append("same email")
        r["why"] = " and ".join(why)
    return [_iso(r) for r in out]


def make_crm_router(identity):
    router = APIRouter(prefix="/crm", tags=["crm"])

    def who(xt, xr, auth, sess):
        tid, role, uid = identity(xt, xr, auth, sess)
        if not tid:
            raise HTTPException(400, "Choose a business first.")
        return tid, role, uid

    # ------------------------------------------------------------------ leads
    @router.get("/leads")
    def leads(q: str = "", status: str = "", source: str = "", owner: str = "", follow: str = "",
              created_from: str = "", created_to: str = "", sort: str = "newest",
              page: int = Query(1, ge=1, le=10000), page_size: int = Query(25, ge=5, le=100),
              x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Leads, searched and filtered on the server, one page at a time."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if sort not in SORTS:
            raise HTTPException(400, "Sort by one of: %s." % ", ".join(SORTS))
        where, args = [], []
        q = q.strip()[:100]
        if q:
            digits = re.sub(r"\D", "", q)
            cond = "(l.name ILIKE %s OR l.email ILIKE %s OR l.company ILIKE %s OR l.service ILIKE %s OR l.phone ILIKE %s"
            args += [_like(q)] * 5
            if len(digits) >= 4:
                cond += " OR regexp_replace(coalesce(l.phone,''), '\\D', '', 'g') LIKE %s"
                args.append(_like(digits[-10:]))
            where.append(cond + ")")
        if status:
            sts = [s for s in status.split(",") if s]
            from .main import LEAD_STATUSES
            if any(s not in LEAD_STATUSES for s in sts):
                raise HTTPException(400, "Status must be one of: %s." % ", ".join(LEAD_STATUSES))
            where.append("l.status::text = ANY(%s)")
            args.append(sts)
        if source:
            where.append("l.source::text = %s")
            args.append(source)
        if owner == "me":
            if not _is_person(uid):
                raise HTTPException(400, "Sign in as a person to see your own leads.")
            where.append("l.assigned_operator = %s")
            args.append(str(uid))
        elif owner == "none":
            where.append("l.assigned_operator IS NULL")
        elif owner:
            where.append("l.assigned_operator = %s")
            args.append(_uuid(owner, "person"))
        with tenant_session(tid, role) as cur:
            tz = _tz(cur)
            if follow == "overdue":
                where.append("EXISTS (SELECT 1 FROM crm_activities a WHERE a.lead_id = l.id AND a.done_at IS NULL AND a.due_at < now())")
            elif follow == "today":
                where.append("EXISTS (SELECT 1 FROM crm_activities a WHERE a.lead_id = l.id AND a.done_at IS NULL "
                             "AND (a.due_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date)")
                args += [tz, tz]
            elif follow == "none":
                where.append("NOT EXISTS (SELECT 1 FROM crm_activities a WHERE a.lead_id = l.id AND a.done_at IS NULL) "
                             "AND l.status::text <> ALL(%s)")
                args.append(list(CLOSED))
            elif follow:
                raise HTTPException(400, "Follow-up filter must be overdue, today or none.")
            for v, op in ((created_from, ">="), (created_to, "<=")):
                if v:
                    try:
                        d = _dt.date.fromisoformat(v)
                    except ValueError:
                        raise HTTPException(400, "Dates must look like 2026-10-24.")
                    where.append("(l.created_at AT TIME ZONE %%s)::date %s %%s" % op)
                    args += [tz, d]
            sql = """SELECT l.id, l.name, l.phone, l.email, l.company, l.service, l.status::text AS status, l.source::text AS source,
                            l.priority, l.est_revenue, l.assigned_operator, u.name AS owner_name, l.created_at, l.updated_at,
                            l.last_contacted_at, l.converted_customer_id, l.lost_reason,
                            n.id AS next_id, n.title AS next_title, n.due_at AS next_due_at,
                            count(*) OVER () AS total
                       FROM leads l
                       LEFT JOIN users u ON u.id = l.assigned_operator
                       LEFT JOIN LATERAL (SELECT a.id, a.title, a.due_at FROM crm_activities a
                                           WHERE a.lead_id = l.id AND a.done_at IS NULL
                                           ORDER BY a.due_at ASC NULLS LAST LIMIT 1) n ON true
                      %s ORDER BY %s LIMIT %%s OFFSET %%s""" % (("WHERE " + " AND ".join(where)) if where else "", SORTS[sort])
            cur.execute(sql, args + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()
            cur.execute("SELECT status::text AS s, count(*) AS n FROM leads GROUP BY 1")
            by_status = {r["s"]: int(r["n"]) for r in cur.fetchall()}
        total = int(rows[0]["total"]) if rows else 0
        out = []
        for r in rows:
            d = _iso(r)
            d.pop("total", None)
            d["est_revenue"] = float(r["est_revenue"]) if r["est_revenue"] is not None else None
            d["overdue"] = bool(r["next_due_at"] and r["next_due_at"] < _dt.datetime.now(_dt.timezone.utc))
            out.append(d)
        return {"leads": out, "total": total, "page": page, "page_size": page_size, "by_status": by_status}

    @router.get("/leads/{lead_id}")
    def lead(lead_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One lead and everything linked to it, newest first."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        lid = _uuid(lead_id, "lead")
        with tenant_session(tid, role) as cur:
            tz = _tz(cur)
            cur.execute("""SELECT l.id, l.name, l.phone, l.email, l.company, l.service, l.department, l.status::text AS status,
                                  l.source::text AS source, l.priority, l.est_revenue, l.notes, l.tags, l.assigned_operator,
                                  u.name AS owner_name, l.assigned_agent, l.appointment_at, l.followup_at, l.created_at,
                                  l.updated_at, l.last_contacted_at, l.lost_reason, l.converted_customer_id, l.converted_at,
                                  l.status_changed_at, l.click_source, c.name AS customer_name
                             FROM leads l LEFT JOIN users u ON u.id = l.assigned_operator
                             LEFT JOIN customers c ON c.id = l.converted_customer_id
                            WHERE l.id = %s""", (lid,))
            l = cur.fetchone()
            if not l:
                raise HTTPException(404, "That lead was not found.")
            cur.execute("""SELECT a.id, a.kind, a.title, a.body, a.due_at, a.done_at, a.outcome, a.priority, a.assigned_to,
                                  ua.name AS assigned_name, uc.name AS created_name, a.source, a.created_at, a.customer_id
                             FROM crm_activities a LEFT JOIN users ua ON ua.id = a.assigned_to
                             LEFT JOIN users uc ON uc.id = a.created_by
                            WHERE a.lead_id = %s OR (%s::uuid IS NOT NULL AND a.customer_id = %s::uuid)
                            ORDER BY coalesce(a.done_at, a.due_at, a.created_at) DESC LIMIT 200""",
                        (lid, l["converted_customer_id"], l["converted_customer_id"]))
            acts = [_iso(r) for r in cur.fetchall()]
            cur.execute("""SELECT action, actor, actor_type, detail, at FROM executions
                            WHERE resource = 'leads' AND resource_id = %s ORDER BY at DESC LIMIT 50""", (lid,))
            changes = [_iso(r) for r in cur.fetchall()]
            cur.execute("SELECT id, channel, status::text AS status, created_at FROM conversations WHERE lead_id = %s ORDER BY created_at DESC LIMIT 10", (lid,))
            convs = [_iso(r) for r in cur.fetchall()]
            cur.execute("""SELECT a.id, to_char(a.starts_at AT TIME ZONE %s, 'YYYY-MM-DD HH24:MI') AS at, a.status, a.service
                             FROM appointments a WHERE a.lead_id = %s OR (%s::uuid IS NOT NULL AND a.customer_id = %s::uuid)
                            ORDER BY a.starts_at DESC LIMIT 20""", (tz, lid, l["converted_customer_id"], l["converted_customer_id"]))
            appts = [_iso(r) for r in cur.fetchall()]
            dups = duplicates_for(cur, l["phone"], l["email"], exclude_lead=lid, exclude_customer=l["converted_customer_id"])
        d = _iso(l)
        d["est_revenue"] = float(l["est_revenue"]) if l["est_revenue"] is not None else None
        return {"lead": d, "activities": acts, "changes": changes, "conversations": convs, "appointments": appts,
                "duplicates": dups}

    @router.post("/leads/{lead_id}/convert")
    def convert(lead_id: str, body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Make the lead a customer. Reuses a matching customer; never makes a second
        one; pressing twice changes nothing. All or nothing."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        lid = _uuid(lead_id, "lead")
        chosen = body.get("customer_id")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT * FROM leads WHERE id = %s FOR UPDATE", (lid,))
            l = cur.fetchone()
            if not l:
                raise HTTPException(404, "That lead was not found.")
            if l["converted_customer_id"]:
                return {"customer_id": str(l["converted_customer_id"]), "already": True, "created": False}
            if chosen:
                cur.execute("SELECT id FROM customers WHERE id = %s", (_uuid(chosen, "customer"),))
                c = cur.fetchone()
                if not c:
                    raise HTTPException(404, "That customer was not found in this business.")
                cid, created = str(c["id"]), False
            else:
                matches = [m for m in duplicates_for(cur, l["phone"], l["email"]) if m["type"] == "customer"]
                if len(matches) > 1 and not body.get("create_new"):
                    raise HTTPException(409, {"message": "More than one customer matches this lead. Choose which one it is.",
                                              "candidates": matches})
                if matches and not body.get("create_new"):
                    cid, created = matches[0]["id"], False
                else:
                    if not (l["name"] or "").strip():
                        raise HTTPException(400, "The lead needs a name before it can become a customer.")
                    src = SOURCE_TO_CUSTOMER.get(str(l["source"])) or {"meta": "facebook", "google": "google"}.get(l["click_source"] or "", "other")
                    cur.execute("""INSERT INTO customers (tenant_id, lead_id, name, phone, email, source, notes)
                                   VALUES (current_tenant(), %s, %s, %s, %s, %s, %s) RETURNING id""",
                                (lid, l["name"].strip()[:200], (l["phone"] or None), (l["email"] or None), src, l["notes"]))
                    cid, created = str(cur.fetchone()["id"]), True
            cur.execute("UPDATE customers SET lead_id = COALESCE(lead_id, %s), updated_at = now() WHERE id = %s", (lid, cid))
            cur.execute("UPDATE leads SET converted_customer_id = %s, converted_at = now(), updated_at = now() WHERE id = %s", (cid, lid))
            cur.execute("UPDATE crm_activities SET customer_id = %s, updated_at = now() WHERE lead_id = %s AND customer_id IS NULL", (cid, lid))
            record(cur, action="lead.convert", resource="leads", resource_id=lid, actor=str(uid or role),
                   detail={"customer_id": cid, "new_customer": created}, tenant_id=tid)
        return {"customer_id": cid, "already": False, "created": created}

    @router.get("/duplicates")
    def duplicates(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Groups of leads and customers that look like the same person. For review only."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""WITH people AS (
                             SELECT 'lead' AS type, id, name, phone, email, status::text AS status, created_at FROM leads
                             UNION ALL SELECT 'customer', id, name, phone, email, status, created_at FROM customers),
                           keys AS (
                             SELECT *, 'phone:' || right(regexp_replace(coalesce(phone,''), '\\D', '', 'g'), 10) AS k FROM people
                              WHERE length(regexp_replace(coalesce(phone,''), '\\D', '', 'g')) >= 7
                             UNION ALL SELECT *, 'email:' || lower(email) FROM people WHERE coalesce(email, '') <> '')
                           SELECT k, json_agg(json_build_object('type', type, 'id', id, 'name', name, 'phone', phone, 'email', email,
                                                                'status', status, 'created_at', created_at) ORDER BY created_at) AS records
                             FROM keys GROUP BY k HAVING count(*) > 1 ORDER BY count(*) DESC LIMIT 100""")
            groups = [{"reason": "same phone number" if r["k"].startswith("phone:") else "same email",
                       "records": r["records"]} for r in cur.fetchall()]
        return {"groups": groups, "note": "Shown for review. Nothing is merged automatically: two people can share a number."}

    @router.get("/duplicates/check")
    def duplicates_check(phone: str = "", email: str = "", x_tenant_id: str | None = Header(None),
                         x_role: str | None = Header(None), authorization: str | None = Header(None),
                         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Before adding a lead: is this person already here?"""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {"matches": duplicates_for(cur, phone, email)}

    @router.get("/team")
    def team(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, name FROM users ORDER BY name")
            return {"people": [{"id": str(r["id"]), "name": r["name"], "me": str(r["id"]) == str(uid)} for r in cur.fetchall()]}

    # ------------------------------------------------------------------ activities
    def _check_links(cur, lead_id, customer_id, assigned_to):
        if lead_id:
            cur.execute("SELECT 1 FROM leads WHERE id = %s", (lead_id,))
            if not cur.fetchone():
                raise HTTPException(404, "That lead was not found.")
        if customer_id:
            cur.execute("SELECT 1 FROM customers WHERE id = %s", (customer_id,))
            if not cur.fetchone():
                raise HTTPException(404, "That customer was not found.")
        if assigned_to:
            cur.execute("SELECT 1 FROM users WHERE id = %s", (assigned_to,))
            if not cur.fetchone():
                raise HTTPException(400, "That person is not in this business.")

    def _touch_contact(cur, lead_id, customer_id, at):
        if lead_id:
            cur.execute("UPDATE leads SET last_contacted_at = GREATEST(coalesce(last_contacted_at, %s), %s) WHERE id = %s", (at, at, lead_id))
        if customer_id:
            cur.execute("UPDATE customers SET last_interaction_at = GREATEST(coalesce(last_interaction_at, %s), %s) WHERE id = %s",
                        (at, at, customer_id))

    ACT_COLS = """a.id, a.lead_id, a.customer_id, a.kind, a.title, a.body, a.due_at, a.done_at, a.outcome, a.priority,
                  a.assigned_to, ua.name AS assigned_name, a.created_by, a.source, a.created_at, a.updated_at,
                  l.name AS lead_name, c.name AS customer_name"""
    ACT_FROM = """FROM crm_activities a LEFT JOIN users ua ON ua.id = a.assigned_to
                  LEFT JOIN leads l ON l.id = a.lead_id LEFT JOIN customers c ON c.id = a.customer_id"""

    @router.get("/activities")
    def activities(view: str = "all", lead_id: str = "", customer_id: str = "", assigned_to: str = "",
                   page: int = Query(1, ge=1, le=10000), page_size: int = Query(50, ge=5, le=200),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Follow-ups: mine, all open, due today, upcoming, overdue (kept until done or moved), done."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if view not in VIEWS:
            raise HTTPException(400, "View must be one of: %s." % ", ".join(VIEWS))
        where, args = [], []
        with tenant_session(tid, role) as cur:
            tz = _tz(cur)
            if view == "done":
                where.append("a.done_at IS NOT NULL")
            else:
                where.append("a.done_at IS NULL AND a.kind <> 'note'")
            if view == "mine":
                if not _is_person(uid):
                    raise HTTPException(400, "Sign in as a person to see your own follow-ups.")
                where.append("a.assigned_to = %s")
                args.append(str(uid))
            elif view == "today":
                where.append("(a.due_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date")
                args += [tz, tz]
            elif view == "overdue":
                where.append("a.due_at < now()")
            elif view == "upcoming":
                where.append("(a.due_at IS NULL OR (a.due_at AT TIME ZONE %s)::date > (now() AT TIME ZONE %s)::date)")
                args += [tz, tz]
            if lead_id:
                where.append("a.lead_id = %s")
                args.append(_uuid(lead_id, "lead"))
            if customer_id:
                where.append("a.customer_id = %s")
                args.append(_uuid(customer_id, "customer"))
            if assigned_to:
                where.append("a.assigned_to = %s")
                args.append(_uuid(assigned_to, "person"))
            order = "a.done_at DESC" if view == "done" else "a.due_at ASC NULLS LAST, a.created_at"
            cur.execute("SELECT " + ACT_COLS + ", count(*) OVER () AS total " + ACT_FROM + " WHERE " + " AND ".join(where) +
                        " ORDER BY " + order + " LIMIT %s OFFSET %s", args + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()
            cur.execute("""SELECT count(*) FILTER (WHERE due_at < now()) AS overdue,
                                  count(*) FILTER (WHERE (due_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date) AS today,
                                  count(*) FILTER (WHERE assigned_to = %s) AS mine, count(*) AS open
                             FROM crm_activities WHERE done_at IS NULL AND kind <> 'note'""",
                        (tz, tz, str(uid) if _is_person(uid) else None))
            counts = {k: int(v) for k, v in cur.fetchone().items()}
        now = _dt.datetime.now(_dt.timezone.utc)
        out = []
        for r in rows:
            d = _iso(r)
            d.pop("total", None)
            d["overdue"] = bool(r["done_at"] is None and r["due_at"] and r["due_at"] < now)
            out.append(d)
        return {"activities": out, "total": int(rows[0]["total"]) if rows else 0, "page": page, "counts": counts}

    @router.post("/activities", status_code=201)
    def add_activity(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A follow-up to do, or something already done (a call, a visit, a note)."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        kind = body.get("kind") or "task"
        if kind not in KINDS:
            raise HTTPException(400, "Kind must be one of: %s." % ", ".join(KINDS))
        title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()
        if not title:
            raise HTTPException(400, "Give it a short title, e.g. 'Call about the price'.")
        if len(title) > 200 or len(str(body.get("body") or "")) > 4000:
            raise HTTPException(400, "That is too long.")
        lead_id = _uuid(body["lead_id"], "lead") if body.get("lead_id") else None
        customer_id = _uuid(body["customer_id"], "customer") if body.get("customer_id") else None
        if not lead_id and not customer_id:
            raise HTTPException(400, "Say which lead or customer this is for.")
        priority = body.get("priority") or "normal"
        if priority not in PRIORITIES:
            raise HTTPException(400, "Priority must be low, normal or high.")
        due = _when(body.get("due_at"), "due date")
        done = bool(body.get("done")) or kind == "note"
        assigned = body.get("assigned_to")
        assigned = _uuid(assigned, "person") if assigned else (str(uid) if _is_person(uid) and not done else None)
        src = "jarvis" if body.get("via") == "jarvis" else "person"
        with tenant_session(tid, role) as cur:
            _check_links(cur, lead_id, customer_id, assigned)
            if lead_id and not customer_id:
                cur.execute("SELECT converted_customer_id FROM leads WHERE id = %s", (lead_id,))
                customer_id = (cur.fetchone() or {}).get("converted_customer_id")
            cur.execute("""INSERT INTO crm_activities (tenant_id, lead_id, customer_id, kind, title, body, due_at, done_at, outcome,
                                                       priority, assigned_to, created_by, source)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, done_at""",
                        (lead_id, customer_id, kind, title, (str(body.get("body") or "").strip() or None), due,
                         _dt.datetime.now(_dt.timezone.utc) if done else None,
                         (str(body.get("outcome") or "").strip()[:500] or None), priority, assigned,
                         str(uid) if _is_person(uid) else None, src))
            r = cur.fetchone()
            if done and kind in CONTACT_KINDS:
                _touch_contact(cur, lead_id, customer_id, r["done_at"])
            record(cur, action="crm.activity.add", resource="crm_activities", resource_id=str(r["id"]), actor=str(uid or role),
                   detail={"kind": kind, "lead_id": lead_id, "customer_id": str(customer_id) if customer_id else None,
                           "done": done, "via": src}, tenant_id=tid)
            cur.execute("SELECT " + ACT_COLS + " " + ACT_FROM + " WHERE a.id = %s", (r["id"],))
            return {"activity": _iso(cur.fetchone())}

    @router.patch("/activities/{activity_id}")
    def edit_activity(activity_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None), authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Mark done (or not), move the date, change who does it, add the outcome."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        aid = _uuid(activity_id, "follow-up")
        allowed = {"done", "due_at", "title", "body", "outcome", "priority", "assigned_to"}
        bad = sorted(set(body) - allowed)
        if bad:
            raise HTTPException(400, "Cannot change: %s." % ", ".join(bad))
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT * FROM crm_activities WHERE id = %s FOR UPDATE", (aid,))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404, "That follow-up was not found.")
            sets, args = [], []
            if "done" in body:
                sets.append("done_at = %s")
                args.append(_dt.datetime.now(_dt.timezone.utc) if body["done"] else None)
            if "due_at" in body:
                sets.append("due_at = %s")
                args.append(_when(body["due_at"], "due date"))
            if "title" in body:
                t = re.sub(r"\s+", " ", str(body["title"] or "")).strip()
                if not t or len(t) > 200:
                    raise HTTPException(400, "The title must be 1 to 200 characters.")
                sets.append("title = %s")
                args.append(t)
            for k in ("body", "outcome"):
                if k in body:
                    sets.append("%s = %%s" % k)
                    args.append(str(body[k] or "").strip()[:4000 if k == "body" else 500] or None)
            if "priority" in body:
                if body["priority"] not in PRIORITIES:
                    raise HTTPException(400, "Priority must be low, normal or high.")
                sets.append("priority = %s")
                args.append(body["priority"])
            if "assigned_to" in body:
                who_ = _uuid(body["assigned_to"], "person") if body["assigned_to"] else None
                _check_links(cur, None, None, who_)
                sets.append("assigned_to = %s")
                args.append(who_)
            if not sets:
                raise HTTPException(400, "Nothing to change.")
            cur.execute("UPDATE crm_activities SET " + ", ".join(sets) + ", updated_at = now() WHERE id = %s RETURNING done_at",
                        args + [aid])
            done_at = cur.fetchone()["done_at"]
            if body.get("done") and a["done_at"] is None and a["kind"] in CONTACT_KINDS:
                _touch_contact(cur, a["lead_id"], a["customer_id"], done_at)
            record(cur, action="crm.activity.edit", resource="crm_activities", resource_id=aid, actor=str(uid or role),
                   detail={"fields": sorted(body)}, tenant_id=tid)
            cur.execute("SELECT " + ACT_COLS + " " + ACT_FROM + " WHERE a.id = %s", (aid,))
            return {"activity": _iso(cur.fetchone())}

    @router.delete("/activities/{activity_id}")
    def delete_activity(activity_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                        authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Remove a follow-up or note: the owner, or the person who wrote it."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        aid = _uuid(activity_id, "follow-up")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT created_by, kind, title FROM crm_activities WHERE id = %s", (aid,))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404, "That follow-up was not found.")
            if role not in ("owner", "techdeva_admin") and str(a["created_by"]) != str(uid):
                raise HTTPException(403, "Only the owner or the person who wrote it can remove it.")
            cur.execute("DELETE FROM crm_activities WHERE id = %s", (aid,))
            record(cur, action="crm.activity.delete", resource="crm_activities", resource_id=aid, actor=str(uid or role),
                   detail={"kind": a["kind"], "title": a["title"]}, tenant_id=tid)
        return {"ok": True}

    return router
