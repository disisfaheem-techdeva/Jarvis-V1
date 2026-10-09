"""Team, attendance, HR and marketing summary for the client dashboard.

The Beauty clinic's own CRM has these sections; every other client got a
dashboard without them. Same rules as the rest of the platform: the tenant
comes from auth.resolve_scope(), every statement runs inside tenant_session()
so Postgres RLS is what fences one business from another, and no table or
column name ever comes from the caller.

Salaries and personal HR details are owner-only. Operators can mark
attendance, because in a clinic that is what the front desk does.
"""

import re
from datetime import date

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

STATUSES = ("present", "late", "half_day", "leave", "absent")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
OWNER_ROLES = ("owner", "techdeva_admin")


def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "Open a business first.")
    return tid, role, uid


def _day(v):
    v = (v or "").strip() or date.today().isoformat()
    if not _DAY.match(v):
        raise HTTPException(400, "Use a date like 2026-09-28.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "That is not a real date.")


def _time(v, field):
    v = (v or "").strip()
    if not v:
        return None
    if not _HHMM.match(v):
        raise HTTPException(400, "%s must be a time like 09:30." % field)
    return v


def _owner(role):
    if role not in OWNER_ROLES:
        raise HTTPException(403, "Only the business owner can see HR records.")


def make_team_router():
    router = APIRouter(tags=["team"])

    # ------------------------------------------------------------ attendance
    @router.get("/team/attendance")
    def attendance(day: str | None = None,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Every active staff member and how they were marked on one day."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        d = _day(day)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT s.id AS staff_id, s.name, s.role, s.department, a.status, "
                "       to_char(a.check_in, 'HH24:MI') AS check_in, "
                "       to_char(a.check_out, 'HH24:MI') AS check_out, a.notes "
                "FROM staff s "
                "LEFT JOIN staff_attendance a ON a.staff_id = s.id AND a.day = %s "
                "WHERE s.active IS NOT FALSE ORDER BY s.name", (d,))
            rows = cur.fetchall()
        marked = sum(1 for r in rows if r["status"])
        return {"day": d.isoformat(), "staff": rows, "marked": marked,
                "unmarked": len(rows) - marked}

    @router.post("/team/attendance")
    def mark(body: dict = Body(...),
             x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Mark (or correct) one person for one day."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        staff_id = (body.get("staff_id") or "").strip()
        status = (body.get("status") or "").strip().lower()
        if status not in STATUSES:
            raise HTTPException(400, "Status must be present, late, half_day, leave or absent.")
        d = _day(body.get("day"))
        cin = _time(body.get("check_in"), "Check-in")
        cout = _time(body.get("check_out"), "Check-out")
        notes = (body.get("notes") or "").strip()[:500] or None
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
            who = cur.fetchone()
            if not who:
                raise HTTPException(404, "That staff member is not in this business.")
            cur.execute(
                "INSERT INTO staff_attendance (tenant_id, staff_id, day, status, check_in, "
                "check_out, notes, marked_by) VALUES "
                "(current_tenant(), %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, staff_id, day) DO UPDATE SET "
                "status = EXCLUDED.status, check_in = EXCLUDED.check_in, "
                "check_out = EXCLUDED.check_out, notes = EXCLUDED.notes, "
                "marked_by = EXCLUDED.marked_by, updated_at = now() RETURNING id",
                (staff_id, d, status, cin, cout, notes, str(uid)))
            aid = cur.fetchone()["id"]
            record(cur, action="attendance.mark", resource="staff_attendance",
                   resource_id=str(aid), actor=str(uid), actor_type="human", status="ok",
                   detail={"staff": who["name"], "day": d.isoformat(), "status": status},
                   error=None)
        return {"ok": True, "id": str(aid), "name": who["name"], "day": d.isoformat(),
                "status": status}

    @router.get("/team/attendance/summary")
    def summary(month: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Per-person counts for one month."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        month = (month or "").strip() or date.today().strftime("%Y-%m")
        if not _MONTH.match(month):
            raise HTTPException(400, "Use a month like 2026-09.")
        y, m = int(month[:4]), int(month[5:])
        if not 1 <= m <= 12:
            raise HTTPException(400, "That is not a real month.")
        start = date(y, m, 1)
        end = date(y + (m == 12), (m % 12) + 1, 1)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT s.id AS staff_id, s.name, "
                "  count(a.id) FILTER (WHERE a.status = 'present')  AS present, "
                "  count(a.id) FILTER (WHERE a.status = 'late')     AS late, "
                "  count(a.id) FILTER (WHERE a.status = 'half_day') AS half_day, "
                "  count(a.id) FILTER (WHERE a.status = 'leave')    AS leave, "
                "  count(a.id) FILTER (WHERE a.status = 'absent')   AS absent "
                "FROM staff s "
                "LEFT JOIN staff_attendance a ON a.staff_id = s.id "
                "     AND a.day >= %s AND a.day < %s "
                "WHERE s.active IS NOT FALSE GROUP BY s.id, s.name ORDER BY s.name",
                (start, end))
            rows = cur.fetchall()
        return {"month": month, "staff": rows}

    # -------------------------------------------------------------------- HR
    @router.get("/team/hr")
    def hr(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT id, name, role, department, phone, email, active, salary, "
                "joining_date, emergency_contact, address, hr_notes "
                "FROM staff ORDER BY active DESC, name")
            rows = cur.fetchall()
        payroll = sum(float(r["salary"] or 0) for r in rows if r["active"] is not False)
        return {"staff": rows, "monthly_payroll": round(payroll, 2)}

    @router.patch("/team/hr/{staff_id}")
    def hr_update(staff_id: str, body: dict = Body(...),
                  x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        sets, vals = [], []
        if "salary" in body:
            v = body.get("salary")
            if v in (None, ""):
                v = None
            else:
                try:
                    v = round(float(v), 2)
                except (TypeError, ValueError):
                    raise HTTPException(400, "Salary must be a number.")
                if v < 0:
                    raise HTTPException(400, "Salary cannot be negative.")
            sets.append("salary = %s")
            vals.append(v)
        if "joining_date" in body:
            v = (body.get("joining_date") or "").strip()
            sets.append("joining_date = %s")
            vals.append(_day(v) if v else None)
        for f, limit in (("emergency_contact", 60), ("address", 300), ("hr_notes", 1000)):
            if f in body:
                sets.append(f + " = %s")
                vals.append((body.get(f) or "").strip()[:limit] or None)
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE staff SET " + ", ".join(sets) + ", updated_at = now() "
                        "WHERE id = %s RETURNING id, name", (*vals, staff_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That staff member is not in this business.")
            record(cur, action="hr.update", resource="staff", resource_id=str(r["id"]),
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"fields": [s.split(" ")[0] for s in sets]}, error=None)
        return {"ok": True, "name": r["name"]}

    # ------------------------------------------------------------- marketing
    @router.get("/marketing/summary")
    def marketing(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Where leads come from, what campaigns exist, what has been posted."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT coalesce(nullif(trim(source::text), ''), 'unknown') AS source, "
                "  count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS last_30_days, "
                "  count(*) AS all_time "
                "FROM leads GROUP BY 1 ORDER BY 3 DESC LIMIT 30")
            sources = cur.fetchall()
            cur.execute(
                "SELECT name, platform, objective, status, spend, leads, created_at "
                "FROM campaigns ORDER BY created_at DESC LIMIT 50")
            campaigns = cur.fetchall()
            cur.execute("SELECT status, count(*) AS n FROM social_posts GROUP BY status")
            posts = {r["status"]: r["n"] for r in cur.fetchall()}
        return {
            "sources": sources,
            "leads_30_days": sum(r["last_30_days"] for r in sources),
            "leads_all_time": sum(r["all_time"] for r in sources),
            "campaigns": campaigns,
            "spend_total": round(sum(float(c["spend"] or 0) for c in campaigns), 2),
            "posts": posts,
        }

    return router
