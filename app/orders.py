"""Orders: the commerce layer (5 Oct 2026).

Booking = something with a time (appointments). Order = something sold. A
business can have both.

    new -> confirmed -> out_for_delivery -> delivered        (or cancelled)

Money is not a second ledger: an order gets an ordinary invoice (operations.py)
when it is delivered or when the first payment is taken, and payments are
ordinary payments. So Revenue, Daily Closing and P&L see order money exactly as
they see the rest, and "collected" always means money actually received.

A sale is RECOGNISED only when the order is delivered AND fully paid. Then, and
only then, stock is taken out. A delivered order that is part-paid shows the
part collected and the rest as receivable; it is never counted as a full sale.

An AI agent can only create an order in 'new' (create_order with actor_type
'agent'). Confirming, dispatching, delivering, taking payment and cancelling
are human steps. Cancelling is for the owner, and never after money was taken.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity, _next_invoice_number, _paid_so_far, _settle

STATUSES = ("new", "confirmed", "out_for_delivery", "delivered", "cancelled")
NEXT = {"new": {"confirmed", "cancelled"},
        "confirmed": {"out_for_delivery", "delivered", "cancelled"},
        "out_for_delivery": {"delivered", "cancelled"},
        "delivered": set(), "cancelled": set()}
SOURCES = ("whatsapp_ai", "website", "facebook", "instagram", "manual", "dashboard", "api")
LABEL = {"new": "New", "confirmed": "Confirmed", "out_for_delivery": "Out for delivery",
         "delivered": "Delivered", "cancelled": "Cancelled"}
MONEY_ROLES = ("owner", "techdeva_admin")
MAX_ITEMS = 50


class OrderError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _digits(p):
    return re.sub(r"\D", "", str(p or ""))


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _money(v):
    return round(float(v or 0), 2)


def _next_number(cur):
    cur.execute("SELECT number FROM orders WHERE number ~ '^ORD-[0-9]+$' "
                "ORDER BY (substring(number from 5))::int DESC LIMIT 1")
    r = cur.fetchone()
    return "ORD-%04d" % (1 if not r else int(r["number"][4:]) + 1)


def _event(cur, oid, actor, actor_type, event, detail=""):
    cur.execute("""INSERT INTO order_events (tenant_id, order_id, actor, actor_type, event, detail)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s)""",
                (oid, str(actor or "") or None, actor_type, event, str(detail or "")[:500]))


def catalogue(cur):
    """What can be ordered: active products and services with their list price."""
    cur.execute("SELECT id::text, name, price, 'product' AS kind FROM products WHERE active "
                "UNION ALL SELECT id::text, name, price, 'service' AS kind FROM services WHERE active")
    return [dict(r) for r in cur.fetchall()]


def match_item(cat, name):
    """The catalogue entry this name means: exact (ignoring case and spaces) first,
    then a single entry whose name contains it or is contained in it. Never a guess
    between two."""
    n = _norm(name)
    if not n:
        return None
    exact = [c for c in cat if _norm(c["name"]) == n]
    if exact:
        return sorted(exact, key=lambda c: c["kind"] != "product")[0]
    near = [c for c in cat if n in _norm(c["name"]) or _norm(c["name"]) in n]
    return near[0] if len(near) == 1 else None


# ------------------------------------------------------------------ create
def create_order(cur, *, items, customer_name="", customer_phone="", fulfilment="delivery",
                 address="", notes="", delivery_fee=0, discount=0, source="dashboard",
                 source_ref=None, agent_key=None, chat_session_id=None, actor=None,
                 actor_type="human", allow_price_override=False):
    """Create an order in 'new' from catalogue items. Runs on the caller's
    tenant-scoped cursor. Prices come from the price list; only a human with
    allow_price_override may set a different unit price."""
    if source not in SOURCES:
        raise OrderError(400, "Unknown order source.")
    if fulfilment not in ("delivery", "pickup"):
        raise OrderError(400, "Choose delivery or pickup.")
    if not isinstance(items, list) or not items:
        raise OrderError(400, "An order needs at least one item.")
    if len(items) > MAX_ITEMS:
        raise OrderError(400, "Too many items in one order (max %d)." % MAX_ITEMS)
    if source_ref:
        cur.execute("SELECT id::text, number FROM orders WHERE source_ref = %s", (source_ref,))
        dup = cur.fetchone()
        if dup:
            return {"order_id": dup["id"], "number": dup["number"], "duplicate": True}
    cat = catalogue(cur)
    by_id = {c["id"]: c for c in cat}
    lines, unknown = [], []
    for it in items:
        it = it or {}
        c = by_id.get(str(it.get("product_id") or it.get("service_id") or "")) or match_item(cat, it.get("name"))
        if not c:
            unknown.append(str(it.get("name") or "?")[:80])
            continue
        try:
            qty = float(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 0
        if not 0 < qty <= 10000:
            raise OrderError(400, "Quantity for %s must be between 1 and 10000." % c["name"])
        price = float(c["price"]) if c["price"] is not None else None
        if allow_price_override and it.get("unit_price") not in (None, ""):
            try:
                price = float(it["unit_price"])
            except (TypeError, ValueError):
                raise OrderError(400, "The price for %s must be a number." % c["name"])
            if price < 0:
                raise OrderError(400, "A price cannot be negative.")
        lines.append({"c": c, "qty": qty, "price": price})
    if not lines:
        raise OrderError(400, "None of these items are on your list: %s." % ", ".join(unknown))
    if unknown and actor_type != "agent":
        raise OrderError(400, "Not on your list: %s. Add them first, or remove them." % ", ".join(unknown))
    no_price = [l["c"]["name"] for l in lines if l["price"] is None]
    subtotal = round(sum(l["qty"] * (l["price"] or 0) for l in lines), 2)
    fee, disc = max(0.0, float(delivery_fee or 0)), max(0.0, float(discount or 0))
    total = max(0.0, round(subtotal + fee - disc, 2))
    name = re.sub(r"\s+", " ", str(customer_name or "")).strip()[:120]
    phone = str(customer_phone or "").strip()[:40]
    cid = None
    d = _digits(phone)
    if len(d) >= 7:
        cur.execute("SELECT id FROM customers WHERE right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10) "
                    "= right(%s, 10) ORDER BY created_at LIMIT 1", (d,))
        r = cur.fetchone()
        if r:
            cid = r["id"]
        elif name:
            cur.execute("INSERT INTO customers (tenant_id, name, phone, source) VALUES (current_tenant(), %s, %s, %s) "
                        "RETURNING id", (name, phone, "order:" + source))
            cid = cur.fetchone()["id"]
    note = str(notes or "").strip()
    if unknown:
        note = (note + "\n" if note else "") + "The customer also asked for (not on your list, left out): " + ", ".join(unknown)
    if no_price:
        note = (note + "\n" if note else "") + "No price on your list for: " + ", ".join(no_price) + ". Set the price before delivery."
    number = _next_number(cur)
    cur.execute("""INSERT INTO orders (tenant_id, number, customer_id, customer_name, customer_phone, status, fulfilment,
                                       address, subtotal, delivery_fee, discount, total, source, source_ref, agent_key,
                                       chat_session_id, notes, created_by, created_by_type)
                   VALUES (current_tenant(), %s, %s, %s, %s, 'new', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id::text""",
                (number, cid, name, phone, fulfilment, str(address or "").strip()[:500] if fulfilment == "delivery" else "",
                 subtotal, fee, disc, total, source, source_ref, agent_key, chat_session_id, note[:2000],
                 str(actor or "") or None, actor_type))
    oid = cur.fetchone()["id"]
    for l in lines:
        c = l["c"]
        cur.execute("""INSERT INTO order_items (tenant_id, order_id, product_id, service_id, description, quantity, unit_price, line_total)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)""",
                    (oid, c["id"] if c["kind"] == "product" else None, c["id"] if c["kind"] == "service" else None,
                     c["name"], l["qty"], l["price"] or 0, round(l["qty"] * (l["price"] or 0), 2)))
    who = {"whatsapp_ai": "the WhatsApp agent", "dashboard": "the dashboard", "manual": "by hand"}.get(source, source)
    _event(cur, oid, actor, actor_type, "created", "Order placed via %s" % who)
    record(cur, action="order.create", resource="orders", resource_id=oid, actor=str(actor or ""),
           actor_type=actor_type, detail={"number": number, "total": total, "source": source, "items": len(lines)})
    return {"order_id": oid, "number": number, "total": total, "unknown": unknown, "no_price": no_price}


# ------------------------------------------------------------------ from the WhatsApp agent
SUMMARY = re.compile(r"order\s+summary", re.I)
_ITEM = re.compile(r"^\s*(?:[-*•·]\s*)?(\d{1,4})\s*[x×]\s*(.+?)\s*$", re.I)
_ITEM_AFTER = re.compile(r"^\s*(?:[-*•·]\s*)?(.+?)\s*[x×]\s*(\d{1,4})\s*$", re.I)
_PRICE_TAIL = re.compile(r"\s*(?:[(\-–—:=@,]\s*)?(?:rs\.?|pkr|₨)\s*[\d,]+(?:\.\d+)?\)?.*$", re.I)


def parse_summary(text):
    """The ORDER SUMMARY the agent is told to send (categories.knowledge_block).
    Returns {items: [{name, quantity}], name, fulfilment, address, stated_total} or None."""
    if not text or not SUMMARY.search(text):
        return None
    body = text[SUMMARY.search(text).end():]
    items, name, fulfilment, address, total = [], "", "delivery", "", None
    for raw in body.splitlines():
        line = raw.strip().strip("*_").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("*_ ")[:120]
        elif low.startswith(("delivery:", "address:")):
            v = line.split(":", 1)[1].strip().strip("*_ ")
            if re.match(r"^pick\s*-?\s*up\b", v, re.I):
                fulfilment = "pickup"
            else:
                address = v[:500]
        elif re.match(r"^pick\s*-?\s*up\b", low):
            fulfilment = "pickup"
        elif low.startswith("total:"):
            m = re.search(r"([\d,]+(?:\.\d+)?)", line.split(":", 1)[1])
            total = float(m.group(1).replace(",", "")) if m else None
        else:
            m = _ITEM.match(line)
            if m:
                q, nm = int(m.group(1)), m.group(2)
            else:
                m = _ITEM_AFTER.match(_PRICE_TAIL.sub("", line))
                if not m:
                    continue
                nm, q = m.group(1), int(m.group(2))
            nm = _PRICE_TAIL.sub("", nm).strip(" -–:*_")
            if nm and q > 0:
                items.append({"name": nm[:120], "quantity": q})
    if not items:
        return None
    return {"items": items, "name": name, "fulfilment": fulfilment, "address": address, "stated_total": total}


def order_from_chat(tid, *, agent_key, session_id, phone, at, text):
    """A New order from the agent's ORDER SUMMARY, priced from the business's own
    list. Idempotent per message. Returns the order number, or None."""
    p = parse_summary(text)
    if not p:
        return None
    if p["fulfilment"] == "delivery" and not p["address"]:
        p["notes"] = "The agent did not get a delivery address: ask the customer."
    ref = "wa:%s:%s" % (session_id, hashlib.sha256(("%s|%s" % (at.isoformat(), text)).encode()).hexdigest()[:16])
    # a staff-level session of this business only: RLS keeps every lookup
    # (price list, customers, order numbers) inside it
    with tenant_session(tid, "operator") as cur:
        try:
            r = create_order(cur, items=p["items"], customer_name=p["name"], customer_phone=phone or "",
                             fulfilment=p["fulfilment"], address=p["address"], notes=p.get("notes", ""),
                             source="whatsapp_ai", source_ref=ref, agent_key=agent_key, chat_session_id=session_id,
                             actor=agent_key or "whatsapp-agent", actor_type="agent")
        except OrderError as e:
            print("[orders] %s summary not turned into an order: %s" % (tid, e.message), flush=True)
            return None
        if r.get("duplicate"):
            return None
        if p["stated_total"] is not None and abs(p["stated_total"] - r["total"]) > 0.5:
            cur.execute("UPDATE orders SET notes = btrim(notes || %s) WHERE id = %s",
                        ("\nThe agent told the customer a total of Rs {:,.0f}; your price list makes it Rs {:,.0f}. "
                         "Check before confirming.".format(p["stated_total"], r["total"]), r["order_id"]))
            _event(cur, r["order_id"], agent_key, "agent", "check",
                   "Total told to the customer (Rs {:,.0f}) differs from the price list".format(p["stated_total"]))
        return r["number"]


# ------------------------------------------------------------------ money
def _ensure_invoice(cur, o, actor):
    """The order's invoice, made from its lines the first time money or delivery needs it."""
    if o["invoice_id"]:
        return str(o["invoice_id"])
    cur.execute("""INSERT INTO invoices (tenant_id, customer_id, number, discount, notes)
                   VALUES (current_tenant(), %s, %s, %s, %s) RETURNING id""",
                (o["customer_id"], _next_invoice_number(cur), o["discount"], "Order %s" % o["number"]))
    inv = cur.fetchone()["id"]
    cur.execute("SELECT product_id, service_id, description, quantity, unit_price, line_total FROM order_items "
                "WHERE order_id = %s", (o["id"],))
    for it in cur.fetchall():
        cur.execute("""INSERT INTO invoice_items (tenant_id, invoice_id, service_id, product_id, description,
                                                  quantity, unit_price, line_total)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)""",
                    (inv, it["service_id"], it["product_id"], it["description"], it["quantity"],
                     it["unit_price"], it["line_total"]))
    if float(o["delivery_fee"] or 0) > 0:
        cur.execute("""INSERT INTO invoice_items (tenant_id, invoice_id, description, quantity, unit_price, line_total)
                       VALUES (current_tenant(), %s, 'Delivery charge', 1, %s, %s)""",
                    (inv, o["delivery_fee"], o["delivery_fee"]))
    _settle(cur, inv, actor)
    cur.execute("UPDATE orders SET invoice_id = %s, updated_at = now() WHERE id = %s", (inv, o["id"]))
    o["invoice_id"] = inv
    return str(inv)


