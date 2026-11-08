"""Suppliers and purchases (Phase 6, 9 Nov 2026; db/95).

    a purchase bill   from one supplier: lines of product, quantity and unit cost.
                      Each line puts the stock in (stock_movements kind 'purchase'
                      with that unit cost), and the bill's total goes on what the
                      shop owes that supplier. Optionally paid in part at once.
    a payment         to a supplier, against what the shop owes.
    a mistake         a bill is voided (the stock taken back out and the amount
                      taken off what is owed, with a reason); a ledger entry is
                      reversed. Nothing is edited and both stay in the history.

Rules here, the same as the udhaar ledger (app/ledger.py):
  * owner only (and roles with Finance, through RoleGuard);
  * a resent bill or payment (same client_ref) is the first one, never a second;
  * a product that is not on the list is refused, never made up: add it first;
  * a payment is never more than is owed (whether an advance should be allowed is
    the owner's decision, not made here);
  * P&L's "net" is not changed: purchases show beside it (app/operations.py pnl).
"""
from __future__ import annotations

import datetime as _dt
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .orders import match_item

METHODS = ("cash", "bank", "jazzcash", "easypaisa", "card", "cheque", "other")
SOURCES = ("manual", "jarvis", "import")
REF = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
MONEY_ROLES = ("owner", "techdeva_admin")
MAX_LINES = 100


class PurchaseError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _m(v):
    return round(float(v or 0), 2)


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _clean(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _ref(body):
    ref = body.get("client_ref")
    if ref is not None and not REF.match(str(ref)):
        raise PurchaseError(400, "The reference is not valid. Reload the page.")
    return ref or None


def _date(v):
    if not v:
        return None
    try:
        d = _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise PurchaseError(400, "The date must look like 2026-11-09.")
    if d > _dt.date.today() + _dt.timedelta(days=1):
        raise PurchaseError(400, "That date is in the future.")
    return d


# ------------------------------------------------------------------ suppliers
def _srow(r):
    return {"supplier_id": str(r["supplier_id"]), "name": r["name"], "phone": r["phone"] or None, "active": r["active"],
            "owed": _m(r["owed"]), "bills": int(r["bills"]), "bought": _m(r["bought"]), "entries": int(r["entries"]),
            "last_activity": r["last_activity"]}


def supplier(cur, sid):
    cur.execute("SELECT * FROM supplier_balances WHERE supplier_id::text = %s", (str(sid),))
    r = cur.fetchone()
    if not r:
        raise PurchaseError(404, "That supplier is not in this business.")
    return _srow(r)


def find_suppliers(cur, name, limit=8):
    n = _clean(name, 120).lower()
    if not n:
        return []
    cur.execute("SELECT * FROM supplier_balances WHERE lower(btrim(name)) = %s LIMIT %s", (n, limit))
    rows = cur.fetchall()
    if not rows:
        cur.execute("SELECT * FROM supplier_balances WHERE lower(name) LIKE %s ORDER BY owed DESC LIMIT %s",
                    ("%" + n.replace("%", "").replace("_", "") + "%", limit))
        rows = cur.fetchall()
    return [_srow(r) for r in rows]


def add_supplier(cur, body, *, actor):
    name = _clean(body.get("name"), 120)
    if not name:
        raise PurchaseError(400, "A supplier needs a name.")
    cur.execute("SELECT id FROM suppliers WHERE lower(btrim(name)) = lower(%s)", (name,))
    r = cur.fetchone()
    if r:
        return supplier(cur, r["id"])
    cur.execute("INSERT INTO suppliers (tenant_id, name, phone, notes) VALUES (current_tenant(), %s, %s, %s) RETURNING id",
                (name, _clean(body.get("phone"), 40) or None, _clean(body.get("notes"), 500) or None))
    sid = cur.fetchone()["id"]
    record(cur, action="supplier.create", resource="suppliers", resource_id=str(sid), actor=actor, detail={"name": name})
    return supplier(cur, sid)


def _which_supplier(cur, body, actor):
    if body.get("supplier_id"):
        if not UUID.match(str(body["supplier_id"])):
            raise PurchaseError(404, "That supplier is not in this business.")
        return supplier(cur, body["supplier_id"])
    name = _clean(body.get("supplier_name"), 120)
    if not name:
        raise PurchaseError(400, "Which supplier?")
    return add_supplier(cur, {"name": name, "phone": body.get("supplier_phone")}, actor=actor)


def overview(cur):
    cur.execute("SELECT * FROM supplier_balances ORDER BY owed DESC, name")
    rows = [_srow(r) for r in cur.fetchall()]
    cur.execute("""SELECT id::text, number, supplier_ref, bought_on, total, voided_at,
                          (SELECT name FROM suppliers s WHERE s.id = b.supplier_id) AS supplier
                     FROM purchase_bills b ORDER BY bought_on DESC, created_at DESC LIMIT 30""")
    recent = [dict(r, total=_m(r["total"])) for r in cur.fetchall()]
    return {"suppliers": rows, "recent_bills": recent,
            "totals": {"owed": round(sum(r["owed"] for r in rows if r["owed"] > 0), 2),
                       "suppliers_owed": sum(1 for r in rows if r["owed"] > 0.009)}}


def statement(cur, sid):
    s = supplier(cur, sid)
    cur.execute("""SELECT e.id::text, e.kind, e.amount, e.method, e.note, e.occurred_on, e.source, e.reverses::text,
                          e.created_at, b.id::text AS bill_id, b.number,
                          EXISTS (SELECT 1 FROM supplier_ledger_entries r WHERE r.reverses = e.id) AS reversed
                     FROM supplier_ledger_entries e LEFT JOIN purchase_bills b ON b.id = e.bill_id
                    WHERE e.supplier_id = %s ORDER BY e.occurred_on, e.created_at""", (str(sid),))
    lines, run = [], 0.0
    for r in cur.fetchall():
        run = round(run + float(r["amount"]), 2)
        lines.append(dict(r, amount=_m(r["amount"]), running=run, reversed=bool(r["reversed"])))
    return {"supplier": s, "lines": lines}


# ------------------------------------------------------------------ bills
def _next_number(cur):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('purchase_bills:' || current_tenant()::text))")
    cur.execute("SELECT count(*) AS n FROM purchase_bills")
    return "PB-%04d" % (int(cur.fetchone()["n"]) + 1)


