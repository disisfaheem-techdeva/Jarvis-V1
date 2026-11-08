"""POS: counter billing for shops and restaurants (3 Nov 2026).

A till sale is an ordinary order (orders.py) with source 'pos' and pickup
fulfilment, so the money goes through the same invoice and payments as every
other sale and Daily Closing, P&L and stock need nothing new:

    pay now:   new -> confirmed -> delivered + paid            (one transaction)
    open bill: new -> confirmed (a table, a held cart) ... settle later
    udhaar:    new -> confirmed -> delivered, part or no payment; recognised
               when the last rupee is collected (orders._recognise)

The sale is recognised (and stock taken out) by orders._recognise, exactly
once, when the last rupee lands. Everything for one sale runs in one database
transaction: a till that loses its connection halfway never leaves a sale
delivered but unpaid, and the till's own reference (client_ref) makes a resend
return the first sale instead of charging twice.

Money rules, the same as the rest of the dashboard: a discount, a price
change, udhaar, cancelling a bill and removing a line the kitchen has are the
owner's (or a cashier with the owner's/manager's PIN, pos_settings.approve);
no sale is kept short-paid unless it is udhaar to a named customer; cash
handed over beyond the bill is change, not income. Payments taken at the till
carry the drawer they went into (payments.pos_shift_id) for the shift report.

Shifts: pos_shift.py. Refunds: pos_refund.py. Routes: pos_routes.py.
"""
from __future__ import annotations

import re

from .audit import record
from .orders import (MAX_ITEMS, OrderError, _event, _load, _money, catalogue, create_order, match_item, move,
                     pay, view)
from . import pos_settings as cfg
from .pos_refund import for_order as refunds_for, refunded

MODES = ("counter", "dine_in", "takeaway")
METHODS = ("cash", "card", "bank", "online", "other")
MODE_LABEL = {"counter": "Counter", "dine_in": "Dine-in", "takeaway": "Takeaway"}
REF = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
KARACHI_DAY = "(now() AT TIME ZONE 'Asia/Karachi')::date"


class PosError(OrderError):
    pass


def _clean(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _pos_order(cur, oid):
    o = _load(cur, oid)
    if o["source"] != "pos":
        raise PosError(409, "That order was not made at the till. Open it under Orders.")
    return o


def _authority(ctx, action, message):
    """The approver's id (None when the owner acts). Raises when not allowed."""
    try:
        return cfg.need(ctx["role"], ctx["tid"], ctx.get("approval"), action, message)
    except cfg.PosSettingsError as e:
        raise PosError(e.status, e.message)


# ------------------------------------------------------------------ catalogue
def till_catalogue(cur):
    """What the till sells: active products (with stock on hand when tracked)
    and services. Stock is derived from stock_movements, never stored."""
    cur.execute("""SELECT p.id::text, p.name, p.price, p.category, p.sku, 'product' AS kind,
                          (p.track_stock OR count(m.id) > 0) AS tracked,
                          COALESCE(sum(m.quantity_delta), 0) AS on_hand, p.unit, p.aliases
                     FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id
                    WHERE p.active GROUP BY p.id
                   UNION ALL
                   SELECT s.id::text, s.name, s.price, NULL, NULL, 'service', false, 0, NULL, '{}'::text[]
                     FROM services s WHERE s.active
                    ORDER BY 2""")
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "category": r["category"] or None,
             "sku": r["sku"] or None, "price": _money(r["price"]) if r["price"] is not None else None,
             "unit": r["unit"], "aliases": list(r["aliases"] or []),
             # null, not 0: an untracked product has no stock figure at all
             "on_hand": float(r["on_hand"]) if r["tracked"] else None} for r in cur.fetchall()]


# ------------------------------------------------------------------ totals
def _retotal(cur, oid):
    """Subtotal from the lines, then tax as this bill was opened with (a
    settings change later never re-prices an open bill)."""
    cur.execute("""SELECT COALESCE((SELECT sum(line_total) FROM order_items WHERE order_id = o.id), 0) AS sub,
                          o.discount, o.delivery_fee, o.tax_rate, o.tax_mode FROM orders o WHERE o.id = %s""", (oid,))
    r = cur.fetchone()
    sub, disc, fee = _money(r["sub"]), _money(r["discount"]), _money(r["delivery_fee"])
    if disc - sub > 0.009:
        raise PosError(400, "The discount is more than the bill.")
    base = round(max(0.0, sub - disc), 2)
    rate = float(r["tax_rate"] or 0)
    tax = 0.0
    if r["tax_mode"] == "exclusive":
        tax = round(base * rate / 100, 2)
    elif r["tax_mode"] == "inclusive":
        tax = round(base - base / (1 + rate / 100), 2)
    total = round(base + fee + (tax if r["tax_mode"] == "exclusive" else 0), 2)
    cur.execute("UPDATE orders SET subtotal = %s, tax = %s, total = %s, updated_at = now() WHERE id = %s",
                (sub, tax, total, oid))
    return total


