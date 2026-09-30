"""Growth advisor (Pro): a few plain tips a week from the business's own numbers.

How it stays honest:
  * The findings are worked out by rules from real counts (leads, bookings,
    no-shows, revenue, unpaid bills, lapsed customers, ad results). Each tip
    names the numbers it rests on.
  * The AI only re-words those tips in friendlier language. Its wording is
    kept only if every number in it already appears in the rule-based text;
    otherwise the rule wording is shown. It never invents a figure.
  * Owner-only (it talks about money), and only on the Pro plan.

One report per business per week (Monday, Pakistan time), refreshed at most
once a day on open or every 10 minutes on "Refresh"; earlier weeks stay.
"""
import datetime as _dt
import json
import re

from fastapi import APIRouter, Cookie, Header, HTTPException

from . import ads
from .ai import AIUnavailable, generate_json
from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session
from .plans import plan_for

TZ = "Asia/Karachi"
PKT = _dt.timezone(_dt.timedelta(hours=5))
PLANS_WITH_ADVISOR = ("pro",)
MAX_TIPS = 4
STALE_AFTER = _dt.timedelta(hours=24)
REFRESH_GAP = _dt.timedelta(minutes=10)


def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "Open a business first.")
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can see the growth advisor.")
    return tid, role, uid


def _week_start(now=None):
    d = (now or _dt.datetime.now(PKT)).date()
    return d - _dt.timedelta(days=d.weekday())


