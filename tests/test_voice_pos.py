"""Voice POS (Phase 4, 8 Nov 2026): "Jarvis, dhai kilo cheeni aur do kilo aata ka
bill bana do" becomes a till sale, and only after a yes.

The model is a fake and is only allowed to say WHAT was asked for (item words,
quantities, units). Everything else is checked to come from the platform: which
product each word means, its price, the total read back (with the shop's tax),
stock, and the sale itself, made once through POST /pos/sale with a reference
fixed when the bill was read out.

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

K, OFF = str(uuid.uuid4()), str(uuid.uuid4())     # a karyana with the POS on; one with it off
U = {k: str(uuid.uuid4()) for k in ("owner", "cashier", "off_owner")}


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, CASHIER, OFF_OWNER = (_bearer(K, U["owner"], "owner"), _bearer(K, U["cashier"], "operator"),
                             _bearer(OFF, U["off_owner"], "owner"))


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Voice POS Karyana', 'grocery', true, 'karyana'), (%s, 'No POS Karyana', 'grocery', true, 'karyana')",
                    (K, OFF))
        cur.execute("INSERT INTO pos_settings (tenant_id, enabled) VALUES (%s, true)", (K,))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("off_owner", OFF, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@voicepos-test.local" % U[k][:8], role))
        for tid in (K, OFF):
            cur.execute("""INSERT INTO products (tenant_id, name, price, track_stock, unit, aliases) VALUES
                           (%s, 'Sugar', 180, true, 'kg', '{cheeni,chini}'),
                           (%s, 'Flour', 160, true, 'kg', '{aata,atta}'),
                           (%s, 'Cooking Oil 1L', 550, false, 'litre', '{tel,ghee}'),
                           (%s, 'Desi Ghee 1kg', 1900, false, 'pack', '{ghee}'),
                           (%s, 'Soap', 100, false, 'piece', '{sabun}'),
                           (%s, 'Mystery', NULL, false, 'piece', '{}') RETURNING id, name""", (tid,) * 6)
            ids = {r["name"]: r["id"] for r in cur.fetchall()}
            for name in ("Sugar", "Flour"):
                cur.execute("INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta) VALUES (%s, %s, 'opening', 10)",
                            (tid, ids[name]))
    c = TestClient(app)
    assert c.post("/pos/shift/open", headers=OWNER, json={"register": "Till 1", "opening_float": 0}).status_code == 200
    yield c
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, OFF):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM tenants WHERE id IN (%s, %s)", (K, OFF))
        assert cur.fetchone()["n"] == 0


@pytest.fixture()
def ai(monkeypatch):
    said = {"out": {"kind": "question", "reply": "Hello"}}
    # a fresh answer each call, as a real model's JSON is: the platform fills args in place
    monkeypatch.setattr(console, "generate_json", lambda system, prompt, required_keys=(), _retry=True: copy.deepcopy(said["out"]))
    console._FACTS_CACHE.clear()
    return said


@pytest.fixture(autouse=True)
def loopback(client, monkeypatch):
    monkeypatch.setattr(ja.requests, "request", lambda m, url, json=None, timeout=None, headers=None:
                        client.request(m, url.replace(ja.SELF_URL, ""), json=json, headers=headers))
    ja._USED.clear()


# The model's own reply is a question with a wrong total: exactly what must never be said.
def _bill(ai, items, reply="Total 999 rupay hua, bill bana doon?", **args):
    ai["out"] = {"kind": "action", "action": "pos_bill", "args": dict(items=items, **args), "reply": reply}


def _stock(client):
    r = client.get("/pos/catalogue", headers=OWNER).json()["items"]
    return {i["name"]: i["on_hand"] for i in r}


def _say(client, text, h=OWNER):
    return client.post("/jarvis/v1/messages", json={"text": text}, headers=h)


def _yes(client, d, h=OWNER):
    return client.post("/jarvis/v1/confirmations", json={"token": d["confirmation"]["token"], "said": "haan"}, headers=h)


def _till_sales(tid=K):
    with tenant_session(tid, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM orders WHERE source = 'pos'")
        return cur.fetchone()["n"]


# ---------------------------------------------------------------- the bill
def test_bill_is_read_back_from_the_list_not_from_the_model(client, ai):
    _bill(ai, [{"said": "cheeni", "qty": 2.5, "unit": "kg"}, {"said": "aata", "qty": 2, "unit": "kg"},
               {"said": "sabun", "qty": 2}])
    r = _say(client, "dhai kilo cheeni, do kilo aata aur do sabun ka bill bana do")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "confirm" and d["confirmation"]["action"] == "pos_bill"
    q = d["reply"]
    assert "999" not in q                                    # the model's own total is never said
    assert "Sugar 2.5 kg" in q and "Flour 2 kg" in q and "Soap 2" in q
    assert "Rs 970" in q                                     # 450 + 320 + 200, from the list
    assert q.rstrip().endswith("?")
    assert _till_sales() == 0                                # nothing sold before the yes


def test_yes_sells_once_and_takes_stock_once(client, ai):
    before, n = _stock(client), _till_sales()
    _bill(ai, [{"said": "cheeni", "qty": 1, "unit": "kg"}])
    d = _say(client, "ek kilo cheeni").json()
    r = _yes(client, d)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "done" and out["action"]["ok"] and "Rs 180" in out["reply"]
    sale = out["data"]["sale"]
    assert sale["id"] and sale["number"] and sale["number"] in out["reply"]      # the till opens this receipt
    assert client.get("/pos/bills/" + sale["id"], headers=OWNER).json()["total"] == 180
    assert _till_sales() == n + 1 and _stock(client)["Sugar"] == before["Sugar"] - 1
    again = _yes(client, d)                                  # double tap
    assert again.status_code == 409
    assert _till_sales() == n + 1 and _stock(client)["Sugar"] == before["Sugar"] - 1


def test_the_same_yes_after_a_restart_still_sells_only_once(client, ai):
    n = _till_sales()
    _bill(ai, [{"said": "soap", "qty": 1}])
    d = _say(client, "ek sabun").json()
    assert _yes(client, d).status_code == 200
    ja._USED.clear()                                         # restart, and Redis forgot too (worst case)
    ja._REDIS = None
    r = _yes(client, d)
    # the sale reference sealed with the bill makes the POS return the first sale
    assert r.status_code in (200, 409)
    assert _till_sales() == n + 1


def test_grams_are_turned_into_kilos(client, ai):
    _bill(ai, [{"said": "cheeni", "qty": 500, "unit": "g"}])
    q = _say(client, "aadha kilo cheeni").json()["reply"]
    assert "Sugar 0.5 kg" in q and "Rs 90" in q


# ---------------------------------------------------------------- never a guess
def test_a_word_two_products_share_is_asked_about(client, ai):
    _bill(ai, [{"said": "ghee", "qty": 1}])
    d = _say(client, "ek ghee").json()
    assert d["kind"] == "answer" and "Cooking Oil 1L" in d["reply"] and "Desi Ghee 1kg" in d["reply"]
    assert "confirmation" not in d


def test_an_unknown_item_or_no_price_is_said_and_nothing_is_offered(client, ai):
    _bill(ai, [{"said": "chawal", "qty": 1, "unit": "kg"}, {"said": "cheeni", "qty": 1}])
    d = _say(client, "chawal aur cheeni").json()
    assert d["kind"] == "answer" and "chawal" in d["reply"] and "confirmation" not in d
    _bill(ai, [{"said": "mystery", "qty": 1}])
    d = _say(client, "mystery").json()
    assert d["kind"] == "answer" and "price" in d["reply"].lower()


def test_a_unit_that_does_not_fit_is_asked_about(client, ai):
    _bill(ai, [{"said": "sabun", "qty": 2, "unit": "kg"}])
    d = _say(client, "do kilo sabun").json()
    assert d["kind"] == "answer" and "Soap" in d["reply"] and "confirmation" not in d


def test_not_enough_stock_is_said_before_anything_is_sold(client, ai):
    _bill(ai, [{"said": "aata", "qty": 500, "unit": "kg"}])
    d = _say(client, "500 kilo aata").json()
    assert d["kind"] == "answer" and "Flour" in d["reply"] and "confirmation" not in d


def test_a_price_change_after_the_read_back_stops_the_sale(client, ai):
    n = _till_sales()
    _bill(ai, [{"said": "tel", "qty": 1}])
    d = _say(client, "ek tel").json()
    with tenant_session(K, "owner") as cur:
        cur.execute("UPDATE products SET price = 600 WHERE name = 'Cooking Oil 1L'")
    try:
        r = _yes(client, d)
        assert r.status_code == 200 and r.json()["action"]["ok"] is False and "price" in r.json()["reply"].lower()
        assert _till_sales() == n
    finally:
        with tenant_session(K, "owner") as cur:
            cur.execute("UPDATE products SET price = 550 WHERE name = 'Cooking Oil 1L'")


# ---------------------------------------------------------------- who and where
def test_a_cashier_can_bill_by_voice(client, ai):
    n = _till_sales()
    _bill(ai, [{"said": "sabun", "qty": 1}])
    d = _say(client, "ek sabun", CASHIER).json()
    assert d["kind"] == "confirm", d["reply"]
    assert _yes(client, d, CASHIER).status_code == 200 and _till_sales() == n + 1


def test_no_till_no_voice_bill(client, ai):
    _bill(ai, [{"said": "cheeni", "qty": 1}])
    d = _say(client, "ek kilo cheeni", OFF_OWNER).json()
    assert d["kind"] == "answer" and "confirmation" not in d and _till_sales(OFF) == 0


def test_no_open_shift_is_said(client, ai):
    _bill(ai, [{"said": "cheeni", "qty": 1}], register="Till 2")
    d = _say(client, "ek kilo cheeni").json()
    assert d["kind"] == "answer" and "shift" in d["reply"].lower()


def test_a_bill_said_at_a_till_goes_on_that_till(client, ai):
    """The till sends its register with each message (context.register)."""
    _bill(ai, [{"said": "cheeni", "qty": 1}])
    d = client.post("/jarvis/v1/messages", headers=OWNER,
                    json={"text": "ek kilo cheeni", "context": {"register": "Till 2"}}).json()
    assert d["kind"] == "answer" and "Till 2" in d["reply"]          # Till 2 has no shift open
    d = client.post("/jarvis/v1/messages", headers=OWNER,
                    json={"text": "ek kilo cheeni", "context": {"register": "Till 1"}}).json()
    assert d["kind"] == "confirm", d["reply"]
    # a register the model heard wins over the till's own
    _bill(ai, [{"said": "cheeni", "qty": 1}], register="Till 2")
    d = client.post("/jarvis/v1/messages", headers=OWNER,
                    json={"text": "till 2 pe ek kilo cheeni", "context": {"register": "Till 1"}}).json()
    assert d["kind"] == "answer" and "Till 2" in d["reply"]


def test_the_till_address_reaches_jarvis(client, ai):
    _bill(ai, [{"said": "sabun", "qty": 1}])
    r = client.post("/jarvis/v1/messages", headers=dict(OWNER, host="pos.techdeva.net"), json={"text": "ek sabun"})
    assert r.status_code == 200 and r.json()["kind"] == "confirm"


# ---------------------------------------------------------------- stock question
def test_stock_question_is_answered_from_the_ledger(client, ai):
    ai["out"] = {"kind": "action", "action": "stock_check", "args": {"item": "cheeni"}, "reply": "Aap ke paas 9999 kg hai."}
    d = _say(client, "meri dukaan mein kitni cheeni baqi hai?").json()
    s = _stock(client)["Sugar"]
    assert d["kind"] == "done" and "9999" not in d["reply"]
    assert ("%g kg" % s) in d["reply"] and "Sugar" in d["reply"]
    ai["out"] = {"kind": "action", "action": "stock_check", "args": {"item": "tel"}, "reply": "x"}
    d = _say(client, "tel kitna hai").json()
    assert "not tracked" in d["reply"].lower() or "track" in d["reply"].lower()
