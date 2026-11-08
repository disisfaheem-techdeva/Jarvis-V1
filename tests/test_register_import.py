"""Bring the old register (Phase 7, db/96, app/register_import.py).

The AI that reads a page is a fake here (what it "reads" is set per test); the
storage, flags, review, import into the udhaar ledger and the measures are real.
Checked: pages are private; rows keep the page and the text as read; uncertain or
incomplete rows are flagged and never approved in bulk; blocking flags stop an
approval; duplicates are caught; nothing reaches the ledger before Import; Import
writes each approved row once, even if pressed twice; imported rows are locked;
accuracy is the share of kept rows the owner did not have to change; owner only;
nothing crosses businesses.

Runs against a SCRATCH database only. Makes its own businesses and deletes them.
"""
import base64
import os
import uuid

import psycopg
import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import register_import as RI  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

K, B = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "cashier", "b_owner")}
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 200).decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200).decode()


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, CASHIER, B_OWNER = (_bearer(K, U["owner"], "owner"), _bearer(K, U["cashier"], "operator"),
                           _bearer(B, U["b_owner"], "owner"))


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Register Karyana', 'grocery', true, 'karyana'), (%s, 'Other Karyana', 'grocery', true, 'karyana')",
                    (K, B))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@register-test.local" % U[k][:8], role))
        # two existing customers called Ahmed, and Bilal
        cur.execute("""INSERT INTO customers (tenant_id, name, phone) VALUES (%s, 'Ahmed', '03001234567'),
                       (%s, 'Ahmed', '03219876543'), (%s, 'Bilal', '03335550001')""", (K, K, K))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM ledger_imports WHERE tenant_id IN (%s, %s)", (K, B))
        assert cur.fetchone()["n"] == 0


@pytest.fixture()
def page(monkeypatch):
    said = {"rows": []}

    def fake(data, mime, mode):
        if said.get("fail"):
            raise RuntimeError("The AI could not read this page. Try a sharper photo.")
        return said["rows"], "gemini:fake"
    monkeypatch.setattr(RI, "read_page", fake)
    return said


def _new(c, mode="balances", h=OWNER):
    r = c.post("/ledger/imports", headers=h, json={"mode": mode, "title": "Register 2025"})
    assert r.status_code == 200, r.text
    return r.json()["import"]["id"]


def _page(c, iid, img=JPEG):
    r = c.post("/ledger/imports/%s/pages" % iid, headers=OWNER, json={"image": img})
    assert r.status_code == 200, r.text
    return r.json()["page"]["id"]


def _read(c, iid, pid):
    r = c.post("/ledger/imports/%s/pages/%s/read" % (iid, pid), headers=OWNER)
    assert r.status_code == 200, r.text
    return r.json()


def _row(d, name):
    return next(r for r in d["rows"] if (r["name"] or "") == name or (r["read_text"] or "") == name)


def _ledger(name):
    with tenant_session(K, "owner") as cur:
        cur.execute("""SELECT e.kind, e.amount, e.source, e.note FROM customer_ledger_entries e
                         JOIN customers c ON c.id = e.customer_id WHERE c.name = %s ORDER BY e.created_at""", (name,))
        return [dict(r) for r in cur.fetchall()]


BALANCES = [
    {"text": "Bilal 2,500", "name": "Bilal", "phone": None, "amount": 2500, "date": None, "kind": "opening", "confidence": 0.95},
    {"text": "Zara Khan 0300 777 8888 1,200", "name": "Zara Khan", "phone": "03007778888", "amount": 1200, "confidence": 0.9},
    {"text": "Ahmed 900", "name": "Ahmed", "phone": None, "amount": 900, "confidence": 0.92},
    {"text": "Nasreen ?50", "name": "Nasreen", "phone": None, "amount": 450, "confidence": 0.4},
    {"text": "(smudged) 700", "name": None, "phone": None, "amount": 700, "confidence": 0.3},
    {"text": "Bilal 2,500", "name": "Bilal", "phone": None, "amount": 2500, "confidence": 0.95},
]


def test_pages_are_private_and_checked(client):
    iid = _new(client)
    assert client.post("/ledger/imports/%s/pages" % iid, headers=OWNER, json={"image": "data:text/plain;base64,aGk="}).status_code == 400
    fake = "data:image/jpeg;base64," + base64.b64encode(b"not a jpeg").decode()
    assert client.post("/ledger/imports/%s/pages" % iid, headers=OWNER, json={"image": fake}).status_code == 400
    pid = _page(client, iid, PNG)
    r = client.get("/ledger/imports/%s/pages/%s/image" % (iid, pid), headers=OWNER)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "no-store" in r.headers["cache-control"]
    assert client.get("/ledger/imports/%s/pages/%s/image" % (iid, pid), headers=B_OWNER).status_code == 404
    assert client.get("/ledger/imports/%s/pages/%s/image" % (iid, pid)).status_code in (400, 401)
    assert client.post("/ledger/imports/%s/discard" % iid, headers=OWNER).status_code == 200


