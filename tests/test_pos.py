"""POS till: a sale is an ordinary order, paid at once, kept open or on udhaar,
and the money, stock and recognition follow the same rules as every other
order. Shifts, refunds, manager PIN approvals, tax and kitchen tickets on top.

Needs db/60 and db/90 applied. Makes its own throwaway businesses and deletes
them. The tests share one business and run in file order.
"""
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth import hash_password, issue_token
from app.db import tenant_session
from app.main import app

T, T2 = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "staff", "other")}
OWNER_PIN = "4826"
REG = "Till 1"


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
        # POS is off unless TechDeva turns it on (db/91)
        cur.execute("INSERT INTO pos_settings (tenant_id, enabled) VALUES (%s, true), (%s, true)", (T, T2))
        for k, tid, role in (("owner", T, "owner"), ("staff", T, "operator"), ("other", T2, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, pin_hash) VALUES (%s, %s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@postest.local" % U[k][:8], role,
                         hash_password(OWNER_PIN * 2) if k == "owner" else hash_password("7391" * 2)))
        cur.execute("""INSERT INTO products (tenant_id, name, price, sku, track_stock) VALUES
                       (%s, 'Chicken Karahi', 1800, 'K1', true), (%s, 'Naan', 50, 'N1', false),
                       (%s, 'Mystery box', NULL, NULL, false), (%s, 'Shampoo', 1000, '8964000000017', true) RETURNING id""",
                    (T, T, T, T))
        for pid in [r["id"] for r in cur.fetchall()][::3]:     # Karahi and Shampoo: 10 each
            cur.execute("INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta) VALUES (%s, %s, 'opening', 10)",
                        (T, pid))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (T, T2):
            cur.execute("DELETE FROM pin_attempts WHERE tenant_id = %s", (t,))
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


def line(c, name, q=1, **kw):
    return {"product_id": c[name]["id"], "quantity": q, **kw}


def sell(client, headers, items, payments=None, **kw):
    body = {"client_ref": ref(), "register": REG, "items": items, **kw}
    if payments is not None:
        body["payments"] = payments
    return client.post("/pos/sale", headers=headers, json=body)


def approval(client, action, pin=OWNER_PIN):
    r = client.post("/pos/manager-pin", headers=STAFF, json={"pin": pin, "action": action})
    assert r.status_code == 200, r.text
    return r.json()["approval"]


def xreport(client):
    return client.get("/pos/shift", params={"register": REG}, headers=OWNER).json()["shift"]


# ---------------------------------------------------------------- shift
def test_no_sale_without_an_open_shift(client):
    c = cat(client)
    r = sell(client, STAFF, [line(c, "Naan")], [{"method": "cash", "amount": 50}])
    assert r.status_code == 409 and "shift" in r.json()["detail"].lower()


def test_open_shift_once_per_till(client):
    r = client.post("/pos/shift/open", headers=STAFF, json={"register": REG, "opening_float": 2000})
    assert r.status_code == 200, r.text
    assert r.json()["opening_float"] == 2000
    assert r.json()["expected_cash"] is None            # the cashier's count stays blind
    r = client.post("/pos/shift/open", headers=STAFF, json={"register": REG, "opening_float": 0})
    assert r.status_code == 409


def test_catalogue_shows_stock_only_when_tracked(client):
    c = cat(client)
    assert c["Chicken Karahi"]["on_hand"] == 10
    assert c["Naan"]["on_hand"] is None          # not tracked: no figure, never 0


# ---------------------------------------------------------------- sales
def test_cash_sale_with_change_is_recognised_and_takes_stock(client):
    c = cat(client)
    r = sell(client, STAFF, [line(c, "Chicken Karahi", 2), line(c, "Naan", 4)], [{"method": "cash", "amount": 5000}])
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total"] == 3800 and s["status"] == "delivered" and s["recognised"]
    assert s["payment_status"] == "paid" and s["cash_tendered"] == 5000 and s["change_given"] == 1200
    # only the money kept is a payment: change is not income
    assert [p["amount"] for p in s["payments"]] == [3800]
    assert s["cashier"] == "Staff" and s["register"] == REG
    assert stock(client, "Chicken Karahi") == 8


