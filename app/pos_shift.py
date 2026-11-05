"""POS shifts: one cashier's drawer from opening float to counted close (3 Nov 2026).

    expected cash = opening float
                  + cash payments taken into this drawer (payments.pos_shift_id)
                  + cash put in  - cash taken out (pos_cash_moves)
                  - cash refunds paid from this drawer

The close is a blind count: the cashier types what is in the drawer before
the till shows what was expected, so the count is not steered by the figure.
The variance is stored as it was found and never edited (a shift closes once).
X report = the same figures for an open shift; Z report = the closed shift.
"""
from __future__ import annotations

from .audit import record
from .orders import MONEY_ROLES, _money

MAX_FLOAT = 10_000_000


class ShiftError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _amount(v, what, allow_zero=False):
    try:
        a = round(float(v), 2)
    except (TypeError, ValueError):
        raise ShiftError(400, "%s must be a number." % what)
    if a < 0 or (a == 0 and not allow_zero) or a > MAX_FLOAT:
        raise ShiftError(400, "%s must be %s Rs 0 and sensible." % (what, "at least" if allow_zero else "more than"))
    return a


def _register(v):
    r = " ".join(str(v or "Till 1").split())[:40]
    return r or "Till 1"


def _next_number(cur):
    cur.execute("SELECT number FROM pos_shifts WHERE number ~ '^SH-[0-9]+$' "
                "ORDER BY (substring(number from 4))::int DESC LIMIT 1")
    r = cur.fetchone()
    return "SH-%04d" % (1 if not r else int(r["number"][3:]) + 1)


def current(cur, register):
    cur.execute("SELECT * FROM pos_shifts WHERE lower(register) = lower(%s) AND closed_at IS NULL", (_register(register),))
    r = cur.fetchone()
    return dict(r) if r else None


def require(cur, register, settings):
    """The open shift for this till, or None when the business does not use shifts."""
    s = current(cur, register)
    if not s and settings.get("require_shift", True):
        raise ShiftError(409, "Open the shift first: count the cash in the drawer and start the day.")
    return s


def open_shift(cur, register, opening_float, *, actor):
    register = _register(register)
    if current(cur, register):
        raise ShiftError(409, "%s already has an open shift. Close it first." % register)
    amt = _amount(opening_float, "The opening cash", allow_zero=True)
    number = _next_number(cur)
    cur.execute("""INSERT INTO pos_shifts (tenant_id, number, register, opened_by, opening_float)
                   VALUES (current_tenant(), %s, %s, %s, %s) RETURNING *""", (number, register, str(actor or "") or None, amt))
    s = dict(cur.fetchone())
    record(cur, action="pos.shift_open", resource="pos_shifts", resource_id=str(s["id"]), actor=str(actor or ""),
           detail={"number": number, "register": register, "opening_float": amt})
    return s


def cash_move(cur, register, kind, amount, reason, *, actor):
    s = current(cur, register)
    if not s:
        raise ShiftError(409, "No open shift on this till.")
    if kind not in ("in", "out"):
        raise ShiftError(400, "Choose cash in or cash out.")
    amt = _amount(amount, "The amount")
    reason = " ".join(str(reason or "").split())[:200]
    if len(reason) < 3:
        raise ShiftError(400, "Write why the cash is going %s." % ("in" if kind == "in" else "out"))
    if kind == "out" and amt - figures(cur, s)["expected_cash"] > 0.009:
        raise ShiftError(409, "There is not that much cash in the drawer.")
    cur.execute("""INSERT INTO pos_cash_moves (tenant_id, shift_id, kind, amount, reason, created_by)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s)""", (s["id"], kind, amt, reason, str(actor or "") or None))
    record(cur, action="pos.cash_" + kind, resource="pos_shifts", resource_id=str(s["id"]), actor=str(actor or ""),
           detail={"amount": amt, "reason": reason})
    return figures(cur, s)


