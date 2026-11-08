"""Udhaar ledger (Phase 5, db/94, app/ledger.py, app/jarvis_udhaar.py).

Checked: entries are append-only in the database itself; a mistake is undone by
a reversing entry; a resend is the first entry; the balance adds the ledger and
the till's own udhaar bills; a payment settles only the ledger part and is never
more than owed; money pages are the owner's; nothing crosses businesses; Jarvis
asks first, asks "which one?" for two Ahmeds, and says only the ledger's figures.

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


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, CASHIER, B_OWNER = (_bearer(K, U["owner"], "owner"), _bearer(K, U["cashier"], "operator"),
                           _bearer(B, U["b_owner"], "owner"))
C = {}


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Udhaar Karyana', 'grocery', true, 'karyana'), (%s, 'Other Karyana', 'grocery', true, 'karyana')",
                    (K, B))
        cur.execute("INSERT INTO pos_settings (tenant_id, enabled) VALUES (%s, true)", (K,))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@udhaar-test.local" % U[k][:8], role))
        cur.execute("""INSERT INTO customers (tenant_id, name, phone) VALUES
                       (%s, 'Ahmed', '03001234567'), (%s, 'Ahmed', '03219876543'), (%s, 'Bilal', '03335550001')
                       RETURNING id, phone""", (K, K, K))
        for r in cur.fetchall():
            C[r["phone"]] = str(r["id"])
        cur.execute("INSERT INTO products (tenant_id, name, price) VALUES (%s, 'Sugar', 180)", (K,))
    c = TestClient(app)
    assert c.post("/pos/shift/open", headers=OWNER, json={"register": "Till 1", "opening_float": 0}).status_code == 200
    yield c
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))      # cascades through the ledger
        cur.execute("SELECT count(*) AS n FROM customer_ledger_entries WHERE tenant_id IN (%s, %s)", (K, B))
        assert cur.fetchone()["n"] == 0


AHMED1, AHMED2, BILAL = "03001234567", "03219876543", "03335550001"


def _add(client, phone, kind, amount, h=OWNER, **kw):
    return client.post("/ledger/entries", headers=h, json=dict(customer_id=C[phone], kind=kind, amount=amount, **kw))


def _bal(client, phone):
    return client.get("/ledger/customers/" + C[phone], headers=OWNER).json()["customer"]


# ---------------------------------------------------------------- the ledger
def test_opening_credit_payment_and_the_running_balance(client):
    assert _add(client, AHMED1, "opening", 2000, note="Old register page 4", occurred_on="2026-10-01").status_code == 200
    assert _add(client, AHMED1, "credit", 1500, note="Atta, ghee").status_code == 200
    r = _add(client, AHMED1, "payment", 1000, method="jazzcash")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["amount"] == -1000 and r.json()["customer"]["balance"] == 2500
    s = client.get("/ledger/customers/" + C[AHMED1], headers=OWNER).json()
    assert [(x["kind"], x["amount"], x["running"]) for x in s["lines"]] == [
        ("opening", 2000, 2000), ("credit", 1500, 3500), ("payment", -1000, 2500)]
    assert s["lines"][0]["note"] == "Old register page 4"


def test_a_resend_is_the_first_entry_not_a_second(client):
    ref = "web" + uuid.uuid4().hex
    a = _add(client, BILAL, "credit", 700, client_ref=ref).json()
    b = _add(client, BILAL, "credit", 700, client_ref=ref).json()
    assert b["duplicate"] and a["entry"]["id"] == b["entry"]["id"] and b["customer"]["balance"] == 700


def test_bad_entries_are_refused(client):
    for body in ({"kind": "credit", "amount": 0}, {"kind": "credit", "amount": -5}, {"kind": "gift", "amount": 5},
                 {"kind": "payment", "amount": 5, "method": "barter"}, {"kind": "credit", "amount": "lots"},
                 {"kind": "credit", "amount": 5, "occurred_on": "2099-01-01"}):
        r = client.post("/ledger/entries", headers=OWNER, json=dict(customer_id=C[BILAL], **body))
        assert r.status_code == 400, (body, r.text)


def test_entries_cannot_be_edited_or_deleted_even_by_sql(client):
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT id FROM customer_ledger_entries WHERE customer_id = %s LIMIT 1", (C[AHMED1],))
        eid = cur.fetchone()["id"]
    for sql in ("UPDATE customer_ledger_entries SET amount = 1 WHERE id = %s",
                "DELETE FROM customer_ledger_entries WHERE id = %s"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_session(K, "owner") as cur:
                cur.execute(sql, (eid,))
    with pytest.raises(psycopg.Error):                       # a customer with entries cannot be deleted on its own
        with tenant_session(K, "owner") as cur:
            cur.execute("DELETE FROM customers WHERE id = %s", (C[AHMED1],))
    assert _bal(client, AHMED1)["balance"] == 2500


def test_a_mistake_is_reversed_and_both_stay(client):
    wrong = _add(client, BILAL, "credit", 9000, note="typed one zero too many").json()["entry"]["id"]
    r = client.post("/ledger/entries/%s/reverse" % wrong, headers=OWNER, json={"reason": "wrong amount"})
    assert r.status_code == 200 and r.json()["customer"]["balance"] == 700
    assert client.post("/ledger/entries/%s/reverse" % wrong, headers=OWNER, json={"reason": "again"}).status_code == 409
    rev = r.json()["entry"]["id"]
    assert client.post("/ledger/entries/%s/reverse" % rev, headers=OWNER, json={"reason": "x"}).status_code == 409
    assert client.post("/ledger/entries/%s/reverse" % rev, headers=OWNER, json={}).status_code in (400, 409)
    lines = client.get("/ledger/customers/" + C[BILAL], headers=OWNER).json()["lines"]
    assert any(x["id"] == wrong and x["reversed"] for x in lines) and any(x.get("reverses") == wrong for x in lines)


def test_a_payment_is_never_more_than_the_ledger_owes(client):
    r = _add(client, BILAL, "payment", 701)
    assert r.status_code == 409 and "more than owed" in r.json()["detail"]
    assert _bal(client, BILAL)["balance"] == 700


def test_till_udhaar_is_in_the_balance_and_a_payment_never_settles_it(client):
    r = client.post("/pos/sale", headers=OWNER, json={
        "client_ref": uuid.uuid4().hex, "register": "Till 1", "items": [{"name": "Sugar", "quantity": 5}],
        "customer_name": "Bilal", "customer_phone": BILAL, "credit": True, "payments": []})
    assert r.status_code == 200, r.text
    b = _bal(client, BILAL)
    assert b["till"] == 900 and b["ledger"] == 700 and b["balance"] == 1600 and b["till_bills"] == 1
    lines = client.get("/ledger/customers/" + C[BILAL], headers=OWNER).json()["lines"]
    assert any(x["type"] == "till_bill" and x["amount"] == 900 for x in lines) and lines[-1]["running"] == 1600
    r = _add(client, BILAL, "payment", 1000)
    assert r.status_code == 409 and "till" in r.json()["detail"].lower() and "Rs 700" in r.json()["detail"]
    assert _add(client, BILAL, "payment", 700).status_code == 200
    assert _bal(client, BILAL)["balance"] == 900                      # the till bill is still owed


def test_overview_lists_who_owes_most_first(client):
    o = client.get("/ledger", headers=OWNER).json()
    names = [(c["name"], c["balance"]) for c in o["customers"]]
    assert names[0] == ("Ahmed", 2500) and ("Bilal", 900) in names
    assert o["totals"]["owed"] == 3400 and o["totals"]["customers_owing"] == 2
    assert [c["phone"] for c in client.get("/ledger?q=1234", headers=OWNER).json()["customers"]] == [AHMED1]


def test_money_pages_are_the_owners_and_businesses_stay_apart(client):
    assert client.get("/ledger", headers=CASHIER).status_code == 403
    assert _add(client, BILAL, "credit", 100, h=CASHIER).status_code == 403
    assert client.get("/ledger", headers=B_OWNER).json()["customers"] == []
    assert client.get("/ledger/customers/" + C[BILAL], headers=B_OWNER).status_code == 404
    assert _add(client, BILAL, "credit", 100, h=B_OWNER).status_code == 404
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT id FROM customer_ledger_entries LIMIT 1")
        eid = str(cur.fetchone()["id"])
    assert client.post("/ledger/entries/%s/reverse" % eid, headers=B_OWNER, json={"reason": "x"}).status_code == 404


def test_a_new_customer_is_made_by_name_and_phone(client):
    r = client.post("/ledger/entries", headers=OWNER, json={"customer_name": "Zara Khan", "customer_phone": "0300 777 8888",
                                                          "kind": "opening", "amount": 450})
    assert r.status_code == 200 and r.json()["customer"]["name"] == "Zara Khan" and r.json()["customer"]["balance"] == 450
    r = client.post("/ledger/entries", headers=OWNER, json={"customer_name": "Ahmed", "kind": "credit", "amount": 5})
    assert r.status_code == 409 and "2 customers called Ahmed" in r.json()["detail"]


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


def _say(client, ai, action, args, h=OWNER, reply="Ahmed ka baqi 99999 hai, likh doon?"):
    ai["out"] = {"kind": "action", "action": action, "args": args, "reply": reply}
    return client.post("/jarvis/v1/messages", json={"text": "x"}, headers=h).json()


def test_jarvis_asks_which_ahmed(client, ai, loopback):
    d = _say(client, ai, "udhaar_add", {"customer": "Ahmed", "amount": 1500})
    assert d["kind"] == "answer" and "…4567" in d["reply"] and "…6543" in d["reply"] and "99999" not in d["reply"]
    d = _say(client, ai, "udhaar_add", {"customer": "Ahmed", "phone": "4567", "amount": 1500})
    assert d["kind"] == "confirm" and "…4567" in d["reply"] and "Rs 1,500" in d["reply"]
    assert "Rs 2,500" in d["reply"] and "Rs 4,000" in d["reply"] and "99999" not in d["reply"]


def test_jarvis_writes_udhaar_once_after_a_yes(client, ai, loopback):
    before = _bal(client, AHMED1)["balance"]
    d = _say(client, ai, "udhaar_add", {"customer": "Ahmed", "phone": "4567", "amount": 1500, "note": "ghee"})
    assert _bal(client, AHMED1)["balance"] == before                     # nothing before the yes
    yes = {"token": d["confirmation"]["token"], "said": "haan"}
    r = client.post("/jarvis/v1/confirmations", headers=OWNER, json=yes).json()
    assert r["kind"] == "done" and "Rs %s" % "{:,.0f}".format(before + 1500) in r["reply"]
    assert client.post("/jarvis/v1/confirmations", headers=OWNER, json=yes).status_code == 409
    ja._USED.clear(); ja._REDIS = None                                  # restart: the client_ref still holds
    client.post("/jarvis/v1/confirmations", headers=OWNER, json=yes)
    assert _bal(client, AHMED1)["balance"] == before + 1500
    lines = client.get("/ledger/customers/" + C[AHMED1], headers=OWNER).json()["lines"]
    assert lines[-1]["source"] == "jarvis" and lines[-1]["note"] == "ghee"


def test_jarvis_payment_follows_the_ledger_rule(client, ai, loopback):
    b = _bal(client, BILAL)                                           # 0 ledger, 900 till
    d = _say(client, ai, "udhaar_payment", {"customer": "Bilal", "amount": 500})
    assert d["kind"] == "answer" and "till" in d["reply"].lower() and "confirmation" not in d
    ahmed = _bal(client, AHMED1)["balance"]
    d = _say(client, ai, "udhaar_payment", {"customer": "Ahmed", "phone": "03001234567", "amount": 1000})
    assert d["kind"] == "confirm" and "wusool" in d["reply"]
    r = client.post("/jarvis/v1/confirmations", headers=OWNER, json={"token": d["confirmation"]["token"]}).json()
    assert r["kind"] == "done" and _bal(client, AHMED1)["balance"] == ahmed - 1000
    assert _bal(client, BILAL) == b


def test_jarvis_balance_answers_from_the_ledger(client, ai, loopback):
    d = _say(client, ai, "customer_balance", {"customer": "Bilal"}, reply="Bilal ka 99999 baqi hai.")
    assert d["kind"] == "done" and "Rs 900" in d["reply"] and "till bill" in d["reply"] and "99999" not in d["reply"]
    d = _say(client, ai, "customer_balance", {}, reply="Total 99999.")
    total = client.get("/ledger", headers=OWNER).json()["totals"]["owed"]
    assert "Total udhaar Rs {:,.0f}".format(total) in d["reply"] and "99999" not in d["reply"]


def test_jarvis_udhaar_is_the_owners_and_new_customers_need_a_phone(client, ai, loopback):
    d = _say(client, ai, "udhaar_add", {"customer": "Bilal", "amount": 100}, h=CASHIER)
    assert d["kind"] == "answer" and "owner" in d["reply"].lower()
    d = _say(client, ai, "udhaar_add", {"customer": "Naya Banda", "amount": 100})
    assert d["kind"] == "answer" and "phone" in d["reply"].lower()
    d = _say(client, ai, "udhaar_add", {"customer": "Naya Banda", "phone": "03451112222", "amount": 100})
    assert d["kind"] == "confirm" and "is new" in d["reply"]


def test_a_shopkeeper_role_reaches_jarvis_v1(client, ai):
    """RoleGuard: /jarvis/v1 is open to every login, like /console (what Jarvis does stays limited)."""
    sid = str(uuid.uuid4())
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, 'Shop', %s, 'operator', 'shopkeeper')",
                    (sid, K, "%s@udhaar-test.local" % sid[:8]))
    from app import roles
    roles.forget()
    h = _bearer(K, sid, "operator")
    ai["out"] = {"kind": "question", "reply": "Hello"}
    assert client.post("/jarvis/v1/messages", headers=h, json={"text": "salam"}).status_code == 200
    assert client.get("/jarvis/v1", headers=h).status_code == 200
    assert client.get("/ledger", headers=h).status_code == 403