def _payment_state(cur, o):
    total = _money(o["total"])
    paid = _paid_so_far(cur, o["invoice_id"]) if o["invoice_id"] else 0.0
    st = "unpaid" if paid <= 0 else ("paid" if paid + 0.009 >= total else "partial")
    return {"paid": round(paid, 2), "balance": round(max(0.0, total - paid), 2), "payment_status": st}


def _recognise(cur, o, actor, actor_type="human"):
    """Delivered + fully paid -> a recognised sale, and stock goes out. Once."""
    if o["status"] != "delivered" or o["recognised_at"]:
        return False
    if _payment_state(cur, o)["payment_status"] != "paid":
        return False
    cur.execute("UPDATE orders SET recognised_at = now(), updated_at = now() WHERE id = %s", (o["id"],))
    taken = []
    if not o["stock_deducted_at"]:
        cur.execute("""SELECT i.product_id, i.description, i.quantity,
                              (p.track_stock OR EXISTS (SELECT 1 FROM stock_movements m WHERE m.product_id = p.id)) AS tracked
                         FROM order_items i JOIN products p ON p.id = i.product_id
                        WHERE i.order_id = %s""", (o["id"],))
        for it in cur.fetchall():
            if it["tracked"]:
                cur.execute("""INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta, reason, reference)
                               VALUES (current_tenant(), %s, 'sale', %s, %s, %s)""",
                            (it["product_id"], -float(it["quantity"]), "Order delivered and paid", o["number"]))
                taken.append("%g x %s" % (float(it["quantity"]), it["description"]))
        cur.execute("UPDATE orders SET stock_deducted_at = now() WHERE id = %s", (o["id"],))
    _event(cur, o["id"], actor, actor_type, "recognised",
           "Delivered and fully paid: counted as a sale" + ("; stock taken out: " + ", ".join(taken) if taken else ""))
    record(cur, action="order.recognised", resource="orders", resource_id=str(o["id"]), actor=str(actor or ""),
           actor_type=actor_type, detail={"number": o["number"], "total": _money(o["total"]), "stock": taken})
    return True