def n_of(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def pkr(v):
    return "PKR {:,.0f}".format(float(v or 0))


def _pct(now, before):
    if not before:
        return None
    return round((float(now) - float(before)) * 100.0 / float(before))


# ------------------------------------------------------------------ the numbers
def gather(cur):
    """This week (last 7 days) against the week before, from the business's own
    tables. Runs under the tenant's session, so RLS keeps it to one business."""
    f = {}
    cur.execute("""SELECT count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS now,
                          count(*) FILTER (WHERE created_at >= now() - interval '14 days'
                                             AND created_at < now() - interval '7 days') AS before
                     FROM leads WHERE status <> 'fake'""")
    f["leads"] = dict(cur.fetchone())
    cur.execute("""SELECT coalesce(nullif(click_source, ''), source::text) AS source, count(*) AS n
                     FROM leads WHERE status <> 'fake' AND created_at >= now() - interval '7 days'
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 5""")
    f["lead_sources"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT count(*) AS leads,
                          count(*) FILTER (WHERE status IN ('booked','showed','sold')) AS booked
                     FROM leads WHERE status <> 'fake' AND created_at >= now() - interval '14 days'""")
    f["lead_to_booking_14d"] = dict(cur.fetchone())
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'completed') AS completed,
                          count(*) FILTER (WHERE status = 'no_show') AS no_show,
                          count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                          count(*) FILTER (WHERE status = 'booked') AS still_open
                     FROM appointments
                    WHERE starts_at >= now() - interval '7 days' AND starts_at < now()""")
    f["visits_7d"] = dict(cur.fetchone())
    cur.execute("SELECT count(*) AS n FROM appointments WHERE starts_at >= now() AND status = 'booked'")
    f["upcoming_bookings"] = cur.fetchone()["n"]
    cur.execute("""SELECT coalesce(sum(amount) FILTER (WHERE paid_at >= now() - interval '7 days'), 0)::float AS now,
                          coalesce(sum(amount) FILTER (WHERE paid_at >= now() - interval '14 days'
                                                         AND paid_at < now() - interval '7 days'), 0)::float AS before
                     FROM payments""")
    f["revenue"] = dict(cur.fetchone())
    cur.execute("""SELECT coalesce(sum(amount), 0)::float AS spent FROM expenses
                    WHERE spent_on > (now() AT TIME ZONE %s)::date - 7""", (TZ,))
    f["expenses_7d"] = cur.fetchone()["spent"]
    cur.execute("""SELECT count(*) AS invoices, coalesce(sum(i.total - coalesce(p.paid, 0)), 0)::float AS owed
                     FROM invoices i
                     LEFT JOIN (SELECT invoice_id, sum(amount) AS paid FROM payments GROUP BY 1) p
                            ON p.invoice_id = i.id
                    WHERE i.voided_at IS NULL AND i.status IN ('issued', 'partial')
                      AND i.total - coalesce(p.paid, 0) > 0""")
    f["unpaid"] = dict(cur.fetchone())
    cur.execute("""SELECT count(*) AS n FROM (
                     SELECT a.customer_id, max(a.starts_at) AS last_visit
                       FROM appointments a
                      WHERE a.customer_id IS NOT NULL AND a.status = 'completed'
                      GROUP BY 1) v
                    WHERE v.last_visit < now() - interval '60 days'
                      AND v.last_visit >= now() - interval '180 days'
                      AND NOT EXISTS (SELECT 1 FROM appointments b WHERE b.customer_id = v.customer_id
                                        AND b.starts_at >= now() AND b.status = 'booked')""")
    f["lapsed_customers"] = cur.fetchone()["n"]
    cur.execute("""SELECT coalesce(nullif(service, ''), 'Other') AS service, count(*) AS visits,
                          coalesce(sum(price), 0)::float AS revenue
                     FROM appointments
                    WHERE status = 'completed' AND starts_at >= now() - interval '30 days'
                    GROUP BY 1 ORDER BY 3 DESC, 2 DESC LIMIT 3""")
    f["top_services_30d"] = [dict(r) for r in cur.fetchall()]
    return f


def ad_numbers(tid, role):
    """Last 7 days per connected ad platform; empty when none is connected or
    the platform could not be read (the advisor then says nothing about ads)."""
    out = []
    try:
        perf = ads.performance_for(tid, role, 7)
    except Exception:
        return out
    for p in perf.get("platforms") or []:
        t = p.get("totals")
        if p.get("connected") and t and not p.get("error"):
            results = float(t.get("leads") or 0) + float(t.get("chats") or 0)
            out.append({"platform": "Facebook & Instagram" if p["provider"] == "meta" else "Google",
                        "spend": float(t.get("spend") or 0), "results": results,
                        "cost_per_result": round(float(t["spend"]) / results) if results else None,
                        "currency": p.get("currency") or "PKR"})
    return out


# ------------------------------------------------------------------ the rules
def findings(f, ad):
    """Tips worked out from the numbers, most important first. Every figure in
    a tip comes straight from `f` or `ad`."""
    tips = []

    def tip(score, key, title, why, do):
        tips.append({"key": key, "score": score, "title": title, "why": why, "do": do})

    rev_now, rev_before = f["revenue"]["now"], f["revenue"]["before"]
    ch = _pct(rev_now, rev_before)
    if ch is not None and ch <= -15:
        tip(90, "revenue_down", "Revenue is down %d%% this week" % -ch,
            "You collected %s in the last 7 days, against %s the week before." % (pkr(rev_now), pkr(rev_before)),
            "Call back this week's no-shows and open leads, and promote your best-selling service.")
    elif ch is not None and ch >= 15:
        tip(40, "revenue_up", "Revenue is up %d%% this week" % ch,
            "You collected %s in the last 7 days, against %s the week before." % (pkr(rev_now), pkr(rev_before)),
            "Keep the same ads and offers running; note what changed so you can repeat it.")

    ln, lb = f["leads"]["now"], f["leads"]["before"]
    lch = _pct(ln, lb)
    if lch is not None and lch <= -20 and lb >= 5:
        tip(80, "leads_down", "New leads dropped %d%%" % -lch,
            "%s in the last 7 days, against %d the week before." % (n_of(ln, "new lead", "new leads"), lb),
            "Check your ads are running and post at least 3 times this week.")

    c = f["lead_to_booking_14d"]
    if c["leads"] >= 5:
        rate = round(c["booked"] * 100.0 / c["leads"])
        if rate < 25:
            tip(85, "low_conversion", "Only %d of %d leads booked" % (c["booked"], c["leads"]),
                "In the last 14 days %d%% of new leads booked a visit." % rate,
                "Reply to every lead within 5 minutes and follow up the rest with an offer on WhatsApp.")

    v = f["visits_7d"]
    due = v["completed"] + v["no_show"]
    if v["no_show"] >= 2 and due and v["no_show"] * 100.0 / due >= 15:
        tip(75, "no_shows", "%s this week" % n_of(v["no_show"], "no-show", "no-shows"),
            "%d of %d visits due in the last 7 days did not come." % (v["no_show"], due),
            "Send a WhatsApp reminder the day before and 2 hours before each visit.")

    u = f["unpaid"]
    if u["owed"] > 0 and u["invoices"]:
        tip(70 if u["owed"] >= 10000 else 45, "unpaid",
            "%s is still unpaid" % pkr(u["owed"]),
            "%s a balance due." % n_of(u["invoices"], "bill has", "bills have"),
            "Open Invoices & Payments and collect the balances, starting with the largest.")

    if f["lapsed_customers"] >= 5:
        tip(65, "lapsed", "%d customers have not come back" % f["lapsed_customers"],
            "Their last visit was 2 to 6 months ago and they have nothing booked.",
            "Send them a WhatsApp offer this week to book their next visit.")

    if len(ad) == 2 and all(a["cost_per_result"] for a in ad):
        cheap, dear = sorted(ad, key=lambda a: a["cost_per_result"])
        if dear["cost_per_result"] >= cheap["cost_per_result"] * 1.3:
            tip(80, "ad_mix", "%s brings results cheaper than %s" % (cheap["platform"], dear["platform"]),
                "Last 7 days: %s %s per result, %s %s per result." % (
                    cheap["platform"], pkr(cheap["cost_per_result"]), dear["platform"], pkr(dear["cost_per_result"])),
                "Move part of the %s budget to %s." % (dear["platform"], cheap["platform"]))
    for a in ad:
        if a["spend"] >= 1000 and not a["results"]:
            tip(88, "ad_waste", "%s spent %s with no results" % (a["platform"], pkr(a["spend"])),
                "No leads or chats came from %s in the last 7 days." % a["platform"],
                "Pause those ads or change the picture and offer.")

    if f["expenses_7d"] > rev_now > 0:
        tip(60, "costs", "You spent more than you collected",
            "Expenses were %s in the last 7 days; payments in were %s." % (pkr(f["expenses_7d"]), pkr(rev_now)),
            "Check this week's expenses in Expenses and hold anything that can wait.")

    top = f["top_services_30d"]
    if top and top[0]["revenue"] > 0:
        t = top[0]
        tip(35, "top_service", "%s is your best seller" % t["service"],
            "%s brought %s in the last 30 days." % (n_of(t["visits"], "visit", "visits"), pkr(t["revenue"])),
            "Feature it in your next post and ad.")

    tips.sort(key=lambda t: -t["score"])
    return tips[:MAX_TIPS]


# ------------------------------------------------------------------ the wording
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

WORDING = """You write weekly tips for a small business owner in Pakistan (clinic, salon, gym).
You get tips already worked out from their numbers. Re-word each one warmly and simply,
in plain English a busy owner reads in 5 seconds. Keep every number exactly as given;
never add a number, a percentage, a name or a claim that is not in the tip.
Reply with JSON: {"tips": [{"key": "<same key>",
"title": "<max 8 words>", "why": "<one sentence>", "do": "<one sentence, starts with a verb>"}]}"""


def _numbers(text):
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def word(tips):
    """AI wording when it keeps to the facts; the rule wording otherwise."""
    base = {"headline": ("%s to look at this week" % n_of(len(tips), "thing", "things")) if tips
            else "A quiet week: nothing needs your attention.",
            "tips": [{k: t[k] for k in ("key", "title", "why", "do")} for t in tips]}
    if not tips:
        return base, "rules"
    try:
        out = generate_json(WORDING, json.dumps(base["tips"]), required_keys=("tips",), _retry=False)
    except AIUnavailable:
        return base, "rules"
    got = out.get("tips") if isinstance(out.get("tips"), list) else []
    by_key = {t.get("key"): t for t in got if isinstance(t, dict)}
    worded = []
    for t in base["tips"]:
        w = by_key.get(t["key"]) or {}
        cand = {k: str(w.get(k) or "").strip()[:240] for k in ("title", "why", "do")}
        text = " ".join(cand.values())
        # each tip may use only its own numbers, so a figure cannot wander
        # from one finding into another's sentence
        if all(cand.values()) and _numbers(text) <= _numbers(" ".join((t["title"], t["why"], t["do"]))):
            worded.append(dict(cand, key=t["key"]))
        else:
            worded.append(t)
    # The headline stays the rules' own: a one-line summary is where a model
    # most easily joins true numbers into a false sentence.
    return {"headline": base["headline"], "tips": worded}, "ai"


# ------------------------------------------------------------------ the routes
def _row(cur, week):
    cur.execute("SELECT week_start, facts, advice, worded_by, generated_at FROM advisor_reports "
                "WHERE week_start = %s", (week,))
    return cur.fetchone()


def _build(tid, role, uid, week):
    with tenant_session(tid, role) as cur:
        f = gather(cur)
    ad = ad_numbers(tid, role)
    f["ads_7d"] = ad
    tips = findings(f, ad)
    advice, by = word(tips)
    with tenant_session(tid, role) as cur:
        cur.execute("""INSERT INTO advisor_reports (tenant_id, week_start, facts, advice, worded_by, generated_at)
                       VALUES (%s, %s, %s, %s, %s, now())
                       ON CONFLICT (tenant_id, week_start) DO UPDATE
                         SET facts = EXCLUDED.facts, advice = EXCLUDED.advice,
                             worded_by = EXCLUDED.worded_by, generated_at = now()
                       RETURNING week_start, facts, advice, worded_by, generated_at""",
                    (str(tid), week, json.dumps(f, default=str), json.dumps(advice), by))
        row = cur.fetchone()
        record(cur, action="advisor.report", resource="advisor_reports", resource_id=str(week),
               actor=str(uid), actor_type="human", status="ok",
               detail={"tips": [t["key"] for t in tips], "worded_by": by}, error=None, tenant_id=tid)
    return row


def _answer(row, plan, history):
    return {"available": True, "plan": plan["name"], "week_start": str(row["week_start"]),
            "generated_at": row["generated_at"].isoformat(), "worded_by": row["worded_by"],
            "headline": row["advice"].get("headline"), "tips": row["advice"].get("tips") or [],
            "history": history}


def make_advisor_router():
    router = APIRouter()

    def _open(x_tenant_id, x_role, authorization, td_session, force):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        plan = plan_for(tid)
        if plan["key"] not in PLANS_WITH_ADVISOR and role != "techdeva_admin":
            return {"available": False, "plan": plan["name"]}
        week = _week_start()
        with tenant_session(tid, role) as cur:
            row = _row(cur, week)
            cur.execute("SELECT week_start, advice->>'headline' AS headline FROM advisor_reports "
                        "WHERE week_start < %s ORDER BY week_start DESC LIMIT 6", (week,))
            history = [{"week_start": str(r["week_start"]), "headline": r["headline"]} for r in cur.fetchall()]
        age = (_dt.datetime.now(_dt.timezone.utc) - row["generated_at"]) if row else None
        if not row or age > STALE_AFTER or (force and age > REFRESH_GAP):
            row = _build(tid, role, uid, week)
        elif force:
            raise HTTPException(429, "The advice was refreshed a few minutes ago. Try again in 10 minutes.")
        return _answer(row, plan, history)

    @router.get("/advisor")
    def advisor(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return _open(x_tenant_id, x_role, authorization, td_session, False)

    @router.post("/advisor/refresh")
    def refresh(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return _open(x_tenant_id, x_role, authorization, td_session, True)

    return router
