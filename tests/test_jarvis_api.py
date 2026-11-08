"""Jarvis API v1 (8 Nov 2026; app/jarvis_api.py, ADR-003): the versioned contract
over the same Jarvis as /console.

Runs against a SCRATCH database only. The AI is a fake; the database, the
loopback call, roles, the sealed confirmation and row-level security are real.
Every business it makes is deleted at the end.
"""
import os
import time
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import console, jarvis_actions as ja, roles  # noqa: E402
from app.ai import AIUnavailable  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def world():
    """Business A (owner, a second person, a Shopkeeper) and business B (owner)."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    people = {k: str(uuid.uuid4()) for k in ("owner", "other", "shop", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, business_type) VALUES "
                    "(%s, 'JV1 A', 'x', 'active', true, 'shop'), (%s, 'JV1 B', 'x', 'active', true, 'shop')", (a, b))
        for k, tid, role, sr in (("owner", a, "owner", None), ("other", a, "owner", None),
                                 ("shop", a, "operator", "shopkeeper"), ("b_owner", b, "owner", None)):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, %s, %s, %s, %s)",
                        (people[k], tid, k, "%s-%s@jv1-test.local" % (k, people[k][:8]), role, sr))
    roles.forget()
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=people[k],
                                                        role=("operator" if k == "shop" else "owner"))} for k in people}
    yield {"a": a, "b": b, "people": people, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM tenants WHERE id IN (%s, %s)", (a, b))
        assert cur.fetchone()["n"] == 0


@pytest.fixture()
def ai(monkeypatch):
    said = {"out": {"kind": "question", "reply": "Hello"}, "prompts": []}

    def generate_json(system, prompt, required_keys=(), _retry=True):
        said["prompts"].append(prompt)
        if said.get("fail"):
            raise AIUnavailable("every AI provider refused: test")
        return dict(said["out"])
    monkeypatch.setattr(console, "generate_json", generate_json)
    console._FACTS_CACHE.clear()
    return said


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture()
def loopback(client, monkeypatch):
    """Jarvis's actions call the platform over HTTP; here that goes to the test app."""
    monkeypatch.setattr(ja.requests, "request", lambda m, url, json=None, timeout=None, headers=None:
                        client.request(m, url.replace(ja.SELF_URL, ""), json=json, headers=headers))


class FakeRedis:
    """SET NX with an expiry, like the real one, shared by every 'process'."""
    def __init__(self):
        self.keys = {}

    def set(self, k, v, nx=False, exat=None):
        now = time.time()
        if nx and k in self.keys and self.keys[k] > now:
            return None
        self.keys[k] = exat or now + 3600
        return True


class DeadRedis:
    def set(self, *a, **kw):
        raise ConnectionError("redis is down")


@pytest.fixture()
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(ja, "_REDIS", r)
    ja._USED.clear()
    return r


