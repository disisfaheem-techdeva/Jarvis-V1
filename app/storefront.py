"""Storefront orders: a tenant's own website pushes its orders into the platform.

Generic, not tenant-specific. Instant Grill (instantgrill.shop) is the first
storefront; any client with a website can connect the same way.

    website ──signed webhook──► POST /storefront/webhook/{tenant_id}
                                   │  verify HMAC (secret in `integrations`,
                                   │  provider='storefront', encrypted at rest)
                                   ▼
                     storefront_orders / storefront_inbox   (RLS, tenant-scoped)
                                   │
                                   ├─ the platform's own Orders (orders, source 'website'):
                                   │    same order number, status follows the site, and on
                                   │    delivery an invoice + cash payment (COD), so Revenue,
                                   │    Daily Closing and P&L count it like any other sale
                                   ├─ enquiries and contact messages become Leads
                                   ├─ revenue_events row when an order is delivered (attribution)
                                   └─ platform events  storefront.order.created,
                                      storefront.order.status_changed,
                                      storefront.enquiry.created, storefront.contact.created
                                      -> orchestrator rules (owner alerts today; Hermes later)

Signing (the site does the same, see instantgrill-web/src/lib/platform.ts):
    X-Storefront-Signature: t=<unix seconds>,v1=<hex hmac_sha256(secret, "<t>.<raw body>")>
Requests older or newer than 5 minutes are refused, so a captured request cannot
be replayed later. Orders are upserted by the site's order number and an older
update can never overwrite a newer one, so retries and out-of-order delivery are safe.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query, Request
from psycopg.types.json import Json
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import crypto
from .auth import SESSION_COOKIE, AuthError, resolve_scope
from .db import tenant_session

log = logging.getLogger("storefront")

PROVIDER = "storefront"
SIGNATURE_HEADER = "X-Storefront-Signature"
MAX_SKEW_SECONDS = 300
MAX_BODY_BYTES = 256 * 1024
TZ = "Asia/Karachi"  # day boundaries for "today" and delivery dates
STATUSES = ("new", "confirmed", "preparing", "out_for_delivery", "delivered", "cancelled")


# --------------------------------------------------------------------- signing
def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    """Header value for a body. Used by tests; the site implements the same."""
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(secret: str, header: str | None, body: bytes, now: float | None = None) -> bool:
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > MAX_SKEW_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


# -------------------------------------------------------------------- payloads
class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SFLine(_Loose):
    slug: str = Field(max_length=80)
    name: str = Field(max_length=120)
    unitPrice: int = Field(ge=0)
    qty: int = Field(ge=1, le=1000)
    lineTotal: int = Field(ge=0)
    chutney: Optional[str] = Field(None, max_length=40)
    salad: Optional[str] = Field(None, max_length=40)


class SFCustomer(_Loose):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=7, max_length=20)
    area: Optional[str] = Field(None, max_length=80)
    address: Optional[str] = Field(None, max_length=400)
    landmark: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=400)


class SFSlot(_Loose):
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    slot: Optional[str] = Field(None, max_length=10)


class SFHistory(_Loose):
    status: str
    at: datetime


class SFOrder(_Loose):
    id: str = Field(min_length=1, max_length=40)
    createdAt: datetime
    status: Literal["new", "confirmed", "preparing", "out_for_delivery", "delivered", "cancelled"]
    customer: SFCustomer
    lines: list[SFLine] = Field(max_length=100)
    subtotal: int = Field(ge=0)
    deliveryFee: Optional[int] = Field(None, ge=0)
    total: int = Field(ge=0)
    slot: Optional[SFSlot] = None
    rider: Optional[str] = Field(None, max_length=80)
    history: list[SFHistory] = Field(default_factory=list, max_length=50)
    source: Optional[dict[str, Any]] = None

    def updated_at(self) -> datetime:
        return max([h.at for h in self.history] + [self.createdAt])


class SFInboxItem(_Loose):
    id: str = Field(min_length=1, max_length=64)
    createdAt: datetime
    name: str = Field(min_length=1, max_length=120)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=160)


class Envelope(_Loose):
    id: str = Field(min_length=1, max_length=64)
    type: Literal["order.created", "order.updated", "enquiry.created", "contact.created"]
    occurred_at: datetime
    data: dict[str, Any]


# ------------------------------------------------------------------- identity
def _scope(x_tenant_id, x_role, authorization, session, tenant_id_q=None) -> tuple[str, str]:
    """Owner/operator see their own tenant; a TechDeva admin picks one with ?tenant_id=."""
    try:
        tid, role, _uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role == "techdeva_admin":
        tid = tenant_id_q
    if not tid:
        raise HTTPException(400, "tenant_id required")
    try:
        uuid.UUID(str(tid))
    except ValueError:
        raise HTTPException(400, "bad tenant_id")
    return str(tid), role


def _secret_for(tenant_id: str) -> str | None:
    with tenant_session(tenant_id, "operator") as cur:
        cur.execute(
            "SELECT credentials, status FROM integrations WHERE tenant_id = current_tenant() AND provider = %s",
            (PROVIDER,),
        )
        row = cur.fetchone()
    if not row or row["status"] != "connected":
        return None
    try:
        return crypto.decrypt_json(row["credentials"], tenant_id, PROVIDER).get("secret")
    except Exception:  # undecryptable = not connected; never 500 to the caller
        log.exception("storefront secret could not be decrypted for %s", tenant_id)
        return None


# ------------------------------------------------------------------- ingestion
_UPSERT = """
INSERT INTO storefront_orders
  (tenant_id, external_id, status, customer_name, customer_phone, area, address, landmark, notes,
   slot_date, slot, subtotal, delivery_fee, total, items, source, rider, history, placed_at, site_updated_at)