def test_resend_returns_the_first_sale_not_a_second_charge(client):
    c = cat(client)
    body = {"client_ref": ref(), "register": REG, "items": [line(c, "Naan")], "payments": [{"method": "cash", "amount": 50}]}
    a = client.post("/pos/sale", headers=STAFF, json=body).json()
    b = client.post("/pos/sale", headers=STAFF, json=body).json()
    assert b["duplicate"] and a["number"] == b["number"] and len(b["payments"]) == 1


def test_short_payment_is_refused_and_leaves_nothing(client):
    c = cat(client)
    before = len(client.get("/orders", headers=OWNER).json()["orders"])
    r = sell(client, STAFF, [line(c, "Chicken Karahi")], [{"method": "cash", "amount": 1000}])
    assert r.status_code == 409 and "short" in r.json()["detail"]
    assert len(client.get("/orders", headers=OWNER).json()["orders"]) == before
    assert stock(client, "Chicken Karahi") == 8


def test_card_more_than_bill_is_refused(client):
    c = cat(client)
    assert sell(client, STAFF, [line(c, "Naan")], [{"method": "card", "amount": 100}]).status_code == 409


def test_split_card_and_cash(client):
    c = cat(client)
    r = sell(client, STAFF, [line(c, "Chicken Karahi")],
             [{"method": "card", "amount": 1000, "reference": "slip 42"}, {"method": "cash", "amount": 1000}])
    s = r.json()
    assert r.status_code == 200, r.text
    assert sorted((p["method"], p["amount"]) for p in s["payments"]) == [("card", 1000), ("cash", 800)]
    assert s["change_given"] == 200 and s["recognised"]


def test_scanned_barcode_product_sells(client):
    c = cat(client)
    assert c["Shampoo"]["sku"] == "8964000000017"
    r = sell(client, STAFF, [line(c, "Shampoo")], [{"method": "cash", "amount": 1000}])
    assert r.status_code == 200 and stock(client, "Shampoo") == 9


def test_item_without_price_cannot_be_sold(client):
    c = cat(client)
    r = sell(client, STAFF, [line(c, "Mystery box")], [{"method": "cash", "amount": 10}])
    assert r.status_code == 400 and "price" in r.json()["detail"]


def test_missing_reference_is_refused(client):
    c = cat(client)
    r = client.post("/pos/sale", headers=STAFF, json={"register": REG, "items": [line(c, "Naan")],
                                                      "payments": [{"method": "cash", "amount": 50}]})
    assert r.status_code == 400


# ---------------------------------------------------------------- approvals
def test_staff_discount_needs_a_manager_pin(client):
    c = cat(client)
    items = [line(c, "Naan", 2)]
    assert sell(client, STAFF, items, [{"method": "cash", "amount": 100}], discount=10).status_code == 403
    r = client.post("/pos/manager-pin", headers=STAFF, json={"pin": "9999", "action": "discount"})
    assert r.status_code == 403                          # 403, not 401: the cashier stays signed in
    r = client.post("/pos/manager-pin", headers=STAFF, json={"pin": "7391", "action": "discount"})
    assert r.status_code == 403                          # a cashier's own PIN approves nothing
    wrong = approval(client, "refund")                   # an approval is for one kind of action
    assert sell(client, STAFF, items, [{"method": "cash", "amount": 90}], discount=10, approval=wrong).status_code == 403
    r = sell(client, STAFF, items, [{"method": "cash", "amount": 90}], discount=10, approval=approval(client, "discount"))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 90


def test_owner_discounts_without_a_pin(client):
    c = cat(client)
    r = sell(client, OWNER, [line(c, "Naan", 2)], [{"method": "cash", "amount": 90}], discount=10)
    assert r.status_code == 200 and r.json()["total"] == 90


