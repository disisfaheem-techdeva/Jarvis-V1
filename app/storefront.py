"""Storefront orders: a tenant's own website pushes its orders into the platform.

Generic, not tenant-specific. Instant Grill (instantgrill.shop) is the first
storefront; any client with a website can connect the same way.

    website ──signed webhook──► POST /storefront/webhook/{tenant_id}
                                   │  verify HMAC (secret in `integrations`,
                                   │  provider='storefront', encrypted at rest)
                                   ▼
                     storefront_orders / storefront_inbox   (RLS, tenant-scoped)
                                   │
                                   ├─ revenue_events row when an order is delivered
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
        return cur.fetchone() is not None


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
