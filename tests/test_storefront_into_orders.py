"""Website orders land in the platform's own Orders, enquiries in Leads.

Needs the same database as tests/test_storefront.py (db/29 and the test-only
seed db/30 applied, plus db/60_orders.sql).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.db import tenant_session
from app.main import app
from app.storefront import sign

IG = "33333333-3333-3333-3333-333333333333"
OWNER = {"X-Tenant-Id": IG, "X-Role": "owner"}
STEPS = ["new", "confirmed", "preparing", "out_for_delivery", "delivered"]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def secret(client):
    res = client.post("/storefront/connect", headers=OWNER, json={"site_url": "https://instantgrill.shop"})
    assert res.status_code == 200, res.text
    return res.json()["secret"]


def _at(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _order(oid, status="new", fee=200, rider=None, phone="03214445566"):
    upto = STEPS.index(status) if status != "cancelled" else 0
    history = [{"status": STEPS[i], "at": _at(i)} for i in range(upto + 1)]
    if status == "cancelled":
        history.append({"status": "cancelled", "at": _at(1)})
    return {
        "id": oid, "createdAt": _at(0), "status": status,
        "customer": {"name": "Hira Saleem", "phone": phone, "area": "Johar Town",
                     "address": "House 12, Block R", "landmark": "near Emporium", "notes": "Ring twice"},
        "lines": [{"slug": "chicken-tikka", "name": "Chicken Tikka Box", "unitPrice": 3000, "qty": 2,
                   "lineTotal": 6000, "chutney": "Imli", "salad": "Russian"}],
        "subtotal": 6000, "deliveryFee": fee, "total": 6000 + (fee or 0),
        "slot": {"date": "2026-10-20", "slot": "19:30"}, "rider": rider, "history": history,
        "source": {"utmSource": "instagram"},
    }


def _send(client, secret, type_, data):
    body = json.dumps({"id": str(uuid.uuid4()), "type": type_, "occurred_at": _at(0), "data": data}).encode()
    res = client.post(f"/storefront/webhook/{IG}", content=body,
                      headers={"X-Storefront-Signature": sign(secret, body), "Content-Type": "application/json"})
    assert res.status_code == 200, res.text
    return res.json()


def _platform_order(number):
    with tenant_session(IG, "owner") as cur:
        cur.execute("SELECT * FROM orders WHERE number = %s", (number,))
        return cur.fetchone()


def _oid():
    return "IG-W" + uuid.uuid4().hex[:6].upper()


def test_new_website_order_is_a_platform_order(client, secret):
    oid = _oid()
    _send(client, secret, "order.created", {"order": _order(oid)})
    _send(client, secret, "order.created", {"order": _order(oid)})          # a retry: still one order
    with tenant_session(IG, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM orders WHERE source_ref = %s", ("website:" + oid,))
        assert cur.fetchone()["n"] == 1
    o = _platform_order(oid)
    assert o["source"] == "website" and o["status"] == "new" and o["created_by_type"] == "system"
    assert float(o["total"]) == 6200 and float(o["delivery_fee"]) == 200
    assert "House 12, Block R" in o["address"] and "Johar Town" in o["address"]
    assert "Tue 20 Oct, 7:30 PM" in o["notes"] and "Ring twice" in o["notes"]
    assert o["customer_id"] is not None

    # It is on the dashboard's Orders list and opens with no buttons that would fight the website.
    listed = client.get("/orders", headers=OWNER).json()
    rows = listed.get("orders", listed) if isinstance(listed, dict) else listed
    assert any(r["number"] == oid for r in rows)
    view = client.get(f"/orders/{o['id']}", headers=OWNER).json()
    assert view["website_owned"] is True and view["next"] == []
    assert view["items"][0]["description"] == "Chicken Tikka Box (Imli chutney, Russian salad)"
    assert float(view["items"][0]["quantity"]) == 2 and float(view["items"][0]["line_total"]) == 6000


def test_status_follows_the_website_and_delivery_records_cash(client, secret):
    oid = _oid()
    _send(client, secret, "order.created", {"order": _order(oid)})
    _send(client, secret, "order.updated", {"order": _order(oid, "preparing")})
    assert _platform_order(oid)["status"] == "confirmed"                      # no 'preparing' on the platform
    _send(client, secret, "order.updated", {"order": _order(oid, "out_for_delivery", rider="Bilal")})
    o = _platform_order(oid)
    assert o["status"] == "out_for_delivery" and o["dispatched_at"] is not None
    assert o["recognised_at"] is None                                         # not a sale yet

    _send(client, secret, "order.updated", {"order": _order(oid, "delivered", rider="Bilal")})
    _send(client, secret, "order.updated", {"order": _order(oid, "delivered", rider="Bilal")})  # resent
    o = _platform_order(oid)
    assert o["status"] == "delivered" and o["recognised_at"] is not None and o["invoice_id"] is not None
    with tenant_session(IG, "owner") as cur:
        cur.execute("SELECT count(*) AS n, sum(amount) AS paid, min(method) AS method FROM payments WHERE invoice_id = %s",
                    (o["invoice_id"],))
        pay = cur.fetchone()
        assert pay["n"] == 1 and float(pay["paid"]) == 6200 and pay["method"] == "cash"
        cur.execute("SELECT status FROM invoices WHERE id = %s", (o["invoice_id"],))
        assert cur.fetchone()["status"] == "paid"
        cur.execute("SELECT event, detail FROM order_events WHERE order_id = %s ORDER BY at", (o["id"],))
        events = cur.fetchall()
    names = [e["event"] for e in events]
    for step in ("created", "confirmed", "out_for_delivery", "delivered", "payment", "recognised"):
        assert step in names
    assert any(e["detail"] == "Rider: Bilal" for e in events)

    # An older update arriving late changes nothing.
    _send(client, secret, "order.updated", {"order": _order(oid, "confirmed")})
    assert _platform_order(oid)["status"] == "delivered"


def test_skipped_steps_and_cancellation(client, secret):
    jump = _oid()
    _send(client, secret, "order.created", {"order": _order(jump, "delivered", fee=None)})
    o = _platform_order(jump)
    assert o["status"] == "delivered" and o["confirmed_at"] and o["dispatched_at"] and o["recognised_at"]
    assert float(o["total"]) == 6000 and "Delivery charge to be confirmed" in o["notes"]

    gone = _oid()
    _send(client, secret, "order.created", {"order": _order(gone)})
    _send(client, secret, "order.updated", {"order": _order(gone, "cancelled")})
    o = _platform_order(gone)
    assert o["status"] == "cancelled" and o["cancel_reason"] == "Cancelled on the website" and o["recognised_at"] is None


def test_dashboard_buttons_refuse_website_orders(client, secret):
    oid = _oid()
    _send(client, secret, "order.created", {"order": _order(oid)})
    pid = str(_platform_order(oid)["id"])
    moved = client.post(f"/orders/{pid}/status", headers=OWNER, json={"status": "confirmed"})
    assert moved.status_code == 409 and "website admin" in moved.json()["detail"]
    paid = client.post(f"/orders/{pid}/pay", headers=OWNER, json={"amount": 100, "method": "cash"})
    assert paid.status_code == 409
    assert _platform_order(oid)["status"] == "new"
    # The website can still move it.
    _send(client, secret, "order.updated", {"order": _order(oid, "confirmed")})
    assert _platform_order(oid)["status"] == "confirmed"


def test_enquiries_and_messages_become_leads(client, secret):
    eid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    sana, kamran = "Sana Tariq " + eid[:6], "Kamran " + mid[:6]   # unique, so the test can run again
    enquiry = {"id": eid, "createdAt": _at(0), "type": "wedding", "name": sana, "phone": "03007778899",
               "people": 120, "date": "2026-12-05", "area": "Bahria Town"}
    _send(client, secret, "enquiry.created", {"enquiry": enquiry})
    _send(client, secret, "enquiry.created", {"enquiry": enquiry})          # retry: one lead
    msg = {"id": mid, "createdAt": _at(0), "name": kamran, "email": "kamran@example.com",
           "message": "Do you do office lunches?"}
    _send(client, secret, "contact.created", {"message": msg})
    with tenant_session(IG, "owner") as cur:
        cur.execute("SELECT service, source::text AS source, tags, notes, created_by FROM leads WHERE name = %s", (sana,))
        leads = cur.fetchall()
        assert len(leads) == 1
        lead = leads[0]
        assert lead["service"] == "Catering / event (120 people)" and lead["source"] == "api"
        assert "website" in lead["tags"] and "catering" in lead["tags"] and lead["created_by"] == "website"
        assert "2026-12-05" in lead["notes"] and "Bahria Town" in lead["notes"]
        cur.execute("SELECT service, email, notes FROM leads WHERE name = %s", (kamran,))
        m = cur.fetchone()
        assert m["service"] == "Website message" and m["email"] == "kamran@example.com"
        assert "office lunches" in m["notes"]
