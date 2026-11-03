"""POS: counter billing for shops and restaurants (3 Nov 2026).

A till sale is an ordinary order (orders.py) with source 'pos' and pickup
fulfilment, so the money goes through the same invoice and payments as every
other sale and Daily Closing, P&L and stock need nothing new:

    pay now:   new -> confirmed -> delivered + paid     (one transaction)
    open tab:  new -> confirmed                        (a table, or a held cart)
               ... more items ...
               settle -> delivered + paid

The sale is recognised (and stock taken out) by orders._recognise, exactly
once, when the last rupee lands. Everything for one sale runs in one database
transaction: a till that loses its connection halfway never leaves a sale
delivered but unpaid, and the till's own reference (client_ref) makes a resend
return the first sale instead of charging twice.

Money rules, the same as the rest of the dashboard: only the owner gives a
discount or changes a price at the till; no sale is kept short-paid (credit
sales are not in v1); cash handed over beyond the bill is change, not income.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity
from .orders import (MAX_ITEMS, MONEY_ROLES, OrderError, _event, _load, _money, catalogue,
                     create_order, match_item, move, pay, view)

MODES = ("counter", "dine_in", "takeaway")
METHODS = ("cash", "card", "bank", "online", "other")
MODE_LABEL = {"counter": "Counter", "dine_in": "Dine-in", "takeaway": "Takeaway"}
REF = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class PosError(OrderError):
    pass


def _clean(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _pos_order(cur, oid):
    o = _load(cur, oid)
    if o["source"] != "pos":
        raise PosError(409, "That order was not made at the till. Open it under Orders.")
    return o


# ------------------------------------------------------------------ catalogue
def till_catalogue(cur):
    """What the till sells: active products (with stock on hand when tracked)
    and services. Stock is derived from stock_movements, never stored."""
    cur.execute("""SELECT p.id::text, p.name, p.price, p.category, p.sku, 'product' AS kind,
                          (p.track_stock OR count(m.id) > 0) AS tracked,
                          COALESCE(sum(m.quantity_delta), 0) AS on_hand
                     FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id
                    WHERE p.active GROUP BY p.id
                   UNION ALL
                   SELECT s.id::text, s.name, s.price, NULL, NULL, 'service', false, 0
                     FROM services s WHERE s.active
                    ORDER BY 2""")
    out = []
    for r in cur.fetchall():
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "category": r["category"] or None,
                    "sku": r["sku"] or None,
                    "price": _money(r["price"]) if r["price"] is not None else None,
                    # null, not 0: an untracked product has no stock figure at all
                    "on_hand": float(r["on_hand"]) if r["tracked"] else None})
    return out


# ------------------------------------------------------------------ settle
def _plan_payments(total, payments):
    """[(method, amount kept)], cash tendered, change. Card, bank and online
    must not exceed the bill; cash may (the rest is change). Never short."""
    if not isinstance(payments, list) or not payments:
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
    if cash + 0.009 < cash_needed:
        raise PosError(409, "Rs {:,.0f} short. The bill is Rs {:,.0f}.".format(cash_needed - cash, total))
    plan = [(m, a, ref) for m, a, ref in other]
    if cash_needed > 0:
        plan.append(("cash", cash_needed, None))
    return plan, (round(cash, 2) if cash > 0 else None), (round(cash - cash_needed, 2) if cash > 0 else None)


def settle(cur, oid, payments, *, actor, role):
    """Hand over and take the money for a confirmed till order."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed":
        raise PosError(409, "This bill is already %s." % ("paid" if o["status"] == "delivered" else o["status"]))
    total = _money(o["total"])
    if total <= 0:
        raise PosError(409, "The bill is Rs 0. Add items, or cancel it.")
    plan, tendered, change = _plan_payments(total, payments)
    move(cur, oid, "delivered", actor=actor, role=role)
    for method, amount, ref in plan:
        pay(cur, oid, amount, method=method, reference=ref, actor=actor, role=role)
    cur.execute("UPDATE orders SET cash_tendered = %s, change_given = %s, updated_at = now() WHERE id = %s",
                (tendered, change, o["id"]))
    if change:
        _event(cur, o["id"], actor, "human", "change", "Cash Rs {:,.0f} handed over, Rs {:,.0f} change".format(tendered, change))
    return receipt(cur, oid, role)


