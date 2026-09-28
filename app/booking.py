"""Appointments the way the Beauty clinic books them.

One form: who (name + phone, matched to an existing customer by phone), what
(one or more services, each with price, quantity and discount), the bill
(discount flat or %, GST %), money taken now, who booked / assisted / did the
procedure, sessions, next visit, status, notes.

The appointment and its bill are written in one transaction. Money lives on
the invoice and its payments -- the same books the rest of the platform reads --
so the balance on an appointment can never disagree with the finance pages.
"""

import re
from datetime import date

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session
from .operations import _next_invoice_number, _paid_so_far, _settle

TZ = "Asia/Karachi"
STATUSES = ("booked", "completed", "cancelled", "no_show")
METHODS = {"cash": ("cash", None), "card": ("card", None), "bank": ("bank", None),
           "online": ("online", None), "jazzcash": ("online", "JazzCash"),
           "easypaisa": ("online", "EasyPaisa"), "other": ("other", None)}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DETAIL_TEXT = {"department": 60, "booked_by": 80, "assisted_by": 80,
               "procedure_by": 80, "notes": 2000}


def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "Open a business first.")
    return tid, role, uid


def _num(v, field, minimum=0.0):
    if v in (None, ""):
        return 0.0
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "%s must be a number." % field)
    if n < minimum:
        raise HTTPException(400, "%s cannot be negative." % field)
    return round(n, 2)


def _day(v, field):
    v = (v or "").strip()
    if not v:
        return None
    if not _DAY.match(v):
        raise HTTPException(400, "%s must be a date like 2026-09-28." % field)
    try:
        date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "%s is not a real date." % field)
    return v


def _digits(p):
    return re.sub(r"\D", "", p or "")


