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


def credit_available(cur, customer_id):
    """Advances paid on this customer's cancelled visits, minus credit already used (db/63)."""
    if not customer_id:
        return 0.0
    cur.execute("""SELECT COALESCE((SELECT sum(p.amount) FROM payments p
                                      JOIN appointments a ON a.invoice_id = p.invoice_id
                                     WHERE a.customer_id = %s AND a.status = 'cancelled'), 0)
                        - COALESCE((SELECT sum(amount) FROM customer_credit_uses WHERE customer_id = %s), 0) AS c""",
                (customer_id, customer_id))
    return max(0.0, round(float(cur.fetchone()["c"] or 0), 2))
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


_CUR = re.compile(r"^[A-Z]{3}$")
OWNER_ROLES = ("owner", "techdeva_admin")


def _price_lines(body):
    """The bill a request describes: its lines and its money, validated.
    Shared by booking and by editing a saved bill so both price the same way."""
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
        lines.append({"service_id": s.get("service_id") or None,
                      "package_id": s.get("package_id") or None, "description": nm,
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
    return {"lines": lines, "subtotal": subtotal, "discount": discount, "tax": tax,
            "total": total}




def _currency(v):
    c = (v or "PKR").strip().upper()
    if not _CUR.match(c):
        raise HTTPException(400, "Currency must be a 3-letter code like PKR.")
    return c


def _end(day, start_tm, end_tm):
    """The end time on the same day, or None. An end before the start is refused."""
    end_tm = (end_tm or "").strip()
    if not end_tm:
        return None
    if not _HHMM.match(end_tm):
        raise HTTPException(400, "End time must look like 15:30.")
    if start_tm and end_tm <= start_tm:
        raise HTTPException(400, "The end time must be after the start time.")
    return "%s %s:00" % (day, end_tm)


def make_booking_router():
    router = APIRouter(tags=["appointments"])

    @router.get("/appointments/list")
    def listing(start: str | None = None, end: str | None = None, status: str | None = None,
                q: str | None = None, customer_id: str | None = None,
                include_unlinked: bool = False,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Appointments between two days (inclusive), with the bill's money.

        include_unlinked also returns bills in the same range that have no
        appointment (recorded before Daily Entry booked through here), so money
        on the books is never invisible on the appointments screen."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        s = _day(start, "Start") or "1900-01-01"
        e = _day(end, "End") or "2999-12-31"
        where, args = ["(a.starts_at AT TIME ZONE %s)::date BETWEEN %s AND %s"], [TZ, s, e]
        if status:
            if status not in STATUSES:
                raise HTTPException(400, "Unknown status.")
            where.append("a.status = %s")
            args.append(status)
        if customer_id:
            where.append("a.customer_id = %s")
            args.append(customer_id)
        q = (q or "").strip()[:80]
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(c.name ILIKE %s OR c.phone ILIKE %s OR a.service ILIKE %s "
                         "OR i.number ILIKE %s OR a.notes ILIKE %s)")
            args += [like] * 5
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT a.id, a.starts_at, a.ends_at, "
                "  to_char(a.starts_at AT TIME ZONE %s, 'YYYY-MM-DD') AS day, "
                "  to_char(a.starts_at AT TIME ZONE %s, 'HH24:MI') AS time, "
                "  to_char(a.ends_at AT TIME ZONE %s, 'HH24:MI') AS end_time, "
                "  a.status, a.service, a.service_id, a.price, a.notes, a.department, a.booked_by, "
                "  a.assisted_by, a.procedure_by, a.payment_method, a.total_sessions, "
                "  a.next_appointment, a.invoice_id, a.customer_id, a.staff_id, a.location_id, "
                "  COALESCE(i.currency, a.currency, 'PKR') AS currency, "
                "  c.name AS customer_name, c.phone AS customer_phone, "
                "  st.name AS staff_name, l.name AS location_name, "
                "  i.number AS invoice_number, i.subtotal, i.discount, i.tax, i.total, "
                "  i.voided_at, "
                "  (SELECT string_agg(DISTINCT pk.name, ', ') FROM invoice_items ii "
                "     JOIN packages pk ON pk.id = ii.package_id WHERE ii.invoice_id = i.id) AS package_names, "
                "  COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid "
                "FROM appointments a "
                "LEFT JOIN customers c ON c.id = a.customer_id "
                "LEFT JOIN staff st ON st.id = a.staff_id "
                "LEFT JOIN locations l ON l.id = a.location_id "
                "LEFT JOIN invoices i ON i.id = a.invoice_id "
                "WHERE " + " AND ".join(where) + " ORDER BY a.starts_at DESC LIMIT 1000",
                (TZ, TZ, TZ, *args))
            rows = cur.fetchall()
            unlinked = None
            if include_unlinked:
                cur.execute(
                    "SELECT i.id AS invoice_id, i.number AS invoice_number, i.issued_on, "
                    "  to_char(i.issued_on, 'YYYY-MM-DD') AS day, i.status, i.total, i.currency, "
                    "  i.notes, i.customer_id, c.name AS customer_name, c.phone AS customer_phone, "
                    "  (SELECT string_agg(ii.description, ', ') FROM invoice_items ii "
                    "     WHERE ii.invoice_id = i.id) AS service, "
                    "  COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid "
                    "FROM invoices i LEFT JOIN customers c ON c.id = i.customer_id "
                    "WHERE i.voided_at IS NULL AND i.issued_on BETWEEN %s AND %s "
                    "  AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.invoice_id = i.id) "
                    + ("AND i.customer_id = %s " if customer_id else "") +
                    "ORDER BY i.issued_on DESC, i.number DESC LIMIT 500",
                    (s, e, customer_id) if customer_id else (s, e))
                unlinked = cur.fetchall()
                for u in unlinked:
                    u["total"] = round(float(u["total"] or 0), 2)
                    u["paid"] = round(float(u["paid"] or 0), 2)
                    u["balance"] = round(max(u["total"] - u["paid"], 0), 2)
        for r in rows:
            total = float(r["total"] or 0) if r["invoice_id"] else float(r["price"] or 0)
            paid = float(r["paid"] or 0)
            r["total"] = round(total, 2)
            r["paid"] = round(paid, 2)
            r["balance"] = round(max(total - paid, 0), 2) if r["invoice_id"] else None
            if r.get("status") == "cancelled":
                r["balance"] = 0 if r["invoice_id"] else None
                r["credit"] = round(paid, 2)          # an advance on a cancelled visit is the customer's credit
        out = {"count": len(rows), "appointments": rows}
        if unlinked is not None:
            out["unlinked_bills"] = unlinked
        return out

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
                cur.execute("SELECT description, quantity, unit_price, line_total, service_id, package_id "
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

        pb = _price_lines(body)
        lines, subtotal, discount, tax, total = (pb["lines"], pb["subtotal"], pb["discount"],
                                                 pb["tax"], pb["total"])
        received = _num(body.get("received"), "Amount received")
        use_credit = round(_num(body.get("use_credit"), "Credit") or 0, 2)
        if use_credit < 0 or use_credit - total > 0.009:
            raise HTTPException(400, "The credit used cannot be more than the bill.")
        if received + use_credit - total > 0.009:
            raise HTTPException(400, "Amount received (%s) plus credit (%s) is more than the bill (%s)." % (received, use_credit, total))
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
        staff_id = (body.get("procedure_staff_id") or body.get("staff_id") or "").strip() or None
        location_id = (body.get("location_id") or "").strip() or None
        currency = _currency(body.get("currency"))
        ends = _end(day, tm, body.get("end_time"))
        csource = (body.get("source") or "").strip().lower()[:40] or "appointment"

        with tenant_session(tid, role) as cur:
            if location_id:
                cur.execute("SELECT 1 FROM locations WHERE id = %s", (location_id,))
                if not cur.fetchone():
                    raise HTTPException(404, "That location is not in this business.")
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
                                "VALUES (current_tenant(), %s, %s, %s) "
                                "RETURNING id, name", (name, phone or None, csource))
                    c = cur.fetchone()
                customer_id = c["id"]
            if use_credit > 0:
                if not lines:
                    raise HTTPException(400, "Credit can only be used on a visit with a bill.")
                if use_credit - credit_available(cur, customer_id) > 0.009:
                    raise HTTPException(409, "This customer has only Rs {:,.0f} of credit.".format(credit_available(cur, customer_id)))
            if staff_id:
                cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
                st = cur.fetchone()
                if not st:
                    raise HTTPException(404, "That staff member is not in this business.")
                det["procedure_by"] = det["procedure_by"] or st["name"]

            cur.execute(
                "INSERT INTO appointments (tenant_id, customer_id, service_id, staff_id, location_id, "
                "service, starts_at, ends_at, status, price, currency, notes, department, booked_by, "
                "assisted_by, procedure_by, payment_method, total_sessions, next_appointment) VALUES "
                "(current_tenant(), %s, %s, %s, %s, %s, (%s::timestamp AT TIME ZONE %s), "
                "(%s::timestamp AT TIME ZONE %s), %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (customer_id, lines[0]["service_id"] if lines else None, staff_id, location_id,
                 ", ".join(l["description"] for l in lines)[:500] or None,
                 "%s %s:00" % (day, tm), TZ, ends, TZ, status, total if lines else None, currency,
                 det["notes"], det["department"], det["booked_by"], det["assisted_by"],
                 det["procedure_by"], (body.get("payment_method") or None), sessions, nxt))
            appt_id = cur.fetchone()["id"]

            money = None
            if lines:
                number = _next_invoice_number(cur)
                cur.execute(
                    "INSERT INTO invoices (tenant_id, customer_id, location_id, number, issued_on, "
                    "currency, discount, tax, notes) VALUES (current_tenant(), %s, %s, %s, %s, %s, "
                    "%s, %s, %s) RETURNING id",
                    (customer_id, location_id, number, day, currency, round(discount + use_credit, 2), tax,
                     "Appointment %s %s" % (day, tm) + (" (credit used: %s)" % use_credit if use_credit else "")))
                inv_id = cur.fetchone()["id"]
                if use_credit > 0:          # paid earlier, on a cancelled visit: never counted twice
                    cur.execute("INSERT INTO customer_credit_uses (tenant_id, customer_id, invoice_id, amount, created_by) "
                                "VALUES (current_tenant(), %s, %s, %s, %s)", (customer_id, inv_id, use_credit, str(uid)))
                for l in lines:
                    cur.execute(
                        "INSERT INTO invoice_items (tenant_id, invoice_id, service_id, package_id, "
                        "description, quantity, unit_price, line_total) "
                        "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)",
                        (inv_id, l["service_id"], l["package_id"], l["description"], l["quantity"],
                         l["unit_price"], l["line_total"]))
                if received > 0:
                    method, ref = METHODS[mkey]
                    cur.execute(
                        "INSERT INTO payments (tenant_id, invoice_id, customer_id, location_id, amount, "
                        "currency, method, reference, notes) "
                        "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s)",
                        (inv_id, customer_id, location_id, received, currency, method, ref,
                         "Taken at booking"))
                money = _settle(cur, inv_id, uid)
                cur.execute("UPDATE appointments SET invoice_id = %s WHERE id = %s", (inv_id, appt_id))
                money["invoice_number"] = number
            record(cur, action="appointment.book", resource="appointments",
                   resource_id=str(appt_id), actor=str(uid), actor_type="human", status="ok",
                   detail={"services": len(lines), "total": total, "received": received, "credit_used": use_credit},
                   error=None)
        return {"ok": True, "appointment_id": str(appt_id), "customer_id": str(customer_id),
                "total": total, "money": money}

    @router.get("/appointments/credit")
    def credit(phone: str = "", customer_id: str = "",
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A customer's credit from advances on cancelled visits (by customer or phone)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cid = customer_id.strip() or None
            if cid and not re.match(r"^[0-9a-f-]{36}$", cid):
                cid = None
            if not cid and len(_digits(phone)) >= 7:
                cur.execute("SELECT id FROM customers WHERE right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10) "
                            "= right(%s, 10) ORDER BY created_at LIMIT 1", (_digits(phone),))
                r = cur.fetchone()
                cid = r["id"] if r else None
            return {"customer_id": str(cid) if cid else None, "credit": credit_available(cur, cid) if cid else 0.0}

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
            if "end_time" in body:
                sets.append("ends_at = (%s::timestamp AT TIME ZONE %s)")
                vals += [_end(d, t, body.get("end_time")), TZ]
        elif "end_time" in body:
            raise HTTPException(400, "Send the date and start time with the end time.")
        for k in ("location_id", "staff_id"):
            if k in body:
                sets.append(k + " = %s")
                vals.append((body.get(k) or "").strip() or None)
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
            if (body.get("location_id") or "").strip():
                cur.execute("SELECT 1 FROM locations WHERE id = %s", (body["location_id"].strip(),))
                if not cur.fetchone():
                    raise HTTPException(404, "That location is not in this business.")
            if (body.get("staff_id") or "").strip():
                cur.execute("SELECT name FROM staff WHERE id = %s", (body["staff_id"].strip(),))
                st = cur.fetchone()
                if not st:
                    raise HTTPException(404, "That staff member is not in this business.")
                if not (body.get("procedure_by") or "").strip():
                    sets.append("procedure_by = %s")
                    vals.append(st["name"])
            cur.execute("SELECT status, invoice_id FROM appointments WHERE id = %s", (appt_id,))
            before = cur.fetchone()
            if not before:
                raise HTTPException(404, "Appointment not found.")
            new_status = (body.get("status") or "").strip().lower() if "status" in body else before["status"]
            if before["status"] == "cancelled" and new_status != "cancelled" and before["invoice_id"]:
                cur.execute("SELECT voided_at, notes FROM invoices WHERE id = %s", (before["invoice_id"],))
                iv = cur.fetchone() or {}
                if iv.get("voided_at") or "advance kept as credit" in (iv.get("notes") or ""):
                    raise HTTPException(409, "This visit was cancelled and its bill closed. Book a new appointment instead.")
            cur.execute("UPDATE appointments SET " + ", ".join(sets) +
                        " WHERE id = %s RETURNING id", (*vals, appt_id))
            if not cur.fetchone():
                raise HTTPException(404, "Appointment not found.")
            if new_status == "cancelled" and before["status"] != "cancelled" and before["invoice_id"]:
                # Nothing is owed for a visit that will not happen. No money taken:
                # the bill is voided. An advance taken: the bill is settled at the
                # advance (the business keeps it) and it becomes the customer's credit.
                inv = before["invoice_id"]
                cur.execute("SELECT total, discount, voided_at FROM invoices WHERE id = %s", (inv,))
                iv = cur.fetchone()
                paid = _paid_so_far(cur, inv)
                if iv and not iv["voided_at"]:
                    if paid <= 0:
                        cur.execute("UPDATE invoices SET voided_at = now(), void_reason = 'Appointment cancelled', "
                                    "status = 'void', updated_at = now() WHERE id = %s", (inv,))
                    elif paid + 0.009 < float(iv["total"]):
                        cur.execute("UPDATE invoices SET discount = discount + %s, notes = trim(coalesce(notes, '') || "
                                    "' | Appointment cancelled: advance kept as credit'), updated_at = now() WHERE id = %s",
                                    (round(float(iv["total"]) - paid, 2), inv))
                        _settle(cur, inv, uid)
                    else:
                        cur.execute("UPDATE invoices SET notes = trim(coalesce(notes, '') || "
                                    "' | Appointment cancelled: advance kept as credit') WHERE id = %s", (inv,))
                    record(cur, action="appointment.cancel_bill", resource="invoices", resource_id=str(inv),
                           actor=str(uid), actor_type="human", status="ok",
                           detail={"paid": paid, "outcome": "voided" if paid <= 0 else "advance kept as credit"}, error=None)
            record(cur, action="appointment.update", resource="appointments",
                   resource_id=appt_id, actor=str(uid), actor_type="human", status="ok",
                   detail={"fields": [s.split(" ")[0] for s in sets]}, error=None)
        return {"ok": True}

    @router.put("/appointments/{appt_id}/bill")
    def edit_bill(appt_id: str, body: dict = Body(...),
                  x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Correct what a visit was billed: its services, prices, discount, GST.

        The owner's call only: changing a price after the fact is exactly what
        a till has to be protected from. Payments already taken are never
        touched, and the bill can never be made smaller than what was paid
        (that would be a refund, which is its own decision). The lines are
        replaced, the totals recomputed by the same code as booking, and the
        before/after totals are written to the audit log."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if role not in OWNER_ROLES:
            raise HTTPException(403, "Only the business owner can change the prices on a saved bill.")
        pb = _price_lines(body)
        if not pb["lines"]:
            raise HTTPException(400, "A bill needs at least one service. To remove the bill, "
                                     "delete the appointment instead.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, invoice_id, customer_id, location_id, currency, "
                        "to_char(starts_at AT TIME ZONE %s, 'YYYY-MM-DD') AS day "
                        "FROM appointments WHERE id = %s", (TZ, appt_id))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404, "Appointment not found.")
            before = None
            inv_id = a["invoice_id"]
            if inv_id:
                cur.execute("SELECT total, voided_at, currency FROM invoices WHERE id = %s", (inv_id,))
                iv = cur.fetchone()
                if iv and iv["voided_at"]:
                    raise HTTPException(409, "This bill was voided, so it cannot be changed.")
                before = float(iv["total"] or 0) if iv else None
                paid = _paid_so_far(cur, inv_id)
                if pb["total"] + 0.009 < paid:
                    raise HTTPException(409, "PKR {:,.0f} has already been paid on this bill, so the new total "
                                             "({:,.0f}) cannot be less than that.".format(paid, pb["total"]))
                cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (inv_id,))
                cur.execute("UPDATE invoices SET discount = %s, tax = %s, updated_at = now() WHERE id = %s",
                            (pb["discount"], pb["tax"], inv_id))
            else:
                number = _next_invoice_number(cur)
                cur.execute(
                    "INSERT INTO invoices (tenant_id, customer_id, location_id, number, issued_on, "
                    "currency, discount, tax, notes) VALUES (current_tenant(), %s, %s, %s, %s, %s, "
                    "%s, %s, %s) RETURNING id",
                    (a["customer_id"], a["location_id"], number, a["day"], a["currency"] or "PKR",
                     pb["discount"], pb["tax"], "Appointment %s" % a["day"]))
                inv_id = cur.fetchone()["id"]
            for l in pb["lines"]:
                cur.execute(
                    "INSERT INTO invoice_items (tenant_id, invoice_id, service_id, package_id, "
                    "description, quantity, unit_price, line_total) "
                    "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)",
                    (inv_id, l["service_id"], l["package_id"], l["description"], l["quantity"],
                     l["unit_price"], l["line_total"]))
            money = _settle(cur, inv_id, uid)
            cur.execute("UPDATE appointments SET invoice_id = %s, service = %s, price = %s WHERE id = %s",
                        (inv_id, ", ".join(l["description"] for l in pb["lines"])[:500],
                         pb["total"], appt_id))
            record(cur, action="appointment.bill_edit", resource="appointments",
                   resource_id=str(appt_id), actor=str(uid), actor_type="human", status="ok",
                   detail={"invoice_id": str(inv_id), "total_before": before,
                           "total_after": pb["total"], "lines": len(pb["lines"])}, error=None)
        return {"ok": True, "invoice_id": str(inv_id), "money": money}

    @router.delete("/appointments/{appt_id}")
    def remove(appt_id: str,
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Remove a booking. Money already taken is never erased: if its bill has
        a payment the delete is refused (cancel it instead). An unpaid bill is
        voided with a reason, so the invoice number and history stay on record."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if role not in OWNER_ROLES:
            raise HTTPException(403, "Only the business owner can delete an appointment. "
                                     "Mark it cancelled instead.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, invoice_id FROM appointments WHERE id = %s", (appt_id,))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404, "Appointment not found.")
            voided = None
            if a["invoice_id"]:
                if _paid_so_far(cur, a["invoice_id"]) > 0:
                    raise HTTPException(409, "Money has been received on this appointment's bill, "
                                             "so it cannot be deleted. Mark it cancelled instead.")
                cur.execute("UPDATE invoices SET voided_at = COALESCE(voided_at, now()), "
                            "void_reason = COALESCE(void_reason, 'Appointment deleted'), "
                            "status = 'void', updated_at = now() WHERE id = %s RETURNING number",
                            (a["invoice_id"],))
                voided = (cur.fetchone() or {}).get("number")
            cur.execute("DELETE FROM appointments WHERE id = %s", (appt_id,))
            record(cur, action="appointment.delete", resource="appointments",
                   resource_id=appt_id, actor=str(uid), actor_type="human", status="ok",
                   detail={"voided_invoice": voided}, error=None)
        return {"ok": True, "deleted": appt_id, "voided_invoice": voided}

    @router.get("/customers/{customer_id}/history")
    def history(customer_id: str,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Everything this business has on one customer, read from the real rows:
        appointments, bills with their lines and packages, payments, balance.
        Nothing is summarised that is not also listed."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, name, phone, email, source, status, tags, notes, "
                        "first_seen_at, last_interaction_at, created_at, updated_at "
                        "FROM customers WHERE id = %s", (customer_id,))
            c = cur.fetchone()
            if not c:
                raise HTTPException(404, "Customer not found.")
            cur.execute(
                "SELECT a.id, to_char(a.starts_at AT TIME ZONE %s, 'YYYY-MM-DD') AS day, "
                "  to_char(a.starts_at AT TIME ZONE %s, 'HH24:MI') AS time, "
                "  to_char(a.ends_at AT TIME ZONE %s, 'HH24:MI') AS end_time, a.status, "
                "  a.service, a.department, a.booked_by, a.assisted_by, a.procedure_by, "
                "  a.total_sessions, a.next_appointment, a.notes, a.invoice_id, "
                "  st.name AS staff_name, l.name AS location_name, i.number AS invoice_number "
                "FROM appointments a LEFT JOIN staff st ON st.id = a.staff_id "
                "LEFT JOIN locations l ON l.id = a.location_id "
                "LEFT JOIN invoices i ON i.id = a.invoice_id "
                "WHERE a.customer_id = %s ORDER BY a.starts_at DESC LIMIT 500",
                (TZ, TZ, TZ, customer_id))
            appts = cur.fetchall()
            cur.execute(
                "SELECT i.id, i.number, i.issued_on, i.status, i.currency, i.subtotal, i.discount, "
                "  i.tax, i.total, i.voided_at, i.void_reason, i.notes, "
                "  COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid "
                "FROM invoices i WHERE i.customer_id = %s ORDER BY i.issued_on DESC, i.number DESC "
                "LIMIT 500", (customer_id,))
            invoices = cur.fetchall()
            ids = [i["id"] for i in invoices]
            lines = []
            if ids:
                cur.execute(
                    "SELECT ii.invoice_id, ii.description, ii.quantity, ii.unit_price, ii.line_total, "
                    "  ii.service_id, ii.product_id, ii.package_id, pk.name AS package_name "
                    "FROM invoice_items ii LEFT JOIN packages pk ON pk.id = ii.package_id "
                    "WHERE ii.invoice_id = ANY(%s) ORDER BY ii.description", (ids,))
                lines = cur.fetchall()
            cur.execute(
                "SELECT p.id, p.invoice_id, i.number AS invoice_number, p.amount, p.currency, "
                "  p.method, p.reference, p.notes, p.paid_at "
                "FROM payments p LEFT JOIN invoices i ON i.id = p.invoice_id "
                "WHERE p.customer_id = %s OR p.invoice_id = ANY(%s) "
                "ORDER BY p.paid_at DESC LIMIT 500", (customer_id, ids or [None]))
            payments = cur.fetchall()
        by_inv = {}
        for ln in lines:
            by_inv.setdefault(ln["invoice_id"], []).append(ln)
        billed = paid_total = 0.0
        for i in invoices:
            i["items"] = by_inv.get(i["id"], [])
            i["paid"] = round(float(i["paid"] or 0), 2)
            i["total"] = round(float(i["total"] or 0), 2)
            i["balance"] = round(max(i["total"] - i["paid"], 0), 2) if not i["voided_at"] else 0
            if not i["voided_at"]:
                billed += i["total"]
                paid_total += i["paid"]
        packages = sorted({ln["package_name"] for ln in lines if ln.get("package_name")})
        staff = sorted({x for a in appts for x in (a["staff_name"], a["procedure_by"],
                                                   a["assisted_by"], a["booked_by"]) if x})
        return {
            "customer": c,
            "appointments": appts,
            "invoices": invoices,
            "payments": payments,
            "packages": packages,
            "staff": staff,
            "totals": {"visits": len(appts), "billed": round(billed, 2),
                       "paid": round(paid_total, 2),
                       "balance": round(max(billed - paid_total, 0), 2)},
        }

    return router