VALUES (current_tenant(), %(external_id)s, %(status)s, %(name)s, %(phone)s, %(area)s, %(address)s, %(landmark)s,
        %(notes)s, %(slot_date)s, %(slot)s, %(subtotal)s, %(delivery_fee)s, %(total)s, %(items)s, %(source)s,
        %(rider)s, %(history)s, %(placed_at)s, %(updated_at)s)
ON CONFLICT (tenant_id, external_id) DO UPDATE SET
  status = EXCLUDED.status, customer_name = EXCLUDED.customer_name, customer_phone = EXCLUDED.customer_phone,
  area = EXCLUDED.area, address = EXCLUDED.address, landmark = EXCLUDED.landmark, notes = EXCLUDED.notes,
  slot_date = EXCLUDED.slot_date, slot = EXCLUDED.slot, subtotal = EXCLUDED.subtotal,
  delivery_fee = EXCLUDED.delivery_fee, total = EXCLUDED.total, items = EXCLUDED.items,
  source = EXCLUDED.source, rider = EXCLUDED.rider, history = EXCLUDED.history,
  site_updated_at = EXCLUDED.site_updated_at, received_at = now()
WHERE storefront_orders.site_updated_at <= EXCLUDED.site_updated_at
RETURNING id, status, total, revenue_event_id
"""


# ------------------------------------------------------- into the platform's Orders
# A website order is also an ordinary platform order (app/orders.py), so it shows
# on the dashboard's Orders page next to WhatsApp and walk-in orders, and its
# money reaches Revenue, Daily Closing and P&L through the usual invoice and
# payment. The website stays in charge of it: its status follows the site, and
# the dashboard's order buttons refuse it (orders.website_owned).
SITE_TO_ORDER = {"new": "new", "confirmed": "confirmed", "preparing": "confirmed",
                 "out_for_delivery": "out_for_delivery", "delivered": "delivered", "cancelled": "cancelled"}
_LADDER = ("new", "confirmed", "out_for_delivery", "delivered")
_STAMP = {"confirmed": "confirmed_at", "out_for_delivery": "dispatched_at", "delivered": "delivered_at"}
SITE_ACTOR = "website"


def _slot_text(slot: SFSlot | None) -> str:
    """'Tue 20 Oct, 7:30 PM' (or the morning window) from the site's date + HH:MM."""
    if not slot or not slot.date:
        return ""
    try:
        day = datetime.strptime(slot.date, "%Y-%m-%d").strftime("%a %d %b").replace(" 0", " ")
    except ValueError:
        day = slot.date
    if not slot.slot:
        return day
    if slot.slot == "morning":
        return f"{day}, 10 AM to 12 PM"
    try:
        t = datetime.strptime(slot.slot, "%H:%M")
        return f"{day}, {t.hour % 12 or 12}:{t.minute:02d} {'PM' if t.hour >= 12 else 'AM'}"
    except ValueError:
        return f"{day}, {slot.slot}"