def test_staff_price_change_needs_a_pin(client):
    c = cat(client)
    items = [line(c, "Naan", 1, unit_price=40)]
    assert sell(client, STAFF, items, [{"method": "cash", "amount": 40}]).status_code == 403
    r = sell(client, STAFF, items, [{"method": "cash", "amount": 40}], approval=approval(client, "price"))
    assert r.status_code == 200 and r.json()["total"] == 40


# ---------------------------------------------------------------- open bills
def test_open_table_add_items_kitchen_then_settle(client):
    c = cat(client)
    r = sell(client, STAFF, [line(c, "Chicken Karahi", note="less spicy")], mode="dine_in", table="T4", hold=True)
    bill = r.json()
    assert r.status_code == 200 and bill["open"] and bill["table"] == "T4" and not bill["recognised"]
    assert bill["items"][0]["note"] == "less spicy"
    assert stock(client, "Chicken Karahi") == 7          # stock leaves only when paid
    t = client.get("/pos/today", headers=STAFF).json()
    assert any(b["id"] == bill["id"] and b["table"] == "T4" for b in t["open_bills"])
    k = client.post("/pos/bills/%s/kitchen" % bill["id"], headers=STAFF).json()
    assert [(l["description"], l["note"]) for l in k["lines"]] == [("Chicken Karahi", "less spicy")]
    assert client.post("/pos/bills/%s/kitchen" % bill["id"], headers=STAFF).json()["lines"] == []   # sent once
    r = client.post("/pos/bills/%s/items" % bill["id"], headers=STAFF, json={"items": [line(c, "Naan", 3)]})
    assert r.status_code == 200 and r.json()["total"] == 1950
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=STAFF,
                    json={"register": REG, "payments": [{"method": "online", "amount": 1950}]})
    s = r.json()
    assert r.status_code == 200 and s["recognised"] and not s["open"] and s["change_given"] is None
    assert stock(client, "Chicken Karahi") == 6
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=STAFF,
                    json={"register": REG, "payments": [{"method": "cash", "amount": 1}]})
    assert r.status_code == 409                           # never paid twice
    r = client.post("/pos/bills/%s/items" % bill["id"], headers=STAFF, json={"items": [line(c, "Naan")]})
    assert r.status_code == 409


