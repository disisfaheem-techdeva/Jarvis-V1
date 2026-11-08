"""Shop intelligence (Phase 8, app/shopintel.py, app/jarvis_shop.py).

A small shop is built through the real endpoints (purchases with costs, till
sales, a till udhaar bill, ledger udhaar, an expense), then every figure of
/shop/summary is checked against what was done. Profit is an estimate that
covers only products with a known cost, and says so. Jarvis's answers are the
platform's words, never the model's figures. Owner only; nothing crosses
businesses.

Runs against a SCRATCH database only. Makes its own businesses and deletes them.
"""
import copy
import os
import uuid

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


def _sale(c, items, pay=None, **kw):
    body = {"client_ref": uuid.uuid4().hex, "register": "Till 1", "items": items, **kw}
    body["payments"] = [] if pay is None else [{"method": "cash", "amount": pay}]
    r = c.post("/pos/sale", headers=OWNER, json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Intel Karyana', 'grocery', true, 'karyana'), (%s, 'Other Karyana', 'grocery', true, 'karyana')",
                    (K, B))
        cur.execute("INSERT INTO pos_settings (tenant_id, enabled) VALUES (%s, true)", (K,))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@intel-test.local" % U[k][:8], role))
        cur.execute("""INSERT INTO products (tenant_id, name, price, unit, track_stock, low_stock_threshold) VALUES
                       (%s, 'Sugar', 180, 'kg', true, 5), (%s, 'Flour', 160, 'kg', true, 30),
                       (%s, 'Cooking Oil 1L', 550, 'litre', true, NULL), (%s, 'Soap', 100, 'piece', false, NULL)
                       RETURNING id, name""", (K, K, K, K))
        for r in cur.fetchall():
            P[r["name"]] = str(r["id"])
    c = TestClient(app)
    assert c.post("/pos/shift/open", headers=OWNER, json={"register": "Till 1", "opening_float": 0}).status_code == 200
    # stock in at known costs (Soap has no cost anywhere)
    r = c.post("/purchases/bills", headers=OWNER, json={"supplier_name": "Bilal Traders", "items": [
        {"product_id": P["Sugar"], "quantity": 50, "unit_cost": 160},
        {"product_id": P["Flour"], "quantity": 20, "unit_cost": 150},
        {"product_id": P["Cooking Oil 1L"], "quantity": 5, "unit_cost": 500}], "paid_now": {"amount": 3000}})
    assert r.status_code == 200, r.text
    # today's sales: paid, with a product of unknown cost; and one on udhaar
    _sale(c, [{"product_id": P["Sugar"], "quantity": 10}, {"product_id": P["Soap"], "quantity": 2}], pay=2000)
    _sale(c, [{"product_id": P["Cooking Oil 1L"], "quantity": 4}], pay=2200)
    _sale(c, [{"product_id": P["Flour"], "quantity": 1}], customer_name="Ahmed", customer_phone="03001234567", credit=True)
    c.post("/ledger/entries", headers=OWNER, json={"customer_name": "Sana", "customer_phone": "03215550002",
                                                  "kind": "credit", "amount": 300})
    # dated in the shop's own day: the expenses table's default date is the database's (UTC) day
    import datetime as _dt
    from zoneinfo import ZoneInfo
    today = _dt.datetime.now(ZoneInfo("Asia/Karachi")).date().isoformat()
    r = c.post("/business/expenses", headers=OWNER, json={"description": "Bijli", "amount": 250, "spent_on": today})
    assert r.status_code in (200, 201), r.text
    yield c
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM tenants WHERE id IN (%s, %s)", (K, B))
        assert cur.fetchone()["n"] == 0