def figures(cur, s):
    """Everything the X/Z report shows for one shift."""
    sid = s["id"]
    cur.execute("SELECT method, sum(amount) AS amount, count(*) AS n FROM payments WHERE pos_shift_id = %s GROUP BY 1", (sid,))
    taken = {r["method"]: _money(r["amount"]) for r in cur.fetchall()}
    cur.execute("SELECT kind, COALESCE(sum(amount), 0) AS amount FROM pos_cash_moves WHERE shift_id = %s GROUP BY 1", (sid,))
    moves = {r["kind"]: _money(r["amount"]) for r in cur.fetchall()}
    cur.execute("SELECT method, sum(amount) AS amount, count(*) AS n FROM pos_refunds WHERE shift_id = %s GROUP BY 1", (sid,))
    refunds = {r["method"]: _money(r["amount"]) for r in cur.fetchall()}
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'delivered') AS sales,
                          count(*) FILTER (WHERE status = 'confirmed') AS open,
                          count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                          count(*) FILTER (WHERE status = 'delivered' AND recognised_at IS NULL) AS udhaar,
                          COALESCE(sum(total) FILTER (WHERE status = 'delivered'), 0) AS gross,
                          COALESCE(sum(discount) FILTER (WHERE status = 'delivered'), 0) AS discounts,
                          COALESCE(sum(tax) FILTER (WHERE status = 'delivered'), 0) AS tax
                     FROM orders WHERE shift_id = %s""", (sid,))
    o = cur.fetchone()
    expected = round(_money(s["opening_float"]) + taken.get("cash", 0) + moves.get("in", 0)
                     - moves.get("out", 0) - refunds.get("cash", 0), 2)
    return {"id": str(sid), "number": s["number"], "register": s["register"], "opened_at": s["opened_at"],
            "closed_at": s.get("closed_at"), "opening_float": _money(s["opening_float"]),
            "taken_by_method": taken, "taken_total": round(sum(taken.values()), 2),
            "cash_in": moves.get("in", 0), "cash_out": moves.get("out", 0),
            "refunds_by_method": refunds, "refunds_total": round(sum(refunds.values()), 2),
            "sales": int(o["sales"]), "open_bills": int(o["open"]), "cancelled": int(o["cancelled"]),
            "udhaar_bills": int(o["udhaar"]), "gross_sales": _money(o["gross"]), "discounts": _money(o["discounts"]),
            "tax": _money(o["tax"]), "expected_cash": expected,
            "counted_cash": _money(s["counted_cash"]) if s.get("counted_cash") is not None else None,
            "variance": _money(s["variance"]) if s.get("variance") is not None else None,
            "notes": s.get("notes")}


def report(cur, s, role):
    """X/Z figures as this login may see them. A cashier does not see the
    expected cash of an OPEN shift: that would steer the blind count."""
    f = figures(cur, s)
    if role not in MONEY_ROLES and not f["closed_at"]:
        for k in ("expected_cash", "taken_by_method", "taken_total", "gross_sales"):
            f[k] = None
        f["hidden"] = "The owner sees the money figures; you see them after you count and close."
    return f


def close_shift(cur, register, counted_cash, notes, *, actor, role):
    s = current(cur, register)
    if not s:
        raise ShiftError(409, "No open shift on this till.")
    counted = _amount(counted_cash, "The counted cash", allow_zero=True)
    # open bills (a table still eating) are not blocked: they carry over and
    # their money lands in whichever drawer settles them
    f = figures(cur, s)
    variance = round(counted - f["expected_cash"], 2)
    note = " ".join(str(notes or "").split())[:500] or None
    if abs(variance) > 0.009 and not note:
        raise ShiftError(400, "The drawer is Rs {:,.0f} {}. Write a note on why.".format(abs(variance), "over" if variance > 0 else "short"))
    cur.execute("""UPDATE pos_shifts SET closed_at = now(), closed_by = %s, counted_cash = %s, expected_cash = %s,
                          variance = %s, notes = %s WHERE id = %s AND closed_at IS NULL RETURNING *""",
                (str(actor or "") or None, counted, f["expected_cash"], variance, note, s["id"]))
    s = dict(cur.fetchone())
    record(cur, action="pos.shift_close", resource="pos_shifts", resource_id=str(s["id"]), actor=str(actor or ""),
           detail={"number": s["number"], "counted": counted, "expected": f["expected_cash"], "variance": variance})
    return report(cur, s, role)


def history(cur, limit=30):
    cur.execute("SELECT * FROM pos_shifts ORDER BY opened_at DESC LIMIT %s", (max(1, min(int(limit or 30), 100)),))
    return [figures(cur, dict(r)) for r in cur.fetchall()]


def load(cur, sid):
    cur.execute("SELECT * FROM pos_shifts WHERE id = %s::uuid", (sid,))
    r = cur.fetchone()
    if not r:
        raise ShiftError(404, "That shift is not in this business.")
    return dict(r)