def test_remove_line_free_before_kitchen_pin_after(client):
    c = cat(client)
    bill = sell(client, STAFF, [line(c, "Naan"), line(c, "Chicken Karahi")], hold=True).json()
    naan = next(i for i in bill["items"] if i["description"] == "Naan")
    r = client.post("/pos/bills/%s/lines/%s/remove" % (bill["id"], naan["id"]), headers=STAFF, json={})
    assert r.status_code == 200 and r.json()["total"] == 1800
    client.post("/pos/bills/%s/kitchen" % bill["id"], headers=STAFF)
    client.post("/pos/bills/%s/items" % bill["id"], headers=STAFF, json={"items": [line(c, "Naan")]})
    karahi = next(i for i in client.get("/pos/bills/%s" % bill["id"], headers=STAFF).json()["items"]
                  if i["description"] == "Chicken Karahi")
    path = "/pos/bills/%s/lines/%s/remove" % (bill["id"], karahi["id"])
    assert client.post(path, headers=STAFF, json={"reason": "customer changed mind"}).status_code == 403
    r = client.post(path, headers=STAFF, json={"reason": "customer changed mind", "approval": approval(client, "void")})
    assert r.status_code == 200 and r.json()["total"] == 50
    last = r.json()["items"][0]
    assert client.post("/pos/bills/%s/lines/%s/remove" % (bill["id"], last["id"]), headers=STAFF, json={}).status_code == 409
    # cancelling needs the owner, with a reason
    assert client.post("/pos/bills/%s/cancel" % bill["id"], headers=STAFF, json={"reason": "left"}).status_code == 403
    r = client.post("/pos/bills/%s/cancel" % bill["id"], headers=OWNER, json={"reason": "table left"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


# ---------------------------------------------------------------- udhaar
def test_udhaar_needs_a_named_customer_and_approval(client):
    c = cat(client)
    items = [line(c, "Chicken Karahi")]
    assert sell(client, OWNER, items, [], credit=True).status_code == 400            # no name/phone
    named = {"customer_name": "Bilal", "customer_phone": "03001234567"}
    assert sell(client, STAFF, items, [], credit=True, **named).status_code == 403   # cashier needs a PIN
    r = sell(client, STAFF, items, [{"method": "cash", "amount": 500}], credit=True,
             approval=approval(client, "credit"), **named)
    b = r.json()
    assert r.status_code == 200, r.text
    assert b["udhaar"] and not b["recognised"] and b["balance"] == 1300
    assert stock(client, "Chicken Karahi") == 6          # not a sale until fully paid
    assert any(x["id"] == b["id"] for x in client.get("/pos/bills", params={"status": "udhaar"}, headers=STAFF).json()["bills"])
    r = client.post("/pos/bills/%s/collect" % b["id"], headers=STAFF,
                    json={"register": REG, "payments": [{"method": "cash", "amount": 2000}]})
    s = r.json()
    assert r.status_code == 200 and s["recognised"] and s["balance"] == 0
    assert stock(client, "Chicken Karahi") == 5


# ---------------------------------------------------------------- tax
def test_tax_added_and_included(client):
    c = cat(client)
    assert client.put("/pos/settings", headers=STAFF, json={"tax_mode": "exclusive", "tax_rate": 10}).status_code == 403
    assert client.put("/pos/settings", headers=OWNER, json={"tax_mode": "exclusive", "tax_rate": 0}).status_code == 400
    assert client.put("/pos/settings", headers=OWNER, json={"tax_mode": "exclusive", "tax_rate": 10}).status_code == 200
    s = sell(client, OWNER, [line(c, "Naan", 20)], [{"method": "cash", "amount": 1100}]).json()
    assert s["subtotal"] == 1000 and s["tax"] == 100 and s["total"] == 1100 and s["recognised"]
    with tenant_session(T, "owner") as cur:
        cur.execute("SELECT i.tax, i.total, i.status FROM invoices i JOIN orders o ON o.invoice_id = i.id WHERE o.id = %s", (s["id"],))
        inv = cur.fetchone()
    assert float(inv["tax"]) == 100 and float(inv["total"]) == 1100 and inv["status"] == "paid"
    client.put("/pos/settings", headers=OWNER, json={"tax_mode": "inclusive", "tax_rate": 16})
    s = sell(client, OWNER, [line(c, "Naan", 20)], [{"method": "cash", "amount": 1000}]).json()
    assert s["total"] == 1000 and s["tax"] == 137.93
    client.put("/pos/settings", headers=OWNER, json={"tax_mode": "off"})
    assert sell(client, OWNER, [line(c, "Naan")], [{"method": "cash", "amount": 50}]).json()["tax"] == 0


# ---------------------------------------------------------------- refunds
def test_refund_rules_money_and_stock(client):
    c = cat(client)
    s = sell(client, OWNER, [line(c, "Chicken Karahi", 2)], [{"method": "cash", "amount": 3240}], discount=360).json()
    karahi = s["items"][0]
    path = "/pos/bills/%s/refund" % s["id"]
    body = {"register": REG, "method": "cash", "reason": "one was cold",
            "lines": [{"order_item_id": karahi["id"], "quantity": 1}]}
    assert client.post(path, headers=STAFF, json=body).status_code == 403
    before = xreport(client)["expected_cash"]
    r = client.post(path, headers=STAFF, json={**body, "approval": approval(client, "refund")})
    assert r.status_code == 200, r.text
    # its share of what was paid: 3240 for 2, so 1620 back, not the list 1800
    assert r.json()["refund"]["amount"] == 1620 and r.json()["refunded"] == 1620
    assert stock(client, "Chicken Karahi") == 4          # 5 - 2 sold + 1 back
    assert xreport(client)["expected_cash"] == round(before - 1620, 2)
    r = client.post(path, headers=OWNER, json={**body, "lines": [{"order_item_id": karahi["id"], "quantity": 2}]})
    assert r.status_code == 409                           # only 1 left to return
    r = client.post(path, headers=OWNER, json={**body, "reason": ""})
    assert r.status_code == 400
    open_bill = sell(client, STAFF, [line(c, "Naan")], hold=True).json()
    r = client.post("/pos/bills/%s/refund" % open_bill["id"], headers=OWNER,
                    json={**body, "lines": [{"order_item_id": open_bill["items"][0]["id"], "quantity": 1}]})
    assert r.status_code == 409
    client.post("/pos/bills/%s/cancel" % open_bill["id"], headers=OWNER, json={"reason": "test bill"})


def test_owner_figures_are_net_of_refunds(client):
    m = client.get("/pos/today", headers=OWNER).json()["money"]
    assert m["refunded"] == 1620 and m["collected"] == round(m["taken"] - 1620, 2)
    assert "money" not in client.get("/pos/today", headers=STAFF).json()
    with tenant_session(T, "owner") as cur:
        cur.execute("SELECT COALESCE(sum(amount), 0) AS a FROM payments")
        paid = float(cur.fetchone()["a"])
    closing = client.get("/operations/closing/preview", headers=OWNER).json()
    assert closing["expected_total"] == round(paid - 1620, 2)
    pnl = client.get("/operations/pnl", headers=OWNER).json()
    assert pnl["refunded"] == 1620 and pnl["collected"] == round(paid - 1620, 2)
    summ = client.get("/orders/summary", headers=OWNER).json()["money"]
    assert summ["refunded_month"] == 1620 and summ["collected_today"] == round(paid - 1620, 2)


# ---------------------------------------------------------------- isolation
def test_non_pos_order_is_not_a_till_bill(client):
    c = cat(client)
    o = client.post("/orders", headers=OWNER, json={"items": [line(c, "Naan")], "fulfilment": "pickup"}).json()
    assert client.get("/pos/bills/%s" % o["id"], headers=STAFF).status_code == 409


def test_other_business_cannot_see_or_touch_the_bill(client):
    c = cat(client)
    bill = sell(client, STAFF, [line(c, "Naan")], hold=True).json()
    assert client.get("/pos/bills/%s" % bill["id"], headers=OTHER).status_code == 404
    r = client.post("/pos/bills/%s/settle" % bill["id"], headers=OTHER, json={"payments": [{"method": "cash", "amount": 50}]})
    assert r.status_code in (404, 409)                    # the other business has no shift and no such bill
    client.post("/pos/bills/%s/cancel" % bill["id"], headers=OWNER, json={"reason": "test bill"})


# ---------------------------------------------------------------- close
def test_cash_moves_and_blind_close(client):
    r = client.post("/pos/shift/cash", headers=STAFF, json={"register": REG, "kind": "out", "amount": 10_000_000,
                                                            "reason": "too much"})
    assert r.status_code in (400, 409)
    r = client.post("/pos/shift/cash", headers=STAFF, json={"register": REG, "kind": "out", "amount": 500,
                                                            "reason": "bought milk"})
    assert r.status_code == 200 and r.json()["expected_cash"] is None
    x = xreport(client)
    assert x["cash_out"] == 500
    assert x["expected_cash"] == round(x["opening_float"] + x["taken_by_method"]["cash"] + x["cash_in"]
                                       - x["cash_out"] - x["refunds_by_method"].get("cash", 0), 2)
    short = round(x["expected_cash"] - 100, 2)
    r = client.post("/pos/shift/close", headers=STAFF, json={"register": REG, "counted_cash": short})
    assert r.status_code == 400 and "short" in r.json()["detail"]
    r = client.post("/pos/shift/close", headers=STAFF, json={"register": REG, "counted_cash": short, "notes": "gave wrong change"})
    z = r.json()
    assert r.status_code == 200 and z["variance"] == -100 and z["expected_cash"] == x["expected_cash"]
    assert client.get("/pos/shift", params={"register": REG}, headers=STAFF).json()["shift"] is None
    h = client.get("/pos/shifts", headers=OWNER).json()["shifts"]
    assert h[0]["variance"] == -100 and h[0]["counted_cash"] == short
    assert client.get("/pos/shifts", headers=STAFF).status_code == 403