def _lines(cur, items):
    if not isinstance(items, list) or not items:
        raise PurchaseError(400, "A purchase needs at least one item.")
    if len(items) > MAX_LINES:
        raise PurchaseError(400, "Too many items on one bill (max %d)." % MAX_LINES)
    cur.execute("SELECT id::text, name, price, 'product' AS kind, aliases, unit FROM products WHERE active")
    cat = [dict(r) for r in cur.fetchall()]
    by_id = {c["id"]: c for c in cat}
    out, unknown = [], []
    for it in items:
        it = it or {}
        c = by_id.get(str(it.get("product_id") or "")) or match_item(cat, it.get("name"))
        if not c:
            unknown.append(_clean(it.get("name") or "?", 80))
            continue
        try:
            qty = round(float(str(it.get("quantity")).replace(",", "")), 3)
        except (TypeError, ValueError):
            raise PurchaseError(400, "How much %s?" % c["name"])
        if not 0 < qty <= 1000000:
            raise PurchaseError(400, "The quantity of %s must be more than zero." % c["name"])
        cost, total = it.get("unit_cost"), it.get("line_total")
        try:
            if cost not in (None, ""):
                cost = round(float(str(cost).replace(",", "")), 2)
            elif total not in (None, ""):
                cost = round(float(str(total).replace(",", "")) / qty, 2)
            else:
                raise PurchaseError(400, "What did %s cost? Give the price per unit or the line total." % c["name"])
        except (TypeError, ValueError):
            raise PurchaseError(400, "The cost of %s must be a number." % c["name"])
        if cost < 0:
            raise PurchaseError(400, "A cost cannot be negative.")
        line_total = round(qty * cost, 2) if total in (None, "") else round(float(str(total).replace(",", "")), 2)
        out.append({"product": c, "quantity": qty, "unit_cost": cost, "line_total": line_total})
    if unknown:
        raise PurchaseError(400, "Not on your products list: %s. Add %s under Products first." % (
            ", ".join(unknown), "it" if len(unknown) == 1 else "them"))
    return out


