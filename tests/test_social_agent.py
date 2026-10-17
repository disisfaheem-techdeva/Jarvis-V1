"""Social agent: Meta webhook -> stored -> AI reply sent -> Lead -> serious client alert.

Meta and the AI are fakes; the database (RLS, leads, audit) is real. Needs
db/68_social_inbox.sql applied, like the other API tests.
"""
import hashlib
import hmac
import json
import uuid

import os

import pytest
from psycopg.types.json import Json
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import account, ai, crypto, oauth, social_agent
from app.db import tenant_session
from app.main import app

CLINIC = "11111111-1111-1111-1111-111111111111"
OWNER = {"X-Tenant-Id": CLINIC, "X-Role": "owner"}
PAGE, IG = "1001001", "2002002"
SECRET = "test-app-secret"


@pytest.fixture()
def env(monkeypatch):
    sent, mails, verdicts = [], [], {}
    monkeypatch.setattr(oauth, "META_APP_SECRET", SECRET)
    monkeypatch.setattr(social_agent, "VERIFY_TOKEN", "verify-me")

    def graph_post(path, data):
        sent.append((path, {k: v for k, v in data.items() if k != "access_token"}))
        if path.endswith("/subscribed_apps"):
            return {"success": True}, None
        if "FAIL" in json.dumps(data):
            return None, "Meta said no (code 10)"
        return {"message_id": "m_" + uuid.uuid4().hex[:8], "id": "c_" + uuid.uuid4().hex[:8]}, None

    def generate_json(system, prompt, required_keys=(), _retry=True):
        last = prompt.rsplit("Customer", 1)[-1]
        for key, v in verdicts.items():
            if key.lower() in last.lower():
                return dict(v)
        return {"reply": "Shukriya! Hum jald batate hain.", "intent": "thanks", "serious": False, "score": 5}

    monkeypatch.setattr(social_agent, "_graph_post", graph_post)
    monkeypatch.setattr(social_agent, "_graph_get",
                        lambda path, params: {"first_name": "Bilal", "last_name": "Khan"} if "first_name" in params.get("fields", "") else None)
    monkeypatch.setattr(ai, "generate_json", generate_json)
    monkeypatch.setattr(account, "mail_configured", lambda: True)
    monkeypatch.setattr(account, "send_mail", lambda to, subject, text: mails.append((to, subject, text)))
    creds = {"access_token": "u", "pages": [{"id": PAGE, "name": "Clinic Page", "access_token": "page-tok",
                                               "ig_id": IG, "ig_username": "clinic"}]}
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("""INSERT INTO integrations (tenant_id, provider, status, credentials, connected_at)
                       VALUES (current_tenant(), 'meta', 'connected', %s, now())
                       ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'connected', credentials = EXCLUDED.credentials,
                         connected_at = now()""", (Json(crypto.encrypt_json(creds, CLINIC, "meta")),))
        cur.execute("DELETE FROM social_agent_settings")
        # opt-in: the agent answers only for a business where it was switched on
        cur.execute("INSERT INTO social_agent_settings (tenant_id, enabled) VALUES (current_tenant(), true)")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM meta_pages WHERE page_id = %s OR ig_id = %s", (PAGE, IG))
    social_agent.refresh_pages(force=True)
    return {"sent": sent, "mails": mails, "verdicts": verdicts}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/meta/webhook", content=body, headers={"X-Hub-Signature-256": sig,
                                                                "Content-Type": "application/json"})


def _dm(person, text, mid=None, platform="page", owner=PAGE, echo=False):
    return {"object": platform, "entry": [{"id": owner, "messaging": [{
        "sender": {"id": person}, "recipient": {"id": owner},
        "message": {"mid": mid or "mid." + uuid.uuid4().hex, "text": text, **({"is_echo": True} if echo else {})}}]}]}


def _thread(person):
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT * FROM social_threads WHERE person_id = %s", (person,))
        return cur.fetchone()


def test_page_map_and_subscription(env):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT tenant_id::text AS t, ig_id, subscribed FROM meta_pages WHERE page_id = %s", (PAGE,))
        row = cur.fetchone()
    assert row["t"] == CLINIC and row["ig_id"] == IG and row["subscribed"]
    assert any(p == "/%s/subscribed_apps" % PAGE for p, _ in env["sent"])