def _load(cur, oid):
    cur.execute("SELECT * FROM orders WHERE id = %s::uuid", (oid,))
    o = cur.fetchone()
    if not o:
        raise OrderError(404, "That order is not in this business.")
    return dict(o)


def move(cur, oid, to, *, actor, role, reason=""):
    """Move an order along its lifecycle. Humans only (callers check)."""
    o = _load(cur, oid)
    if to not in STATUSES:
        raise OrderError(400, "Unknown status.")
    if to == o["status"]:
        return view(cur, oid, role)
    if to not in NEXT[o["status"]]:
        raise OrderError(409, "A %s order cannot be marked %s." % (LABEL[o["status"]].lower(), LABEL[to].lower()))
    if to == "out_for_delivery" and o["fulfilment"] == "pickup":
        raise OrderError(409, "This order is for pickup: mark it delivered when the customer collects it.")
    if to == "cancelled":
        if role not in MONEY_ROLES:
            raise OrderError(403, "Only the owner can cancel an order.")
        reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:300]
        if len(reason) < 3:
            raise OrderError(400, "Write why the order is cancelled.")
        if o["invoice_id"] and _paid_so_far(cur, o["invoice_id"]) > 0:
            raise OrderError(409, "Money was already taken for this order. Refund it first, then cancel.")
        if o["invoice_id"]:
            cur.execute("UPDATE invoices SET voided_at = now(), void_reason = %s, status = 'void', updated_at = now() "
                        "WHERE id = %s AND voided_at IS NULL", ("Order %s cancelled: %s" % (o["number"], reason), o["invoice_id"]))
    stamp = {"confirmed": "confirmed_at", "out_for_delivery": "dispatched_at", "delivered": "delivered_at",
             "cancelled": "cancelled_at"}[to]
    cur.execute("UPDATE orders SET status = %s, " + stamp + " = now(), cancel_reason = COALESCE(%s, cancel_reason), "
                "updated_at = now() WHERE id = %s",
                (to, reason if to == "cancelled" else None, o["id"]))
    _event(cur, o["id"], actor, "human", to, ("Reason: " + reason) if to == "cancelled" else "")
    record(cur, action="order." + to, resource="orders", resource_id=str(o["id"]), actor=str(actor or ""),
           detail={"number": o["number"], "from": o["status"], "reason": reason or None})
    o["status"] = to
    if to == "delivered":
        _ensure_invoice(cur, o, actor)
        _recognise(cur, o, actor)
    return view(cur, oid, role)