def add_bill(cur, body, *, actor, source="manual"):
    """One purchase bill: stock in, owed to the supplier, and an optional payment now."""
    if source not in SOURCES:
        raise PurchaseError(400, "Unknown source.")
    ref = _ref(body)
    if ref:
        cur.execute("SELECT id::text FROM purchase_bills WHERE client_ref = %s", (ref,))
        dup = cur.fetchone()
        if dup:
            return dict(bill(cur, dup["id"]), duplicate=True)
    on = _date(body.get("bought_on"))
    lines = _lines(cur, body.get("items"))
    s = _which_supplier(cur, body, actor)
    paid = body.get("paid_now") or {}
    pay_amt = 0.0
    if paid.get("amount") not in (None, "", 0):
        try:
            pay_amt = round(float(str(paid["amount"]).replace(",", "")), 2)
        except (TypeError, ValueError):
            raise PurchaseError(400, "The amount paid must be a number.")
    total = round(sum(l["line_total"] for l in lines), 2)
    if pay_amt < 0 or pay_amt - total > 0.009:
        raise PurchaseError(400, "Paid now must be between zero and the bill's total (%s)." % _rs(total))
    method = str(paid.get("method") or "cash").lower()
    if pay_amt and method not in METHODS:
        raise PurchaseError(400, "Paid how? cash, bank, JazzCash, EasyPaisa, card, cheque or other.")
    number = _next_number(cur)
    cur.execute("""INSERT INTO purchase_bills (tenant_id, supplier_id, number, supplier_ref, bought_on, total, note,
                                               source, client_ref, created_by)
                   VALUES (current_tenant(), %s, %s, %s, COALESCE(%s, (now() AT TIME ZONE 'Asia/Karachi')::date),
                           %s, %s, %s, %s, %s) RETURNING id::text, bought_on""",
                (s["supplier_id"], number, _clean(body.get("supplier_ref"), 60) or None, on, total,
                 _clean(body.get("note"), 300) or None, source, ref, actor))
    b = cur.fetchone()
    for l in lines:
        cur.execute("""INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta, unit_cost, reason,
                                                    reference, occurred_at)
                       VALUES (current_tenant(), %s, 'purchase', %s, %s, %s, %s, %s::date) RETURNING id""",
                    (l["product"]["id"], l["quantity"], l["unit_cost"], "Bought from " + s["name"], number, b["bought_on"]))
        mid = cur.fetchone()["id"]
        cur.execute("""INSERT INTO purchase_bill_items (tenant_id, bill_id, product_id, quantity, unit_cost, line_total,
                                                        stock_movement_id)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s)""",
                    (b["id"], l["product"]["id"], l["quantity"], l["unit_cost"], l["line_total"], mid))
        # the product is now one the shop keeps stock of
        cur.execute("UPDATE products SET track_stock = true WHERE id = %s AND NOT track_stock", (l["product"]["id"],))
    if total > 0:
        cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, bill_id, occurred_on,
                                                            source, created_by)
                       VALUES (current_tenant(), %s, 'bill', %s, %s, %s, %s, %s)""",
                    (s["supplier_id"], total, b["id"], b["bought_on"], source, actor))
    if pay_amt:
        cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, method, note,
                                                            occurred_on, source, created_by)
                       VALUES (current_tenant(), %s, 'payment', %s, %s, %s, %s, %s, %s)""",
                    (s["supplier_id"], -pay_amt, method, "Paid with " + number, b["bought_on"], source, actor))
    record(cur, action="purchase.bill", resource="purchase_bills", resource_id=b["id"], actor=actor,
           detail={"number": number, "supplier": s["name"], "total": total, "paid_now": pay_amt, "lines": len(lines),
                   "source": source})
    return dict(bill(cur, b["id"]), duplicate=False)


