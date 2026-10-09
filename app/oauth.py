"""OAuth flow for client account connections (Meta / Google).

Each client clicks "Connect Facebook" or "Connect Google" → redirected to the
provider's auth screen → grants permission → redirected back to our callback →
token stored in `integrations` (RLS-scoped per tenant).

Config (env):
  META_APP_ID, META_APP_SECRET     — from developers.facebook.com
  GOOGLE_CLIENT_ID, GOOGLE_SECRET  — from Google Cloud Console
  OAUTH_REDIRECT_BASE              — e.g. https://api.techdeva.net (public URL of this API)

The client never shares their password with TechDeva. Tokens are stored per-tenant
and isolated by Row-Level Security — one client's credentials can never leak to another.
"""
import os
import re
import json
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ---------- config ----------
# .strip(): a trailing space or newline pasted into .env makes Meta answer
# "Error validating client secret" (HTTP 400) on the token exchange.
META_APP_ID = os.environ.get("META_APP_ID", "").strip().strip("\"'")
def _unquote(v):
    """A value written as "abc" or 'abc' in .env can reach the process with the
    quotes still on it; Meta then rejects the signature (error 190)."""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v


META_APP_SECRET = _unquote(os.environ.get("META_APP_SECRET", ""))
_RAW_META_SECRET = os.environ.get("META_APP_SECRET", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_SECRET = os.environ.get("GOOGLE_SECRET", "")
# rstrip("/"): the redirect_uri must be byte-identical in the authorize call,
# the token call and Meta's "Valid OAuth Redirect URIs" list.
OAUTH_REDIRECT_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "http://localhost:8000").strip().rstrip("/")
# overridable like the AI base URLs, so a gateway or a stub can stand in
META_GRAPH = os.environ.get("META_GRAPH_BASE", "https://graph.facebook.com").strip().rstrip("/") + "/v21.0"


def meta_redirect_uri() -> str:
    return f"{OAUTH_REDIRECT_BASE}/oauth/meta/callback"


class MetaOAuthError(Exception):
    """Meta refused a step. Carries Meta's own words, never a secret or a code."""

    def __init__(self, step, status, message, code=None, subcode=None, trace=None):
        self.step, self.status, self.code, self.subcode, self.trace = step, status, code, subcode, trace
        self.message = message
        super().__init__(self.describe())

    def describe(self):
        bits = [f"Facebook refused the {self.step} (HTTP {self.status}): {self.message}"]
        if self.code is not None:
            bits.append(f"error code {self.code}" + (f"/{self.subcode}" if self.subcode else ""))
        if self.trace:
            bits.append(f"fbtrace_id {self.trace}")
        return " - ".join(bits)


def _scrub(text, *secrets):
    text = str(text)
    for sec in secrets:
        if sec and len(str(sec)) >= 6:
            text = text.replace(str(sec), "[hidden]")
    return text


def _meta_get(step, url, *hide):
    """GET a Graph URL; on failure raise MetaOAuthError with Meta's own message."""
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = (json.loads(raw) or {}).get("error") or {}
        except ValueError:
            err = {"message": raw[:300]}
        raise MetaOAuthError(step, e.code, _scrub(err.get("message") or "no message", META_APP_SECRET, *hide),
                             err.get("code"), err.get("error_subcode"), err.get("fbtrace_id"))
    except urllib.error.URLError as e:
        raise MetaOAuthError(step, 0, "could not reach graph.facebook.com (%s)" % e.reason)


def meta_diagnose() -> dict:
    """What is configured, and whether Meta accepts this app id + secret.

    Uses an app access token (id|secret) against GET /{app-id}: Meta answers 200
    with the app's name when the pair is valid and 400 when the secret is wrong.
    Nothing secret is returned: only whether it is set, its length and whether
    the raw .env value carried whitespace."""
    out = {
        "app_id": META_APP_ID or None,
        "secret_set": bool(META_APP_SECRET),
        "secret_length": len(META_APP_SECRET),
        "secret_had_whitespace": _RAW_META_SECRET != _RAW_META_SECRET.strip(),
        "secret_had_quotes": _unquote(_RAW_META_SECRET) != _RAW_META_SECRET.strip(),
        # Meta app secrets are 32 lowercase hex characters.
        "secret_format_ok": bool(re.fullmatch(r"[0-9a-f]{32}", META_APP_SECRET)),
        "redirect_uri": meta_redirect_uri(),
        "redirect_base_is_localhost": "localhost" in OAUTH_REDIRECT_BASE,
    }
    if not (META_APP_ID and META_APP_SECRET):
        out["app_check"] = "not configured"
        return out
    try:
        app = _meta_get("app secret check", f"{META_GRAPH}/{META_APP_ID}?fields=id,name&access_token="
                        + urllib.parse.quote(f"{META_APP_ID}|{META_APP_SECRET}"))
        out["app_check"] = "ok"
        out["app_name"] = app.get("name")
    except MetaOAuthError as e:
        out["app_check"] = "refused"
        out["app_error"] = e.describe()
    return out

# Meta permissions needed for ads + publishing + insights
META_SCOPES = ("ads_management,pages_manage_posts,pages_read_engagement,pages_show_list,"
               "instagram_basic,instagram_content_publish,business_management")
# Only what the dashboard uses (Google Ads results). Google's verification
# rejects apps that ask for more; add business.manage back only together with
# a Business Profile feature that uses it.
GOOGLE_SCOPES = "https://www.googleapis.com/auth/adwords"


