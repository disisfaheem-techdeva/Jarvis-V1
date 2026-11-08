"""Speech gateway (Phase 2, app/speech.py, /jarvis/v1/transcribe).

The providers' HTTP answers are faked; the gateway, the API, sign-in and the
usage log are real. Checked: by default the server does not hear (the browser
does) and says so; with Gemini or an OpenAI-compatible service set, a recording
becomes text in Roman Urdu; bad, empty or oversized recordings are refused;
credits used up are said; voice can be switched off or sent to another service;
and every hearing and every register page read is written to the usage log
(ai_usage only takes known kinds: a wrong kind is silently not counted).

Runs against a SCRATCH database only.
"""
import base64
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import speech, usage  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

T, UID = str(uuid.uuid4()), str(uuid.uuid4())
H = {"Authorization": "Bearer " + issue_token(tenant_id=T, user_id=UID, role="owner")}
WEBM = "data:audio/webm;codecs=opus;base64," + base64.b64encode(b"\x1aE\xdf\xa3" + b"\x00" * 400).decode()


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test) VALUES (%s, 'Speech Shop', 'grocery', true)", (T,))
        cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, 'Owner', %s, 'owner')",
                    (UID, T, "%s@speech-test.local" % UID[:8]))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM ai_usage WHERE tenant_id = %s", (T,))
        cur.execute("DELETE FROM executions WHERE tenant_id = %s", (T,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (T,))


class R:
    def __init__(self, code, body=None, content=b""):
        self.status_code, self._b, self.content = code, body or {}, content

    def json(self):
        return self._b


def _usage(feature):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT kind, ok FROM ai_usage WHERE feature = %s AND tenant_id = %s", (feature, T))
        return [dict(r) for r in cur.fetchall()]


def test_by_default_the_browser_hears(client, monkeypatch):
    monkeypatch.delenv("SPEECH_STT_PROVIDER", raising=False)
    assert client.get("/jarvis/v1", headers=H).json()["speech"] == {"input": "client", "output": "audio/wav"}
    r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": WEBM})
    assert r.status_code == 501 and "browser" in r.json()["error"]["message"]


def test_gemini_hears_roman_urdu_and_it_is_counted(client, monkeypatch):
    monkeypatch.setenv("SPEECH_STT_PROVIDER", "gemini")
    monkeypatch.setattr("app.platform_key.gemini_key", lambda: "test-key-not-real")
    sent = {}

    def post(url, params=None, json=None, timeout=None, **kw):
        sent["url"], sent["json"] = url, json
        return R(200, {"candidates": [{"content": {"parts": [{"text": " do kilo cheeni aur\n ek sabun ka bill bana do "}]}}]})
    monkeypatch.setattr(speech.requests, "post", post)
    assert client.get("/jarvis/v1", headers=H).json()["speech"]["input"] == "server"
    r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": WEBM, "lang": "ur"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "do kilo cheeni aur ek sabun ka bill bana do"
    part = sent["json"]["contents"][0]["parts"]
    assert part[0]["inline_data"]["mime_type"] == "audio/webm" and "Roman Urdu" in part[1]["text"]
    assert _usage("speech_in") == [{"kind": "voice", "ok": True}]


def test_an_openai_compatible_service_can_hear_instead(client, monkeypatch):
    monkeypatch.setenv("SPEECH_STT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    sent = {}

    def post(url, headers=None, data=None, files=None, timeout=None, **kw):
        sent.update(url=url, data=data, files=files)
        return R(200, {"text": "Ahmed ne 1000 de diye"})
    monkeypatch.setattr(speech.requests, "post", post)
    r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": WEBM, "lang": "ur"})
    assert r.status_code == 200 and r.json()["text"] == "Ahmed ne 1000 de diye"
    assert sent["url"].endswith("/v1/audio/transcriptions") and sent["data"]["language"] == "ur"
    assert sent["files"]["file"][0] == "speech.webm" and sent["data"]["model"] == "whisper-1"


def test_bad_recordings_are_refused_and_nothing_is_sent(client, monkeypatch):
    monkeypatch.setenv("SPEECH_STT_PROVIDER", "gemini")
    monkeypatch.setattr(speech.requests, "post", lambda *a, **k: pytest.fail("nothing should be sent"))
    for audio, code in (("hello", 400), ("data:video/mp4;base64,AAAA", 400), ("data:audio/webm;base64,", 400),
                        ("data:audio/webm;base64," + base64.b64encode(b"\x00" * (speech.MAX_AUDIO + 1)).decode(), 413)):
        r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": audio})
        assert r.status_code == code, (audio[:30], r.text)
    assert TestClient(app).post("/jarvis/v1/transcribe", json={"audio": WEBM}).status_code in (400, 401)


def test_used_up_credits_and_a_busy_service_are_said(client, monkeypatch):
    monkeypatch.setenv("SPEECH_STT_PROVIDER", "gemini")
    monkeypatch.setattr("app.platform_key.gemini_key", lambda: "test-key-not-real")
    monkeypatch.setattr(usage, "check", lambda: "This month's AI credits are used up.")
    r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": WEBM})
    assert r.status_code == 402 and "credits" in r.json()["error"]["message"]
    monkeypatch.setattr(usage, "check", lambda: None)
    monkeypatch.setattr(speech.requests, "post", lambda *a, **k: R(429))
    r = client.post("/jarvis/v1/transcribe", headers=H, json={"audio": WEBM})
    assert r.status_code == 429 and "Type it" in r.json()["error"]["message"]


def test_voice_can_be_switched_off_or_sent_elsewhere(client, monkeypatch):
    monkeypatch.setenv("SPEECH_TTS_PROVIDER", "off")
    assert client.get("/jarvis/v1", headers=H).json()["speech"]["output"] is None
    r = client.post("/jarvis/v1/speech", headers=H, json={"text": "Bill ban gaya, unique " + uuid.uuid4().hex[:6]})
    assert r.status_code == 503
    monkeypatch.setenv("SPEECH_TTS_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(usage, "check", lambda: None)
    pcm = b"\x01\x00" * 2400
    monkeypatch.setattr(speech.requests, "post", lambda url, **k: R(200, content=pcm))
    r = client.post("/jarvis/v1/speech", headers=H, json={"text": "Total Rs 770, unique " + uuid.uuid4().hex[:6]})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"


def test_a_register_page_read_is_counted(monkeypatch):
    """Phase 7's reader charged a kind ai_usage refuses, so reads went uncounted; now they are counted."""
    from app import ai, register_import as RI
    monkeypatch.setattr(ai, "chain_entries", lambda: [("gemini", "gemini-test")])
    monkeypatch.setenv(ai._KEY_ENV["gemini"], "test-key-not-real")
    monkeypatch.setattr(usage, "check", lambda: None)
    monkeypatch.setattr(RI.requests, "post", lambda *a, **k: R(200, {"candidates": [{"content": {"parts": [
        {"text": '{"rows": [{"name": "Bilal", "amount": 100, "confidence": 0.9}]}'}]}}]}))
    tok = usage._METER.set({"tenant_id": T})
    try:
        RI.read_page(b"\xff\xd8\xff", "image/jpeg", "balances")
    finally:
        usage._METER.reset(tok)
    assert _usage("register_read") == [{"kind": "photo_read", "ok": True}]


def test_jarvis_v1_and_register_reads_are_counted_to_the_business():
    """The usage meter attributes AI calls by address; Jarvis v1 and register reads were missing."""
    from app.usage import AGENT_BY_PATH
    agent = lambda p: next((a for x, a in AGENT_BY_PATH if p.startswith(x)), None)  # noqa: E731
    assert agent("/jarvis/v1/messages") == "jarvis" and agent("/jarvis/v1/transcribe") == "jarvis"
    assert agent("/ledger/imports/abc/pages/def/read") == "register"