def _site_customer(cur, name: str, phone: str):
    """The business's customer with this phone (last 10 digits), or a new one."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 7:
        return None
    cur.execute("SELECT id FROM customers WHERE right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10) "
                "= right(%s, 10) ORDER BY created_at LIMIT 1", (digits,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO customers (tenant_id, name, phone, source) VALUES (current_tenant(), %s, %s, 'order:website') "
                "RETURNING id", (name[:120], phone[:40]))
    return cur.fetchone()["id"]


def _create_platform_order(cur, o: SFOrder) -> dict:
    from .orders import _event, catalogue, match_item
    c = o.customer
    address = ", ".join(x for x in (c.address, c.landmark, c.area) if x)
    notes = []
    when = _slot_text(o.slot)
    if when:
        notes.append(f"Delivery slot: {when}")
    if o.deliveryFee is None:
        notes.append("Delivery charge to be confirmed with the customer.")
    if c.notes:
        notes.append(f"Customer note: {c.notes}")
    cid = _site_customer(cur, c.name, c.phone)
    cur.execute("""INSERT INTO orders (tenant_id, number, customer_id, customer_name, customer_phone, status, fulfilment,
                                       address, subtotal, delivery_fee, discount, total, source, source_ref, notes,
                                       created_by, created_by_type, created_at)
                   VALUES (current_tenant(), %s, %s, %s, %s, 'new', 'delivery', %s, %s, %s, 0, %s, 'website', %s, %s,
                           %s, 'system', %s)
                   RETURNING *""",
                (o.id, cid, c.name[:120], c.phone[:40], address[:500], o.subtotal, o.deliveryFee or 0, o.total,
                 "website:" + o.id, "\n".join(notes)[:2000], SITE_ACTOR, o.createdAt))
    row = dict(cur.fetchone())
    cat = catalogue(cur)
    for line in o.lines:
        extras = ", ".join(x for x in (line.chutney and f"{line.chutney} chutney", line.salad and f"{line.salad} salad") if x)
        match = match_item(cat, line.name)
        cur.execute("""INSERT INTO order_items (tenant_id, order_id, product_id, service_id, description, quantity,
                                                unit_price, line_total)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)""",
                    (row["id"], match["id"] if match and match["kind"] == "product" else None,
                     match["id"] if match and match["kind"] == "service" else None,
                     (line.name + (f" ({extras})" if extras else ""))[:200], line.qty, line.unitPrice, line.lineTotal))
    _event(cur, row["id"], SITE_ACTOR, "system", "created",
           "Order placed on the website" + (f" for {when}" if when else ""))
    return row


def _follow_site_status(cur, row: dict, o: SFOrder) -> None:
    """Move the platform order to where the website says it is. Forward only."""
    from .audit import record
    from .operations import _paid_so_far, _settle
    from .orders import _ensure_invoice, _event, _recognise
    target = SITE_TO_ORDER[o.status]
    if row["status"] == target or row["status"] in ("delivered", "cancelled"):
        return
    if target == "cancelled":
        if row["invoice_id"] and _paid_so_far(cur, row["invoice_id"]) > 0:
            _event(cur, row["id"], SITE_ACTOR, "system", "check",
                   "Cancelled on the website, but money was already recorded here. Check it.")
            return
        if row["invoice_id"]:
            cur.execute("UPDATE invoices SET voided_at = now(), void_reason = %s, status = 'void', updated_at = now() "
                        "WHERE id = %s AND voided_at IS NULL", (f"Order {row['number']} cancelled on the website",
                                                                 row["invoice_id"]))
        cur.execute("UPDATE orders SET status = 'cancelled', cancelled_at = now(), cancel_reason = %s, updated_at = now() "
                    "WHERE id = %s", ("Cancelled on the website", row["id"]))
        _event(cur, row["id"], SITE_ACTOR, "system", "cancelled", "Cancelled on the website")
        record(cur, action="order.cancelled", resource="orders", resource_id=str(row["id"]), actor=SITE_ACTOR,
               actor_type="system", detail={"number": row["number"], "from": row["status"]})
        return
    for step in _LADDER[_LADDER.index(row["status"]) + 1:_LADDER.index(target) + 1]:
        cur.execute(f"UPDATE orders SET status = %s, {_STAMP[step]} = now(), updated_at = now() WHERE id = %s",
                    (step, row["id"]))
        detail = f"Rider: {o.rider}" if step == "out_for_delivery" and o.rider else "Updated on the website"
        _event(cur, row["id"], SITE_ACTOR, "system", step, detail)
        record(cur, action="order." + step, resource="orders", resource_id=str(row["id"]), actor=SITE_ACTOR,
               actor_type="system", detail={"number": row["number"], "from": row["status"]})
        row["status"] = step
    if row["status"] == "delivered":
        # Cash on delivery: marked delivered on the website means the cash was collected.
        inv = _ensure_invoice(cur, row, SITE_ACTOR)
        owed = round(float(row["total"]) - _paid_so_far(cur, inv), 2)
        if owed > 0:
            cur.execute("""INSERT INTO payments (tenant_id, invoice_id, customer_id, amount, method, reference, notes)
                           VALUES (current_tenant(), %s, %s, %s, 'cash', %s, %s)""",
                        (inv, row["customer_id"], owed, row["number"],
                         f"Order {row['number']}: cash on delivery, marked delivered on the website"))
            _settle(cur, inv, SITE_ACTOR)
            _event(cur, row["id"], SITE_ACTOR, "system", "payment",
                   "Rs {:,.0f} cash on delivery (marked delivered on the website). Fully paid.".format(owed))
        _recognise(cur, row, SITE_ACTOR, "system")


def mirror_order(cur, o: SFOrder) -> None:
    """Create or update the platform order for a website order. Idempotent."""
    cur.execute("SELECT * FROM orders WHERE source_ref = %s FOR UPDATE", ("website:" + o.id,))
    row = cur.fetchone()
    row = dict(row) if row else _create_platform_order(cur, o)
    _follow_site_status(cur, row, o)


def _site_lead(cur, kind: str, item: SFInboxItem, payload: dict) -> None:
    """A catering enquiry or contact message becomes a Lead, so it is followed up
    like every other enquiry. No automation is started from here."""
    if kind == "enquiry":
        people = payload.get("people")
        service = "Catering / event" + (f" ({people} people)" if people else "")
        bits = [payload.get("type"), payload.get("date") and f"date {payload.get('date')}", payload.get("area"),
                payload.get("budget") and f"budget {payload.get('budget')}", payload.get("notes") or payload.get("message")]
        tags = ["website", "catering"]
    else:
        service = "Website message"
        bits = [payload.get("subject"), payload.get("message")]
        tags = ["website", "message"]
    notes = " · ".join(str(b) for b in bits if b)
    cur.execute("""INSERT INTO leads (tenant_id, name, phone, email, service, source, tags, notes, created_by)
                   VALUES (current_tenant(), %s, %s, %s, %s, 'api', %s, %s, %s)""",
                (item.name, item.phone, item.email, service[:120], tags, notes[:2000] or None, SITE_ACTOR))


def ingest_order(tenant_id: str, o: SFOrder) -> dict:
    """Upsert one order. Returns what changed, for event emission. Idempotent."""
    slot = o.slot or SFSlot()
    params = {
        "external_id": o.id,
        "status": o.status,
        "name": o.customer.name,
        "phone": o.customer.phone,
        "area": o.customer.area,
        "address": o.customer.address,
        "landmark": o.customer.landmark,
        "notes": o.customer.notes,
        "slot_date": slot.date,
        "slot": slot.slot,
        "subtotal": o.subtotal,
        "delivery_fee": o.deliveryFee,
        "total": o.total,
        "items": Json([line.model_dump() for line in o.lines]),
        "source": Json(o.source or {}),
        "rider": o.rider,
        "history": Json([{"status": h.status, "at": h.at.isoformat()} for h in o.history]),
        "placed_at": o.createdAt,
        "updated_at": o.updated_at(),
    }
    with tenant_session(tenant_id, "operator") as cur:
        cur.execute(
            "SELECT status FROM storefront_orders WHERE tenant_id = current_tenant() AND external_id = %s FOR UPDATE",
            (o.id,),
        )
        prev = cur.fetchone()
        cur.execute(_UPSERT, params)
        row = cur.fetchone()
        if row is None:  # an older update arrived after a newer one: keep the newer
            return {"applied": False, "created": False, "previous": prev["status"] if prev else None, "status": prev and prev["status"]}
        revenue_id = row["revenue_event_id"]
        if row["status"] == "delivered" and revenue_id is None:
            # Money is recorded once, at delivery (cash on delivery: delivered = paid).
            cur.execute(
                """INSERT INTO revenue_events (tenant_id, amount, type, attributed_source)
                   VALUES (current_tenant(), %s, 'storefront_order', 'api') RETURNING id""",
                (row["total"],),
            )
            revenue_id = cur.fetchone()["id"]
            cur.execute("UPDATE storefront_orders SET revenue_event_id = %s WHERE id = %s", (revenue_id, row["id"]))
        try:
            with cur.connection.transaction():  # a savepoint: the website order is kept even if this fails
                mirror_order(cur, o)
        except Exception:
            log.exception("storefront: order %s not copied into Orders for %s (a re-send retries it)", o.id, tenant_id)
    return {
        "applied": True,
        "created": prev is None,
        "previous": prev["status"] if prev else None,
        "status": row["status"],
        "revenue_recorded": revenue_id is not None and row["revenue_event_id"] is None,
    }


def ingest_inbox(tenant_id: str, kind: str, item: SFInboxItem, raw: dict) -> bool:
    payload = {k: v for k, v in raw.items() if k not in {"id", "createdAt", "name", "phone", "email"}}
    with tenant_session(tenant_id, "operator") as cur:
        cur.execute(
            """INSERT INTO storefront_inbox (tenant_id, external_id, kind, name, phone, email, payload, created_at)
               VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, external_id) DO NOTHING RETURNING id""",
            (item.id, kind, item.name, item.phone, item.email, Json(payload), item.createdAt),
        )
        created = cur.fetchone() is not None
        if created:
            try:
                with cur.connection.transaction():
                    _site_lead(cur, kind, item, payload)
            except Exception:
                log.exception("storefront: %s %s not added to Leads for %s", kind, item.id, tenant_id)
        return created


def _money(n: int | None) -> str:
    return "Rs {:,}".format(n or 0)


# ---------------------------------------------------------------------- router
def make_storefront_router(persistence_sink=None) -> APIRouter:
    r = APIRouter(prefix="/storefront", tags=["storefront"])

    def emit(tenant_id: str, type_: str, payload: dict):
        """Best-effort: the order is already saved; an orchestrator outage must not lose it."""
        if persistence_sink is None:
            return
        try:
            persistence_sink.emit(tenant_id, type_, payload, "storefront")
        except Exception:
            log.exception("storefront: could not emit %s for %s", type_, tenant_id)

    # ---- connect ---------------------------------------------------------
    @r.post("/connect")
    def connect(
        request: Request,
        body: dict = Body(default={}),
        tenant_id: Optional[str] = Query(None),
        x_tenant_id: Optional[str] = Header(None),
        x_role: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        td_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
    ):
        """Create (or rotate) the website's webhook secret. The secret is shown ONCE."""
        tid, role = _scope(x_tenant_id, x_role, authorization, td_session, tenant_id)
        if role not in {"owner", "techdeva_admin"}:
            raise HTTPException(403, "only the owner can connect a website")
        site = str(body.get("site_url") or "").strip()[:200] or None
        secret = secrets.token_urlsafe(32)
        stored = crypto.encrypt_json({"secret": secret}, tid, PROVIDER)
        with tenant_session(tid, "owner" if role == "owner" else "techdeva_admin") as cur:
            cur.execute(
                """INSERT INTO integrations (tenant_id, provider, status, external_account, credentials, connected_at, last_error)
                   VALUES (%s, %s, 'connected', %s, %s, now(), NULL)
                   ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'connected',
                     external_account = EXCLUDED.external_account, credentials = EXCLUDED.credentials,
                     connected_at = now(), last_error = NULL""",
                (tid, PROVIDER, site, Json(stored)),
            )
        base = str(request.base_url).rstrip("/")
        return {
            "webhook_url": f"{base}/storefront/webhook/{tid}",
            "secret": secret,
            "note": "Copy the secret now; it is not shown again. Set PLATFORM_WEBHOOK_URL and PLATFORM_WEBHOOK_SECRET on the website.",
        }

    @r.get("/connection")
    def connection(
        tenant_id: Optional[str] = Query(None),
        x_tenant_id: Optional[str] = Header(None),
        x_role: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        td_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
    ):
        tid, role = _scope(x_tenant_id, x_role, authorization, td_session, tenant_id)
        with tenant_session(tid, role if role != "techdeva_admin" else "techdeva_admin") as cur:
            cur.execute(
                "SELECT status, external_account, connected_at FROM integrations WHERE tenant_id = %s AND provider = %s",
                (tid, PROVIDER),
            )
            row = cur.fetchone()
            cur.execute("SELECT max(received_at) AS last FROM storefront_orders WHERE tenant_id = %s", (tid,))
            last = cur.fetchone()["last"]
        return {
            "connected": bool(row and row["status"] == "connected"),
            "site": row["external_account"] if row else None,
            "connected_at": row["connected_at"].isoformat() if row and row["connected_at"] else None,
            "last_order_received_at": last.isoformat() if last else None,
        }

    # ---- webhook (public, signed) -----------------------------------------
    @r.post("/webhook/{tenant_id}")
    async def webhook(tenant_id: str, request: Request):
        try:
            uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(404, "not found")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")
        secret = _secret_for(tenant_id)
        # Same answer for "no such tenant" and "bad signature": nothing to probe.
        if not secret or not verify_signature(secret, request.headers.get(SIGNATURE_HEADER), body):
            raise HTTPException(401, "invalid signature")
        try:
            env = Envelope.model_validate(json.loads(body))
        except (ValueError, ValidationError) as e:
            raise HTTPException(422, f"bad payload: {str(e)[:300]}")

        if env.type in ("order.created", "order.updated"):
            try:
                order = SFOrder.model_validate(env.data.get("order") or {})
            except ValidationError as e:
                raise HTTPException(422, f"bad order: {str(e)[:300]}")
            res = ingest_order(tenant_id, order)
            if res["applied"]:
                summary = {
                    "order_id": order.id,
                    "total": order.total,
                    "area": order.customer.area,
                    "slot_date": order.slot.date if order.slot else None,
                    "slot": order.slot.slot if order.slot else None,
                    "customer_phone": order.customer.phone,
                    "status": order.status,
                }
                if res["created"]:
                    emit(tenant_id, "storefront.order.created", summary)
                elif res["previous"] != res["status"]:
                    emit(tenant_id, "storefront.order.status_changed", {**summary, "previous": res["previous"]})
            return {"ok": True, **res}

        kind = "enquiry" if env.type == "enquiry.created" else "contact"
        raw = env.data.get(kind if kind == "enquiry" else "message") or {}
        try:
            item = SFInboxItem.model_validate(raw)
        except ValidationError as e:
            raise HTTPException(422, f"bad {kind}: {str(e)[:300]}")
        created = ingest_inbox(tenant_id, kind, item, raw)
        if created:
            emit(tenant_id, f"storefront.{kind}.created", {"name": item.name, "phone": item.phone,
                                                          "people": raw.get("people"), "date": raw.get("date")})
        return {"ok": True, "created": created}

    # ---- dashboard data ---------------------------------------------------
    def _ro_session(tid: str, role: str):
        return tenant_session(tid, "techdeva_admin" if role == "techdeva_admin" else role)

    @r.get("/summary")
    def summary(
        tenant_id: Optional[str] = Query(None),
        x_tenant_id: Optional[str] = Header(None),
        x_role: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        td_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
    ):
        tid, role = _scope(x_tenant_id, x_role, authorization, td_session, tenant_id)
        live = "status <> 'cancelled'"
        local_day = f"(placed_at AT TIME ZONE '{TZ}')::date"
        today = f"(now() AT TIME ZONE '{TZ}')::date"
        with _ro_session(tid, role) as cur:
            q = lambda sql, *a: (cur.execute(sql, a), cur.fetchall())[1]  # noqa: E731
            kpi = q(f"""
                SELECT
                  count(*) FILTER (WHERE {local_day} = {today})                              AS orders_today,
                  coalesce(sum(total) FILTER (WHERE {local_day} = {today} AND {live}), 0)    AS revenue_today,
                  count(*) FILTER (WHERE {local_day} > {today} - 7)                          AS orders_week,
                  coalesce(sum(total) FILTER (WHERE {local_day} > {today} - 7 AND {live}), 0) AS revenue_week,
                  count(*) FILTER (WHERE {local_day} > {today} - 30 AND {live})              AS orders_30,
                  coalesce(sum(total) FILTER (WHERE {local_day} > {today} - 30 AND {live}), 0) AS revenue_30,
                  count(*) FILTER (WHERE {local_day} > {today} - 30 AND status = 'cancelled') AS cancelled_30,
                  coalesce(sum(total) FILTER (WHERE status = 'delivered'), 0)                AS delivered_revenue_all
                FROM storefront_orders WHERE tenant_id = %s""", tid)[0]
            by_status = {row["status"]: row["n"] for row in q(
                "SELECT status, count(*) AS n FROM storefront_orders WHERE tenant_id = %s GROUP BY status", tid)}
            daily = q(f"""
                SELECT d::date AS day,
                       count(o.id) FILTER (WHERE o.status <> 'cancelled') AS orders,
                       coalesce(sum(o.total) FILTER (WHERE o.status <> 'cancelled'), 0) AS revenue
                FROM generate_series({today} - 13, {today}, interval '1 day') d
                LEFT JOIN storefront_orders o ON o.tenant_id = %s AND (o.placed_at AT TIME ZONE '{TZ}')::date = d::date
                GROUP BY d ORDER BY d""", tid)
            upcoming = q(f"""
                SELECT external_id, status, customer_name, customer_phone, area, address, landmark, slot_date, slot,
                       total, delivery_fee, items, rider
                FROM storefront_orders
                WHERE tenant_id = %s AND slot_date BETWEEN {today} AND {today} + 1
                  AND status IN ('new','confirmed','preparing','out_for_delivery')
                ORDER BY slot_date, (slot = 'morning') DESC, slot""", tid)
            top_items = q(f"""
                SELECT li->>'name' AS name, sum((li->>'qty')::int) AS qty, sum((li->>'lineTotal')::int) AS revenue
                FROM storefront_orders, jsonb_array_elements(items) li
                WHERE tenant_id = %s AND {live} AND {local_day} > {today} - 30
                GROUP BY 1 ORDER BY qty DESC, revenue DESC LIMIT 8""", tid)
            areas = q(f"""
                SELECT coalesce(area, 'Unknown') AS area, count(*) AS orders, sum(total) AS revenue
                FROM storefront_orders WHERE tenant_id = %s AND {live} AND {local_day} > {today} - 30
                GROUP BY 1 ORDER BY orders DESC LIMIT 8""", tid)
            sources = q(f"""
                SELECT coalesce(nullif(source->>'utmSource', ''), CASE WHEN source->>'referrer' IS NOT NULL
                         THEN 'referral' ELSE 'direct' END) AS source,
                       nullif(source->>'utmCampaign', '') AS campaign,
                       count(*) AS orders, sum(total) AS revenue
                FROM storefront_orders WHERE tenant_id = %s AND {live} AND {local_day} > {today} - 30
                GROUP BY 1, 2 ORDER BY revenue DESC LIMIT 10""", tid)
            cust = q(f"""
                SELECT count(*) AS customers, count(*) FILTER (WHERE n > 1) AS repeat_customers
                FROM (SELECT customer_phone, count(*) AS n FROM storefront_orders
                      WHERE tenant_id = %s AND {live} GROUP BY 1) c""", tid)[0]
            inbox_new = q(f"""SELECT count(*) AS n FROM storefront_inbox
                              WHERE tenant_id = %s AND created_at > now() - interval '7 days'""", tid)[0]["n"]

        orders_30 = kpi["orders_30"] or 0
        return {
            "today": {"orders": kpi["orders_today"], "revenue": int(kpi["revenue_today"])},
            "week": {"orders": kpi["orders_week"], "revenue": int(kpi["revenue_week"])},
            "last_30_days": {
                "orders": orders_30,
                "revenue": int(kpi["revenue_30"]),
                "avg_order_value": int(kpi["revenue_30"] / orders_30) if orders_30 else None,
                "cancelled": kpi["cancelled_30"],
            },
            "delivered_revenue_all_time": int(kpi["delivered_revenue_all"]),
            "by_status": {s: by_status.get(s, 0) for s in STATUSES},
            "daily": [{"day": d["day"].isoformat(), "orders": d["orders"], "revenue": int(d["revenue"])} for d in daily],
            "upcoming": [{**u, "slot_date": u["slot_date"].isoformat() if u["slot_date"] else None} for u in upcoming],
            "top_items": [{"name": t["name"], "qty": int(t["qty"]), "revenue": int(t["revenue"])} for t in top_items],
            "areas": [{"area": a["area"], "orders": a["orders"], "revenue": int(a["revenue"])} for a in areas],
            "sources": [{"source": s["source"], "campaign": s["campaign"], "orders": s["orders"], "revenue": int(s["revenue"])} for s in sources],
            "customers": {"total": cust["customers"], "repeat": cust["repeat_customers"]},
            "inbox_last_7_days": inbox_new,
        }

    @r.get("/orders")
    def orders(
        status: Optional[str] = Query(None),
        date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(100, ge=1, le=500),
        tenant_id: Optional[str] = Query(None),
        x_tenant_id: Optional[str] = Header(None),
        x_role: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        td_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
    ):
        tid, role = _scope(x_tenant_id, x_role, authorization, td_session, tenant_id)
        if status and status not in STATUSES:
            raise HTTPException(400, "unknown status")
        sql = """SELECT external_id, status, customer_name, customer_phone, area, slot_date, slot, subtotal,
                        delivery_fee, total, items, source, rider, placed_at
                 FROM storefront_orders WHERE tenant_id = %s"""
        args: list[Any] = [tid]
        if status:
            sql += " AND status = %s"
            args.append(status)
        if date:
            sql += " AND slot_date = %s"
            args.append(date)
        sql += " ORDER BY placed_at DESC LIMIT %s"
        args.append(limit)
        with _ro_session(tid, role) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return {"orders": [{**o, "slot_date": o["slot_date"].isoformat() if o["slot_date"] else None,
                            "placed_at": o["placed_at"].isoformat()} for o in rows]}

    @r.get("/inbox")
    def inbox(
        limit: int = Query(50, ge=1, le=200),
        tenant_id: Optional[str] = Query(None),
        x_tenant_id: Optional[str] = Header(None),
        x_role: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        td_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
    ):
        tid, role = _scope(x_tenant_id, x_role, authorization, td_session, tenant_id)
        with _ro_session(tid, role) as cur:
            cur.execute("""SELECT kind, name, phone, email, payload, created_at FROM storefront_inbox
                           WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s""", (tid, limit))
            rows = cur.fetchall()
        return {"items": [{**i, "created_at": i["created_at"].isoformat()} for i in rows]}

    return r