def pay(cur, oid, amount, *, method="cash", reference=None, actor, role):
    """Money received for an order (cash on delivery, bank, JazzCash...)."""
    o = _load(cur, oid)
    if o["status"] == "cancelled":
        raise OrderError(409, "This order is cancelled.")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise OrderError(400, "The amount must be a number.")
    if amount <= 0:
        raise OrderError(400, "The amount must be more than 0.")
    method = str(method or "cash").strip().lower()
    if method not in ("cash", "card", "bank", "online", "other"):
        raise OrderError(400, "Method must be cash, card, bank, online or other.")
    inv = _ensure_invoice(cur, o, actor)
    owed = round(_money(o["total"]) - _paid_so_far(cur, inv), 2)
    if amount - owed > 0.009:
        raise OrderError(409, "Rs %s is more than the Rs %s still owed on this order." % (amount, owed))
    cur.execute("""INSERT INTO payments (tenant_id, invoice_id, customer_id, amount, method, reference, notes)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s, %s)""",
                (inv, o["customer_id"], amount, method, (str(reference or "").strip()[:120] or None), "Order %s" % o["number"]))
    _settle(cur, inv, actor)
    st = _payment_state(cur, o)
    _event(cur, o["id"], actor, "human", "payment",
           "Rs {:,.0f} received ({}). {}".format(amount, method, "Fully paid." if st["payment_status"] == "paid"
                                                  else "Rs {:,.0f} still owed.".format(st["balance"])))
    record(cur, action="order.payment", resource="orders", resource_id=str(o["id"]), actor=str(actor or ""),
           detail={"number": o["number"], "amount": amount, "method": method, "payment_status": st["payment_status"]})
    _recognise(cur, o, actor)
    return view(cur, oid, role)