def _tag_shift(cur, oid, shift):
    """Payments made in this transaction went into this drawer."""
    if not shift:
        return
    cur.execute("SELECT invoice_id FROM orders WHERE id = %s", (oid,))
    inv = cur.fetchone()["invoice_id"]
    if inv:
        cur.execute("UPDATE payments SET pos_shift_id = %s WHERE invoice_id = %s AND pos_shift_id IS NULL "
                    "AND created_at = now()", (shift["id"], inv))


# ------------------------------------------------------------------ payments
def _plan_payments(total, payments, *, credit=False):
    """[(method, amount kept, ref)], cash tendered, change. Card, bank and
    online must not exceed what is owed; cash may (the rest is change).
    Never short, unless this is udhaar."""
    payments = payments or []
    if not isinstance(payments, list):
        raise PosError(400, "Payments must be a list.")
    if not payments and not credit:
        raise PosError(400, "Take a payment: cash, card, bank or online.")
    if len(payments) > 6:
        raise PosError(400, "Split the bill into at most 6 payments.")
    cash, other = 0.0, []
    for p in payments:
        p = p or {}
        method = str(p.get("method") or "cash").strip().lower()
        if method not in METHODS:
            raise PosError(400, "Method must be cash, card, bank, online or other.")
        try:
            amt = round(float(p.get("amount")), 2)
        except (TypeError, ValueError):
            raise PosError(400, "Each payment amount must be a number.")
        if amt <= 0:
            raise PosError(400, "Each payment must be more than Rs 0.")
        if method == "cash":
            cash += amt
        else:
            other.append((method, amt, _clean(p.get("reference"), 120) or None))
    non_cash = round(sum(a for _, a, _ in other), 2)
    if non_cash - total > 0.009:
        raise PosError(409, "Card/bank/online payments (Rs {:,.0f}) are more than the bill (Rs {:,.0f}).".format(non_cash, total))
    cash_needed = round(total - non_cash, 2)
    if cash + 0.009 < cash_needed and not credit:
        raise PosError(409, "Rs {:,.0f} short. The bill is Rs {:,.0f}.".format(cash_needed - cash, total))
    kept = round(min(cash, cash_needed), 2)
    plan = list(other) + ([("cash", kept, None)] if kept > 0 else [])
    change = round(cash - kept, 2) if cash > 0 else None
    return plan, (round(cash, 2) if cash > 0 else None), change