def bill(cur, bid):
    cur.execute("""SELECT b.id::text, b.number, b.supplier_ref, b.bought_on, b.total, b.note, b.source, b.voided_at,
                          b.void_reason, b.supplier_id::text, s.name AS supplier
                     FROM purchase_bills b JOIN suppliers s ON s.id = b.supplier_id WHERE b.id::text = %s""", (str(bid),))
    b = cur.fetchone()
    if not b:
        raise PurchaseError(404, "That purchase bill is not in this business.")
    cur.execute("""SELECT i.product_id::text, p.name, p.unit, i.quantity, i.unit_cost, i.line_total
                     FROM purchase_bill_items i JOIN products p ON p.id = i.product_id WHERE i.bill_id = %s""", (b["id"],))
    items = [dict(r, quantity=float(r["quantity"]), unit_cost=_m(r["unit_cost"]), line_total=_m(r["line_total"]))
             for r in cur.fetchall()]
    return {"bill": dict(b, total=_m(b["total"]), items=items), "supplier": supplier(cur, b["supplier_id"])}


def void_bill(cur, bid, reason, *, actor):
    """Take the stock back out and the amount off what is owed. The bill stays, marked void."""
    reason = _clean(reason, 250)
    if not reason:
        raise PurchaseError(400, "Say why the bill is being voided.")
    cur.execute("SELECT id, number, supplier_id, total, voided_at FROM purchase_bills WHERE id::text = %s FOR UPDATE", (str(bid),))
    b = cur.fetchone()
    if not b:
        raise PurchaseError(404, "That purchase bill is not in this business.")
    if b["voided_at"]:
        raise PurchaseError(409, "That bill is already void.")
    cur.execute("""SELECT i.product_id, i.quantity, i.unit_cost, p.name FROM purchase_bill_items i
                     JOIN products p ON p.id = i.product_id WHERE i.bill_id = %s""", (b["id"],))
    for it in cur.fetchall():
        cur.execute("""INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta, unit_cost, reason, reference)
                       VALUES (current_tenant(), %s, 'adjustment', %s, %s, %s, %s)""",
                    (it["product_id"], -float(it["quantity"]), it["unit_cost"], "Purchase bill voided: " + reason, b["number"]))
    cur.execute("SELECT id FROM supplier_ledger_entries WHERE bill_id = %s AND kind = 'bill'", (b["id"],))
    e = cur.fetchone()
    if e:
        cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, bill_id, note, reverses, created_by)
                       VALUES (current_tenant(), %s, 'adjustment', %s, %s, %s, %s, %s)""",
                    (b["supplier_id"], -float(b["total"]), b["id"], "Bill %s voided: %s" % (b["number"], reason), e["id"], actor))
    cur.execute("UPDATE purchase_bills SET voided_at = now(), void_reason = %s, voided_by = %s WHERE id = %s",
                (reason, actor, b["id"]))
    record(cur, action="purchase.void", resource="purchase_bills", resource_id=str(b["id"]), actor=actor,
           detail={"number": b["number"], "total": _m(b["total"]), "reason": reason})
    return bill(cur, b["id"])


# ------------------------------------------------------------------ payments
def pay(cur, body, *, actor, source="manual"):
    ref = _ref(body)
    if ref:
        cur.execute("SELECT id::text, supplier_id FROM supplier_ledger_entries WHERE client_ref = %s", (ref,))
        dup = cur.fetchone()
        if dup:
            return {"supplier": supplier(cur, dup["supplier_id"]), "duplicate": True}
    try:
        amt = round(float(str(body.get("amount")).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise PurchaseError(400, "The amount must be a number.")
    if amt <= 0:
        raise PurchaseError(400, "The amount must be more than zero.")
    method = str(body.get("method") or "cash").lower()
    if method not in METHODS:
        raise PurchaseError(400, "Paid how? cash, bank, JazzCash, EasyPaisa, card, cheque or other.")
    s = _which_supplier(cur, body, actor)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('supplier:' || %s))", (s["supplier_id"],))
    s = supplier(cur, s["supplier_id"])
    if amt - max(s["owed"], 0) > 0.009:
        raise PurchaseError(409, "You owe %s %s, so a payment of %s is more than owed." % (s["name"], _rs(max(s["owed"], 0)), _rs(amt)))
    cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, method, note, occurred_on,
                                                        source, client_ref, created_by)
                   VALUES (current_tenant(), %s, 'payment', %s, %s, %s,
                           COALESCE(%s, (now() AT TIME ZONE 'Asia/Karachi')::date), %s, %s, %s) RETURNING id::text""",
                (s["supplier_id"], -amt, method, _clean(body.get("note"), 300) or None, _date(body.get("occurred_on")),
                 source, ref, actor))
    eid = cur.fetchone()["id"]
    record(cur, action="supplier.payment", resource="supplier_ledger_entries", resource_id=eid, actor=actor,
           detail={"supplier": s["name"], "amount": amt, "method": method, "source": source})
    return {"supplier": supplier(cur, s["supplier_id"]), "duplicate": False}