# ------------------------------------------------------------------ read
def view(cur, oid, role):
    o = _load(cur, oid)
    cur.execute("SELECT id::text, product_id::text, service_id::text, description, quantity, unit_price, line_total "
                "FROM order_items WHERE order_id = %s ORDER BY description", (o["id"],))
    items = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT at, actor_type, event, detail FROM order_events WHERE order_id = %s ORDER BY at", (o["id"],))
    events = [dict(r) for r in cur.fetchall()]
    out = {k: o[k] for k in ("number", "customer_name", "customer_phone", "status", "fulfilment", "address", "source",
                             "notes", "cancel_reason", "created_by_type", "agent_key", "chat_session_id",
                             "created_at", "confirmed_at", "dispatched_at", "delivered_at", "cancelled_at", "recognised_at")}
    out.update({"id": str(o["id"]), "customer_id": str(o["customer_id"]) if o["customer_id"] else None,
                "invoice_id": str(o["invoice_id"]) if o["invoice_id"] else None,
                "subtotal": _money(o["subtotal"]), "delivery_fee": _money(o["delivery_fee"]),
                "discount": _money(o["discount"]), "total": _money(o["total"]),
                "items": items, "timeline": events, "status_label": LABEL[o["status"]],
                "next": sorted(NEXT[o["status"]] - ({"out_for_delivery"} if o["fulfilment"] == "pickup" else set()),
                               key=STATUSES.index),
                "recognised": bool(o["recognised_at"])})
    out.update(_payment_state(cur, o))
    return out