def test_webhook_verification_and_signature(client, env):
    ok = client.get("/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                             "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert client.get("/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                               "hub.challenge": "1"}).status_code == 403
    assert _post(client, _dm("p1", "hi"), secret="wrong").status_code == 401


def test_serious_dm_gets_reply_lead_and_owner_alert(client, env):
    person = "psid" + uuid.uuid4().hex[:8]
    env["verdicts"]["family box"] = {
        "reply": "Ji zaroor! Aap kis area mein hain? Delivery details bata dein.", "intent": "price",
        "serious": True, "score": 88, "name": "Ahmed", "phone": "", "wants": "Family box for 6 people"}
    mid = "mid." + uuid.uuid4().hex
    res = _post(client, _dm(person, "Family box ka price? 6 log hain, call me 0300 1234567", mid=mid))
    assert res.status_code == 200 and res.json()["stored"] == 1
    # replied as a Messenger DM to that person
    sends = [d for p, d in env["sent"] if p == "/%s/messages" % PAGE]
    assert sends and sends[-1]["recipient"] == {"id": person} and "kis area" in sends[-1]["message"]["text"]
    th = _thread(person)
    assert th["hot"] and th["intent"] == "price" and th["score"] == 88 and th["lead_id"]
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT name, phone, source::text AS source, priority, service, tags FROM leads WHERE id = %s",
                    (th["lead_id"],))
        lead = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM executions WHERE action = 'social.hot_lead' AND resource_id = %s",
                    (str(th["id"]),))
        assert cur.fetchone()["n"] == 1
    assert lead["source"] == "facebook" and lead["priority"] == 1 and lead["name"] == "Ahmed"
    assert th["person_name"] == "Bilal Khan"            # looked up from Facebook: DMs carry no name
    assert lead["phone"].replace(" ", "") == "03001234567" and "Family box" in lead["service"]
    assert env["mails"] and "Serious client" in env["mails"][-1][1] and str(th["id"]) in env["mails"][-1][2]
    # Meta retries the same delivery: nothing doubles
    n_sent, n_mail = len(env["sent"]), len(env["mails"])
    _post(client, _dm(person, "Family box ka price? 6 log hain, call me 0300 1234567", mid=mid))
    assert len(env["sent"]) == n_sent and len(env["mails"]) == n_mail


def test_echoes_and_unknown_pages_are_ignored(client, env):
    assert _post(client, _dm("x" + uuid.uuid4().hex[:6], "our own message", echo=True)).json()["stored"] == 0
    assert _post(client, _dm(PAGE, "the page talking to itself")).json()["stored"] == 0
    assert _post(client, _dm("someone", "hello", owner="9999999")).json()["stored"] == 0


def test_instagram_comment_public_and_private_reply(client, env):
    person, cid = "ig" + uuid.uuid4().hex[:8], "c" + uuid.uuid4().hex[:10]
    env["verdicts"]["how much"] = {"reply": "Details yeh rahi: ...", "public_reply": "Thank you! DM check karein 📩",
                                   "intent": "price", "serious": False, "score": 40}
    payload = {"object": "instagram", "entry": [{"id": IG, "changes": [{"field": "comments", "value": {
        "id": cid, "text": "how much for this?", "from": {"id": person, "username": "sana.k"}, "media": {"id": "med1"}}}]}]}
    assert _post(client, payload).json()["stored"] == 1
    paths = [p for p, _ in env["sent"]]
    assert "/%s/replies" % cid in paths
    priv = [d for p, d in env["sent"] if p == "/%s/messages" % PAGE and d.get("recipient") == {"comment_id": cid}]
    assert priv and priv[0]["message"]["text"].startswith("Details")
    th = _thread(person)
    assert th["kind"] == "comment" and th["platform"] == "instagram" and th["person_name"] == "sana.k"
    assert th["lead_id"] and not th["hot"]          # asked a price: a lead, but not serious enough to alert