def opening(cur, body, *, actor):
    try:
        amt = round(float(str(body.get("amount")).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise PurchaseError(400, "The amount must be a number.")
    if amt <= 0:
        raise PurchaseError(400, "The amount must be more than zero.")
    s = _which_supplier(cur, body, actor)
    cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, note, occurred_on, created_by)
                   VALUES (current_tenant(), %s, 'opening', %s, %s, COALESCE(%s, (now() AT TIME ZONE 'Asia/Karachi')::date), %s)
                   RETURNING id::text""",
                (s["supplier_id"], amt, _clean(body.get("note"), 300) or None, _date(body.get("occurred_on")), actor))
    eid = cur.fetchone()["id"]
    record(cur, action="supplier.opening", resource="supplier_ledger_entries", resource_id=eid, actor=actor,
           detail={"supplier": s["name"], "amount": amt})
    return {"supplier": supplier(cur, s["supplier_id"])}


def reverse(cur, eid, reason, *, actor):
    reason = _clean(reason, 250)
    if not reason:
        raise PurchaseError(400, "Say why it is being reversed.")
    cur.execute("SELECT id, supplier_id, kind, amount, reverses, bill_id FROM supplier_ledger_entries WHERE id::text = %s", (str(eid),))
    e = cur.fetchone()
    if not e:
        raise PurchaseError(404, "That entry is not in this business.")
    if e["kind"] == "bill" or e["bill_id"]:
        raise PurchaseError(409, "That line belongs to a purchase bill. Void the bill instead, so the stock is taken back out too.")
    if e["reverses"]:
        raise PurchaseError(409, "That entry is itself a correction. Add a new entry instead.")
    cur.execute("SELECT 1 FROM supplier_ledger_entries WHERE reverses = %s", (e["id"],))
    if cur.fetchone():
        raise PurchaseError(409, "That entry was already reversed.")
    cur.execute("""INSERT INTO supplier_ledger_entries (tenant_id, supplier_id, kind, amount, note, reverses, created_by)
                   VALUES (current_tenant(), %s, 'adjustment', %s, %s, %s, %s)""",
                (e["supplier_id"], -float(e["amount"]), "Reversed: " + reason, e["id"], actor))
    record(cur, action="supplier.reverse", resource="supplier_ledger_entries", resource_id=str(e["id"]), actor=actor,
           detail={"amount": -float(e["amount"]), "reason": reason})
    return {"supplier": supplier(cur, e["supplier_id"])}


def summary(cur, start, end):
    """For P&L: stock bought (bills dated in the period, not void) and paid to suppliers."""
    cur.execute("SELECT COALESCE(sum(total), 0) AS a, count(*) AS n FROM purchase_bills "
                "WHERE voided_at IS NULL AND bought_on BETWEEN %s AND %s", (start, end))
    r = cur.fetchone()
    cur.execute("SELECT COALESCE(-sum(amount), 0) AS a FROM supplier_ledger_entries "
                "WHERE kind = 'payment' AND occurred_on BETWEEN %s AND %s", (start, end))
    paid = _m(cur.fetchone()["a"])
    cur.execute("SELECT COALESCE(sum(owed), 0) AS a FROM supplier_balances WHERE owed > 0")
    return {"stock_bought": _m(r["a"]), "purchase_bills": int(r["n"]), "paid_to_suppliers": paid,
            "owed_to_suppliers": _m(cur.fetchone()["a"])}


# ------------------------------------------------------------------ routes
def make_purchases_router(identity):
    router = APIRouter(prefix="/purchases", tags=["purchases"])

    def run(fn, h):
        tid, role, uid = identity(*h)
        if role not in MONEY_ROLES:
            raise HTTPException(403, "Purchases and suppliers are for the owner. Ask the owner, or a role with Finance.")
        try:
            with tenant_session(tid, role) as cur:
                return fn(cur, str(uid or ""))
        except PurchaseError as e:
            raise HTTPException(e.status, e.message)

    def hdr(x_tenant_id, x_role, authorization, td_session):
        return (x_tenant_id, x_role, authorization, td_session)

    @router.get("")
    def purchases_overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                           authorization: str | None = Header(None),
                           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Suppliers with what the shop owes each, and the recent purchase bills."""
        return run(lambda cur, uid: overview(cur), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.get("/suppliers/find")
    def suppliers_find(name: str = "", x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: {"suppliers": find_suppliers(cur, name)}, hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/suppliers")
    def suppliers_add(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: {"supplier": add_supplier(cur, body, actor=uid)}, hdr(x_tenant_id, x_role, authorization, td_session))

    @router.get("/suppliers/{sid}")
    def suppliers_statement(sid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                            authorization: str | None = Header(None),
                            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        if not UUID.match(sid):
            raise HTTPException(404, "That supplier is not in this business.")
        return run(lambda cur, uid: statement(cur, sid), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/bills")
    def bills_add(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Record a purchase bill: the stock comes in and the total is owed to the supplier."""
        src = "jarvis" if body.get("via") == "jarvis" else "manual"
        return run(lambda cur, uid: add_bill(cur, body, actor=uid, source=src), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.get("/bills/{bid}")
    def bills_one(bid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        if not UUID.match(bid):
            raise HTTPException(404, "That purchase bill is not in this business.")
        return run(lambda cur, uid: bill(cur, bid), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/bills/{bid}/void")
    def bills_void(bid: str, body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None), authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        if not UUID.match(bid):
            raise HTTPException(404, "That purchase bill is not in this business.")
        return run(lambda cur, uid: void_bill(cur, bid, body.get("reason"), actor=uid), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/payments")
    def payments_add(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Pay a supplier, against what the shop owes them."""
        src = "jarvis" if body.get("via") == "jarvis" else "manual"
        return run(lambda cur, uid: pay(cur, body, actor=uid, source=src), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/openings")
    def openings_add(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What the shop already owed a supplier before TechDeva (from the old register)."""
        return run(lambda cur, uid: opening(cur, body, actor=uid), hdr(x_tenant_id, x_role, authorization, td_session))

    @router.post("/entries/{eid}/reverse")
    def entries_reverse(eid: str, body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                        x_role: str | None = Header(None), authorization: str | None = Header(None),
                        td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        if not UUID.match(eid):
            raise HTTPException(404, "That entry is not in this business.")
        return run(lambda cur, uid: reverse(cur, eid, body.get("reason"), actor=uid), hdr(x_tenant_id, x_role, authorization, td_session))

    return router
