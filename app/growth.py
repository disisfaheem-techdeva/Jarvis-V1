"""Growth proof (founder console): has each client grown since TechDeva started?

The whole offer rests on one number -- how much more a client takes in since
TechDeva began -- because TechDeva sells on results and wants to be paid on
the growth it brings. This page shows that number the same way for every
client, from the client's own recorded payments, month by month:

    baseline   the monthly sales before TechDeva. Entered by the founder (from
               the client's old register or accounts), else the average of the
               full months before the start date that already have payments.
               Which one was used is always shown.
    growth     a month's sales minus the baseline (never negative in the share)
    share      growth x the agreed share %, only when the founder set one

It never invents a baseline: with no entry and no earlier payments it says so.
Founder only; clients do not see this page."""
import datetime as _dt

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .tdwork import _admin

TZ = "Asia/Karachi"
MONTHS = 12


def _month_add(d, n):
    m = d.month - 1 + n
    return _dt.date(d.year + m // 12, m % 12 + 1, 1)


def _client(cur, tid, today):
    cur.execute("""SELECT t.id::text AS tenant_id, t.name, (t.created_at AT TIME ZONE %s)::date AS created_on,
                          g.started_on, g.baseline_monthly, g.baseline_note, g.share_pct
                     FROM tenants t LEFT JOIN client_growth_terms g ON g.tenant_id = t.id
                    WHERE t.id::text = %s""", (TZ, tid))
    c = cur.fetchone()
    if not c:
        return None
    c = dict(c)
    start = c["started_on"] or c["created_on"]
    first = _dt.date(start.year, start.month, 1)
    this_month = _dt.date(today.year, today.month, 1)
    begin = max(first, _month_add(this_month, -(MONTHS - 1)))
    lo = _month_add(first, -3)
    cur.execute("""SELECT date_trunc('month', paid_at AT TIME ZONE %s)::date AS m, sum(amount)::float AS v
                     FROM payments WHERE tenant_id::text = %s AND paid_at >= (%s::date)::timestamp AT TIME ZONE %s
                    GROUP BY 1""", (TZ, tid, lo, TZ))
    rev = {r["m"]: r["v"] for r in cur.fetchall()}
    cur.execute("""SELECT date_trunc('month', created_at AT TIME ZONE %s)::date AS m, count(*) AS n
                     FROM leads WHERE tenant_id::text = %s AND created_at >= (%s::date)::timestamp AT TIME ZONE %s
                    GROUP BY 1""", (TZ, tid, begin, TZ))
    leads = {r["m"]: r["n"] for r in cur.fetchall()}
    cur.execute("""SELECT date_trunc('month', starts_at AT TIME ZONE %s)::date AS m, count(*) AS n
                     FROM appointments WHERE tenant_id::text = %s AND status = 'completed'
                      AND starts_at >= (%s::date)::timestamp AT TIME ZONE %s GROUP BY 1""", (TZ, tid, begin, TZ))
    visits = {r["m"]: r["n"] for r in cur.fetchall()}

    if c["baseline_monthly"] is not None:
        base, source = float(c["baseline_monthly"]), "entered" + (": " + c["baseline_note"] if c["baseline_note"] else "")
    else:
        before = [rev[m] for m in (_month_add(first, -i) for i in (1, 2, 3)) if m in rev]
        if before:
            base = sum(before) / len(before)
            source = "average of %d month%s before the start, from recorded payments" % (len(before), "" if len(before) == 1 else "s")
        else:
            base, source = None, None
    share = float(c["share_pct"]) if c["share_pct"] is not None else None

    rows, m = [], begin
    while m <= this_month:
        v = rev.get(m, 0.0)
        r = {"month": m.isoformat(), "revenue": v, "leads": leads.get(m, 0), "visits": visits.get(m, 0),
             "partial": m == this_month}
        if base is not None:
            r["growth"] = v - base
            r["growth_pct"] = round(100 * (v - base) / base, 1) if base > 0 else None
            if share is not None and not r["partial"]:
                r["share"] = round(max(v - base, 0) * share / 100, 2)
        rows.append(r)
        m = _month_add(m, 1)
    full = [r for r in rows if not r["partial"]]
    out = dict(c, started_on=start.isoformat(), start_entered=c["started_on"] is not None,
               created_on=c["created_on"].isoformat(), baseline=base, baseline_source=source, share_pct=share,
               baseline_monthly=None if c["baseline_monthly"] is None else float(c["baseline_monthly"]),
               months=rows, last_full=full[-1] if full else None, this_month=rows[-1] if rows else None)
    if base is not None and full:
        out["since_start"] = {"months": len(full), "revenue": sum(r["revenue"] for r in full),
                              "growth": sum(r["growth"] for r in full),
                              "share": sum(r.get("share", 0) for r in full) if share is not None else None}
    return out


def make_growth_router():
    router = APIRouter(prefix="/techdeva/growth", tags=["growth"])

    @router.get("")
    def overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5))).date()
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id::text AS id FROM tenants WHERE NOT coalesce(is_test, false) ORDER BY name")
            ids = [r["id"] for r in cur.fetchall()]
            clients = [_client(cur, tid, today) for tid in ids]
        return {"clients": [c for c in clients if c], "today": today.isoformat(), "timezone": TZ}

    @router.put("/{tid}")
    def set_terms(tid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)

        def num(k, lo, hi):
            v = body.get(k)
            if v in (None, ""):
                return None
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, "%s must be a number." % k.replace("_", " "))
            if not lo <= v <= hi:
                raise HTTPException(400, "%s must be between %s and %s." % (k.replace("_", " "), lo, hi))
            return v
        base = num("baseline_monthly", 0, 1e12)
        share = num("share_pct", 0, 50)
        start = body.get("started_on") or None
        if start:
            try:
                start = _dt.date.fromisoformat(str(start))
            except ValueError:
                raise HTTPException(400, "The start date must look like 2026-07-01.")
        note = str(body.get("baseline_note") or "").strip()[:200]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM tenants WHERE id::text = %s", (tid,))
            if not cur.fetchone():
                raise HTTPException(404, "No such business.")
            cur.execute("""INSERT INTO client_growth_terms (tenant_id, started_on, baseline_monthly, baseline_note,
                                                            share_pct, updated_at)
                           VALUES (%s, %s, %s, %s, %s, now())
                           ON CONFLICT (tenant_id) DO UPDATE SET started_on = EXCLUDED.started_on,
                             baseline_monthly = EXCLUDED.baseline_monthly, baseline_note = EXCLUDED.baseline_note,
                             share_pct = EXCLUDED.share_pct, updated_at = now()""",
                        (tid, start, base, note, share))
            record(cur, action="growth.terms", resource="client_growth_terms", resource_id=tid, actor=str(uid),
                   actor_type="human", status="ok", tenant_id=tid,
                   detail={"started_on": start.isoformat() if start else None, "baseline": base, "share_pct": share},
                   error=None)
            today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5))).date()
            return {"client": _client(cur, tid, today)}

    return router
