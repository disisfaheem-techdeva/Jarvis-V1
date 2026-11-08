"""Suppliers and purchases (Phase 6, db/95, app/purchases.py, app/jarvis_purchase.py).

Checked: a purchase bill puts the stock in with its unit cost and is owed to the
supplier; part-paid at once; a resend is the first bill; an unknown product is
refused, never made up; payments are never more than owed; a bill is voided
(stock back out, amount off) and never edited or deleted, even by SQL; ledger
entries are append-only; P&L shows purchases beside net without changing net;
owner only; nothing crosses businesses; Jarvis asks first, matches Urdu names and
units, and says only the platform's figures.

Runs against a SCRATCH database only. Makes its own businesses and deletes them.
"""
import copy
import os
import uuid

import psycopg
import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import console, jarvis_actions as ja  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

K, B = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "cashier", "b_owner")}
P = {}


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, CASHIER, B_OWNER = (_bearer(K, U["owner"], "owner"), _bearer(K, U["cashier"], "operator"),
                           _bearer(B, U["b_owner"], "owner"))


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Purchase Karyana', 'grocery', true, 'karyana'), (%s, 'Other Karyana', 'grocery', true, 'karyana')",
                    (K, B))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@purchase-test.local" % U[k][:8], role))
        cur.execute("""INSERT INTO products (tenant_id, name, price, unit, aliases, track_stock) VALUES
                       (%s, 'Sugar', 180, 'kg', '{cheeni,chini}', false),
                       (%s, 'Flour', 160, 'kg', '{aata}', true),
                       (%s, 'Soap', 100, 'piece', '{sabun}', false) RETURNING id, name""", (K, K, K))
        for r in cur.fetchall():
            P[r["name"]] = str(r["id"])
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM purchase_bills WHERE tenant_id IN (%s, %s)", (K, B))
        assert cur.fetchone()["n"] == 0


def _stock(name):
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT COALESCE(sum(quantity_delta), 0) AS q FROM stock_movements WHERE product_id = %s", (P[name],))
        return float(cur.fetchone()["q"])


def _sup(client, name="Bilal Traders"):
    o = client.get("/purchases", headers=OWNER).json()
    return next(s for s in o["suppliers"] if s["name"] == name)


def _bill(client, h=OWNER, **kw):
    body = dict(supplier_name="Bilal Traders", items=[{"product_id": P["Sugar"], "quantity": 50, "unit_cost": 160},
                                                      {"name": "aata", "quantity": 20, "line_total": 3000}], **kw)
    return client.post("/purchases/bills", headers=h, json=body)


# ---------------------------------------------------------------- bills
def test_a_bill_puts_stock_in_at_its_cost_and_is_owed(client):
    r = _bill(client, supplier_ref="BT-778", paid_now={"amount": 3000, "method": "cash"}, bought_on="2026-11-08")
    assert r.status_code == 200, r.text
    b = r.json()["bill"]
    assert b["number"] == "PB-0001" and b["total"] == 11000 and b["supplier_ref"] == "BT-778"
    assert {(i["name"], i["quantity"], i["unit_cost"]) for i in b["items"]} == {("Sugar", 50, 160), ("Flour", 20, 150)}
    assert _stock("Sugar") == 50 and _stock("Flour") == 20
    s = r.json()["supplier"]
    assert s["owed"] == 8000 and s["bills"] == 1                             # 11,000 less 3,000 paid now
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT unit_cost, kind, reference FROM stock_movements WHERE product_id = %s", (P["Sugar"],))
        m = cur.fetchone()
        assert float(m["unit_cost"]) == 160 and m["kind"] == "purchase" and m["reference"] == "PB-0001"
        cur.execute("SELECT track_stock FROM products WHERE id = %s", (P["Sugar"],))
        assert cur.fetchone()["track_stock"] is True                          # bought stock is kept stock


def test_a_resend_is_the_first_bill(client):
    ref = "web" + uuid.uuid4().hex
    a = _bill(client, client_ref=ref).json()
    b = _bill(client, client_ref=ref).json()
    assert b["duplicate"] and a["bill"]["id"] == b["bill"]["id"]
    assert _sup(client)["owed"] == 19000 and _stock("Sugar") == 100