def settle(cur, oid, payments, *, ctx, shift, credit=False):
    """Hand over and take the money for a confirmed till bill."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed":
        raise PosError(409, "This bill is already %s." % ("paid" if o["status"] == "delivered" else o["status"]))
    total = _money(o["total"])
    if total <= 0:
        raise PosError(409, "The bill is Rs 0. Add items, or cancel it.")
    approver = None
    if credit:
        if not (o["customer_name"] and len(re.sub(r"\D", "", o["customer_phone"] or "")) >= 7):
            raise PosError(400, "Udhaar needs the customer's name and phone number.")
        if not ctx["settings"].get("allow_staff_credit"):
            approver = _authority(ctx, "credit", "Udhaar needs the owner's or a manager's PIN.")
    plan, tendered, change = _plan_payments(total, payments, credit=credit)
    move(cur, oid, "delivered", actor=ctx["uid"], role=ctx["role"])
    for method, amount, ref in plan:
        pay(cur, oid, amount, method=method, reference=ref, actor=ctx["uid"], role=ctx["role"])
    _tag_shift(cur, o["id"], shift)
    cur.execute("UPDATE orders SET cash_tendered = %s, change_given = %s, updated_at = now() WHERE id = %s",
                (tendered, change, o["id"]))
    if change:
        _event(cur, o["id"], ctx["uid"], "human", "change", "Cash Rs {:,.0f} handed over, Rs {:,.0f} change".format(tendered, change))
    if credit:
        owed = round(total - sum(a for _, a, _ in plan), 2)
        if owed > 0:
            _event(cur, o["id"], ctx["uid"], "human", "udhaar",
                   "Rs {:,.0f} on udhaar to {}{}".format(owed, o["customer_name"], (" (approved by %s)" % approver) if approver else ""))
            record(cur, action="pos.udhaar", resource="orders", resource_id=str(o["id"]), actor=str(ctx["uid"] or ""),
                   detail={"number": o["number"], "owed": owed, "approved_by": approver})
    return receipt(cur, oid, ctx)


def collect(cur, oid, payments, *, ctx, shift):
    """Money for an udhaar bill, at the till."""
    o = _pos_order(cur, oid)
    if o["status"] != "delivered" or o["recognised_at"]:
        raise PosError(409, "Nothing is owed on this bill.")
    if not payments:
        raise PosError(400, "Enter the amount received.")
    plan, tendered, change = _plan_payments(round(_money(o["total"]) - _paid(cur, o), 2), payments, credit=True)
    for method, amount, ref in plan:
        pay(cur, oid, amount, method=method, reference=ref, actor=ctx["uid"], role=ctx["role"])
    _tag_shift(cur, o["id"], shift)
    if change:
        _event(cur, o["id"], ctx["uid"], "human", "change", "Cash Rs {:,.0f} handed over, Rs {:,.0f} change".format(tendered, change))
    return receipt(cur, oid, ctx)


def _paid(cur, o):
    if not o["invoice_id"]:
        return 0.0
    cur.execute("SELECT COALESCE(sum(amount), 0) AS a FROM payments WHERE invoice_id = %s", (o["invoice_id"],))
    return _money(cur.fetchone()["a"])


# ------------------------------------------------------------------ sale
def _items_with_prices(items, ctx):
    """A line with its own price needs the owner (or a 'price' approval)."""
    if any((it or {}).get("unit_price") not in (None, "") for it in (items or [])):
        _authority(ctx, "price", "Changing a price needs the owner's or a manager's PIN.")
        return True
    return ctx["role"] in ("owner", "techdeva_admin")


def sale(cur, body, *, ctx, shift):
    """One till sale: pay now (payments), keep it open (hold), or udhaar (credit)."""
    ref = str(body.get("client_ref") or "")
    if not REF.match(ref):
        raise PosError(400, "The till did not send a sale reference. Reload the till.")
    mode = body.get("mode") or "counter"
    if mode not in MODES:
        raise PosError(400, "Choose counter, dine-in or takeaway.")
    hold, credit = bool(body.get("hold")), bool(body.get("credit"))
    if hold and (body.get("payments") or credit):
        raise PosError(400, "An open bill takes no payment yet. Settle it when the customer pays.")
    try:
        discount = round(float(body.get("discount") or 0), 2)
    except (TypeError, ValueError):
        raise PosError(400, "The discount must be a number.")
    if discount < 0:
        raise PosError(400, "A discount cannot be negative.")
    approver = None
    if discount > 0:
        approver = _authority(ctx, "discount", "A discount needs the owner's or a manager's PIN.")
    override = _items_with_prices(body.get("items"), ctx)
    s = ctx["settings"]
    r = create_order(cur, items=body.get("items"), customer_name=body.get("customer_name"),
                     customer_phone=body.get("customer_phone"), fulfilment="pickup", notes=body.get("notes"),
                     discount=discount, source="pos", source_ref="pos:" + ref, actor=ctx["uid"], actor_type="human",
                     allow_price_override=override)
    if r.get("duplicate"):
        # the till resent a sale it already made (lost connection): the first one stands
        return {**receipt(cur, r["order_id"], ctx), "duplicate": True}
    if r["no_price"]:
        raise PosError(400, "No price on your list for: %s. Set the price first." % ", ".join(r["no_price"]))
    tax_mode = s["tax_mode"] if s["tax_mode"] != "off" else None
    cur.execute("""UPDATE orders SET pos_mode = %s, table_label = %s, register = %s, shift_id = %s,
                          tax_mode = %s, tax_rate = %s WHERE id = %s""",
                (mode, _clean(body.get("table"), 40) or None, _clean(body.get("register") or "Till 1", 40),
                 shift["id"] if shift else None, tax_mode, s["tax_rate"] if tax_mode else None, r["order_id"]))
    total = _retotal(cur, r["order_id"])
    move(cur, r["order_id"], "confirmed", actor=ctx["uid"], role=ctx["role"])
    if approver:
        _event(cur, r["order_id"], ctx["uid"], "human", "approval", "Discount Rs {:,.0f} approved by PIN".format(discount))
    record(cur, action="pos.sale", resource="orders", resource_id=r["order_id"], actor=str(ctx["uid"] or ""),
           detail={"number": r["number"], "total": total, "mode": mode, "hold": hold, "credit": credit,
                   "discount": discount, "approved_by": approver})
    if hold:
        return receipt(cur, r["order_id"], ctx)
    return settle(cur, r["order_id"], body.get("payments"), ctx=ctx, shift=shift, credit=credit)


def add_items(cur, oid, items, *, ctx):
    """More for an open bill (the table ordered again). Prices from the list."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed" or o["invoice_id"]:
        raise PosError(409, "Only an open bill takes more items.")
    if not isinstance(items, list) or not items:
        raise PosError(400, "Add at least one item.")
    cur.execute("SELECT count(*) AS n FROM order_items WHERE order_id = %s", (o["id"],))
    if cur.fetchone()["n"] + len(items) > MAX_ITEMS:
        raise PosError(400, "Too many items on one bill (max %d)." % MAX_ITEMS)
    override = _items_with_prices(items, ctx)
    cat = catalogue(cur)
    by_id = {c["id"]: c for c in cat}
    added = []
    for it in items:
        it = it or {}
        c = by_id.get(str(it.get("product_id") or it.get("service_id") or "")) or match_item(cat, it.get("name"))
        if not c:
            raise PosError(400, "Not on your list: %s." % _clean(it.get("name") or "?", 80))
        try:
            qty = float(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 0
        if not 0 < qty <= 10000:
            raise PosError(400, "Quantity for %s must be between 1 and 10000." % c["name"])
        price = float(c["price"]) if c["price"] is not None else None
        if override and it.get("unit_price") not in (None, ""):
            try:
                price = float(it["unit_price"])
            except (TypeError, ValueError):
                raise PosError(400, "The price for %s must be a number." % c["name"])
        if price is None or price < 0:
            raise PosError(400, "No price on your list for: %s. Set the price first." % c["name"])
        cur.execute("""INSERT INTO order_items (tenant_id, order_id, product_id, service_id, description, quantity,
                                                unit_price, line_total, note)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (o["id"], c["id"] if c["kind"] == "product" else None, c["id"] if c["kind"] == "service" else None,
                     c["name"], qty, price, round(qty * price, 2), _clean(it.get("note"), 200) or None))
        added.append("%g x %s" % (qty, c["name"]))
    _retotal(cur, o["id"])
    _event(cur, o["id"], ctx["uid"], "human", "items", "Added: " + ", ".join(added))
    record(cur, action="pos.add_items", resource="orders", resource_id=str(o["id"]), actor=str(ctx["uid"] or ""),
           detail={"number": o["number"], "added": added})
    return receipt(cur, oid, ctx)


def void_line(cur, oid, item_id, reason, *, ctx):
    """Take a line off an open bill. A line the kitchen already has needs the
    owner's or a manager's PIN and a reason: the food may already be made."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed" or o["invoice_id"]:
        raise PosError(409, "Only a line on an open bill can be removed. A paid bill is refunded.")
    cur.execute("SELECT id, description, quantity, kot_at FROM order_items WHERE id = %s::uuid AND order_id = %s",
                (item_id, o["id"]))
    it = cur.fetchone()
    if not it:
        raise PosError(404, "That line is not on this bill.")
    reason = _clean(reason, 200)
    approver = None
    if it["kot_at"]:
        approver = _authority(ctx, "void", "The kitchen has this item. Removing it needs the owner's or a manager's PIN.")
        if len(reason) < 3:
            raise PosError(400, "Write why the item is removed.")
    cur.execute("SELECT count(*) AS n FROM order_items WHERE order_id = %s", (o["id"],))
    if cur.fetchone()["n"] <= 1:
        raise PosError(409, "This is the last item. Cancel the bill instead.")
    cur.execute("DELETE FROM order_items WHERE id = %s", (it["id"],))
    _retotal(cur, o["id"])
    what = "%g x %s" % (float(it["quantity"]), it["description"])
    _event(cur, o["id"], ctx["uid"], "human", "void",
           "Removed %s%s%s" % (what, (" after the kitchen had it. Reason: " + reason) if it["kot_at"] else "",
                               " (approved by PIN)" if approver else ""))
    record(cur, action="pos.void_line", resource="orders", resource_id=str(o["id"]), actor=str(ctx["uid"] or ""),
           detail={"number": o["number"], "line": what, "sent_to_kitchen": bool(it["kot_at"]), "reason": reason or None,
                   "approved_by": approver})
    return receipt(cur, oid, ctx)


def cancel(cur, oid, reason, *, ctx):
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed":
        raise PosError(409, "Only an open bill can be cancelled. A paid bill is refunded.")
    approver = _authority(ctx, "cancel", "Cancelling a bill needs the owner's or a manager's PIN.")
    # orders.move lets only the owner cancel; an approved cashier acts with the owner's authority
    move(cur, oid, "cancelled", actor=ctx["uid"], role="owner",
         reason=(_clean(reason, 260) + (" (approved by PIN)" if approver else "")))
    return receipt(cur, oid, ctx)


def kitchen_ticket(cur, oid, *, ctx):
    """The lines the kitchen has not had yet, marked as sent. Printing is the
    till's job; this only says what to print, once."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed":
        raise PosError(409, "Only an open bill goes to the kitchen.")
    cur.execute("""UPDATE order_items SET kot_at = now() WHERE order_id = %s AND kot_at IS NULL
                   RETURNING description, quantity, note""", (o["id"],))
    lines = [{"description": r["description"], "quantity": float(r["quantity"]), "note": r["note"]} for r in cur.fetchall()]
    if lines:
        _event(cur, o["id"], ctx["uid"], "human", "kot", "Sent to kitchen: " + ", ".join(
            "%g x %s" % (l["quantity"], l["description"]) for l in lines))
    return {"number": o["number"], "table": o["table_label"], "mode_label": MODE_LABEL.get(o["pos_mode"]),
            "lines": lines}


# ------------------------------------------------------------------ read
def receipt(cur, oid, ctx):
    """The bill as the till prints it: lines, tax, payments, change, refunds."""
    out = view(cur, oid, ctx["role"])
    o = _load(cur, oid)
    cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
    t = cur.fetchone()
    cur.execute("SELECT id::text, note, kot_at FROM order_items WHERE order_id = %s", (o["id"],))
    extra = {r["id"]: r for r in cur.fetchall()}
    for it in out["items"]:
        e = extra.get(it["id"]) or {}
        it["note"], it["sent_to_kitchen"] = e.get("note"), bool(e.get("kot_at"))
    pays = []
    if o["invoice_id"]:
        cur.execute("SELECT method, amount, reference, paid_at FROM payments WHERE invoice_id = %s ORDER BY paid_at",
                    (o["invoice_id"],))
        pays = [{"method": r["method"], "amount": _money(r["amount"]), "reference": r["reference"], "paid_at": r["paid_at"]}
                for r in cur.fetchall()]
    cashier = None
    if o["created_by"] and re.match(r"^[0-9a-f-]{36}$", o["created_by"]):
        cur.execute("SELECT name FROM users WHERE id = %s::uuid", (o["created_by"],))
        u = cur.fetchone()
        cashier = u["name"] if u else None
    rf_total, rf_qty = refunded(cur, o["id"])
    for it in out["items"]:
        it["refunded_quantity"] = rf_qty.get(it["id"], 0.0)
    s = ctx["settings"]
    out.update({"business": t["name"] if t else None, "mode": o["pos_mode"], "mode_label": MODE_LABEL.get(o["pos_mode"]),
                "table": o["table_label"], "register": o["register"], "cashier": cashier, "payments": pays,
                "tax": _money(o["tax"]), "tax_mode": o["tax_mode"],
                "tax_rate": float(o["tax_rate"]) if o["tax_rate"] is not None else None,
                "tax_label": s["tax_label"],
                "cash_tendered": _money(o["cash_tendered"]) if o["cash_tendered"] is not None else None,
                "change_given": _money(o["change_given"]) if o["change_given"] is not None else None,
                "open": o["status"] == "confirmed", "udhaar": o["status"] == "delivered" and not o["recognised_at"],
                "refunds": refunds_for(cur, o["id"]), "refunded": rf_total,
                "receipt": {"header": s["receipt_header"], "footer": s["receipt_footer"], "ntn": s["ntn"]}})
    return out


def open_bills(cur):
    cur.execute("""SELECT o.id::text, o.number, o.pos_mode, o.table_label AS "table", o.customer_name, o.total, o.created_at,
                          (SELECT count(*) FROM order_items i WHERE i.order_id = o.id) AS lines
                     FROM orders o WHERE o.source = 'pos' AND o.status = 'confirmed'
                    ORDER BY o.created_at""")
    return [{**dict(r), "total": _money(r["total"]), "lines": int(r["lines"]), "mode_label": MODE_LABEL.get(r["pos_mode"])}
            for r in cur.fetchall()]


def bills(cur, *, day=None, q=None, status=None, limit=100):
    """Till bills for a day (Pakistan time), newest first. status: paid | open | udhaar | cancelled."""
    where, args = ["o.source = 'pos'"], []
    if day:
        where.append("(o.created_at AT TIME ZONE 'Asia/Karachi')::date = %s")
        args.append(day)
    if q:
        where.append("(o.number ILIKE %s OR o.customer_name ILIKE %s OR o.customer_phone ILIKE %s OR o.table_label ILIKE %s)")
        args += ["%" + q[:60] + "%"] * 4
    if status == "paid":
        where.append("o.recognised_at IS NOT NULL")
    elif status == "open":
        where.append("o.status = 'confirmed'")
    elif status == "udhaar":
        where.append("o.status = 'delivered' AND o.recognised_at IS NULL")
    elif status == "cancelled":
        where.append("o.status = 'cancelled'")
    cur.execute("""SELECT o.id::text, o.number, o.status, o.pos_mode, o.table_label AS "table", o.customer_name, o.total,
                          o.created_at, o.recognised_at,
                          COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = o.invoice_id), 0) AS paid,
                          COALESCE((SELECT sum(r.amount) FROM pos_refunds r WHERE r.order_id = o.id), 0) AS refunded
                     FROM orders o WHERE """ + " AND ".join(where) + """
                    ORDER BY o.created_at DESC LIMIT %s""", args + [max(1, min(int(limit or 100), 300))])
    out = []
    for r in cur.fetchall():
        r = dict(r)
        r["total"], r["paid"], r["refunded"] = _money(r["total"]), _money(r["paid"]), _money(r["refunded"])
        r["state"] = ("cancelled" if r["status"] == "cancelled" else "open" if r["status"] == "confirmed"
                      else "paid" if r["recognised_at"] else "udhaar")
        r["mode_label"] = MODE_LABEL.get(r["pos_mode"])
        out.append(r)
    return out


def today(cur, role):
    """The till's day (Pakistan time). Counts for everyone, money for the owner."""
    cur.execute("""SELECT count(*) FILTER (WHERE recognised_at IS NOT NULL
                                             AND (recognised_at AT TIME ZONE 'Asia/Karachi')::date = """ + KARACHI_DAY + """) AS sales,
                          count(*) FILTER (WHERE status = 'confirmed') AS open,
                          count(*) FILTER (WHERE status = 'delivered' AND recognised_at IS NULL) AS udhaar
                     FROM orders WHERE source = 'pos'""")
    r = cur.fetchone()
    out = {"sales": int(r["sales"]), "open": int(r["open"]), "udhaar": int(r["udhaar"])}
    if role in ("owner", "techdeva_admin"):
        cur.execute("""SELECT p.method, sum(p.amount) AS amount FROM payments p JOIN orders o ON o.invoice_id = p.invoice_id
                        WHERE o.source = 'pos' AND (p.paid_at AT TIME ZONE 'Asia/Karachi')::date = """ + KARACHI_DAY + """
                        GROUP BY 1 ORDER BY 2 DESC""")
        by = [{"method": x["method"], "amount": _money(x["amount"])} for x in cur.fetchall()]
        cur.execute("SELECT COALESCE(sum(amount), 0) AS a FROM pos_refunds WHERE (created_at AT TIME ZONE 'Asia/Karachi')::date = "
                    + KARACHI_DAY)
        rf = _money(cur.fetchone()["a"])
        taken = round(sum(x["amount"] for x in by), 2)
        out["money"] = {"taken": taken, "refunded": rf, "collected": round(taken - rf, 2), "by_method": by}
    return out