# ------------------------------------------------------------------ sale
def sale(cur, body, *, actor, role):
    """One till sale: pay now (payments) or keep it open (hold)."""
    ref = str(body.get("client_ref") or "")
    if not REF.match(ref):
        raise PosError(400, "The till did not send a sale reference. Reload the till.")
    mode = body.get("mode") or "counter"
    if mode not in MODES:
        raise PosError(400, "Choose counter, dine-in or takeaway.")
    table = _clean(body.get("table"), 40) or None
    hold = bool(body.get("hold"))
    if hold and body.get("payments"):
        raise PosError(400, "An open bill takes no payment yet. Settle it when the customer pays.")
    try:
        discount = round(float(body.get("discount") or 0), 2)
    except (TypeError, ValueError):
        raise PosError(400, "The discount must be a number.")
    if discount < 0:
        raise PosError(400, "A discount cannot be negative.")
    if discount > 0 and role not in MONEY_ROLES:
        raise PosError(403, "Only the owner can give a discount at the till.")
    r = create_order(cur, items=body.get("items"), customer_name=body.get("customer_name"),
                     customer_phone=body.get("customer_phone"), fulfilment="pickup", notes=body.get("notes"),
                     discount=discount, source="pos", source_ref="pos:" + ref, actor=actor, actor_type="human",
                     allow_price_override=role in MONEY_ROLES)
    if r.get("duplicate"):
        # the till resent a sale it already made (lost connection): the first one stands
        return {**receipt(cur, r["order_id"], role), "duplicate": True}
    if r["no_price"]:
        raise PosError(400, "No price on your list for: %s. Set the price first." % ", ".join(r["no_price"]))
    cur.execute("SELECT subtotal FROM orders WHERE id = %s", (r["order_id"],))
    if discount - float(cur.fetchone()["subtotal"]) > 0.009:
        raise PosError(400, "The discount is more than the bill.")
    cur.execute("UPDATE orders SET pos_mode = %s, table_label = %s WHERE id = %s", (mode, table, r["order_id"]))
    move(cur, r["order_id"], "confirmed", actor=actor, role=role)
    record(cur, action="pos.sale", resource="orders", resource_id=r["order_id"], actor=str(actor or ""),
           detail={"number": r["number"], "total": r["total"], "mode": mode, "hold": hold})
    if hold:
        return receipt(cur, r["order_id"], role)
    return settle(cur, r["order_id"], body.get("payments"), actor=actor, role=role)


def add_items(cur, oid, items, *, actor, role):
    """More for an open bill (the table ordered again). Prices from the list."""
    o = _pos_order(cur, oid)
    if o["status"] != "confirmed" or o["invoice_id"]:
        raise PosError(409, "Only an open bill takes more items.")
    if not isinstance(items, list) or not items:
        raise PosError(400, "Add at least one item.")
    cur.execute("SELECT count(*) AS n FROM order_items WHERE order_id = %s", (o["id"],))
    if cur.fetchone()["n"] + len(items) > MAX_ITEMS:
        raise PosError(400, "Too many items on one bill (max %d)." % MAX_ITEMS)
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
        if role in MONEY_ROLES and it.get("unit_price") not in (None, ""):
            try:
                price = float(it["unit_price"])
            except (TypeError, ValueError):
                raise PosError(400, "The price for %s must be a number." % c["name"])
        if price is None or price < 0:
            raise PosError(400, "No price on your list for: %s. Set the price first." % c["name"])
        line = round(qty * price, 2)
        cur.execute("""INSERT INTO order_items (tenant_id, order_id, product_id, service_id, description, quantity, unit_price, line_total)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)""",
                    (o["id"], c["id"] if c["kind"] == "product" else None, c["id"] if c["kind"] == "service" else None,
                     c["name"], qty, price, line))
        added.append("%g x %s" % (qty, c["name"]))
    cur.execute("""UPDATE orders SET subtotal = s.sub, total = GREATEST(0, s.sub + delivery_fee - discount), updated_at = now()
                     FROM (SELECT COALESCE(sum(line_total), 0) AS sub FROM order_items WHERE order_id = %s) s
                    WHERE id = %s""", (o["id"], o["id"]))
    _event(cur, o["id"], actor, "human", "items", "Added: " + ", ".join(added))
    record(cur, action="pos.add_items", resource="orders", resource_id=str(o["id"]), actor=str(actor or ""),
           detail={"number": o["number"], "added": added})
    return receipt(cur, oid, role)