def test_unknown_items_and_bad_figures_are_refused(client):
    r = client.post("/purchases/bills", headers=OWNER, json={"supplier_name": "Bilal Traders",
                                                           "items": [{"name": "chawal", "quantity": 10, "unit_cost": 300}]})
    assert r.status_code == 400 and "chawal" in r.json()["detail"] and "Products" in r.json()["detail"]
    for items in ([{"product_id": P["Soap"], "quantity": 0, "unit_cost": 5}], [{"product_id": P["Soap"], "quantity": 2}],
                  [{"product_id": P["Soap"], "quantity": 2, "unit_cost": -1}], []):
        r = client.post("/purchases/bills", headers=OWNER, json={"supplier_name": "Bilal Traders", "items": items})
        assert r.status_code == 400, items
    r = _bill(client, paid_now={"amount": 99999})
    assert r.status_code == 400 and _sup(client)["owed"] == 19000


def test_payments_are_never_more_than_owed(client):
    sid = _sup(client)["supplier_id"]
    r = client.post("/purchases/payments", headers=OWNER, json={"supplier_id": sid, "amount": 19001})
    assert r.status_code == 409 and "more than owed" in r.json()["detail"]
    r = client.post("/purchases/payments", headers=OWNER, json={"supplier_id": sid, "amount": 4000, "method": "bank"})
    assert r.status_code == 200 and r.json()["supplier"]["owed"] == 15000


