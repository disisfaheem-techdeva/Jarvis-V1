"""The Meta token exchange must report Meta's own reason, never a bare 400,
and never echo the app secret or the authorization code."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import oauth

SECRET = "s3cr3t-app-secret-value"
CODE = "AQD-one-time-code-123456"


class _Fake(BaseHTTPRequestHandler):
    reply = (400, {})
    seen = []

    def do_GET(self):
        _Fake.seen.append(self.path)
        code, body = _Fake.reply
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def graph(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(oauth, "META_GRAPH", "http://127.0.0.1:%d/v21.0" % srv.server_port)
    monkeypatch.setattr(oauth, "META_APP_ID", "894985669798073")
    monkeypatch.setattr(oauth, "META_APP_SECRET", SECRET)
    monkeypatch.setattr(oauth, "OAUTH_REDIRECT_BASE", "https://api.techdeva.net")
    _Fake.seen = []
    yield _Fake
    srv.shutdown()


def test_redirect_uri_mismatch_is_reported_in_metas_words(graph):
    graph.reply = (400, {"error": {"message": "Error validating verification code. Please make sure your "
                                               "redirect_uri is identical to the one you used in the OAuth dialog request",
                                   "type": "OAuthException", "code": 100, "error_subcode": 36008,
                                   "fbtrace_id": "AbCdEf"}})
    with pytest.raises(oauth.MetaOAuthError) as e:
        oauth.meta_exchange_code(CODE)
    msg = e.value.describe()
    assert "redirect_uri is identical" in msg and "code 100/36008" in msg and "code-for-token" in msg
    assert SECRET not in msg and CODE not in msg


def test_bad_secret_is_reported_and_the_secret_is_scrubbed(graph):
    graph.reply = (400, {"error": {"message": "Error validating client secret. (%s)" % SECRET, "code": 1}})
    with pytest.raises(oauth.MetaOAuthError) as e:
        oauth.meta_exchange_code(CODE)
    assert "Error validating client secret" in str(e.value) and SECRET not in str(e.value)


def test_same_redirect_uri_in_dialog_and_exchange(graph):
    graph.reply = (400, {"error": {"message": "x", "code": 100}})
    with pytest.raises(oauth.MetaOAuthError):
        oauth.meta_exchange_code(CODE)
    dialog = oauth.meta_auth_url("state")
    from urllib.parse import parse_qs, urlparse
    sent = parse_qs(urlparse(graph.seen[0]).query)["redirect_uri"][0]
    assert parse_qs(urlparse(dialog).query)["redirect_uri"][0] == sent == "https://api.techdeva.net/oauth/meta/callback"


def test_diagnose_names_a_refused_secret_without_leaking_it(graph):
    graph.reply = (400, {"error": {"message": "Invalid OAuth access token signature.", "code": 190}})
    d = oauth.meta_diagnose()
    assert d["app_check"] == "refused" and "code 190" in d["app_error"]
    assert SECRET not in json.dumps(d) and d["secret_length"] == len(SECRET)


def test_diagnose_ok(graph):
    graph.reply = (200, {"id": "894985669798073", "name": "TechDeva"})
    d = oauth.meta_diagnose()
    assert d["app_check"] == "ok" and d["app_name"] == "TechDeva"
