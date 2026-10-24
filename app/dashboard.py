"""Dashboard 2.0 data (Faheem, 24 Oct 2026).

Two reads for the Overview page, built on the tables and rules that already
exist (no new tables, no second set of definitions):

  GET /dashboard/overview     orders and sales (orders.summary, the Orders page's
                              own numbers), setup progress (the setup wizard's own
                              rules), AI work and approvals (what the Approval
                              Queue lists), connections (connected AND healthy),
                              this month's money (the P&L page's definitions),
                              appointments, customers, and Today's focus.
  GET /dashboard/performance  one figure per day for a period, with the period
                              before it for comparison.

Who sees what: a block is included only when the person may view its section
(app/roles.py), and money only for the logins that already see money (base
owner; staff never did). A block left out says why, so the page can show "—"
with the reason instead of a 0.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Cookie, Header, HTTPException

from .auth import SESSION_COOKIE
from .db import tenant_session

DEFAULT_TZ = "Asia/Karachi"
RANGES = (7, 30, 90)
MAX_DAYS = 366
METRICS = {
    # key: (label, section, money, what it counts)
    "sales":     ("Sales", "finance", True, "Orders delivered and fully paid, by the day they became a sale"),
    "collected": ("Payments collected", "finance", True, "Every payment received, by the day it was paid"),
    "orders":    ("Orders placed", "operations", False, "Orders by the day they were placed, cancelled ones included"),
    "customers": ("New customers", "crm", False, "Customers by the day they were added"),
    "leads":     ("New leads", "crm", False, "Leads by the day they arrived"),
    "appointments": ("Appointments booked", "crm", False, "Appointments by the day they were booked, cancelled ones included"),
}
# the setup wizard (web/onboarding.html) decides what "done" means; same rules here
SETUP_STEPS = (
    ("brand", "Your name, logo and colours", None), ("services", "What you sell", None),
    ("price_list", "What it costs", None), ("working_hours", "When you are open", None),
    ("geo", "Where you are", None), ("hermes_persona", "How your AI should sound", None),
    ("locations", "Where you work", "locations"), ("staff", "Who works there", "staff"),
    ("products", "What you sell over the counter", "products"),
    ("business_goals", "What you are aiming for", "business_goals"),
    ("booking_rules", "How appointments work", None),
)
SETUP_OPTIONAL = {"products", "business_goals"}
SETUP_REQUIRED = {"brand": ["name"], "geo": ["city"], "working_hours": ["mon_sat"], "booking_rules": ["cancellation_hours"]}
BAD_HEALTH = ("error", "failing", "down", "expired", "revoked")


# ------------------------------------------------------------------ who may see what
def _access(uid, role):
    """(sections the person may view or None for all, may see money)."""
    from .roles import permissions_for
    perms = permissions_for(uid) if uid else None
    may = None
    if perms:
        may = {s for s, acts in (perms.get("permissions") or {}).items() if "view" in acts}
    money = role in ("owner", "techdeva_admin") and (may is None or "finance" in may)
    return may, money


def _sees(may, section):
    return may is None or section in may


def _hidden(section):
    from .roles import SECTIONS
    return {"hidden": "Your role does not include %s." % SECTIONS.get(section, section)}


def _tz(cur):
    """The business's own time zone (Settings), so "today" is its today."""
    cur.execute("SELECT timezone FROM tenant_config WHERE tenant_id = current_tenant()")
    r = cur.fetchone()
    tz = (r or {}).get("timezone") or DEFAULT_TZ
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TZ
    return tz


def _num(v):
    return round(float(v or 0), 2)


# ------------------------------------------------------------------ blocks
def setup_progress(cur):
    """The setup wizard's steps, with its own idea of done (not just "not empty")."""
    cur.execute("""SELECT brand, services, price_list, working_hours, geo, hermes_persona, booking_rules
                     FROM tenant_config WHERE tenant_id = current_tenant()""")
    cfg = cur.fetchone() or {}
    done, missing = [], []
    for key, label, table in SETUP_STEPS:
        if table:
            if key in SETUP_OPTIONAL:
                ok = True
            else:
                cur.execute("SELECT EXISTS (SELECT 1 FROM %s) AS x" % table)   # table names are ours, not input
                ok = bool(cur.fetchone()["x"])
        else:
            v = cfg.get(key)
            if v is None:
                ok = False
            elif isinstance(v, list):
                ok = len(v) > 0
            elif isinstance(v, str):
                ok = bool(v.strip())
            elif isinstance(v, dict):
                need = SETUP_REQUIRED.get(key)
                ok = any(str(v.get(k) or "").strip() for k in need) if need else len(v) > 0
            else:
                ok = bool(v)
        (done if ok else missing).append({"key": key, "label": label})
    total = len(SETUP_STEPS)
    return {"complete": len(done), "total": total, "percent": round(100 * len(done) / total),
            "next": missing[0] if missing else None, "missing": missing, "href": "/onboarding"}