def test_rows_are_staged_with_page_text_confidence_and_flags(client, page):
    page["rows"] = BALANCES
    iid = _new(client)
    pid = _page(client, iid)
    d = _read(client, iid, pid)
    assert len(d["rows"]) == 6 and all(r["page_no"] == 1 for r in d["rows"])
    b = d["rows"][0]
    assert b["read_text"] == "Bilal 2,500" and b["confidence"] == 0.95 and b["kind"] == "opening"
    by = {(r["read_text"], r["row_no"]): r for r in d["rows"]}
    assert set(by[("Bilal 2,500", 1)]["flags"]) == {"possible_duplicate"}           # the same line twice on the page
    assert by[("Zara Khan 0300 777 8888 1,200", 2)]["flags"] == ["new_customer"] and by[("Zara Khan 0300 777 8888 1,200", 2)]["status"] == "ready"
    assert "ambiguous_customer" in by[("Ahmed 900", 3)]["flags"]                       # two Ahmeds, no phone
    assert "low_confidence" in by[("Nasreen ?50", 4)]["flags"]
    assert {"no_name", "low_confidence"} <= set(by[("(smudged) 700", 5)]["flags"])
    assert _ledger("Bilal") == []                                                      # nothing in the ledger yet
    pytest.iid = iid


def test_bulk_approval_takes_only_clean_rows_and_blocking_flags_stop_approval(client):
    iid = pytest.iid
    d = client.post("/ledger/imports/%s/approve-ready" % iid, headers=OWNER).json()
    assert [r["read_text"] for r in d["rows"] if r["status"] == "approved"] == ["Zara Khan 0300 777 8888 1,200"]
    smudge = _row(d, "(smudged) 700")
    r = client.post("/ledger/imports/%s/rows/%s/decide" % (iid, smudge["id"]), headers=OWNER, json={"decision": "approve"})
    assert r.status_code == 409 and "whose line" in r.json()["detail"]
    ahmed = _row(d, "Ahmed")
    r = client.post("/ledger/imports/%s/rows/%s/decide" % (iid, ahmed["id"]), headers=OWNER, json={"decision": "approve"})
    assert r.status_code == 409 and "which customer" in r.json()["detail"]


def test_the_owner_corrects_approves_and_rejects(client):
    iid = pytest.iid
    d = client.get("/ledger/imports/" + iid, headers=OWNER).json()
    ahmed, nas, smudge = _row(d, "Ahmed"), _row(d, "Nasreen"), _row(d, "(smudged) 700")
    dup = [r for r in d["rows"] if r["read_text"] == "Bilal 2,500"]
    d = client.patch("/ledger/imports/%s/rows/%s" % (iid, ahmed["id"]), headers=OWNER, json={"phone": "03001234567"}).json()
    assert "ambiguous_customer" not in _row(d, "Ahmed")["flags"]
    d = client.patch("/ledger/imports/%s/rows/%s" % (iid, nas["id"]), headers=OWNER, json={"amount": 350}).json()
    n = _row(d, "Nasreen")
    assert n["amount"] == 350 and n["edited"] and "low_confidence" not in n["flags"]   # a person checked it
    for row in (ahmed, nas, dup[0]):
        assert client.post("/ledger/imports/%s/rows/%s/decide" % (iid, row["id"]), headers=OWNER, json={"decision": "approve"}).status_code == 200
    for row in (smudge, dup[1]):
        client.post("/ledger/imports/%s/rows/%s/decide" % (iid, row["id"]), headers=OWNER, json={"decision": "reject"})
    d = client.get("/ledger/imports/" + iid, headers=OWNER).json()
    assert d["counts"] == {"needs_check": 0, "ready": 0, "approved": 4, "rejected": 2, "imported": 0}
    assert {t["name"]: t["total"] for t in d["totals_by_customer"]} == {"Bilal": 2500, "Zara Khan": 1200, "Ahmed": 900, "Nasreen": 350}
    # accuracy: 4 kept, 2 changed by the owner (Ahmed's phone, Nasreen's amount)
    assert d["accuracy"] == {"rows_read": 6, "kept": 4, "kept_as_read": 2, "rejected": 2, "accuracy": 0.5}