# ---------- Meta OAuth ----------
def meta_auth_url(tenant_id: str) -> str:
    """URL to redirect the client to for Facebook login + permission grant."""
    params = urllib.parse.urlencode({
        "client_id": META_APP_ID,
        "redirect_uri": meta_redirect_uri(),
        "scope": META_SCOPES,
        "response_type": "code",
        "state": tenant_id,   # carry tenant_id through the redirect so we know who connected
    })
    return f"https://www.facebook.com/v21.0/dialog/oauth?{params}"


def meta_exchange_code(code: str) -> dict:
    """Exchange the auth code for a long-lived token."""
    if not (META_APP_ID and META_APP_SECRET):
        raise MetaOAuthError("setup", 0, "META_APP_ID or META_APP_SECRET is not set in the API container")
    # step 1: code → short-lived token. redirect_uri must be the exact string
    # sent to the authorize dialog (meta_redirect_uri() builds both).
    params = urllib.parse.urlencode({
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": meta_redirect_uri(),
        "code": code,
    })
    short = _meta_get("code-for-token exchange", f"{META_GRAPH}/oauth/access_token?{params}", code)

    # step 2: short-lived → long-lived token (~60 days)
    params2 = urllib.parse.urlencode({
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": short["access_token"],
    })
    long = _meta_get("long-lived token exchange", f"{META_GRAPH}/oauth/access_token?{params2}",
                     short.get("access_token"))

    # fetch the ad accounts this token has access to
    ad_accounts = []
    try:
        with urllib.request.urlopen(
            f"https://graph.facebook.com/v21.0/me/adaccounts?"
            f"fields=name,account_id&access_token={long['access_token']}"
        ) as r:
            ad_accounts = json.loads(r.read()).get("data", [])
    except Exception:
        pass  # non-fatal; the client may not have an ad account yet

    # the pages this person manages, each with its own page token (page tokens
    # made from a long-lived user token do not expire) and any Instagram
    # business account linked to it -- this is what posting uses.
    pages = []
    try:
        with urllib.request.urlopen(
            "https://graph.facebook.com/v21.0/me/accounts?limit=50&fields="
            "id,name,access_token,instagram_business_account%7Bid,username%7D"
            f"&access_token={long['access_token']}", timeout=20
        ) as r:
            for pg in json.loads(r.read()).get("data", []):
                ig = pg.get("instagram_business_account") or {}
                pages.append({"id": pg.get("id"), "name": pg.get("name", ""),
                              "access_token": pg.get("access_token", ""),
                              "ig_id": ig.get("id"), "ig_username": ig.get("username")})
    except Exception:
        pass  # non-fatal; posting will say "connect Facebook" until pages exist

    return {
        "access_token": long["access_token"],
        "token_type": "long_lived",
        "expires_in": long.get("expires_in"),
        "ad_accounts": ad_accounts,
        "pages": pages,
    }


# ---------- Google OAuth ----------
def google_auth_url(tenant_id: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/google/callback",
        "scope": GOOGLE_SCOPES,
        "response_type": "code",
        "access_type": "offline",        # get a refresh_token
        "prompt": "consent",
        "state": tenant_id,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def google_exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_SECRET,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/google/callback",
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ---------- store the connection ----------
def save_connection(persist, tenant_id: str, provider: str, token_data: dict,
                    external_account: str = ""):
    """Write the OAuth token into the integrations table for this tenant.

    The token is encrypted at rest (app/crypto.py) and bound to this tenant and
    provider, so a stolen database dump does not hand over every client's ad
    account. With no key configured it stores plaintext exactly as before —
    nothing breaks — and the preflight check reports it as a launch blocker.
    """
    from .db import tenant_session
    from .crypto import encrypt_json
    token_data = encrypt_json(token_data, tenant_id, provider)
    with tenant_session(tenant_id, "owner") as cur:
        cur.execute("""
            INSERT INTO integrations (tenant_id, provider, status, external_account,
                                       credentials, connected_at)
            VALUES (current_tenant(), %s, 'connected', %s, %s, now())
            ON CONFLICT (tenant_id, provider)
            DO UPDATE SET status='connected', credentials=EXCLUDED.credentials,
                          external_account=EXCLUDED.external_account,
                          connected_at=now(), last_error=NULL
        """, (provider, external_account,
              json.dumps(token_data)))
    # emit an event so the orchestrator (and TechDeva view) knows
    persist.emit(tenant_id, "integration.connected",
                 {"provider": provider, "account": external_account}, "oauth")


def save_failure(tenant_id: str, provider: str, reason: str):
    """Record why a connection attempt failed, without touching a working one.

    A row that is already connected keeps its status and token (the old token
    still works); only last_error changes. A row that never connected becomes
    'error' so the Integrations page shows the reason instead of 'never'."""
    from .db import tenant_session
    try:
        with tenant_session(tenant_id, "owner") as cur:
            cur.execute("""
                INSERT INTO integrations (tenant_id, provider, status, last_error)
                VALUES (current_tenant(), %s, 'error', %s)
                ON CONFLICT (tenant_id, provider) DO UPDATE
                   SET last_error = EXCLUDED.last_error,
                       status = CASE WHEN integrations.status = 'connected'
                                     THEN integrations.status ELSE 'error' END
            """, (provider, str(reason)[:500]))
    except Exception as e:  # recording the failure must not hide the failure
        print("[oauth] could not record failure: %s" % type(e).__name__, flush=True)