def _sum(c, period="today", h=OWNER):
    r = c.get("/shop/summary?period=" + period, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_sales_udhaar_collected_and_expenses_are_counted(client):
    d = _sum(client)
    assert d["sales"] == {"total": 4200, "count": 2}                 # 1,800 + 200 and 2,200; the udhaar bill is not a sale
    assert d["udhaar_given"]["total"] == 460 and d["udhaar_given"]["till_bills"] == 1 and d["udhaar_given"]["ledger_entries"] == 1
    assert d["collected"] == 4200 + 3000 * 0 and d["expenses"] == 250  # supplier payments are not money collected


def test_profit_is_an_estimate_that_says_what_it_covers(client):
    p = _sum(client)["profit"]
    # Sugar 10 x (180 - 160) = 200; Oil 4 x (550 - 500) = 200; Soap has no cost
    assert p["estimate"] is True and p["gross"] == 400 and p["after_expenses"] == 150
    assert p["covered_sales"] == 4000 and p["item_sales"] == 4200 and p["coverage"] == round(4000 / 4200, 3)
    assert p["products_without_cost"] == ["Soap"]


def test_best_sellers_low_stock_value_and_balances(client):
    d = _sum(client)
    assert [x["name"] for x in d["best_sellers"]["by_quantity"]] == ["Sugar", "Cooking Oil 1L", "Soap"]
    assert d["best_sellers"]["by_money"][0]["name"] == "Cooking Oil 1L"
    low = {x["name"]: x for x in d["stock"]["low"]}
    assert "Flour" in low and low["Flour"]["on_hand"] == 20 and not low["Flour"]["out"]   # warn at 30 (udhaar not yet taken out)
    assert "Sugar" not in low
    # 40 x 160 + 20 x 150 + 1 x 500
    assert d["stock"]["value"] == 9900 and d["stock"]["estimate"] is True and d["stock"]["products_without_cost"] == 0
    assert d["udhaar"] == {"total": 460, "customers": 2}
    assert d["owed_to_suppliers"] == {"total": 10500, "suppliers": 1}  # 8,000 + 3,000 + 2,500 bill less 3,000 paid


def test_reorder_suggests_what_runs_out_soon(client):
    r = _sum(client)["reorder"]
    assert r["suggestion"] is True
    oil = next(x for x in r["items"] if x["name"] == "Cooking Oil 1L")
    # 4 sold in 14 days -> 0.29/day; 1 left -> 3.5 days; cover 14 days -> 4 - 1 = 3
    assert oil["on_hand"] == 1 and oil["days_left"] == 3.5 and oil["suggest"] == 3
    assert all(x["name"] != "Sugar" for x in r["items"])                  # 40 left lasts 56 days


def test_periods_and_access(client):
    assert _sum(client, "week")["sales"]["total"] == 4200 and _sum(client, "month")["sales"]["count"] == 2
    assert client.get("/shop/summary?period=year", headers=OWNER).status_code == 400
    assert client.get("/shop/summary", headers=CASHIER).status_code == 403
    other = _sum(client, h=B_OWNER)
    assert other["sales"]["total"] == 0 and other["stock"]["low"] == [] and other["udhaar"]["total"] == 0


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


def _ask(client, ai, args, h=OWNER):
    ai["out"] = {"kind": "action", "action": "shop_report", "args": args, "reply": "Aaj 99999 ki sale hui."}
    return client.post("/jarvis/v1/messages", json={"text": "x"}, headers=h).json()


def test_jarvis_says_the_day_from_the_records(client, ai, loopback):
    d = _ask(client, ai, {"topic": "summary"})
    t = d["reply"]
    assert d["kind"] == "done" and "99999" not in t
    assert "Aaj ki sale Rs 4,200 (2 bills)" in t and "udhaar diya Rs 460" in t and "kharcha Rs 250" in t
    assert "andaza munafa Rs 400 (95% sale par)" in t and "Flour" in t and "Customers ka udhaar Rs 460" in t
    assert "suppliers ko Rs 10,500 dena hai" in t


def test_jarvis_profit_best_low_reorder_and_value(client, ai, loopback):
    t = _ask(client, ai, {"topic": "profit"})["reply"]
    assert "andaza munafa Rs 400" in t and "kharche ke baad Rs 150" in t and "Soap" in t and "99999" not in t
    t = _ask(client, ai, {"topic": "best_sellers"})["reply"]
    assert t.startswith("Pichle 7 din") and "Sugar 10 kg (Rs 1,800)" in t
    t = _ask(client, ai, {"topic": "low_stock"})["reply"]
    assert "Flour 20 kg baqi" in t
    t = _ask(client, ai, {"topic": "reorder"})["reply"]
    assert "Cooking Oil 1L 3.5 din mein khatam, 3 litre mangwa lein" in t
    t = _ask(client, ai, {"topic": "stock_value"})["reply"]
    assert "Rs 9,900" in t and "andazan" in t


def test_jarvis_shop_figures_are_the_owners(client, ai, loopback):
    d = _ask(client, ai, {"topic": "summary"}, h=CASHIER)
    assert "owner" in d["reply"].lower() and "4,200" not in d["reply"]