def test_a_bill_is_voided_never_edited_or_deleted(client):
    bid = _bill(client).json()["bill"]["id"]
    assert _sup(client)["owed"] == 26000 and _stock("Sugar") == 150
    for sql in ("UPDATE purchase_bills SET total = 1 WHERE id = %s", "DELETE FROM purchase_bills WHERE id = %s",
                "UPDATE purchase_bill_items SET quantity = 1 WHERE bill_id = %s",
                "DELETE FROM supplier_ledger_entries WHERE bill_id = %s"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_session(K, "owner") as cur:
                cur.execute(sql, (bid,))
    assert client.post("/purchases/bills/%s/void" % bid, headers=OWNER, json={}).status_code == 400
    r = client.post("/purchases/bills/%s/void" % bid, headers=OWNER, json={"reason": "entered twice"})
    assert r.status_code == 200 and r.json()["bill"]["voided_at"] and r.json()["bill"]["void_reason"] == "entered twice"
    assert _sup(client)["owed"] == 15000 and _stock("Sugar") == 100 and _stock("Flour") == 40
    assert client.post("/purchases/bills/%s/void" % bid, headers=OWNER, json={"reason": "again"}).status_code == 409
    st = client.get("/purchases/suppliers/" + _sup(client)["supplier_id"], headers=OWNER).json()
    assert st["lines"][-1]["running"] == 15000 and any(x["kind"] == "adjustment" and x["bill_id"] == bid for x in st["lines"])
    # a bill line in the statement is voided, not reversed on its own
    bill_line = next(x for x in st["lines"] if x["kind"] == "bill")
    r = client.post("/purchases/entries/%s/reverse" % bill_line["id"], headers=OWNER, json={"reason": "x"})
    assert r.status_code == 409 and "Void the bill" in r.json()["detail"]


def test_opening_balance_and_reversal(client):
    r = client.post("/purchases/openings", headers=OWNER, json={"supplier_name": "Karachi Ghee Co", "amount": 12000,
                                                              "note": "Old register"})
    assert r.status_code == 200 and r.json()["supplier"]["owed"] == 12000
    sid = r.json()["supplier"]["supplier_id"]
    line = client.get("/purchases/suppliers/" + sid, headers=OWNER).json()["lines"][0]
    r = client.post("/purchases/entries/%s/reverse" % line["id"], headers=OWNER, json={"reason": "wrong supplier"})
    assert r.status_code == 200 and r.json()["supplier"]["owed"] == 0


def test_pnl_shows_purchases_beside_net_not_inside_it(client):
    p = client.get("/operations/pnl?start=2026-11-01&end=2026-11-30", headers=OWNER).json()
    assert p["purchases"]["stock_bought"] == 22000                # two live bills of 11,000 (one voided)
    assert p["purchases"]["paid_to_suppliers"] == 7000            # 3,000 with the first bill + 4,000 by bank
    assert p["purchases"]["owed_to_suppliers"] == 15000
    assert p["net"] == round(p["collected"] - p["expenses"], 2)   # net is exactly what it was


def test_owner_only_and_businesses_stay_apart(client):
    assert client.get("/purchases", headers=CASHIER).status_code == 403
    assert _bill(client, h=CASHIER).status_code == 403
    assert client.get("/purchases", headers=B_OWNER).json()["suppliers"] == []
    sid = _sup(client)["supplier_id"]
    assert client.get("/purchases/suppliers/" + sid, headers=B_OWNER).status_code == 404
    assert client.post("/purchases/payments", headers=B_OWNER, json={"supplier_id": sid, "amount": 1}).status_code == 404
    r = client.post("/purchases/bills", headers=B_OWNER, json={"supplier_name": "X", "items": [{"product_id": P["Soap"], "quantity": 1, "unit_cost": 1}]})
    assert r.status_code == 400                                   # another business's product is not on its list


# ---------------------------------------------------------------- Jarvis
@pytest.fixture()
def ai(monkeypatch):
    said = {"out": {"kind": "question", "reply": "Hello"}}
    monkeypatch.setattr(console, "generate_json", lambda system, prompt, required_keys=(), _retry=True: copy.deepcopy(said["out"]))
    console._FACTS_CACHE.clear()
    return said


@pytest.fixture()
def loopback(client, monkeypatch):
    monkeypatch.setattr(ja.requests, "request", lambda m, url, json=None, timeout=None, headers=None:
                        client.request(m, url.replace(ja.SELF_URL, ""), json=json, headers=headers))
    ja._USED.clear()


def _say(client, ai, action, args, h=OWNER, reply="Total 99999 hua, likh doon?"):
    ai["out"] = {"kind": "action", "action": action, "args": args, "reply": reply}
    return client.post("/jarvis/v1/messages", json={"text": "x"}, headers=h).json()


def test_jarvis_reads_the_purchase_back_and_records_it_once(client, ai, loopback):
    owed, sugar = _sup(client)["owed"], _stock("Sugar")
    d = _say(client, ai, "record_purchase", {"supplier": "Bilal Traders", "paid": 1000,
                                             "items": [{"said": "cheeni", "qty": 25, "unit": "kilo", "unit_cost": 165},
                                                       {"said": "sabun", "qty": 2, "unit": "dozen", "line_total": 2160}]})
    assert d["kind"] == "confirm", d
    q = d["reply"]
    assert "99999" not in q and "Sugar 25 kg @ Rs 165 = Rs 4,125" in q and "Soap 24 @ Rs 90 = Rs 2,160" in q
    assert "Total Rs 6,285" in q and "Rs 1,000 abhi diye" in q
    assert ("Rs {:,.0f} se Rs {:,.0f}".format(owed, owed + 6285 - 1000)) in q
    assert _stock("Sugar") == sugar                                          # nothing before the yes
    tok = {"token": d["confirmation"]["token"]}
    r = client.post("/jarvis/v1/confirmations", headers=OWNER, json=tok).json()
    assert r["kind"] == "done" and "PB-" in r["reply"] and "99999" not in r["reply"]
    assert client.post("/jarvis/v1/confirmations", headers=OWNER, json=tok).status_code == 409
    assert _stock("Sugar") == sugar + 25 and _sup(client)["owed"] == owed + 5285


def test_jarvis_asks_for_a_cost_and_refuses_unknown_items(client, ai, loopback):
    d = _say(client, ai, "record_purchase", {"supplier": "Bilal Traders", "items": [{"said": "cheeni", "qty": 5}]})
    assert d["kind"] == "answer" and "cost" in d["reply"].lower()
    d = _say(client, ai, "record_purchase", {"supplier": "Bilal Traders", "items": [{"said": "chawal", "qty": 5, "unit_cost": 1}]})
    assert d["kind"] == "answer" and "chawal" in d["reply"]
    d = _say(client, ai, "record_purchase", {"supplier": "Naya Wholesale", "items": [{"said": "aata", "qty": 1, "unit_cost": 140}]})
    assert d["kind"] == "confirm" and "naya supplier" in d["reply"]


def test_jarvis_supplier_payment_and_balance(client, ai, loopback):
    owed = _sup(client)["owed"]
    d = _say(client, ai, "supplier_payment", {"supplier": "Bilal Traders", "amount": owed + 1})
    assert d["kind"] == "answer" and "more than owed" in d["reply"]
    d = _say(client, ai, "supplier_payment", {"supplier": "Bilal Traders", "amount": 5000, "method": "jazzcash"})
    assert d["kind"] == "confirm"
    r = client.post("/jarvis/v1/confirmations", headers=OWNER, json={"token": d["confirmation"]["token"]}).json()
    assert r["kind"] == "done" and _sup(client)["owed"] == owed - 5000
    d = _say(client, ai, "supplier_balance", {"supplier": "Bilal Traders"}, reply="99999 dena hai.")
    assert d["kind"] == "done" and "Rs {:,.0f}".format(owed - 5000) in d["reply"] and "99999" not in d["reply"]
    d = _say(client, ai, "supplier_balance", {}, reply="99999.")
    assert "Suppliers ko total" in d["reply"] and "99999" not in d["reply"]
    d = _say(client, ai, "supplier_balance", {"supplier": "Bilal"}, h=CASHIER)
    assert "owner" in d["reply"].lower()