def list_orders(cur, status=None, q=None, limit=100):
    where, args = ["TRUE"], []
    if status in STATUSES:
        where.append("o.status = %s"); args.append(status)
    elif status == "waiting":
        where.append("o.status IN ('confirmed','out_for_delivery')")
    elif status == "unpaid":
        where.append("o.status = 'delivered' AND o.recognised_at IS NULL")
    if q:
        where.append("(o.customer_name ILIKE %s OR o.customer_phone ILIKE %s OR o.number ILIKE %s)")
        args += ["%" + q[:60] + "%"] * 3
    cur.execute("""SELECT o.id::text, o.number, o.customer_name, o.customer_phone, o.status, o.fulfilment, o.source,
                          o.total, o.created_at, o.recognised_at, o.address,
                          COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = o.invoice_id), 0) AS paid,
                          (SELECT string_agg(trim(to_char(i.quantity, 'FM999990.##')) || ' x ' || i.description, ', ')
                             FROM order_items i WHERE i.order_id = o.id) AS items
                     FROM orders o WHERE """ + " AND ".join(where) + """
                    ORDER BY o.created_at DESC LIMIT %s""", args + [max(1, min(int(limit or 100), 300))])
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        r["total"], r["paid"] = _money(r["total"]), _money(r["paid"])
        r["balance"] = round(max(0.0, r["total"] - r["paid"]), 2)
        r["payment_status"] = "unpaid" if r["paid"] <= 0 else ("paid" if r["balance"] <= 0.009 else "partial")
        r["status_label"] = LABEL[r["status"]]
        rows.append(r)
    return rows


def summary(cur, role):
    """The numbers the owner sees on the home page and the Orders page."""
    cur.execute("""SELECT
          count(*) FILTER (WHERE status = 'new') AS new,
          count(*) FILTER (WHERE status = 'confirmed') AS confirmed,
          count(*) FILTER (WHERE status = 'out_for_delivery') AS out_for_delivery,
          count(*) FILTER (WHERE status = 'delivered' AND delivered_at >= date_trunc('month', now())) AS delivered_month,
          count(*) FILTER (WHERE status = 'cancelled' AND cancelled_at >= date_trunc('month', now())) AS cancelled_month,
          count(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today,
          count(*) FILTER (WHERE created_at >= date_trunc('day', now()) AND status = 'new') AS today_new,
          count(*) FILTER (WHERE created_at >= date_trunc('day', now()) AND status = 'confirmed') AS today_confirmed,
          count(*) FILTER (WHERE created_at >= date_trunc('day', now()) AND status = 'cancelled') AS today_cancelled,
          count(*) FILTER (WHERE created_at >= date_trunc('month', now())) AS month
        FROM orders""")
    c = {k: int(v) for k, v in cur.fetchone().items()}
    c["waiting_delivery"] = c["confirmed"] + c["out_for_delivery"]
    cur.execute("""SELECT count(*) AS n FROM (
                     SELECT p.id FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id
                      WHERE p.active AND p.low_stock_threshold IS NOT NULL
                      GROUP BY p.id, p.low_stock_threshold
                     HAVING COALESCE(sum(m.quantity_delta), 0) <= p.low_stock_threshold
                        AND (bool_or(p.track_stock) OR count(m.id) > 0)) x""")
    c["low_stock"] = int(cur.fetchone()["n"])
    cur.execute("""SELECT source, count(*) AS orders, count(*) FILTER (WHERE recognised_at IS NOT NULL) AS sold
                     FROM orders WHERE created_at >= date_trunc('month', now()) GROUP BY source ORDER BY 2 DESC""")
    src = [dict(r) for r in cur.fetchall()]
    out = {"counts": c, "sources": [{"source": s["source"], "orders": int(s["orders"]), "sold": int(s["sold"]),
                                     "share": round(100.0 * int(s["orders"]) / c["month"]) if c["month"] else 0} for s in src]}
    if role in MONEY_ROLES:
        cur.execute("""SELECT
              COALESCE(sum(total) FILTER (WHERE recognised_at >= date_trunc('day', now())), 0) AS sales_today,
              COALESCE(sum(total) FILTER (WHERE recognised_at >= date_trunc('month', now())), 0) AS sales_month
            FROM orders""")
        m = {k: _money(v) for k, v in cur.fetchone().items()}
        cur.execute("""SELECT COALESCE(sum(o.total - COALESCE(p.paid, 0)), 0) AS outstanding,
                              count(*) AS n
                         FROM orders o LEFT JOIN (SELECT invoice_id, sum(amount) AS paid FROM payments GROUP BY invoice_id) p
                           ON p.invoice_id = o.invoice_id
                        WHERE o.status = 'delivered' AND o.recognised_at IS NULL""")
        r = cur.fetchone()
        m["outstanding"], m["outstanding_orders"] = _money(r["outstanding"]), int(r["n"])
        cur.execute("""SELECT COALESCE(sum(p.amount), 0) AS a FROM payments p JOIN orders o ON o.invoice_id = p.invoice_id
                        WHERE p.paid_at >= date_trunc('day', now())""")
        m["collected_today"] = _money(cur.fetchone()["a"])
        out["money"] = m
    return out


