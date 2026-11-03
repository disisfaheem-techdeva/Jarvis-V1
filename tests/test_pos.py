"""POS till: a sale is an ordinary order, paid at once or kept open, and the
money, stock and recognition follow the same rules as every other order.

Needs db/60 and db/90 applied. Makes its own throwaway business and deletes it.
"""
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth import issue_token
from app.db import tenant_session
from app.main import app

T, T2 = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "staff", "other")}


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER = _bearer(T, U["owner"], "owner")
STAFF = _bearer(T, U["staff"], "operator")
OTHER = _bearer(T2, U["other"], "owner")


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test) VALUES (%s, 'POS Test Shop', 'retail', true),"
                    " (%s, 'POS Other Shop', 'retail', true)", (T, T2))
        for k, tid, role in (("owner", T, "owner"), ("staff", T, "operator"), ("other", T2, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, 'Person', %s, %s)",
                        (U[k], tid, "%s@postest.local" % U[k][:8], role))
        cur.execute("""INSERT INTO products (tenant_id, name, price, sku, track_stock) VALUES
                       (%s, 'Chicken Karahi', 1800, 'K1', true), (%s, 'Naan', 50, 'N1', false),
                       (%s, 'Mystery box', NULL, NULL, false) RETURNING id""", (T, T, T))
        karahi = cur.fetchone()["id"]
        cur.execute("INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta) VALUES (%s, %s, 'opening', 10)",
                    (T, karahi))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (T, T2):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM orders WHERE tenant_id IN (%s, %s)", (T, T2))
        assert cur.fetchone()["n"] == 0


def ref():
    return uuid.uuid4().hex


def cat(client):
    r = client.get("/pos/catalogue", headers=OWNER)
    assert r.status_code == 200, r.text
    return {i["name"]: i for i in r.json()["items"]}


def stock(client, name):
    return cat(client)[name]["on_hand"]


def test_catalogue_shows_stock_only_when_tracked(client):
    c = cat(client)
    assert c["Chicken Karahi"]["on_hand"] == 10
    assert c["Naan"]["on_hand"] is None          # not tracked: no figure, never 0


def test_cash_sale_with_change_is_recognised_and_takes_stock(client):
    c = cat(client)
    body = {"client_ref": ref(), "items": [{"product_id": c["Chicken Karahi"]["id"], "quantity": 2},
                                           {"product_id": c["Naan"]["id"], "quantity": 4}],
            "payments": [{"method": "cash", "amount": 5000}]}
    r = client.post("/pos/sale", headers=STAFF, json=body)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total"] == 3800 and s["status"] == "delivered" and s["recognised"]
    assert s["payment_status"] == "paid" and s["cash_tendered"] == 5000 and s["change_given"] == 1200
    # only the money kept is a payment: change is not income
    assert [p["amount"] for p in s["payments"]] == [3800]
    assert stock(client, "Chicken Karahi") == 8


def test_resend_returns_the_first_sale_not_a_second_charge(client):
    c = cat(client)
    body = {"client_ref": ref(), "items": [{"product_id": c["Naan"]["id"], "quantity": 1}],
            "payments": [{"method": "cash", "amount": 50}]}
    a = client.post("/pos/sale", headers=STAFF, json=body).json()
    b = client.post("/pos/sale", headers=STAFF, json=body).json()
    assert b["duplicate"] and a["number"] == b["number"] and len(b["payments"]) == 1


def test_short_payment_is_refused_and_leaves_nothing(client):
    c = cat(client)
    before = client.get("/orders", headers=OWNER).json()["orders"]
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(),
                    "items": [{"product_id": c["Chicken Karahi"]["id"]}], "payments": [{"method": "cash", "amount": 1000}]})
    assert r.status_code == 409 and "short" in r.json()["detail"]
    assert len(client.get("/orders", headers=OWNER).json()["orders"]) == len(before)
    assert stock(client, "Chicken Karahi") == 8


def test_card_more_than_bill_is_refused(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(),
                    "items": [{"product_id": c["Naan"]["id"]}], "payments": [{"method": "card", "amount": 100}]})
    assert r.status_code == 409


