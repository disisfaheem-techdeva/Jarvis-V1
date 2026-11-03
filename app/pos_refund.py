"""POS refunds and returns (3 Nov 2026).

A refund is money handed back for lines of a PAID till sale. It is its own
record (pos_refunds), never a negative payment, so "payments" keeps meaning
money received; Daily Closing, P&L, the Orders summary and the shift report
subtract refunds themselves.

Rules:
  * only a till sale that was fully paid (recognised) can be refunded;
  * per line, never more than was sold minus what was already refunded;
  * the money for a line is its share of what the customer actually paid,
    so discount and added tax come back in the same proportion;
  * all refunds of a bill together never exceed what was paid for it;
  * a cash refund comes out of an open drawer (when the business uses shifts),
    and never more cash than the drawer holds;
  * restock puts tracked products back with a 'return' stock movement;
  * the owner refunds, or a cashier with the owner's or a manager's PIN.
"""
from __future__ import annotations

from .audit import record
from .orders import _event, _load, _money

METHODS = ("cash", "card", "bank", "online", "other")


class RefundError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _next_number(cur):
    cur.execute("SELECT number FROM pos_refunds WHERE number ~ '^RF-[0-9]+$' "
                "ORDER BY (substring(number from 4))::int DESC LIMIT 1")
    r = cur.fetchone()
    return "RF-%04d" % (1 if not r else int(r["number"][3:]) + 1)


def refunded(cur, order_id):
    """(total refunded, {order_item_id: quantity refunded})."""
    cur.execute("SELECT COALESCE(sum(amount), 0) AS a FROM pos_refunds WHERE order_id = %s", (order_id,))
    total = _money(cur.fetchone()["a"])
    cur.execute("""SELECT ri.order_item_id::text AS id, sum(ri.quantity) AS q FROM pos_refund_items ri
                     JOIN pos_refunds r ON r.id = ri.refund_id WHERE r.order_id = %s GROUP BY 1""", (order_id,))
    return total, {r["id"]: float(r["q"]) for r in cur.fetchall()}


def refund(cur, oid, *, lines, method, reason, restock, shift, actor, approved_by):
    o = _load(cur, oid)
    if o["source"] != "pos":
        raise RefundError(409, "Only till sales are refunded here.")
    if not o["recognised_at"]:
        raise RefundError(409, "Only a fully paid sale can be refunded. Open bills are cancelled, not refunded.")
    if method not in METHODS:
        raise RefundError(400, "Method must be cash, card, bank, online or other.")
    reason = " ".join(str(reason or "").split())[:300]
    if len(reason) < 3:
        raise RefundError(400, "Write why the money is going back.")
    if not isinstance(lines, list) or not lines:
        raise RefundError(400, "Choose what is being returned.")
    cur.execute("SELECT id::text, product_id, description, quantity, line_total FROM order_items WHERE order_id = %s", (o["id"],))
    items = {r["id"]: dict(r) for r in cur.fetchall()}
    done_total, done_qty = refunded(cur, o["id"])
    # the share of each rupee of list price the customer actually paid
    sub = _money(o["subtotal"])
    paid_total = _money(o["total"])
    factor = (paid_total / sub) if sub > 0 else 0.0
    picked, amount = [], 0.0
    for ln in lines:
        ln = ln or {}
        it = items.get(str(ln.get("order_item_id") or ""))
        if not it:
            raise RefundError(400, "That line is not on this bill.")
        try:
            q = round(float(ln.get("quantity")), 2)
        except (TypeError, ValueError):
            raise RefundError(400, "The quantity must be a number.")
        left = round(float(it["quantity"]) - done_qty.get(it["id"], 0.0), 2)
        if q <= 0 or q - left > 0.009:
            raise RefundError(409, "%s: only %g can still be returned." % (it["description"], max(left, 0)))
        unit_paid = float(it["line_total"]) / float(it["quantity"]) * factor
        a = round(unit_paid * q, 2)
        picked.append((it, q, a))
        amount += a
    amount = round(min(amount, paid_total - done_total), 2)
    if amount <= 0:
        raise RefundError(409, "Everything paid on this bill has already been refunded.")
    if method == "cash" and shift is not None:
        from .pos_shift import figures
        if amount - figures(cur, shift)["expected_cash"] > 0.009:
            raise RefundError(409, "There is not that much cash in the drawer.")
    number = _next_number(cur)
    cur.execute("""INSERT INTO pos_refunds (tenant_id, number, order_id, shift_id, method, amount, reason, restocked,
                                            created_by, approved_by)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (number, o["id"], shift["id"] if shift else None, method, amount, reason, bool(restock),
                 str(actor or "") or None, str(approved_by or "") or None))
    rid = cur.fetchone()["id"]
    back = []
    for it, q, a in picked:
        cur.execute("""INSERT INTO pos_refund_items (tenant_id, refund_id, order_item_id, description, quantity, amount)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s)""", (rid, it["id"], it["description"], q, a))
        if restock and it["product_id"]:
            cur.execute("""SELECT (p.track_stock OR EXISTS (SELECT 1 FROM stock_movements m WHERE m.product_id = p.id)) AS t
                             FROM products p WHERE p.id = %s""", (it["product_id"],))
            t = cur.fetchone()
            if t and t["t"]:
                cur.execute("""INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta, reason, reference)
                               VALUES (current_tenant(), %s, 'return', %s, %s, %s)""",
                            (it["product_id"], q, "Returned at the till", number))
                back.append("%g x %s" % (q, it["description"]))
    _event(cur, o["id"], actor, "human", "refund",
           "{} Rs {:,.0f} back ({}). Reason: {}{}".format(number, amount, method, reason,
                                                          ("; back in stock: " + ", ".join(back)) if back else ""))
    record(cur, action="pos.refund", resource="orders", resource_id=str(o["id"]), actor=str(actor or ""),
           detail={"number": o["number"], "refund": number, "amount": amount, "method": method, "reason": reason,
                   "approved_by": approved_by, "restocked": back})
    return {"refund_id": str(rid), "number": number, "amount": amount, "method": method, "restocked": back}


def for_order(cur, order_id):
    cur.execute("""SELECT r.id::text, r.number, r.method, r.amount, r.reason, r.restocked, r.created_at,
                          COALESCE(json_agg(json_build_object('order_item_id', ri.order_item_id, 'description', ri.description,
                                                              'quantity', ri.quantity, 'amount', ri.amount))
                                   FILTER (WHERE ri.id IS NOT NULL), '[]') AS items
                     FROM pos_refunds r LEFT JOIN pos_refund_items ri ON ri.refund_id = r.id
                    WHERE r.order_id = %s GROUP BY r.id ORDER BY r.created_at""", (order_id,))
    return [{**dict(r), "amount": _money(r["amount"])} for r in cur.fetchall()]