def _expenses(tid, like):
    with tenant_session(tid, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM expenses WHERE description = %s", (like,))
        return cur.fetchone()["n"]


# ---------------------------------------------------------------- contract
def test_capabilities_need_a_login_and_name_no_model(client, world):
    assert TestClient(app).get("/jarvis/v1").status_code in (400, 401)
    r = client.get("/jarvis/v1", headers=world["h"]["owner"])
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["api"] == "jarvis/v1" and c["kinds"] == ["answer", "confirm", "done"]
    assert "add_expense" in c["actions"] and "add_expense" in c["confirm_first"]
    assert set(c["confirm_first"]) <= set(c["actions"])
    text = r.text.lower()
    assert not any(w in text for w in ("gemini", "claude", "openai", "anthropic", "api_key"))


def test_a_question_gets_the_v1_shape_and_is_kept(client, world, ai):
    h = world["h"]["owner"]
    ai["out"] = {"kind": "question", "reply": "Aap ke paas koi product nahi."}
    r = client.post("/jarvis/v1/messages", json={"text": "Kitne products hain?"}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["api"] == "jarvis/v1" and d["kind"] == "answer" and d["intent"] == "question"
    assert d["reply"] == "Aap ke paas koi product nahi." and d["conversation_id"]
    # the same conversation the dashboard keeps, readable through both APIs
    v1 = client.get("/jarvis/v1/conversations/" + d["conversation_id"], headers=h).json()
    old = client.get("/console/conversations/" + d["conversation_id"], headers=h).json()
    assert v1["messages"] == old["messages"] and len(v1["messages"]) == 2 and v1["api"] == "jarvis/v1"
    assert any(c["id"] == d["conversation_id"] for c in client.get("/jarvis/v1/conversations", headers=h).json()["conversations"])


def test_the_dashboard_route_is_unchanged(client, world, ai):
    ai["out"] = {"kind": "question", "reply": "Same as ever."}
    r = client.post("/console", json={"message": "hello"}, headers=world["h"]["owner"])
    assert r.status_code == 200 and r.json()["kind"] == "question" and r.json()["reply"] == "Same as ever."
    assert "api" not in r.json()


def test_bad_input_and_a_deaf_ai_use_one_error_shape(client, world, ai):
    h = world["h"]["owner"]
    r = client.post("/jarvis/v1/messages", json={"text": "  "}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["status"] == 400 and r.json()["api"] == "jarvis/v1"
    r = client.post("/jarvis/v1/messages", json={"text": "x" * 2001}, headers=h)
    assert r.status_code == 400 and "too long" in r.json()["error"]["message"]
    r = client.post("/jarvis/v1/confirmations", json={}, headers=h)
    assert r.status_code == 400
    ai["fail"] = True
    r = client.post("/jarvis/v1/messages", json={"text": "Aaj kitni sale hui?"}, headers=h)
    assert r.status_code == 503 and "nothing was changed" in r.json()["error"]["message"]
    conv = r.json()["conversation_id"]                       # the failure is kept in the person's history
    msgs = client.get("/jarvis/v1/conversations/" + conv, headers=h).json()["messages"]
    assert msgs[-1]["kind"] == "error"


# ---------------------------------------------------------------- money needs a yes, once
def test_money_waits_for_a_yes_and_runs_exactly_once(client, world, ai, loopback, redis):
    h, a = world["h"]["owner"], world["a"]
    what = "JV1 bijli bill " + uuid.uuid4().hex[:6]
    ai["out"] = {"kind": "action", "action": "add_expense", "args": {"amount": 4200, "description": what},
                 "reply": "Bijli ka bill likh doon?"}
    d = client.post("/jarvis/v1/messages", json={"text": "4200 bijli ka bill likh do"}, headers=h).json()
    assert d["kind"] == "confirm" and d["confirmation"]["action"] == "add_expense"
    assert d["confirmation"]["expires_in"] == 300 and "4,200" in d["confirmation"]["summary"]
    assert _expenses(a, what) == 0                                           # nothing before the yes
    n_prompts = len(ai["prompts"])
    yes = {"token": d["confirmation"]["token"], "said": "Haan", "conversation_id": d["conversation_id"]}
    r = client.post("/jarvis/v1/confirmations", json=yes, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "done" and r.json()["action"] == {"name": "add_expense", "ok": True}
    assert len(ai["prompts"]) == n_prompts                                   # the yes asks no model
    assert _expenses(a, what) == 1
    r = client.post("/jarvis/v1/confirmations", json=yes, headers=h)        # double tap
    assert r.status_code == 409 and r.json()["error"]["message"] == "That is already done."
    assert _expenses(a, what) == 1


def test_a_yes_stays_used_after_a_restart(client, world, ai, loopback, redis):
    h, a = world["h"]["owner"], world["a"]
    what = "JV1 restart " + uuid.uuid4().hex[:6]
    ai["out"] = {"kind": "action", "action": "add_expense", "args": {"amount": 500, "description": what}, "reply": "?"}
    tok = client.post("/jarvis/v1/messages", json={"text": "500 kharcha"}, headers=h).json()["confirmation"]["token"]
    assert client.post("/jarvis/v1/confirmations", json={"token": tok}, headers=h).status_code == 200
    ja._USED.clear()                                     # the API process restarted: its own list is gone
    r = client.post("/jarvis/v1/confirmations", json={"token": tok}, headers=h)
    assert r.status_code == 409                          # Redis still remembers
    assert _expenses(a, what) == 1


def test_redis_down_falls_back_to_the_process_list(client, world, ai, loopback, monkeypatch):
    monkeypatch.setattr(ja, "_REDIS", DeadRedis())
    ja._USED.clear()
    h, a = world["h"]["owner"], world["a"]
    what = "JV1 no redis " + uuid.uuid4().hex[:6]
    ai["out"] = {"kind": "action", "action": "add_expense", "args": {"amount": 300, "description": what}, "reply": "?"}
    tok = client.post("/jarvis/v1/messages", json={"text": "300 kharcha"}, headers=h).json()["confirmation"]["token"]
    assert client.post("/jarvis/v1/confirmations", json={"token": tok}, headers=h).status_code == 200
    assert client.post("/jarvis/v1/confirmations", json={"token": tok}, headers=h).status_code == 409
    assert _expenses(a, what) == 1


# ---------------------------------------------------------------- isolation
def test_a_yes_cannot_be_used_by_someone_else(client, world, ai, loopback, redis):
    h, a = world["h"], world["a"]
    what = "JV1 not yours " + uuid.uuid4().hex[:6]
    ai["out"] = {"kind": "action", "action": "add_expense", "args": {"amount": 900, "description": what}, "reply": "?"}
    tok = client.post("/jarvis/v1/messages", json={"text": "900 kharcha"}, headers=h["owner"]).json()["confirmation"]["token"]
    for who in ("other", "b_owner"):                     # same business other person; other business
        r = client.post("/jarvis/v1/confirmations", json={"token": tok}, headers=h[who])
        assert r.status_code == 403, (who, r.text)
    forged = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    assert client.post("/jarvis/v1/confirmations", json={"token": forged}, headers=h["owner"]).status_code == 400
    assert _expenses(a, what) == 0


def test_conversations_stay_with_their_person_and_business(client, world, ai):
    h = world["h"]
    ai["out"] = {"kind": "question", "reply": "private"}
    conv = client.post("/jarvis/v1/messages", json={"text": "Ahmed ka hisaab"}, headers=h["owner"]).json()["conversation_id"]
    for who in ("other", "b_owner"):
        r = client.get("/jarvis/v1/conversations/" + conv, headers=h[who])
        assert r.status_code == 404 and r.json()["error"]["status"] == 404
        r = client.post("/jarvis/v1/messages", json={"text": "continue", "conversation_id": conv}, headers=h[who])
        assert r.status_code == 404
        assert all(c["id"] != conv for c in client.get("/jarvis/v1/conversations", headers=h[who]).json()["conversations"])


def test_jarvis_cannot_do_more_than_the_role_allows(client, world, ai, loopback, redis):
    """A Shopkeeper's Jarvis is refused what a Shopkeeper is refused by hand."""
    h = world["h"]["shop"]
    ai["out"] = {"kind": "dashboard", "prefs": {"accent": "#C9A227"}, "reply": "Done"}
    r = client.post("/jarvis/v1/messages", json={"text": "mera colour gold kar do"}, headers=h)
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json()["kind"] == "answer" and "cannot" in r.json()["reply"]
    with tenant_session(world["a"], "owner") as cur:
        cur.execute("SELECT dashboard_prefs FROM tenant_config WHERE tenant_id = %s", (world["a"],))
        row = cur.fetchone()
    assert not row or (row["dashboard_prefs"] or {}).get("accent") != "#C9A227"