def test_split_card_and_cash(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(),
                    "items": [{"product_id": c["Chicken Karahi"]["id"]}],
                    "payments": [{"method": "card", "amount": 1000, "reference": "slip 42"}, {"method": "cash", "amount": 1000}]})
    s = r.json()
    assert r.status_code == 200, r.text
    assert sorted((p["method"], p["amount"]) for p in s["payments"]) == [("card", 1000), ("cash", 800)]
    assert s["change_given"] == 200 and s["recognised"]


def test_staff_cannot_discount_owner_can(client):
    c = cat(client)
    item = [{"product_id": c["Naan"]["id"], "quantity": 2}]
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(), "items": item, "discount": 10,
                                                      "payments": [{"method": "cash", "amount": 100}]})
    assert r.status_code == 403
    r = client.post("/pos/sale", headers=OWNER, json={"client_ref": ref(), "items": item, "discount": 10,
                                                      "payments": [{"method": "cash", "amount": 90}]})
    assert r.status_code == 200 and r.json()["total"] == 90


def test_item_without_price_cannot_be_sold(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(), "items": [{"product_id": c["Mystery box"]["id"]}],
                                                      "payments": [{"method": "cash", "amount": 10}]})
    assert r.status_code == 400 and "price" in r.json()["detail"]


def test_open_table_add_items_then_settle(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(), "mode": "dine_in", "table": "T4", "hold": True,
                                                      "items": [{"product_id": c["Chicken Karahi"]["id"]}]})
    bill = r.json()
    assert r.status_code == 200 and bill["open"] and bill["table"] == "T4" and not bill["recognised"]
    assert stock(client, "Chicken Karahi") == 7          # stock leaves only when paid
    t = client.get("/pos/today", headers=STAFF).json()
    assert any(b["id"] == bill["id"] and b["table"] == "T4" for b in t["open_bills"])
    r = client.post("/pos/bills/%s/items" % bill["id"], headers=STAFF, json={"items": [{"product_id": c["Naan"]["id"], "quantity": 3}]})
    assert r.status_code == 200 and r.json()["total"] == 1950
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=STAFF, json={"payments": [{"method": "online", "amount": 1950}]})
    s = r.json()
    assert r.status_code == 200 and s["recognised"] and not s["open"] and s["change_given"] is None
    assert stock(client, "Chicken Karahi") == 6
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=STAFF, json={"payments": [{"method": "cash", "amount": 1}]})
    assert r.status_code == 409                           # never paid twice
    r = client.post("/pos/bills/%s/items" % bill["id"], headers=STAFF, json={"items": [{"product_id": c["Naan"]["id"]}]})
    assert r.status_code == 409


def test_today_money_is_owner_only(client):
    assert "money" not in client.get("/pos/today", headers=STAFF).json()
    m = client.get("/pos/today", headers=OWNER).json()["money"]
    assert m["collected"] == round(sum(x["amount"] for x in m["by_method"]), 2) and m["collected"] > 0


def test_non_pos_order_is_not_a_till_bill(client):
    c = cat(client)
    o = client.post("/orders", headers=OWNER, json={"items": [{"product_id": c["Naan"]["id"]}], "fulfilment": "pickup"}).json()
    assert client.get("/pos/bills/%s" % o["id"], headers=STAFF).status_code == 409


def test_other_business_cannot_see_the_bill(client):
    c = cat(client)
    bill = client.post("/pos/sale", headers=STAFF, json={"client_ref": ref(), "hold": True,
                                                         "items": [{"product_id": c["Naan"]["id"]}]}).json()
    assert client.get("/pos/bills/%s" % bill["id"], headers=OTHER).status_code == 404
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=OTHER, json={"payments": [{"method": "cash", "amount": 50}]})
    assert r.status_code == 404


def test_missing_reference_is_refused(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"items": [{"product_id": c["Naan"]["id"]}],
                                                      "payments": [{"method": "cash", "amount": 50}]})
    assert r.status_code == 400