def integrations(cur):
    cur.execute("""SELECT provider, status, COALESCE(health, 'unknown') AS health, expires_at, last_error
                     FROM integrations""")
    rows = cur.fetchall()
    now = _dt.datetime.now(_dt.timezone.utc)
    healthy, attention = [], []
    configured = [r for r in rows if r["status"] not in ("never_connected",)]
    for r in rows:
        expired = r["expires_at"] is not None and r["expires_at"] < now
        bad = r["health"] in BAD_HEALTH or expired
        if r["status"] == "connected" and not bad:
            healthy.append(r["provider"])
        elif r["status"] == "connected" or r["status"] in ("error", "expired"):
            attention.append({"provider": r["provider"],
                              "why": "the login has expired" if expired else "the last check failed"})
    return {"healthy": len(healthy), "configured": len(configured), "attention": attention,
            "providers": sorted(healthy)}


def ai_work(cur, owner_like):
    cur.execute("SELECT status::text AS s, count(*) AS n FROM tasks GROUP BY 1")
    tasks = {r["s"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute("SELECT status, count(*) AS n FROM deliverables GROUP BY 1")
    deliv = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute("SELECT count(*) AS n FROM analysis_reports")
    runs = int(cur.fetchone()["n"])
    posts = None
    if owner_like:
        # the Approval Queue lists drafts and failed posts for the owner too
        cur.execute("SELECT count(*) AS n FROM social_posts WHERE status IN ('draft','failed')")
        posts = int(cur.fetchone()["n"])
    approvals = {"deliverables": deliv.get("awaiting_approval", 0), "paused_tasks": tasks.get("needs_human", 0),
                 "posts": posts}
    approvals["total"] = approvals["deliverables"] + approvals["paused_tasks"] + (posts or 0)
    return {"tasks": {"total": sum(tasks.values()), "running": tasks.get("running", 0),
                      "waiting": tasks.get("queued", 0) + tasks.get("waiting", 0), "done": tasks.get("done", 0),
                      "failed": tasks.get("failed", 0) + tasks.get("dead", 0), "paused": tasks.get("needs_human", 0)},
            "deliverables": {"total": sum(deliv.values()), "by_status": deliv},
            "approvals": approvals, "analysis_runs": runs}


def month_money(cur, tz):
    """The P&L page's definitions for the current month in the business's time zone."""
    cur.execute("SELECT date_trunc('month', now() AT TIME ZONE %s)::date AS m, (now() AT TIME ZONE %s)::date AS d", (tz, tz))
    r = cur.fetchone()
    start, today = r["m"], r["d"]
    cur.execute("SELECT COALESCE(sum(amount), 0) AS a, count(*) AS n FROM payments "
                "WHERE (paid_at AT TIME ZONE %s)::date BETWEEN %s AND %s", (tz, start, today))
    p = cur.fetchone()
    cur.execute("SELECT COALESCE(sum(total), 0) AS a, count(*) AS n FROM invoices "
                "WHERE issued_on BETWEEN %s AND %s AND voided_at IS NULL", (start, today))
    i = cur.fetchone()
    cur.execute("SELECT COALESCE(sum(i.total), 0) - COALESCE((SELECT sum(p.amount) FROM payments p JOIN invoices i2 "
                "ON i2.id = p.invoice_id WHERE i2.voided_at IS NULL), 0) AS a FROM invoices i WHERE i.voided_at IS NULL")
    out = _num(cur.fetchone()["a"])
    return {"start": str(start), "end": str(today), "collected": _num(p["a"]), "payment_count": int(p["n"]),
            "invoiced": _num(i["a"]), "invoice_count": int(i["n"]), "outstanding_all_time": out}


def people(cur, tz):
    cur.execute("""SELECT count(*) FILTER (WHERE starts_at >= now() AND status <> 'cancelled') AS upcoming,
                          count(*) FILTER (WHERE (starts_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date
                                             AND status <> 'cancelled') AS today
                     FROM appointments""", (tz, tz))
    a = cur.fetchone()
    cur.execute("""SELECT count(*) AS total,
                          count(*) FILTER (WHERE created_at AT TIME ZONE %s >= date_trunc('month', now() AT TIME ZONE %s)) AS month
                     FROM customers""", (tz, tz))
    c = cur.fetchone()
    cur.execute("""SELECT count(*) AS total,
                          count(*) FILTER (WHERE status = 'new') AS new,
                          count(*) FILTER (WHERE status IN ('qualified','booked','showed')) AS in_progress,
                          count(*) FILTER (WHERE status = 'sold') AS won
                     FROM leads""")
    l = dict(cur.fetchone())
    # the team's own follow-ups (CRM, db/77), due today or late; the WhatsApp
    # agent's automatic follow-up (leads.followup_at) is the agent's work, not the team's
    cur.execute("SELECT to_regclass('crm_activities') IS NOT NULL AS ok")
    if cur.fetchone()["ok"]:
        cur.execute("""SELECT count(*) AS n FROM crm_activities WHERE done_at IS NULL AND kind <> 'note'
                          AND (due_at AT TIME ZONE %s)::date <= (now() AT TIME ZONE %s)::date""", (tz, tz))
        l["followups_due"] = int(cur.fetchone()["n"])
    else:
        l["followups_due"] = 0
    return {"appointments": {"upcoming": int(a["upcoming"]), "today": int(a["today"])},
            "customers": {"total": int(c["total"]), "new_this_month": int(c["month"])},
            "leads": {k: int(v) for k, v in l.items()}}


def focus(blocks, money_ok, cur, may):
    """Today's focus: work that is waiting, from the records, each with where to do it."""
    items = []

    def add(key, n, one, many, label, href):
        if n:
            items.append({"key": key, "count": n, "text": (one if n == 1 else many) % n, "action": label, "href": href})

    o = blocks.get("orders") or {}
    c = o.get("counts") or {}
    add("orders_new", c.get("new", 0), "%d order waiting for you to confirm", "%d orders waiting for you to confirm",
        "Review orders", "#/orders?status=new")
    add("orders_delivery", c.get("waiting_delivery", 0), "%d confirmed order still to deliver",
        "%d confirmed orders still to deliver", "Open deliveries", "#/orders?status=waiting")
    add("low_stock", c.get("low_stock", 0), "%d product is low on stock", "%d products are low on stock",
        "Check stock", "#/inventory")
    ap = (blocks.get("ai") or {}).get("approvals") or {}
    add("approvals", ap.get("total", 0), "%d item needs your approval", "%d items need your approval",
        "Open approvals", "#/needshuman")
    pe = blocks.get("people") or {}
    add("followups", ((pe.get("leads") or {}).get("followups_due")) or 0, "%d follow-up is due today or late",
        "%d follow-ups are due today or late", "Open follow-ups", "#/followups")
    add("appointments_today", ((pe.get("appointments") or {}).get("today")) or 0, "%d appointment today",
        "%d appointments today", "Open calendar", "#/appointments")
    if money_ok and _sees(may, "finance"):
        cur.execute("SELECT count(*) AS n FROM invoices WHERE status IN ('issued','partial') AND voided_at IS NULL "
                    "AND due_on < CURRENT_DATE")
        add("invoices_overdue", int(cur.fetchone()["n"]), "%d invoice is past its due date",
            "%d invoices are past their due date", "Open invoices", "#/invoices")
    att = (blocks.get("integrations") or {}).get("attention") or []
    if att:
        add("integrations", len(att), "%d connection needs attention", "%d connections need attention",
            "Check connections", "#/integrations")
    return items


def overview(tid, role, uid):
    from .orders import summary as orders_summary
    may, money_ok = _access(uid, role)
    owner_like = role in ("owner", "techdeva_admin")
    out = {"money_visible": money_ok}
    with tenant_session(tid, role) as cur:
        tz = _tz(cur)
        cur.execute("SELECT to_char(now() AT TIME ZONE %s, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS t", (tz,))
        out["as_of"], out["timezone"] = cur.fetchone()["t"], tz
        cur.execute("SELECT count(*) AS n FROM orders")
        has_orders = int(cur.fetchone()["n"]) > 0
        if _sees(may, "operations"):
            s = orders_summary(cur, role if money_ok else "operator")   # money only for those who see money
            s["has_orders"] = has_orders
            out["orders"] = s
        else:
            out["orders"] = _hidden("operations")
        out["setup"] = setup_progress(cur) if _sees(may, "business") else _hidden("business")
        out["ai"] = ai_work(cur, owner_like and _sees(may, "marketing")) if _sees(may, "jarvis") else _hidden("jarvis")
        out["integrations"] = integrations(cur) if _sees(may, "admin") else _hidden("admin")
        out["people"] = people(cur, tz) if _sees(may, "crm") else _hidden("crm")
        if money_ok:
            out["month"] = month_money(cur, tz)
        else:
            out["month"] = {"hidden": "Money figures are for the owner." if _sees(may, "finance") or may is None
                            else "Your role does not include Finance."}
        out["focus"] = focus(out, money_ok, cur, may)
        out["chart_metrics"] = chart_metrics(may, money_ok, has_orders)
    return out


# ------------------------------------------------------------------ performance
def _period(days, start, end, today):
    if start or end:
        try:
            s = _dt.date.fromisoformat(start or "")
            e = _dt.date.fromisoformat(end or "")
        except ValueError:
            raise HTTPException(400, "Dates must look like 2026-10-24.")
        if e < s:
            raise HTTPException(400, "The end date is before the start date.")
        if (e - s).days + 1 > MAX_DAYS:
            raise HTTPException(400, "Choose at most a year.")
        if e > today:
            raise HTTPException(400, "The end date is in the future.")
        return s, e
    if days not in RANGES:
        raise HTTPException(400, "Choose 7, 30 or 90 days, or a start and end date.")
    return today - _dt.timedelta(days=days - 1), today


_SERIES_SQL = {
    "sales":     ("SELECT (recognised_at AT TIME ZONE %(tz)s)::date AS d, sum(total) AS v FROM orders "
                  "WHERE recognised_at IS NOT NULL AND (recognised_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
    "collected": ("SELECT (paid_at AT TIME ZONE %(tz)s)::date AS d, sum(amount) AS v FROM payments "
                  "WHERE (paid_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
    "orders":    ("SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v FROM orders "
                  "WHERE (created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
    "customers": ("SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v FROM customers "
                  "WHERE (created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
    "leads":     ("SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v FROM leads "
                  "WHERE (created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
    "appointments": ("SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v FROM appointments "
                     "WHERE (created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s GROUP BY 1"),
}
GROUPS = ("auto", "day", "week", "month")


def _group(series, group):
    """Daily points summed into weeks (from Monday) or calendar months. Each point keeps
    the first day it covers and how many days of it fall in the period."""
    if group == "day":
        return [dict(x, days=1) for x in series]
    out, key = [], None
    for x in series:
        d = _dt.date.fromisoformat(x["date"])
        k = d - _dt.timedelta(days=d.weekday()) if group == "week" else d.replace(day=1)
        if k != key:
            out.append({"date": str(k), "value": 0.0, "days": 0})
            key = k
        out[-1]["value"] = round(out[-1]["value"] + x["value"], 2)
        out[-1]["days"] += 1
    return out


def _align(prev, buckets):
    """The period before, cut into the same number of days per point as `buckets`,
    so point i of each period covers the same stretch of its own period."""
    out, i = [], 0
    for b in buckets:
        part = prev[i:i + b["days"]]
        out.append({"date": part[0]["date"], "value": round(sum(x["value"] for x in part), 2), "days": len(part)})
        i += b["days"]
    return out


def _series(cur, metric, tz, s, e):
    cur.execute(_SERIES_SQL[metric], {"tz": tz, "s": s, "e": e})
    got = {r["d"]: float(r["v"] or 0) for r in cur.fetchall()}
    n = (e - s).days + 1
    return [{"date": str(s + _dt.timedelta(days=i)), "value": round(got.get(s + _dt.timedelta(days=i), 0.0), 2)}
            for i in range(n)]


def chart_metrics(may, money_ok, has_orders):
    """The figures this person may chart; sales and orders only once there are orders."""
    return [{"key": k, "label": lab, "money": money} for k, (lab, sec, money, _w) in METRICS.items()
            if _sees(may, sec) and (money_ok or not money) and (has_orders or k not in ("sales", "orders"))]


def performance(tid, role, uid, metric, days, start, end, group="auto"):
    may, money_ok = _access(uid, role)
    allowed = [k for k, (_l, sec, money, _w) in METRICS.items() if _sees(may, sec) and (money_ok or not money)]
    if metric not in METRICS:
        raise HTTPException(400, "Choose one of: %s." % ", ".join(METRICS))
    if metric not in allowed:
        raise HTTPException(403, "Your role does not include this figure.")
    with tenant_session(tid, role) as cur:
        tz = _tz(cur)
        cur.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,))
        today = cur.fetchone()["d"]
        s, e = _period(days, start, end, today)
        n = (e - s).days + 1
        ps, pe = s - _dt.timedelta(days=n), s - _dt.timedelta(days=1)
        cur_ = _series(cur, metric, tz, s, e)
        prev = _series(cur, metric, tz, ps, pe)
        cur.execute("SELECT count(*) AS n FROM orders")
        has_orders = int(cur.fetchone()["n"]) > 0
    label, _sec, money, what = METRICS[metric]
    total, ptotal = round(sum(x["value"] for x in cur_), 2), round(sum(x["value"] for x in prev), 2)
    if group not in GROUPS:
        raise HTTPException(400, "Group by day, week or month.")
    g = group if group != "auto" else ("day" if n <= 31 else "week" if n <= 120 else "month")
    return {"metric": metric, "label": label, "money": money, "definition": what, "timezone": tz,
            "start": str(s), "end": str(e), "days": n, "group": g, "series": _group(cur_, g),
            "previous": {"start": str(ps), "end": str(pe), "series": _align(prev, _group(cur_, g)), "total": ptotal},
            "total": total, "change_pct": (round(100.0 * (total - ptotal) / ptotal, 1) if ptotal else None),
            "available": chart_metrics(may, money_ok, has_orders)}


def _change(cur_v, prev_v):
    """Percent change, or None when there is nothing to compare with (never infinity)."""
    return round(100.0 * (cur_v - prev_v) / prev_v, 1) if prev_v else None


def summary(tid, role, uid, days, start, end):
    """The KPI row: each figure for the period, the same length of time just before it,
    and a small series for its sparkline. A figure the person may not see is left out."""
    may, money_ok = _access(uid, role)
    with tenant_session(tid, role) as cur:
        tz = _tz(cur)
        cur.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,))
        today = cur.fetchone()["d"]
        s, e = _period(days, start, end, today)
        n = (e - s).days + 1
        ps, pe = s - _dt.timedelta(days=n), s - _dt.timedelta(days=1)
        g = "day" if n <= 31 else "week" if n <= 120 else "month"
        cur.execute("SELECT count(*) AS n FROM orders")
        has_orders = int(cur.fetchone()["n"]) > 0
        cur.execute("SELECT business_type FROM tenants WHERE id = current_tenant()")
        btype = (cur.fetchone() or {}).get("business_type")
        products = has_orders or btype in ("shop", "restaurant")

        def kpi(key, label, metric, href, note, money=False):
            a, b = _series(cur, metric, tz, s, e), _series(cur, metric, tz, ps, pe)
            v, pv = round(sum(x["value"] for x in a), 2), round(sum(x["value"] for x in b), 2)
            return {"key": key, "label": label, "value": v, "previous": pv, "change_pct": _change(v, pv), "money": money,
                    "spark": [x["value"] for x in _group(a, g)], "href": href, "note": note, "definition": METRICS[metric][3]}
        out = []
        if _sees(may, "crm"):
            out.append(kpi("leads", "New leads", "leads", "#/leads", "arrived in the period"))
            cur.execute("SELECT count(*) FILTER (WHERE status = 'active') AS active, count(*) AS total FROM customers")
            c = cur.fetchone()
            k = kpi("customers", "Active customers", "customers", "#/customers", "")
            new_now, new_before = int(k["value"]), int(k["previous"])
            k.update(value=int(c["active"]), previous=None, change_pct=None,
                     note="%d new in the period, %d in the one before" % (new_now, new_before),
                     definition="Customers whose status is active, now. The line shows new customers in the period.")
            out.append(k)
        if products and _sees(may, "operations"):
            out.append(kpi("orders", "Orders placed", "orders", "#/orders", "cancelled ones included"))
        elif _sees(may, "crm"):
            cur.execute("SELECT count(*) AS n FROM appointments WHERE starts_at >= now() AND status <> 'cancelled'")
            out.append(kpi("appointments", "Appointments booked", "appointments", "#/appointments",
                           "%d coming up" % int(cur.fetchone()["n"])))
        if money_ok:
            out.append(kpi("collected", "Payments collected", "collected", "#/revenue", "money received", money=True))
        elif _sees(may, "crm"):
            cur.execute("SELECT to_regclass('crm_activities') IS NOT NULL AS ok")
            if cur.fetchone()["ok"]:
                cur.execute("""SELECT count(*) AS open, count(*) FILTER (WHERE due_at < now()) AS late FROM crm_activities
                                WHERE done_at IS NULL AND kind <> 'note'""")
                f = cur.fetchone()
                out.append({"key": "followups", "label": "Open follow-ups", "value": int(f["open"]), "previous": None, "change_pct": None,
                            "money": False, "spark": None, "href": "#/followups", "note": "%d overdue" % int(f["late"]),
                            "definition": "Your team's planned follow-ups not done yet."})
        if _sees(may, "admin"):
            ig = integrations(cur)
            out.append({"key": "connections", "label": "Connections working", "value": ig["healthy"], "previous": None,
                        "change_pct": None, "money": False, "spark": None, "href": "#/integrations",
                        "note": "%d set up%s" % (ig["configured"], ", %d need attention" % len(ig["attention"]) if ig["attention"] else ""),
                        "definition": "Connected and not expired or failing."})
    return {"start": str(s), "end": str(e), "days": n, "group": g, "timezone": tz,
            "previous": {"start": str(ps), "end": str(pe)}, "kpis": out}


MONTHS_BACK = 12


def insights(tid, role, uid, metric, days, start, end):
    """Highlights worked out from the records: best and slowest complete month, what
    sells most, returning customers and the strongest lead source. Each says what
    it counts, and is left empty (with the reason) when there is too little data."""
    may, money_ok = _access(uid, role)
    allowed = [k for k, (_l, sec, money, _w) in METRICS.items() if _sees(may, sec) and (money_ok or not money)]
    if metric not in METRICS:
        raise HTTPException(400, "Choose one of: %s." % ", ".join(METRICS))
    if metric not in allowed:
        raise HTTPException(403, "Your role does not include this figure.")
    out = {"metric": metric, "label": METRICS[metric][0], "money": METRICS[metric][2]}
    with tenant_session(tid, role) as cur:
        tz = _tz(cur)
        cur.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,))
        today = cur.fetchone()["d"]
        s, e = _period(days, start, end, today)
        first = (today.replace(day=1) - _dt.timedelta(days=1)).replace(day=1)
        for _ in range(MONTHS_BACK - 2):
            first = (first - _dt.timedelta(days=1)).replace(day=1)
        months = _group(_series(cur, metric, tz, first, today), "month")
        for m in months:
            m["complete"] = _dt.date.fromisoformat(m["date"]).month != today.month or _dt.date.fromisoformat(m["date"]).year != today.year
        out["months"] = months
        seen = [m for m in months if m["complete"]]
        while seen and not seen[0]["value"]:
            seen.pop(0)                                    # months before the first record do not count
        if len(seen) >= 3:
            best, slow = max(seen, key=lambda m: m["value"]), min(seen, key=lambda m: m["value"])
            out["best_month"], out["slowest_month"] = best, slow
            last, before = seen[-1], seen[-2]
            out["last_month_change"] = {"month": last["date"], "value": last["value"], "previous": before["value"],
                                        "change_pct": _change(last["value"], before["value"])}
        else:
            out["best_month"] = out["slowest_month"] = out["last_month_change"] = None
            out["months_note"] = "Needs 3 complete months of records (%d so far)." % len(seen)
        if money_ok and _sees(may, "finance"):
            cur.execute("""SELECT COALESCE(sv.name, pr.name, pk.name, ii.description) AS name,
                                  sum(ii.line_total) AS value, sum(ii.quantity) AS qty
                             FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
                             LEFT JOIN services sv ON sv.id = ii.service_id LEFT JOIN products pr ON pr.id = ii.product_id
                             LEFT JOIN packages pk ON pk.id = ii.package_id
                            WHERE i.voided_at IS NULL AND i.issued_on BETWEEN %s AND %s
                            GROUP BY 1 HAVING sum(ii.line_total) > 0 ORDER BY 2 DESC LIMIT 6""", (s, e))
            rows = cur.fetchall()
            cur.execute("""SELECT COALESCE(sum(ii.line_total), 0) AS t FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
                            WHERE i.voided_at IS NULL AND i.issued_on BETWEEN %s AND %s""", (s, e))
            tot = float(cur.fetchone()["t"])
            out["top"] = {"by": "billed", "label": "Top by amount billed", "money": True, "total": tot,
                          "items": [{"name": r["name"], "value": float(r["value"]), "count": float(r["qty"] or 0)} for r in rows],
                          "definition": "Bill lines in the period (voided bills left out), by what was sold."}
        elif _sees(may, "crm"):
            cur.execute("""SELECT COALESCE(sv.name, a.service, 'Other') AS name, count(*) AS value
                             FROM appointments a LEFT JOIN services sv ON sv.id = a.service_id
                            WHERE a.status <> 'cancelled' AND (a.starts_at AT TIME ZONE %s)::date BETWEEN %s AND %s
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", (tz, s, e))
            rows = cur.fetchall()
            out["top"] = {"by": "bookings", "label": "Top by bookings", "money": False,
                          "total": float(sum(int(r["value"]) for r in rows)),
                          "items": [{"name": r["name"], "value": float(r["value"]), "count": float(r["value"])} for r in rows],
                          "definition": "Appointments in the period (cancelled ones left out), by service."}
        else:
            out["top"] = None
        if _sees(may, "crm"):
            cur.execute("""WITH v AS (
                             SELECT customer_id FROM appointments WHERE customer_id IS NOT NULL AND status <> 'cancelled'
                             UNION ALL SELECT customer_id FROM invoices WHERE customer_id IS NOT NULL AND voided_at IS NULL
                                       AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.invoice_id = invoices.id))
                           SELECT count(*) FILTER (WHERE n >= 2) AS returning, count(*) AS served
                             FROM (SELECT customer_id, count(*) AS n FROM v GROUP BY 1) x""")
            c = cur.fetchone()
            served = int(c["served"])
            out["customers"] = {"returning": int(c["returning"]), "served": served,
                                "rate": round(100.0 * int(c["returning"]) / served, 1) if served >= 5 else None,
                                "definition": "Customers with two or more visits or bills, out of everyone served."}
            # "won": sold, or became a customer (that column comes with the CRM upgrade, db/77)
            won = "status = 'sold' OR converted_customer_id IS NOT NULL" if _has_col(cur, "leads", "converted_customer_id") else "status = 'sold'"
            cur.execute("""SELECT source::text AS source, count(*) AS leads, count(*) FILTER (WHERE """ + won + """) AS won
                             FROM leads WHERE (created_at AT TIME ZONE %s)::date BETWEEN %s AND %s
                            GROUP BY 1 ORDER BY 2 DESC""", (tz, s, e))
            src = [dict(r) for r in cur.fetchall()]
            out["sources"] = [{"source": r["source"], "leads": int(r["leads"]), "won": int(r["won"]),
                               "rate": round(100.0 * int(r["won"]) / int(r["leads"]), 1) if int(r["leads"]) >= 5 else None} for r in src]
        else:
            out["customers"] = out["sources"] = None
    out["start"], out["end"] = str(s), str(e)
    return out


def _has_col(cur, table, col):
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s", (table, col))
    return cur.fetchone() is not None


# ------------------------------------------------------------------ routes
def make_dashboard_router(identity):
    """identity: console._identity, the same scope rules as every other read."""
    router = APIRouter(prefix="/dashboard", tags=["dashboard"])

    def who(x_tenant_id, x_role, authorization, session):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, session)
        if not tid:
            raise HTTPException(400, "Choose a business first.")
        return tid, role, uid

    @router.get("/overview")
    def get_overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        return overview(tid, role, uid)

    @router.get("/performance")
    def get_performance(metric: str = "orders", days: int = 30, start: str | None = None, end: str | None = None,
                        group: str = "auto",
                        x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                        authorization: str | None = Header(None),
                        td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        return performance(tid, role, uid, metric, days, start, end, group)

    @router.get("/summary")
    def get_summary(days: int = 30, start: str | None = None, end: str | None = None,
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        return summary(tid, role, uid, days, start, end)

    @router.get("/insights")
    def get_insights(metric: str = "leads", days: int = 30, start: str | None = None, end: str | None = None,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        return insights(tid, role, uid, metric, days, start, end)

    return router