def test_invented_price_is_held_not_sent(client, env):
    person = "p" + uuid.uuid4().hex[:8]
    env["verdicts"]["discount"] = {"reply": "Aaj sirf Rs 999 mein!", "intent": "price", "serious": False, "score": 30}
    before = len(env["sent"])
    _post(client, _dm(person, "koi discount hai?"))
    assert len(env["sent"]) == before
    th = _thread(person)
    assert th["needs_owner"] and "Rs 999" in th["needs_owner"]
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT status FROM social_messages WHERE thread_id = %s AND author = 'ai'", (th["id"],))
        assert [r["status"] for r in cur.fetchall()] == ["held"]


def test_owner_takes_over_then_ai_stays_quiet(client, env):
    person = "p" + uuid.uuid4().hex[:8]
    _post(client, _dm(person, "hello"))
    th = _thread(person)
    res = client.post(f"/social-inbox/threads/{th['id']}/reply", headers=OWNER, json={"text": "Main khud bata deta hoon"})
    assert res.status_code == 200
    assert _thread(person)["ai_paused"]
    before = len(env["sent"])
    _post(client, _dm(person, "acha theek hai"))
    assert len(env["sent"]) == before, "the AI must not answer while the owner is talking"
    client.post(f"/social-inbox/threads/{th['id']}/ai", headers=OWNER, json={"on": True})
    _post(client, _dm(person, "aur kuch?"))
    assert len(env["sent"]) == before + 1
    view = client.get(f"/social-inbox/threads/{th['id']}", headers=OWNER).json()
    assert [m["author"] for m in view["messages"]][:3] == ["customer", "ai", "owner"]


def test_spam_and_switched_off(client, env):
    env["verdicts"]["crypto"] = {"reply": "", "intent": "spam", "serious": False, "score": 0}
    before = len(env["sent"])
    _post(client, _dm("s" + uuid.uuid4().hex[:6], "earn crypto fast click here"))
    assert len(env["sent"]) == before
    assert client.put("/social-inbox/settings", headers=OWNER,
                      json={"enabled": False, "reply_comments": True}).status_code == 200
    _post(client, _dm("q" + uuid.uuid4().hex[:6], "hello?"))
    assert len(env["sent"]) == before
    listing = client.get("/social-inbox/threads", headers=OWNER).json()
    assert listing["settings"]["enabled"] is False and listing["counts"]["total"] >= 1
    assert any(p["page_name"] == "Clinic Page" for p in listing["pages"])


def test_other_business_cannot_read_threads(client, env):
    person = "p" + uuid.uuid4().hex[:8]
    _post(client, _dm(person, "hello"))
    th = _thread(person)
    other = {"X-Tenant-Id": "22222222-2222-2222-2222-222222222222", "X-Role": "owner"}
    assert client.get(f"/social-inbox/threads/{th['id']}", headers=other).status_code == 404
    assert all(t["id"] != str(th["id"]) for t in client.get("/social-inbox/threads", headers=other).json()["threads"])


def test_off_unless_switched_on():
    """A business with no settings row is off: the AI does not answer its customers."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (name, industry, is_test) VALUES ('social-agent-optin-test', 'test', true) "
                    "RETURNING id::text AS id")
        other = cur.fetchone()["id"]
    try:
        with tenant_session(other, "owner") as cur:
            assert social_agent._settings(cur)["enabled"] is False
            cur.execute("INSERT INTO social_agent_settings (tenant_id) VALUES (current_tenant()) RETURNING enabled")
            assert cur.fetchone()["enabled"] is False          # the column default is off too
    finally:
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("DELETE FROM tenants WHERE id = %s", (other,))


def test_a_business_that_is_off_keeps_its_page_untouched(env):
    """Off means Meta is not even asked to send that Page's messages here."""
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("UPDATE social_agent_settings SET enabled = false")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM meta_pages WHERE page_id = %s OR ig_id = %s", (PAGE, IG))
    env["sent"].clear()
    social_agent.refresh_pages(force=True)
    assert not [p for p, _ in env["sent"] if p.endswith("/subscribed_apps")]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) n FROM meta_pages WHERE page_id = %s", (PAGE,))
        assert cur.fetchone()["n"] == 0