# ------------------------------------------------------------------ routes
def _err(e):
    raise HTTPException(e.status, e.message)


def make_orders_router():
    router = APIRouter()

    def who(x_tenant_id, x_role, authorization, td_session):
        return _identity(x_tenant_id, x_role, authorization, td_session)

    @router.get("/orders")
    def orders(status: str | None = None, q: str | None = None, limit: int = 100,
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {"orders": list_orders(cur, status, (q or "").strip() or None, limit), "summary": summary(cur, role)}

    @router.get("/orders/summary")
    def orders_summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return summary(cur, role)

    @router.get("/orders/catalogue")
    def orders_catalogue(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                         authorization: str | None = Header(None),
                         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {"items": [{**c, "price": _money(c["price"]) if c["price"] is not None else None} for c in catalogue(cur)]}

    @router.post("/orders")
    def new_order(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """An order taken by a person (phone, counter, the dashboard)."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        src = body.get("source") if body.get("source") in ("manual", "dashboard", "website", "facebook", "instagram") else "dashboard"
        try:
            with tenant_session(tid, role) as cur:
                r = create_order(cur, items=body.get("items"), customer_name=body.get("customer_name"),
                                 customer_phone=body.get("customer_phone"), fulfilment=body.get("fulfilment") or "delivery",
                                 address=body.get("address"), notes=body.get("notes"),
                                 delivery_fee=body.get("delivery_fee") or 0, discount=body.get("discount") or 0,
                                 source=src, actor=uid, actor_type="human",
                                 allow_price_override=role in MONEY_ROLES)
                return view(cur, r["order_id"], role)
        except OrderError as e:
            _err(e)

    @router.get("/orders/{oid}")
    def one(oid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not re.match(r"^[0-9a-f-]{36}$", oid):
            raise HTTPException(404, "That order is not in this business.")
        try:
            with tenant_session(tid, role) as cur:
                return view(cur, oid, role)
        except OrderError as e:
            _err(e)

    @router.post("/orders/{oid}/status")
    def set_status(oid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None), authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not re.match(r"^[0-9a-f-]{36}$", oid):
            raise HTTPException(404, "That order is not in this business.")
        try:
            with tenant_session(tid, role) as cur:
                return move(cur, oid, str(body.get("status") or ""), actor=uid, role=role, reason=body.get("reason") or "")
        except OrderError as e:
            _err(e)

    @router.post("/orders/{oid}/pay")
    def take_payment(oid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not re.match(r"^[0-9a-f-]{36}$", oid):
            raise HTTPException(404, "That order is not in this business.")
        try:
            with tenant_session(tid, role) as cur:
                return pay(cur, oid, body.get("amount"), method=body.get("method") or "cash",
                           reference=body.get("reference"), actor=uid, role=role)
        except OrderError as e:
            _err(e)

    return router