def make_booking_router():
    router = APIRouter(tags=["appointments"])

    @router.get("/appointments/list")
    def listing(start: str | None = None, end: str | None = None, status: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Appointments between two days (inclusive), with the bill's money."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        s = _day(start, "Start") or "1900-01-01"
        e = _day(end, "End") or "2999-12-31"
        where, args = ["(a.starts_at AT TIME ZONE %s)::date BETWEEN %s AND %s"], [TZ, s, e]
        if status:
            if status not in STATUSES:
                raise HTTPException(400, "Unknown status.")
            where.append("a.status = %s")
            args.append(status)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT a.id, a.starts_at, "
                "  to_char(a.starts_at AT TIME ZONE %s, 'YYYY-MM-DD') AS day, "
                "  to_char(a.starts_at AT TIME ZONE %s, 'HH24:MI') AS time, "
                "  a.status, a.service, a.price, a.notes, a.department, a.booked_by, "
                "  a.assisted_by, a.procedure_by, a.payment_method, a.total_sessions, "
                "  a.next_appointment, a.invoice_id, a.customer_id, "
                "  c.name AS customer_name, c.phone AS customer_phone, "
                "  i.number AS invoice_number, i.subtotal, i.discount, i.tax, i.total, "
                "  i.voided_at, "
                "  COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid "
                "FROM appointments a "
                "LEFT JOIN customers c ON c.id = a.customer_id "
                "LEFT JOIN invoices i ON i.id = a.invoice_id "
                "WHERE " + " AND ".join(where) + " ORDER BY a.starts_at DESC LIMIT 1000",
                (TZ, TZ, *args))
            rows = cur.fetchall()
        for r in rows:
            total = float(r["total"] or 0) if r["invoice_id"] else float(r["price"] or 0)
            paid = float(r["paid"] or 0)
            r["total"] = round(total, 2)
            r["paid"] = round(paid, 2)
            r["balance"] = round(max(total - paid, 0), 2) if r["invoice_id"] else None
        return {"count": len(rows), "appointments": rows}

    @router.get("/appointments/{appt_id}/bill")
    def bill(appt_id: str,
             x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Everything needed to print the bill."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT a.*, c.name AS customer_name, c.phone AS customer_phone, "
                        "to_char(a.starts_at AT TIME ZONE %s, 'YYYY-MM-DD HH24:MI') AS at "
                        "FROM appointments a LEFT JOIN customers c ON c.id = a.customer_id "
                        "WHERE a.id = %s", (TZ, appt_id))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404, "Appointment not found.")
            inv, items, pays = None, [], []
            if a["invoice_id"]:
                cur.execute("SELECT number, subtotal, discount, tax, total, status, currency "
                            "FROM invoices WHERE id = %s", (a["invoice_id"],))
                inv = cur.fetchone()
                cur.execute("SELECT description, quantity, unit_price, line_total "
                            "FROM invoice_items WHERE invoice_id = %s ORDER BY description",
                            (a["invoice_id"],))
                items = cur.fetchall()
                cur.execute("SELECT amount, method, reference, paid_at FROM payments "
                            "WHERE invoice_id = %s ORDER BY paid_at", (a["invoice_id"],))
                pays = cur.fetchall()
            cur.execute("SELECT name FROM tenants WHERE id = %s", (tid,))
            t = cur.fetchone() or {}
        return {"business": t.get("name"), "appointment": a, "invoice": inv,
                "items": items, "payments": pays}

    @router.post("/appointments/book")
    def book(body: dict = Body(...),
             x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)

        day = _day(body.get("date"), "Date")
        if not day:
            raise HTTPException(400, "Pick the appointment date.")
        tm = (body.get("time") or "").strip() or "00:00"
        if not _HHMM.match(tm):
            raise HTTPException(400, "Time must look like 14:30.")
        status = (body.get("status") or "booked").strip().lower()
        if status not in STATUSES:
            raise HTTPException(400, "Status must be booked, completed, cancelled or no_show.")
        name = (body.get("name") or "").strip()[:120]
        phone = (body.get("phone") or "").strip()[:30]
        customer_id = (body.get("customer_id") or "").strip() or None
        if not customer_id and not name:
            raise HTTPException(400, "Enter the client's name.")

        lines, subtotal = [], 0.0
        raw = body.get("services") or []
        if not isinstance(raw, list) or len(raw) > 30:
            raise HTTPException(400, "Services must be a list (max 30).")
        line_disc = 0.0
        for i, s in enumerate(raw, 1):
            if not isinstance(s, dict):
                raise HTTPException(400, "Service %d is not valid." % i)
            nm = (s.get("name") or "").strip()[:300]
            if not nm:
                raise HTTPException(400, "Service %d needs a name." % i)
            qty = _num(s.get("qty") or 1, "Quantity")
            if qty <= 0:
                raise HTTPException(400, "Quantity must be at least 1.")
            price = _num(s.get("price"), "Price")
            ld = _num(s.get("discount"), "Line discount")
            if ld > price * qty:
                raise HTTPException(400, "The discount on %s is more than its price." % nm)
            lines.append({"service_id": s.get("service_id") or None, "description": nm,
                          "quantity": qty, "unit_price": price,
                          "line_total": round(price * qty, 2)})
            subtotal += price * qty
            line_disc += ld
        dtype = (body.get("discount_type") or "flat").lower()
        dval = _num(body.get("discount_value"), "Discount")
        bill_disc = (subtotal - line_disc) * dval / 100.0 if dtype == "percent" else dval
        if dtype == "percent" and dval > 100:
            raise HTTPException(400, "A percentage discount cannot be over 100.")
        discount = round(line_disc + bill_disc, 2)
        if discount > subtotal + 0.001:
            raise HTTPException(400, "The discount is more than the bill.")
        gst_pct = _num(body.get("gst_percent"), "GST %")
        tax = round((subtotal - discount) * gst_pct / 100.0, 2)
        total = round(subtotal - discount + tax, 2)
        received = _num(body.get("received"), "Amount received")
        if received - total > 0.009:
            raise HTTPException(400, "Amount received (%s) is more than the bill (%s)." % (received, total))
        mkey = (body.get("payment_method") or "").strip().lower().replace(" ", "")
        if received > 0 and mkey not in METHODS:
            raise HTTPException(400, "Pick how they paid.")
        sessions = int(_num(body.get("total_sessions") or 1, "Sessions")) or 1
        if not 1 <= sessions <= 100:
            raise HTTPException(400, "Sessions must be between 1 and 100.")
        nxt = _day(body.get("next_appointment"), "Next appointment")
        det = {k: ((body.get(k) or "").strip()[:n] or None) for k, n in DETAIL_TEXT.items()}
        staff_id = (body.get("procedure_staff_id") or "").strip() or None

        with tenant_session(tid, role) as cur:
            if customer_id:
                cur.execute("SELECT id, name FROM customers WHERE id = %s", (customer_id,))
                c = cur.fetchone()
                if not c:
                    raise HTTPException(404, "That customer is not in this business.")
            else:
                c = None
                d = _digits(phone)
                if len(d) >= 7:
                    cur.execute("SELECT id, name FROM customers WHERE right(regexp_replace("
                                "coalesce(phone, ''), '\\D', '', 'g'), 10) = right(%s, 10) "
                                "ORDER BY created_at LIMIT 1", (d,))
                    c = cur.fetchone()
                if not c:
                    cur.execute("INSERT INTO customers (tenant_id, name, phone, source) "
                                "VALUES (current_tenant(), %s, %s, 'appointment') "
                                "RETURNING id, name", (name, phone or None))
                    c = cur.fetchone()
                customer_id = c["id"]
            if staff_id:
                cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
                st = cur.fetchone()
                if not st:
                    raise HTTPException(404, "That staff member is not in this business.")
                det["procedure_by"] = det["procedure_by"] or st["name"]

            cur.execute(
                "INSERT INTO appointments (tenant_id, customer_id, service_id, staff_id, service, "
                "starts_at, status, price, currency, notes, department, booked_by, assisted_by, "
                "procedure_by, payment_method, total_sessions, next_appointment) VALUES "
                "(current_tenant(), %s, %s, %s, %s, (%s::timestamp AT TIME ZONE %s), %s, %s, 'PKR', "
                "%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (customer_id, lines[0]["service_id"] if lines else None, staff_id,
                 ", ".join(l["description"] for l in lines)[:500] or None,
                 "%s %s:00" % (day, tm), TZ, status, total if lines else None,
                 det["notes"], det["department"], det["booked_by"], det["assisted_by"],
                 det["procedure_by"], (body.get("payment_method") or None), sessions, nxt))
            appt_id = cur.fetchone()["id"]

            money = None
            if lines:
                number = _next_invoice_number(cur)
                cur.execute(
                    "INSERT INTO invoices (tenant_id, customer_id, number, issued_on, currency, "
                    "discount, tax, notes) VALUES (current_tenant(), %s, %s, %s, 'PKR', %s, %s, %s) "
                    "RETURNING id",
                    (customer_id, number, day, discount, tax, "Appointment %s %s" % (day, tm)))
                inv_id = cur.fetchone()["id"]
                for l in lines:
                    cur.execute(
                        "INSERT INTO invoice_items (tenant_id, invoice_id, service_id, description, "
                        "quantity, unit_price, line_total) VALUES (current_tenant(), %s, %s, %s, %s, %s, %s)",
                        (inv_id, l["service_id"], l["description"], l["quantity"],
                         l["unit_price"], l["line_total"]))
                if received > 0:
                    method, ref = METHODS[mkey]
                    cur.execute(
                        "INSERT INTO payments (tenant_id, invoice_id, customer_id, amount, currency, "
                        "method, reference, notes) VALUES (current_tenant(), %s, %s, %s, 'PKR', %s, %s, %s)",
                        (inv_id, customer_id, received, method, ref, "Taken at booking"))
                money = _settle(cur, inv_id, uid)
                cur.execute("UPDATE appointments SET invoice_id = %s WHERE id = %s", (inv_id, appt_id))
                money["invoice_number"] = number
            record(cur, action="appointment.book", resource="appointments",
                   resource_id=str(appt_id), actor=str(uid), actor_type="human", status="ok",
                   detail={"services": len(lines), "total": total, "received": received},
                   error=None)
        return {"ok": True, "appointment_id": str(appt_id), "customer_id": str(customer_id),
                "total": total, "money": money}

    @router.patch("/appointments/{appt_id}")
    def update(appt_id: str, body: dict = Body(...),
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Change the booking itself. Money changes go through the bill
        (take a payment), never by editing a number on the appointment."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        sets, vals = [], []
        if "status" in body:
            s = (body.get("status") or "").strip().lower()
            if s not in STATUSES:
                raise HTTPException(400, "Status must be booked, completed, cancelled or no_show.")
            sets.append("status = %s")
            vals.append(s)
        if "date" in body or "time" in body:
            d = _day(body.get("date"), "Date")
            t = (body.get("time") or "").strip()
            if not d or not _HHMM.match(t):
                raise HTTPException(400, "Send both a date (2026-09-28) and a time (14:30).")
            sets.append("starts_at = (%s::timestamp AT TIME ZONE %s)")
            vals += ["%s %s:00" % (d, t), TZ]
        for k, n in DETAIL_TEXT.items():
            if k in body:
                sets.append(k + " = %s")
                vals.append((body.get(k) or "").strip()[:n] or None)
        if "total_sessions" in body:
            n = int(_num(body.get("total_sessions") or 1, "Sessions")) or 1
            if not 1 <= n <= 100:
                raise HTTPException(400, "Sessions must be between 1 and 100.")
            sets.append("total_sessions = %s")
            vals.append(n)
        if "next_appointment" in body:
            sets.append("next_appointment = %s")
            vals.append(_day(body.get("next_appointment"), "Next appointment"))
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE appointments SET " + ", ".join(sets) +
                        " WHERE id = %s RETURNING id", (*vals, appt_id))
            if not cur.fetchone():
                raise HTTPException(404, "Appointment not found.")
            record(cur, action="appointment.update", resource="appointments",
                   resource_id=appt_id, actor=str(uid), actor_type="human", status="ok",
                   detail={"fields": [s.split(" ")[0] for s in sets]}, error=None)
        return {"ok": True}

    return router
