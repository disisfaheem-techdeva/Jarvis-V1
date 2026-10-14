"""Storefront orders: the website -> platform webhook and the orders dashboard API.

Needs the database with db/29_storefront_orders.sql and db/30_seed_instant_grill.sql
applied, like the other API tests.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.db import tenant_session
from app.main import app
from app.storefront import sign, verify_signature

IG = "33333333-3333-3333-3333-333333333333"
CLINIC = "11111111-1111-1111-1111-111111111111"
OWNER = {"X-Tenant-Id": IG, "X-Role": "owner"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def secret(client):
    res = client.post("/storefront/connect", headers=OWNER, json={"site_url": "https://instantgrill.shop"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["webhook_url"].endswith(f"/storefront/webhook/{IG}")
    return data["secret"]


def _now(offset_min=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _order(oid, status="new", history_minutes=(0,), total=3350, area="Gulberg"):
    statuses = ["new", "confirmed", "preparing", "out_for_delivery", "delivered"]
    hist = [{"status": statuses[i] if status != "cancelled" or i == 0 else "cancelled", "at": _now(m)}
            for i, m in enumerate(history_minutes)]
    return {
        "id": oid,
        "createdAt": _now(0),
        "status": status,
        "customer": {"name": "Zainab Akhtar", "phone": "03451112233", "area": area,
                     "address": "House 9, Block C, Gulberg II", "landmark": "near Liberty"},
        "lines": [{"slug": "chicken-tikka", "name": "Chicken Tikka", "unitPrice": 3150, "qty": 1,
                   "lineTotal": 3150, "chutney": "Imli", "salad": "Fresh"}],
        "subtotal": 3150,
        "deliveryFee": 200,
        "total": total,
        "slot": {"date": datetime.now(timezone(timedelta(hours=5))).date().isoformat(), "slot": "19:00"},
        "history": hist,
        "source": {"utmSource": "facebook", "utmCampaign": "launch"},
    }


def _post(client, secret, type_, data, ts=None, tenant=IG, sig=None):
    body = json.dumps({"id": str(uuid.uuid4()), "type": type_, "occurred_at": _now(), "data": data}).encode()
    headers = {"X-Storefront-Signature": sig or sign(secret, body, ts), "Content-Type": "application/json"}
    return client.post(f"/storefront/webhook/{tenant}", content=body, headers=headers)


def _oid():
    return "IG-T" + uuid.uuid4().hex[:6].upper()


def test_signature_helpers():
    body = b'{"a":1}'
    h = sign("s3cret", body, 1_700_000_000)
    assert verify_signature("s3cret", h, body, now=1_700_000_100)
    assert not verify_signature("s3cret", h, body + b" ", now=1_700_000_100)   # tampered body
    assert not verify_signature("other", h, body, now=1_700_000_100)           # wrong secret
    assert not verify_signature("s3cret", h, body, now=1_700_000_000 + 301)    # too old
    assert not verify_signature("s3cret", None, body)
    assert not verify_signature("s3cret", "garbage", body)


def test_only_owner_can_connect(client):
    res = client.post("/storefront/connect", headers={"X-Tenant-Id": IG, "X-Role": "operator"}, json={})
    assert res.status_code == 403


def test_connection_status(client, secret):
    res = client.get("/storefront/connection", headers=OWNER)
    assert res.status_code == 200
    assert res.json()["connected"] is True
    assert res.json()["site"] == "https://instantgrill.shop"
    assert "secret" not in res.text


def test_webhook_rejects_bad_requests(client, secret):
    oid = _oid()
    assert _post(client, secret, "order.created", {"order": _order(oid)}, sig="t=1,v1=00").status_code == 401
    assert _post(client, secret, "order.created", {"order": _order(oid)}, ts=int(time.time()) - 600).status_code == 401
    assert _post(client, "wrong-secret", "order.created", {"order": _order(oid)}).status_code == 401
    # A real tenant with no storefront connected looks the same as a bad signature.
    assert _post(client, secret, "order.created", {"order": _order(oid)}, tenant=CLINIC).status_code == 401
    assert client.post("/storefront/webhook/not-a-uuid", content=b"{}").status_code == 404
    bad = _order(oid)
    bad["total"] = -5
    assert _post(client, secret, "order.created", {"order": bad}).status_code == 422
    # Nothing was stored by any of the above.
    orders = client.get("/storefront/orders", headers=OWNER).json()["orders"]
    assert all(o["external_id"] != oid for o in orders)


def test_order_lifecycle_out_of_order_and_revenue_once(client, secret):
    oid = _oid()
    res = _post(client, secret, "order.created", {"order": _order(oid)})
    assert res.status_code == 200 and res.json()["created"] is True

    # Same event delivered twice (a retry): no duplicate.
    again = _post(client, secret, "order.created", {"order": _order(oid)})
    assert again.json()["created"] is False

    # Newer update (preparing) arrives, then an OLDER one (confirmed) arrives late.
    newer = _order(oid, "preparing", (0, 5, 10))
    older = _order(oid, "confirmed", (0, 5))
    assert _post(client, secret, "order.updated", {"order": newer}).json()["status"] == "preparing"
    late = _post(client, secret, "order.updated", {"order": older}).json()
    assert late["applied"] is False
    row = next(o for o in client.get("/storefront/orders", headers=OWNER).json()["orders"] if o["external_id"] == oid)
    assert row["status"] == "preparing"

    # Delivered (sent twice): revenue recorded exactly once.
    delivered = _order(oid, "delivered", (0, 5, 10, 15, 20))
    first = _post(client, secret, "order.updated", {"order": delivered}).json()
    _post(client, secret, "order.updated", {"order": delivered})
    assert first["revenue_recorded"] is True
    with tenant_session(IG, "owner") as cur:
        cur.execute("""SELECT count(*) AS n FROM revenue_events r JOIN storefront_orders o ON o.revenue_event_id = r.id
                       WHERE o.external_id = %s""", (oid,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT amount, type, attributed_source FROM revenue_events r JOIN storefront_orders o "
                    "ON o.revenue_event_id = r.id WHERE o.external_id = %s", (oid,))
        rev = cur.fetchone()
        assert int(rev["amount"]) == 3350 and rev["type"] == "storefront_order" and rev["attributed_source"] == "api"
        # The orchestrator was told about the new order.
        cur.execute("""SELECT count(*) AS n FROM events WHERE type = 'storefront.order.created'
                       AND payload->>'order_id' = %s""", (oid,))
        assert cur.fetchone()["n"] == 1


def test_other_tenants_cannot_see_the_orders(client, secret):
    oid = _oid()
    _post(client, secret, "order.created", {"order": _order(oid)})
    clinic = client.get("/storefront/orders", headers={"X-Tenant-Id": CLINIC, "X-Role": "owner"}).json()["orders"]
    assert all(o["external_id"] != oid for o in clinic)
    # And at the database level, RLS hides the row even with a hand-written query.
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM storefront_orders WHERE external_id = %s", (oid,))
        assert cur.fetchone()["n"] == 0
    # A TechDeva admin can look at a specific tenant.
    admin = client.get(f"/storefront/orders?tenant_id={IG}", headers={"X-Role": "techdeva_admin"}).json()["orders"]
    assert any(o["external_id"] == oid for o in admin)


def test_enquiries_and_contact_messages(client, secret):
    eid = str(uuid.uuid4())
    enquiry = {"id": eid, "createdAt": _now(), "type": "event", "name": "Omer Farooq", "phone": "03001239876",
               "people": 40, "date": "2026-11-20", "area": "DHA"}
    assert _post(client, secret, "enquiry.created", {"enquiry": enquiry}).json()["created"] is True
    assert _post(client, secret, "enquiry.created", {"enquiry": enquiry}).json()["created"] is False
    msg = {"id": str(uuid.uuid4()), "createdAt": _now(), "name": "Ayesha", "email": "ayesha@example.com",
           "message": "Do you deliver to Bahria Town?"}
    assert _post(client, secret, "contact.created", {"message": msg}).json()["created"] is True
    items = client.get("/storefront/inbox", headers=OWNER).json()["items"]
    e = next(i for i in items if i["name"] == "Omer Farooq")
    assert e["kind"] == "enquiry" and e["payload"]["people"] == 40
    assert any(i["kind"] == "contact" and i["payload"]["message"].startswith("Do you deliver") for i in items)


def test_summary_numbers(client, secret):
    before = client.get("/storefront/summary", headers=OWNER).json()
    oid = _oid()
    _post(client, secret, "order.created", {"order": _order(oid, total=5000, area="Model Town")})
    cancelled = _oid()
    _post(client, secret, "order.created", {"order": _order(cancelled, total=9999)})
    _post(client, secret, "order.updated", {"order": _order(cancelled, "cancelled", (0, 5), total=9999)})
    s = client.get("/storefront/summary", headers=OWNER).json()
    assert s["today"]["orders"] == before["today"]["orders"] + 2
    assert s["today"]["revenue"] == before["today"]["revenue"] + 5000   # cancelled order not counted
    assert s["by_status"]["cancelled"] == before["by_status"]["cancelled"] + 1
    assert len(s["daily"]) == 14 and s["daily"][-1]["revenue"] == s["today"]["revenue"]
    assert any(u["external_id"] == oid for u in s["upcoming"])
    assert all(u["external_id"] != cancelled for u in s["upcoming"])
    assert any(a["area"] == "Model Town" for a in s["areas"])
    assert any(src["source"] == "facebook" and src["campaign"] == "launch" for src in s["sources"])
    assert any(t["name"] == "Chicken Tikka" for t in s["top_items"])


def test_dashboard_page_served(client):
    res = client.get("/orders-view")
    assert res.status_code == 200 and "Online orders" in res.text