# ------------------------------------------------------------------ read
def receipt(cur, oid, role):
    """The order as the till prints it: lines, totals, payments, change."""
    out = view(cur, oid, role)
    o = _load(cur, oid)
    cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
    t = cur.fetchone()
    pays = []
    if o["invoice_id"]:
        cur.execute("SELECT method, amount, reference, paid_at FROM payments WHERE invoice_id = %s ORDER BY paid_at",
                    (o["invoice_id"],))
        pays = [{"method": r["method"], "amount": _money(r["amount"]), "reference": r["reference"], "paid_at": r["paid_at"]}
                for r in cur.fetchall()]
    out.update({"business": t["name"] if t else None, "mode": o["pos_mode"], "mode_label": MODE_LABEL.get(o["pos_mode"]),
                "table": o["table_label"], "payments": pays,
                "cash_tendered": _money(o["cash_tendered"]) if o["cash_tendered"] is not None else None,
                "change_given": _money(o["change_given"]) if o["change_given"] is not None else None,
                "open": o["status"] == "confirmed"})
    return out


def open_bills(cur):
    cur.execute("""SELECT o.id::text, o.number, o.pos_mode, o.table_label AS "table", o.customer_name, o.total, o.created_at,
                          (SELECT count(*) FROM order_items i WHERE i.order_id = o.id) AS lines
                     FROM orders o WHERE o.source = 'pos' AND o.status = 'confirmed'
                    ORDER BY o.created_at""")
    return [{**dict(r), "total": _money(r["total"]), "lines": int(r["lines"]), "mode_label": MODE_LABEL.get(r["pos_mode"])}
            for r in cur.fetchall()]


def today(cur, role):
    """The till's day (Pakistan time). Counts for everyone, money for the owner."""
    day = "(now() AT TIME ZONE 'Asia/Karachi')::date"
    cur.execute("""SELECT count(*) FILTER (WHERE recognised_at IS NOT NULL
                                             AND (recognised_at AT TIME ZONE 'Asia/Karachi')::date = """ + day + """) AS sales,
                          count(*) FILTER (WHERE status = 'confirmed') AS open
                     FROM orders WHERE source = 'pos'""")
    r = cur.fetchone()
    out = {"sales": int(r["sales"]), "open": int(r["open"])}
    if role in MONEY_ROLES:
        cur.execute("""SELECT p.method, sum(p.amount) AS amount FROM payments p JOIN orders o ON o.invoice_id = p.invoice_id
                        WHERE o.source = 'pos' AND (p.paid_at AT TIME ZONE 'Asia/Karachi')::date = """ + day + """
                        GROUP BY 1 ORDER BY 2 DESC""")
        by = [{"method": x["method"], "amount": _money(x["amount"])} for x in cur.fetchall()]
        out["money"] = {"collected": round(sum(x["amount"] for x in by), 2), "by_method": by}
    return out


# ------------------------------------------------------------------ routes
def _err(e):
    raise HTTPException(e.status, e.message)


def _oid(oid):
    if not re.match(r"^[0-9a-f-]{36}$", oid):
        raise HTTPException(404, "That bill is not in this business.")


def make_pos_router():
    router = APIRouter()

    def who(x_tenant_id, x_role, authorization, td_session):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "operator", "techdeva_admin"):
            raise HTTPException(403, "The till is for the business's own team.")
        return tid, role, uid

    @router.get("/pos/catalogue")
    def pos_catalogue(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
            t = cur.fetchone()
            return {"business": t["name"] if t else None, "items": till_catalogue(cur),
                    "can_discount": role in MONEY_ROLES, "methods": list(METHODS)}

    @router.get("/pos/today")
    def pos_today(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {**today(cur, role), "open_bills": open_bills(cur)}

    @router.post("/pos/sale")
    def pos_sale(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        try:
            with tenant_session(tid, role) as cur:
                return sale(cur, body, actor=uid, role=role)
        except OrderError as e:
            _err(e)

    @router.get("/pos/bills/{oid}")
    def pos_bill(oid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        _oid(oid)
        try:
            with tenant_session(tid, role) as cur:
                _pos_order(cur, oid)
                return receipt(cur, oid, role)
        except OrderError as e:
            _err(e)

    @router.post("/pos/bills/{oid}/items")
    def pos_add(oid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        _oid(oid)
        try:
            with tenant_session(tid, role) as cur:
                return add_items(cur, oid, body.get("items"), actor=uid, role=role)
        except OrderError as e:
            _err(e)

    @router.post("/pos/bills/{oid}/settle")
    def pos_settle(oid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None), authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        _oid(oid)
        try:
            with tenant_session(tid, role) as cur:
                return settle(cur, oid, body.get("payments"), actor=uid, role=role)
        except OrderError as e:
            _err(e)

    return router
