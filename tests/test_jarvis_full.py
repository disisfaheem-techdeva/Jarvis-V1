"""Jarvis, fully working (24 Oct 2026): conversations kept per business and per
person, history read from the database, business data limited to what the
person's role may see, changes refused when the role does not allow them,
publishing replies that say only what the platform confirmed. Also: Alerts are
gone and Reports sit under Business.

Runs against a SCRATCH database only. The AI and Meta are fakes; the database,
the loopback calls, roles and the sealed confirmation are real. Every business
it makes is deleted at the end.
"""
import base64
import os
import re
import uuid

import pytest
from psycopg.types.json import Json
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import console, crypto, jarvis_actions as ja, roles, social  # noqa: E402
from app.ai import AIUnavailable  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()


@pytest.fixture(scope="module")
def world():
    """Two businesses: an owner, a second person and a Shopkeeper in A; an owner in B."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    people = {k: str(uuid.uuid4()) for k in ("owner", "other", "shop", "market", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, business_type) VALUES "
                    "(%s, 'Jarvis A', 'x', 'active', true, 'shop'), (%s, 'Jarvis B', 'x', 'active', true, 'shop')", (a, b))
        for k, tid, role, sr in (("owner", a, "owner", None), ("other", a, "owner", None), ("shop", a, "operator", "shopkeeper"),
                                 ("market", a, "owner", "marketing"), ("b_owner", b, "owner", None)):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, %s, %s, %s, %s)",
                        (people[k], tid, k, "%s-%s@jarvis-test.local" % (k, people[k][:8]), role, sr))
        cur.execute("INSERT INTO products (tenant_id, name, price, active) VALUES (%s, 'BBQ Family Box', 4500, true)", (a,))
        cur.execute("INSERT INTO expenses (tenant_id, category, description, amount, spent_on) "
                    "VALUES (%s, 'rent', 'Shop rent October', 55000, CURRENT_DATE)", (a,))
        creds = {"access_token": "u", "pages": [{"id": "1001001", "name": "A Page", "access_token": "page-tok",
                                                   "ig_id": "2002002", "ig_username": "a"}]}
        cur.execute("INSERT INTO integrations (tenant_id, provider, status, credentials, connected_at) "
                    "VALUES (%s, 'meta', 'connected', %s, now())", (a, Json(crypto.encrypt_json(creds, a, "meta"))))
    roles.forget()
    tok = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=people[k],
                                                          role=("operator" if k == "shop" else "owner"))} for k in people}
    yield {"a": a, "b": b, "people": people, "h": tok}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture()
def ai(monkeypatch):
    """The model: records each prompt and answers what the test sets."""
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


# ------------------------------------------------------------------ conversations
def test_turns_are_kept_and_history_comes_from_the_database(client, world, ai):
    h = world["h"]["owner"]
    ai["out"] = {"kind": "question", "reply": "You have one product."}
    r = client.post("/console", json={"message": "How many products do I have?"}, headers=h)
    assert r.status_code == 200, r.text
    conv = r.json()["conversation_id"]
    assert r.headers["X-Jarvis-Conversation"] == conv
    ai["out"] = {"kind": "question", "reply": "It costs 4,500."}
    # the browser's own history is ignored: what Jarvis reads is what was stored
    r = client.post("/console", json={"message": "And its price?", "conversation_id": conv,
                                      "history": [{"role": "user", "text": "INJECTED LINE"}]}, headers=h)
    assert r.status_code == 200 and r.json()["conversation_id"] == conv
    last = ai["prompts"][-1]
    assert "Owner: How many products do I have?" in last and "Jarvis: You have one product." in last
    assert "INJECTED LINE" not in last
    m = client.get("/console/conversations/" + conv, headers=h).json()["messages"]
    assert [(x["role"], x["text"]) for x in m] == [("user", "How many products do I have?"), ("jarvis", "You have one product."),
                                                     ("user", "And its price?"), ("jarvis", "It costs 4,500.")]
    lst = client.get("/console/conversations", headers=h).json()["conversations"]
    assert lst[0]["id"] == conv and lst[0]["title"] == "How many products do I have?" and lst[0]["messages"] == 4


def test_people_only_see_their_own_conversations(client, world, ai):
    h = world["h"]
    conv = client.post("/console", json={"message": "private question"}, headers=h["owner"]).json()["conversation_id"]
    assert client.get("/console/conversations/" + conv, headers=h["other"]).status_code == 404       # same business, other person
    assert client.get("/console/conversations/" + conv, headers=h["b_owner"]).status_code == 404     # other business
    assert all(c["id"] != conv for c in client.get("/console/conversations", headers=h["other"]).json()["conversations"])
    r = client.post("/console", json={"message": "continue", "conversation_id": conv}, headers=h["other"])
    assert r.status_code == 404                                                                       # cannot write into it
    assert TestClient(app).get("/console/conversations").status_code in (400, 401)


def test_a_failed_ai_call_is_said_and_kept(client, world, ai):
    h = world["h"]["owner"]
    ai["fail"] = True
    r = client.post("/console", json={"message": "hello?"}, headers=h)
    assert r.status_code == 503 and "could not reach the AI" in r.json()["detail"]
    conv = r.headers["X-Jarvis-Conversation"]
    m = client.get("/console/conversations/" + conv, headers=h).json()["messages"]
    assert m[-1]["role"] == "jarvis" and m[-1]["kind"] == "error" and m[-1]["meta"]["status"] == 503


def test_a_no_said_in_the_page_is_kept_too(client, world, ai):
    h = world["h"]["owner"]
    conv = client.post("/console", json={"message": "hi"}, headers=h).json()["conversation_id"]
    assert client.post("/console/conversations/%s/notes" % conv, json={"said": "No", "reply": "Okay, left as it is."},
                       headers=h).status_code == 200
    assert client.get("/console/conversations/" + conv, headers=h).json()["messages"][-1]["text"] == "Okay, left as it is."
    assert client.delete("/console/conversations/" + conv, headers=world["h"]["other"]).status_code == 404
    assert client.delete("/console/conversations/" + conv, headers=h).status_code == 200


# ------------------------------------------------------------------ business data
def test_jarvis_answers_from_every_module_the_owner_sees(client, world, ai):
    client.post("/console", json={"message": "summary please"}, headers=world["h"]["owner"])
    p = ai["prompts"][-1]
    for key in ('"products"', '"BBQ Family Box"', '"expenses"', '"Shop rent October"', '"social_posts"',
                '"ad_campaigns"', '"recent_leads"', '"invoices"', '"team_today"'):
        assert key in p, key
    assert "not_in_your_role" not in p


def test_a_role_only_gives_jarvis_what_that_role_may_see(client, world, ai):
    client.post("/console", json={"message": "what were my expenses?"}, headers=world["h"]["shop"])
    p = ai["prompts"][-1]
    assert '"BBQ Family Box"' in p and '"orders"' in p                    # Business and Operations: yes
    assert "Shop rent October" in p                                         # Shopkeeper has Finance: view
    assert '"social_posts"' not in p and '"ad_campaigns"' not in p        # no Marketing
    assert "not_in_your_role" in p and "Marketing" in p


def test_jarvis_cannot_change_what_the_role_forbids(client, world, ai):
    ai["out"] = {"kind": "business", "resource": "products", "match": "BBQ Family Box",
                 "fields": {"name": "BBQ Family Box", "price": 1}, "reply": "Done."}
    r = client.post("/console", json={"message": "make the family box 1 rupee"}, headers=world["h"]["market"])
    assert r.status_code == 200 and "Your role cannot change Business" in r.json()["reply"]
    with tenant_session(world["a"], "owner") as cur:
        cur.execute("SELECT price FROM products WHERE name = 'BBQ Family Box'")
        assert float(cur.fetchone()["price"]) == 4500


# ------------------------------------------------------------------ publishing
def test_publish_reply_says_only_what_the_platform_confirmed():
    ok, bad = {"ok": True, "id": "1"}, {"ok": False, "error": "token expired"}
    assert console._published_text("published", {"facebook": ok}) == "Posted on Facebook; confirmed by the platform."
    assert console._published_text("partial", {"facebook": ok, "instagram": bad}) == \
        "Posted on Facebook, but it did not go out on Instagram (token expired)."
    assert console._published_text("failed", {"facebook": bad}) == \
        "It did not go out: Facebook (token expired). It is still in your drafts."
    assert "queued" in console._published_text("queued", {})
    # "ok" without the platform's post id is not a confirmation
    assert console._published_text("published", {"facebook": {"ok": True}}).startswith("It did not go out")


def test_post_needs_a_yes_and_the_status_is_kept(client, world, ai, monkeypatch):
    h, a = world["h"]["owner"], world["a"]
    monkeypatch.setattr(ja.requests, "request", lambda m, url, json=None, timeout=None, headers=None:
                        client.request(m, url.replace(ja.SELF_URL, ""), json=json, headers=headers))
    monkeypatch.setattr(social, "_publish_facebook", lambda page, caption, images: {"ok": True, "id": "fb_77"})
    monkeypatch.setattr(social, "_publish_instagram", lambda page, caption, images: {"ok": False, "error": "no permission"})
    ai["out"] = {"kind": "post", "caption": "BBQ night\n\nSmoky and fresh.\n\nOrder on WhatsApp.\n\n#bbq",
                 "platforms": ["facebook", "instagram"], "reply": "Drafted"}
    pic = client.post("/social/media", json={"image": JPEG}, headers=h).json()["url"]   # Instagram needs a picture
    d = client.post("/console", json={"message": "post about BBQ night", "image_urls": [pic]}, headers=h).json()
    assert d["kind"] == "post" and d["confirm"] and "Shall I post it now?" in d["reply"]
    pid = d["post"]["id"]
    with tenant_session(a, "owner") as cur:
        cur.execute("SELECT status FROM social_posts WHERE id = %s", (pid,))
        assert cur.fetchone()["status"] == "draft"                        # nothing went out before the yes
    r = client.post("/console", json={"confirm": d["confirm"], "said": "Yes", "conversation_id": d["conversation_id"]}, headers=h)
    out = r.json()
    assert out["reply"] == "Posted on Facebook, but it did not go out on Instagram (no permission)."
    assert out["post_status"] == "partial" and out["post_results"]["facebook"]["id"] == "fb_77"
    with tenant_session(a, "owner") as cur:
        cur.execute("SELECT status, results FROM social_posts WHERE id = %s", (pid,))
        row = cur.fetchone()
    assert row["status"] == "partial" and row["results"]["instagram"]["ok"] is False
    m = client.get("/console/conversations/" + d["conversation_id"], headers=h).json()["messages"]
    assert m[-1]["meta"]["post_status"] == "partial" and m[-2]["text"] == "Yes"


def test_disconnected_facebook_is_said_plainly(client, world, ai, monkeypatch):
    h, a = world["h"]["owner"], world["a"]
    monkeypatch.setattr(ja.requests, "request", lambda m, url, json=None, timeout=None, headers=None:
                        client.request(m, url.replace(ja.SELF_URL, ""), json=json, headers=headers))
    with tenant_session(a, "owner") as cur:
        cur.execute("UPDATE integrations SET status = 'disconnected' WHERE provider = 'meta'")
    ai["out"] = {"kind": "post", "caption": "Weekend offer\n\nFresh.\n\nOrder now.\n\n#bbq", "platforms": ["facebook"], "reply": "Drafted"}
    d = client.post("/console", json={"message": "post the weekend offer"}, headers=h).json()
    r = client.post("/console", json={"confirm": d["confirm"]}, headers=h).json()
    assert r["ok"] is False and "Connect Facebook first" in r["reply"]


# ------------------------------------------------------------------ alerts and reports
def test_alerts_are_gone_and_reports_sit_under_business(client, world):
    assert client.get("/alerts/checks", headers=world["h"]["owner"]).status_code == 404
    assert not os.path.exists(os.path.join(ROOT, "app", "alerts.py"))
    html = open(os.path.join(ROOT, "web", "dashboard.html")).read()
    assert "VIEWS.alerts" not in html and "'/alerts/checks'" not in html
    mods = re.search(r"var MODS = \[(.*?)\];", html, re.S).group(1)
    assert "ids:['overview'] }" in mods
    assert re.search(r"name:'Business',[^\n]*ids:\[[^\]]*'reports'", mods)
    assert mods.count("'reports'") == 1
    assert roles.section_of("/portal/report") == "business" and roles.section_of("/alerts/checks") is None