def test_import_writes_each_approved_row_once_and_locks_it(client):
    iid = pytest.iid
    r = client.post("/ledger/imports/%s/commit" % iid, headers=OWNER)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["imported_now"] == 4 and d["failed_now"] == 0 and d["import"]["status"] == "imported"
    bilal = _ledger("Bilal")
    assert len(bilal) == 1 and float(bilal[0]["amount"]) == 2500 and bilal[0]["source"] == "import"
    assert bilal[0]["kind"] == "opening" and bilal[0]["note"].startswith("Register page 1: Bilal 2,500")
    assert len(_ledger("Zara Khan")) == 1 and len(_ledger("Nasreen")) == 1
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT c.phone FROM customer_ledger_entries e JOIN customers c ON c.id = e.customer_id WHERE e.amount = 900")
        assert cur.fetchone()["phone"] == "03001234567"                                  # the Ahmed the owner chose
    assert client.post("/ledger/imports/%s/commit" % iid, headers=OWNER).status_code == 409   # closed: never twice
    rid = _row(d, "Bilal")["id"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_session(K, "owner") as cur:
            cur.execute("UPDATE ledger_import_rows SET amount = 1 WHERE id = %s", (rid,))
    assert client.patch("/ledger/imports/%s/rows/%s" % (iid, rid), headers=OWNER, json={"amount": 1}).status_code == 409
    assert client.post("/ledger/imports/%s/discard" % iid, headers=OWNER).status_code == 409
    assert client.get("/ledger", headers=OWNER).json()["totals"]["owed"] == 4950


def test_transactions_import_orders_credit_before_payment_and_flags_missing_dates(client, page):
    page["rows"] = [
        {"text": "1/10 Bilal jama 1000", "name": "Bilal", "amount": 1000, "date": "2026-10-01", "kind": "payment", "confidence": 0.9},
        {"text": "1/10 Bilal udhaar 600", "name": "Bilal", "amount": 600, "date": "2026-10-01", "kind": "credit", "confidence": 0.9},
        {"text": "Bilal udhaar 200", "name": "Bilal", "amount": 200, "date": None, "kind": "credit", "confidence": 0.9}]
    iid = _new(client, "transactions")
    d = _read(client, iid, _page(client, iid))
    assert "no_date" in _row(d, "Bilal udhaar 200")["flags"]
    client.post("/ledger/imports/%s/approve-ready" % iid, headers=OWNER)
    r = client.post("/ledger/imports/%s/commit" % iid, headers=OWNER).json()
    assert r["imported_now"] == 2 and r["import"]["status"] == "open"              # the undated row still waits
    assert [e["kind"] for e in _ledger("Bilal")][-2:] == ["credit", "payment"]      # the credit went in before the payment


def test_a_payment_that_would_be_more_than_owed_is_not_imported_and_says_why(client, page):
    page["rows"] = [{"text": "Zara jama 5000", "name": "Zara Khan", "phone": "03007778888", "amount": 5000,
                     "date": "2026-10-02", "kind": "payment", "confidence": 0.95}]
    iid = _new(client, "transactions")
    _read(client, iid, _page(client, iid))
    client.post("/ledger/imports/%s/approve-ready" % iid, headers=OWNER)
    r = client.post("/ledger/imports/%s/commit" % iid, headers=OWNER).json()
    assert r["imported_now"] == 0 and r["failed_now"] == 1
    assert "more than owed" in r["rows"][0]["import_error"] and r["rows"][0]["status"] == "approved"


def test_a_page_the_ai_cannot_read_adds_nothing(client, page):
    page["fail"] = True
    iid = _new(client)
    pid = _page(client, iid)
    r = client.post("/ledger/imports/%s/pages/%s/read" % (iid, pid), headers=OWNER)
    assert r.status_code == 503 and "Nothing was added" in r.json()["detail"]
    d = client.get("/ledger/imports/" + iid, headers=OWNER).json()
    assert d["rows"] == [] and d["pages"][0]["status"] == "failed"


def test_owner_only_and_businesses_stay_apart(client):
    assert client.get("/ledger/imports", headers=CASHIER).status_code == 403
    assert client.post("/ledger/imports", headers=CASHIER, json={"mode": "balances"}).status_code == 403
    assert client.get("/ledger/imports/" + pytest.iid, headers=B_OWNER).status_code == 404
    assert client.get("/ledger/imports", headers=B_OWNER).json()["imports"] == []


def test_read_page_asks_for_json_and_reads_the_answer(monkeypatch):
    """The real transport, with the AI's HTTP answer faked: fenced JSON is read; a 429 says so plainly."""
    from app import ai, usage
    monkeypatch.setattr(ai, "chain_entries", lambda: [("gemini", "gemini-test")])
    monkeypatch.setenv(ai._KEY_ENV["gemini"], "test-key-not-real")
    monkeypatch.setattr(usage, "check", lambda: None)
    monkeypatch.setattr(usage, "charge", lambda *a, **k: None)
    sent = {}

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b
    answer = {"code": 200}

    def post(url, params=None, json=None, timeout=None, headers=None):
        sent["url"], sent["json"] = url, json
        if answer["code"] != 200:
            return R(answer["code"], {})
        text = '```json\n{"rows": [{"text": "Bilal 2,500", "name": "Bilal", "amount": 2500, "confidence": 0.9}]}\n```'
        return R(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})
    monkeypatch.setattr(RI.requests, "post", post)
    rows, model = RI.read_page(b"\xff\xd8\xff", "image/jpeg", "balances")
    assert rows[0]["name"] == "Bilal" and model == "gemini:gemini-test"
    part = sent["json"]["contents"][0]["parts"]
    assert part[0]["inline_data"]["mime_type"] == "image/jpeg" and "Never invent" in part[1]["text"]
    assert "gemini-test:generateContent" in sent["url"]
    answer["code"] = 429
    with pytest.raises(RuntimeError) as e:
        RI.read_page(b"\xff\xd8\xff", "image/jpeg", "balances")
    assert "AI plan" in str(e.value)
