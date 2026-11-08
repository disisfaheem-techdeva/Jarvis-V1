"""Set up the business by talking to Jarvis (Phase 9, app/setup_talk.py).

The AI reader is replaced by the plain line reader (AI_PROVIDER=none in the test
environment), which reads "name ... price" lines; the store, the checklist, the
suggestion and the save are real. Checked: what is said piece by piece is read as
a whole; nothing saved until Save; the next missing thing is asked; nothing from
the model's retelling is used; owner only; nothing crosses businesses.

Runs against a SCRATCH database only.
"""
import copy
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import console, jarvis_actions as ja, setup_talk  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

K, B = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "staff", "b_owner")}


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, STAFF, B_OWNER = _bearer(K, U["owner"], "owner"), _bearer(K, U["staff"], "operator"), _bearer(B, U["b_owner"], "owner")


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'New Karyana', 'grocery', true, 'karyana'), (%s, 'Other', 'grocery', true, 'karyana')", (K, B))
        for k, tid, role in (("owner", K, "owner"), ("staff", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@setup-test.local" % U[k][:8], role))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


def _say(c, text, h=OWNER, **kw):
    r = c.post("/business-profile/setup/describe", headers=h, json=dict(text=text, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def _products():
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT name, price FROM products ORDER BY name")
        return [(r["name"], float(r["price"]) if r["price"] is not None else None) for r in cur.fetchall()]


def test_piece_by_piece_is_read_as_a_whole_and_the_next_thing_is_asked(client):
    d = _say(client, "Cheeni - 180\nAata - 160", fresh=True)
    assert [(s["name"], s["price"]) for s in d["draft"]["services"]] == [("Cheeni", 180), ("Aata", 160)]
    assert d["missing"][0] == "hours" and "kab khulti" in d["next_question"]       # type is known (karyana), items told
    sid = d["suggestion_id"]
    d = _say(client, "Ghee - 550\nHum Gulberg Lahore mein hain, cash aur JazzCash lete hain.")
    assert d["suggestion_id"] == sid                                                 # the same suggestion grows
    assert {s["name"] for s in d["draft"]["services"]} == {"Cheeni", "Aata", "Ghee"}
    assert "location" not in d["missing"] and "payments" not in d["missing"]
    assert d["missing"] == ["hours", "delivery", "udhaar"]
    assert "Likh liya" in d["reply"] and "3 cheezein (3 ke rate)" in d["reply"] and "Save" in d["reply"]
    assert d["reply"].rstrip().endswith("?")
    assert _products() == []                                                         # nothing saved yet


def test_nothing_is_kept_that_was_not_said(client):
    d = _say(client, "Cheeni - 180", fresh=True)
    assert [s["name"] for s in d["draft"]["services"]] == ["Cheeni"]
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT source_text FROM setup_suggestions WHERE id = %s", (d["suggestion_id"],))
        assert cur.fetchone()["source_text"] == "Cheeni - 180"


def test_saving_the_suggestion_puts_the_items_in_as_products(client):
    d = _say(client, "Cheeni - 180\nAata - 160", fresh=True)
    r = client.post("/business-profile/setup/apply", headers=OWNER, json={"draft": d["draft"], "suggestion_id": d["suggestion_id"]})
    assert r.status_code == 200 and r.json()["services_added"] == 2
    assert _products() == [("Aata", 160.0), ("Cheeni", 180.0)]                      # a karyana's items are products
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT status FROM setup_suggestions WHERE id = %s", (d["suggestion_id"],))
        assert cur.fetchone()["status"] == "saved"
    d2 = _say(client, "Daal - 300")                                                  # a saved one is not added to
    assert d2["suggestion_id"] != d["suggestion_id"]


def test_checklist_rules():
    assert setup_talk.missing({}, "", None)[0] == "type"
    assert "udhaar" not in setup_talk.missing({"business_type": "salon"}, "", None)
    full = "Gulberg Lahore. cash, JazzCash. Home delivery 2 km tak. Udhaar sirf purane customers ko."
    assert setup_talk.missing({"services": [{"name": "x"}], "hours": [{"day": "monday"}]}, full, "karyana") == []


def test_owner_only_and_businesses_stay_apart(client):
    r = client.post("/business-profile/setup/describe", headers=STAFF, json={"text": "Cheeni - 180"})
    assert r.status_code == 403
    d = _say(client, "Chawal - 320", h=B_OWNER, fresh=True)
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM setup_suggestions WHERE id = %s", (d["suggestion_id"],))
        assert cur.fetchone()["n"] == 0
    assert client.post("/business-profile/setup/describe", headers=OWNER, json={"text": "x"}).status_code == 400


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


def test_jarvis_reads_the_owners_own_words_not_its_retelling(client, ai, loopback):
    # the model "retells" with an invented item and price; only what the owner said is read
    ai["out"] = {"kind": "action", "action": "describe_business", "args": {"text": "Sugar - 999\nDiamonds - 5"},
                 "reply": "Sab likh liya, 999 ka sugar."}
    r = client.post("/jarvis/v1/messages", headers=OWNER, json={"text": "Doodh - 220\nDahi - 240"}).json()
    assert r["kind"] == "done" and r.get("navigate") == "bizagent"
    t = r["reply"]
    assert "Likh liya" in t and "999" not in t and "Ab batayein" in t and t.rstrip().endswith("?")
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT draft FROM setup_suggestions WHERE id = %s", (r["data"]["setup"]["suggestion_id"],))
        names = {s["name"] for s in cur.fetchone()["draft"]["services"]}
    assert "Doodh" in names and "Dahi" in names and "Sugar" not in names and "Diamonds" not in names
