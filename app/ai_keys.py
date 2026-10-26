"""API Manager: AI provider keys, which WhatsApp agent uses which, and how much
each agent used (founder console > API Manager; Faheem/Rafay, 26 Oct 2026).

Until now one GEMINI_API_KEY in the platform .env was copied into every agent,
so every business shared one free-tier quota and nobody could see it run out.
The founder can now:

  * add keys for Gemini, any listed OpenAI-compatible provider (Groq,
    OpenRouter, Mistral, ...) or a custom one with its own base URL, each
    stored only as a crypto.encrypt_json envelope; the browser only ever sees
    the last 4 characters;
  * test a key: one model-list request (no generation quota), whose result is
    kept and shown (HTTP code, time, models listed, chosen model listed);
  * try a key: one tiny reply from its model, shown with the time it took;
  * choose, per agent, which key and model it runs with (or the platform .env
    key), so paying clients can be put on paid keys;
  * see each agent's daily calls, rate-limit hits (429) and overloads (503).

Applying a choice needs root on the host (the key goes into the agent's own
/docker/hermes-<key>/agent.env, a non-Gemini model into its config.yaml, and
the agent is re-created), which the API container cannot do. So the console
records the choice as 'pending' and ops/agent-template/ai-keys-sync.sh, run by
cron every minute, applies it and reports back through the CLI at the bottom
of this file. The same script reports what each agent really runs with and
the daily usage counts.

Never returned, logged or audited: a key's value. Only its last 4 characters.
"""
import hashlib
import json
import os
import re
import sys
import time

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from . import crypto
from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# name: label, OpenAI-compatible base URL (None: gemini, reached natively by the
# agents), the default model, and the URL Test uses to check the key when the
# base URL's /models does not need one (OpenRouter lists models to anyone).
PROVIDERS = {
    "gemini": {"label": "Google Gemini", "base_url": None, "model": "gemini-2.5-flash-lite"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": None,
                   "auth_url": "https://openrouter.ai/api/v1/key"},
    "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest"},
    "cerebras": {"label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "model": "llama-3.3-70b"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "model": None},
    "xai": {"label": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "model": None},
    "custom": {"label": "Custom (OpenAI-compatible)", "base_url": None, "model": None},
}
GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
GEMINI_CHAT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,120}$")

# Agents this console must never touch: hand-built (irfan) and the clinic/salon
# workspace stack. Matched exactly, not by "salon" anywhere in the name: that
# would also catch a template agent such as emaan-beauty-salon-*.
PROTECTED = re.compile(r"^(irfan$|hermes-workspace|workspace|.*ofsd)")

AAD = "techdeva"          # crypto AAD tenant part: keys belong to TechDeva, not a business


def _admin(x_tenant_id, x_role, authorization, session):
    try:
        _tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role != "techdeva_admin":
        raise HTTPException(403, "the API Manager is admin-only")
    return uid


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _seal(provider: str, value: str) -> dict:
    if not crypto.enabled():
        # A key in plaintext in the database is the one thing this table must
        # never hold; refuse rather than fall back.
        raise HTTPException(503, "Key storage is not available: CREDENTIAL_ENCRYPTION_KEY is not configured.")
    return crypto.encrypt_json({"key": value}, AAD, "ai_key:" + provider)


def _open(row) -> str:
    return crypto.decrypt_json(row["secret"], AAD, "ai_key:" + row["provider"])["key"]


def _platform_last4():
    v = (os.environ.get("GEMINI_API_KEY") or "").strip()
    return v[-4:] if v else None


def _base_url(provider: str, given) -> str | None:
    """The endpoint a key is used at. Custom: the founder's own, https and on
    the public internet (the agents and this API will call it)."""
    if provider == "gemini":
        return None
    if provider != "custom":
        return PROVIDERS[provider]["base_url"]
    url = str(given or "").strip().rstrip("/")
    for tail in ("/chat/completions", "/models"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    if not re.match(r"^https://[^\s/]+(/[^\s]*)?$", url) or len(url) > 300:
        raise HTTPException(400, "Give the provider's base URL, starting with https:// "
                                 "(e.g. https://api.example.com/v1).")
    from .safe_http import assert_fetchable
    try:
        assert_fetchable(url)
    except Exception as e:
        raise HTTPException(400, "That base URL cannot be used: %s." % e)
    return url


def _model(provider: str, given) -> str | None:
    m = str(given or "").strip() or PROVIDERS[provider]["model"]
    if m and not MODEL_RE.match(m):
        raise HTTPException(400, "That model name has characters a model name cannot have.")
    return m or None


def _call(method, url, value, how, body=None, timeout=20):
    """One request to a provider, never following a redirect (a key must not be
    carried to another host). Returns (response or None, ms, error class)."""
    from .safe_http import assert_fetchable
    headers = {"x-goog-api-key": value} if how == "goog" else {"Authorization": "Bearer " + value}
    t0 = time.monotonic()
    try:
        assert_fetchable(url)
        r = requests.request(method, url, headers=headers, json=body, timeout=timeout, allow_redirects=False)
    except Exception as e:
        return None, int((time.monotonic() - t0) * 1000), e.__class__.__name__
    return r, int((time.monotonic() - t0) * 1000), None


def _verdict(provider, code):
    if code == 200:
        return "ok", None
    if code == 429:
        return "rate_limited", "%s says: too many requests (429)" % provider
    if code in (400, 401, 403):
        return "invalid", "%s refused the key (%d)" % (provider, code)
    return "error", "%s answered %d" % (provider, code)


def test_key(provider: str, value: str, base_url=None, model=None):
    """One model-list request (no generation quota). Returns (status, error,
    detail); status is ok / invalid / rate_limited / error. Nothing returned
    contains the key."""
    detail = {"kind": "test", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if provider == "gemini":
        r, ms, err = _call("GET", GEMINI_MODELS, value, "goog")
    else:
        auth_url = PROVIDERS[provider].get("auth_url")
        if auth_url:
            r, ms, err = _call("GET", auth_url, value, "bearer")
            if r is not None and r.status_code == 200:
                r2, _ms2, _e2 = _call("GET", base_url + "/models", value, "bearer")
                r = r2 if r2 is not None and r2.status_code == 200 else r
        else:
            r, ms, err = _call("GET", base_url + "/models", value, "bearer")
    detail["ms"] = ms
    if r is None:
        detail["http"] = None
        return "error", "could not reach %s (%s)" % (provider, err), detail
    detail["http"] = r.status_code
    status, error = _verdict(provider, r.status_code)
    if status == "ok":
        try:
            data = r.json()
            items = data.get("models") if provider == "gemini" else data.get("data")
            ids = [str(m.get("name") or m.get("id") or "").replace("models/", "") for m in (items or [])
                   if isinstance(m, dict)]
            ids = sorted(i for i in ids if i and MODEL_RE.match(i))
        except ValueError:
            ids = []
        detail["models"] = len(ids)
        detail["sample"] = ids[:300]
        if model:
            detail["model_listed"] = model in ids if ids else None
    return status, error, detail


def try_reply(provider: str, value: str, base_url=None, model=None):
    """One tiny chat completion with the key's model: proves the model answers.
    Costs one request of quota. Returns (status, error, detail)."""
    model = model or PROVIDERS[provider]["model"]
    detail = {"kind": "reply", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model}
    if not model:
        return "error", "Choose a model for this key first.", detail
    url = GEMINI_CHAT if provider == "gemini" else base_url + "/chat/completions"
    body = {"model": model, "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}]}
    r, ms, err = _call("POST", url, value, "bearer", body=body, timeout=40)
    detail["ms"] = ms
    if r is None:
        detail["http"] = None
        return "error", "could not reach %s (%s)" % (provider, err), detail
    detail["http"] = r.status_code
    status, error = _verdict(provider, r.status_code)
    if status == "ok":
        try:
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
            detail["reply"] = str(msg.get("content") or "")[:80]
        except ValueError:
            detail["reply"] = ""
    elif r.status_code == 404:
        error = "%s does not have the model %s (404)" % (provider, model)
    return status, error, detail


def _agents(cur):
    """Every template agent the platform knows, with its business, its plan and
    whether it pays (status active)."""
    cur.execute("""SELECT w.agent_key, w.status::text AS wa_status, w.tenant_id, t.name AS business, t.is_test,
                          t.status::text AS tenant_status, ca.plan_key, p.name AS plan_name, ca.monthly_fee
                     FROM whatsapp_connections w JOIN tenants t ON t.id = w.tenant_id
                     LEFT JOIN client_accounts ca ON ca.tenant_id = t.id
                     LEFT JOIN plans p ON p.key = ca.plan_key
                    WHERE w.agent_key IS NOT NULL ORDER BY (t.status::text = 'active') DESC, t.name, w.agent_key""")
    out = []
    for r in cur.fetchall():
        if PROTECTED.match(r["agent_key"] or ""):
            continue
        a = dict(r)
        a["paying"] = a["tenant_status"] == "active"
        a["monthly_fee"] = float(a["monthly_fee"]) if a["monthly_fee"] is not None else None
        out.append(a)
    return out


def make_ai_keys_router() -> APIRouter:
    router = APIRouter()

    @router.get("/techdeva/api-keys")
    def overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT id, provider, label, owner_note, last4, status, last_test_at, last_error,
                                  base_url, model, test_detail, created_at, disabled_at FROM ai_keys ORDER BY created_at""")
            keys = [dict(r) for r in cur.fetchall()]
            agents = _agents(cur)
            cur.execute("SELECT * FROM ai_key_assignments")
            assign = {r["agent_key"]: dict(r) for r in cur.fetchall()}
            cur.execute("SELECT * FROM ai_agent_live")
            live = {r["agent_key"]: dict(r) for r in cur.fetchall()}
            cur.execute("""SELECT day, agent_key, model, ok, rate_limited, overloaded, failed, key_last4,
                                  (day = current_date) AS is_today
                             FROM ai_agent_usage WHERE day > current_date - 7 ORDER BY day DESC, agent_key, model""")
            usage = [dict(r) for r in cur.fetchall()]
        by_last4 = {k["last4"]: k for k in keys}
        today, week = {}, {}
        for u in usage:
            for bucket in ([week, today] if u["is_today"] else [week]):
                a = bucket.setdefault(u["agent_key"], {"ok": 0, "rate_limited": 0, "overloaded": 0, "failed": 0})
                for f in a:
                    a[f] += u[f]
        plat = _platform_last4()
        for a in agents:
            k = a["agent_key"]
            a["assignment"] = assign.get(k)
            a["live"] = live.get(k)
            a["today"] = today.get(k)
            a["week"] = week.get(k)
            l4 = (live.get(k) or {}).get("live_last4")
            a["live_key_label"] = (by_last4[l4]["label"] if l4 in by_last4 else
                                   "Platform key" if l4 and l4 == plat else None)
        for key in keys:
            used_by = [a["agent_key"] for a in agents if (a["live"] or {}).get("live_last4") == key["last4"]]
            key["agents"] = used_by
            key["assigned"] = [a["agent_key"] for a in agents
                               if (a["assignment"] or {}).get("key_id") == key["id"]]
            key["week"] = {f: sum((week.get(x) or {}).get(f, 0) for x in used_by)
                           for f in ("ok", "rate_limited", "overloaded", "failed")}
        return {"keys": keys, "agents": agents, "usage": usage, "platform_key_last4": plat,
                "providers": [{"key": k, "label": v["label"], "base_url": v["base_url"], "model": v["model"]}
                              for k, v in PROVIDERS.items()],
                "agent_providers": list(PROVIDERS), "encryption": crypto.enabled()}

    @router.post("/techdeva/api-keys")
    def add(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        provider = str(body.get("provider") or "").strip().lower()
        label = str(body.get("label") or "").strip()
        note = (str(body.get("owner_note") or "").strip() or None)
        value = str(body.get("key") or "").strip()
        if provider not in PROVIDERS:
            raise HTTPException(400, "Choose a provider.")
        if not label or len(label) > 80:
            raise HTTPException(400, "Give the key a name (up to 80 characters), e.g. \"Ali - Google\".")
        if note and len(note) > 200:
            raise HTTPException(400, "The note is too long (200 characters at most).")
        if len(value) < 8 or len(value) > 400 or re.search(r"\s", value):
            raise HTTPException(400, "That does not look like an API key. Paste the whole key, with no spaces.")
        base_url = _base_url(provider, body.get("base_url"))
        model = _model(provider, body.get("model"))
        status, error, detail = test_key(provider, value, base_url, model)
        fp = _fingerprint(value)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT label FROM ai_keys WHERE fingerprint = %s", (fp,))
            dup = cur.fetchone()
            if dup:
                raise HTTPException(409, "This key is already saved as \"%s\"." % dup["label"])
            cur.execute("""INSERT INTO ai_keys (provider, label, owner_note, secret, last4, fingerprint,
                                                status, last_test_at, last_error, created_by, base_url, model, test_detail)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s)
                           RETURNING id, last4, status""",
                        (provider, label, note, json.dumps(_seal(provider, value)), value[-4:], fp,
                         status, error, str(uid) if uid else None, base_url, model, json.dumps(detail)))
            row = dict(cur.fetchone())
            record(cur, action="techdeva.ai_key.add", resource="ai_keys", resource_id=str(row["id"]),
                   actor=str(uid) if uid else None, status="ok",
                   detail={"provider": provider, "label": label, "last4": row["last4"], "test": status,
                           "base_url": base_url, "model": model},
                   tenant_id=None)
        return {"id": str(row["id"]), "last4": row["last4"], "status": status, "error": error, "detail": detail}

    def _get(cur, key_id):
        cur.execute("SELECT * FROM ai_keys WHERE id = %s", (key_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such key.")
        return dict(row)

    def _run(key_id, uid, fn, action):
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
        status, error, detail = fn(row["provider"], _open(row), row["base_url"], row["model"])
        old = row["test_detail"] or {}
        if "sample" not in detail and old.get("sample"):
            # a reply test keeps the model list the last key test found (the Model box offers it)
            detail["sample"], detail["models"] = old["sample"], old.get("models")
        if row["status"] == "disabled":
            status = "disabled"          # a test does not switch a key back on
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE ai_keys SET status = %s, last_error = %s, last_test_at = now(), test_detail = %s,
                                  updated_at = now() WHERE id = %s""", (status, error, json.dumps(detail), key_id))
            record(cur, action=action, resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok" if status in ("ok", "disabled") else "error",
                   detail={"last4": row["last4"], "result": status, "http": detail.get("http"),
                           "ms": detail.get("ms")}, tenant_id=None)
        return {"status": status, "error": error, "detail": detail}

    @router.post("/techdeva/api-keys/{key_id}/test")
    def test(key_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        return _run(key_id, uid, test_key, "techdeva.ai_key.test")

    @router.post("/techdeva/api-keys/{key_id}/try")
    def try_(key_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One short reply from the key's model (uses one request of quota)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        return _run(key_id, uid, try_reply, "techdeva.ai_key.try")

    @router.put("/techdeva/api-keys/{key_id}")
    def edit(key_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None), authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Name, model, or off/on. A new model reaches the agents using this key
        through a fresh pending assignment (applied within a minute)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
            label = str(body.get("label") or row["label"]).strip()[:80]
            model = _model(row["provider"], body["model"]) if "model" in body else row["model"]
            disabled = body.get("disabled")
            if disabled is True:
                cur.execute("""SELECT count(*) AS n FROM ai_key_assignments WHERE key_id = %s""", (key_id,))
                if cur.fetchone()["n"]:
                    raise HTTPException(409, "Agents are set to use this key. Give them another key first.")
                cur.execute("""UPDATE ai_keys SET label = %s, model = %s, status = 'disabled', disabled_at = now(),
                                      updated_at = now() WHERE id = %s""", (label, model, key_id))
            elif disabled is False:
                cur.execute("""UPDATE ai_keys SET label = %s, model = %s, status = 'untested', disabled_at = NULL,
                                      updated_at = now() WHERE id = %s""", (label, model, key_id))
            else:
                cur.execute("UPDATE ai_keys SET label = %s, model = %s, updated_at = now() WHERE id = %s",
                            (label, model, key_id))
            reapply = []
            if model != row["model"]:
                cur.execute("""UPDATE ai_key_assignments SET status = 'pending', requested_at = now(), applied_at = NULL,
                                      error = NULL WHERE key_id = %s RETURNING agent_key""", (key_id,))
                reapply = [r["agent_key"] for r in cur.fetchall()]
            record(cur, action="techdeva.ai_key.edit", resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"last4": row["last4"], "label": label, "disabled": disabled, "model": model,
                           "reapply": reapply}, tenant_id=None)
        return {"ok": True, "reapply": reapply}

    @router.delete("/techdeva/api-keys/{key_id}")
    def delete(key_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
            cur.execute("SELECT agent_key FROM ai_key_assignments WHERE key_id = %s", (key_id,))
            users = [r["agent_key"] for r in cur.fetchall()]
            cur.execute("SELECT agent_key FROM ai_agent_live WHERE live_last4 = %s", (row["last4"],))
            users += [r["agent_key"] for r in cur.fetchall() if r["agent_key"] not in users]
            if users:
                raise HTTPException(409, "In use by %s. Give those agents another key first." % ", ".join(users))
            cur.execute("DELETE FROM ai_keys WHERE id = %s", (key_id,))
            record(cur, action="techdeva.ai_key.delete", resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"provider": row["provider"], "label": row["label"], "last4": row["last4"]}, tenant_id=None)
        return {"ok": True}

    @router.put("/techdeva/api-keys/assign/{agent_key}")
    def assign(agent_key: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None), authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Choose the key an agent runs with. key_id null = the platform .env key.
        Recorded as pending; ai-keys-sync.sh applies it within a minute."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        key_id = body.get("key_id") or None
        if PROTECTED.match(agent_key):
            raise HTTPException(403, "This agent is managed by hand and cannot be changed here.")
        with tenant_session(None, "techdeva_admin") as cur:
            if agent_key not in {a["agent_key"] for a in _agents(cur)}:
                raise HTTPException(404, "No such agent.")
            last4, provider, model = _platform_last4(), "gemini", None
            if key_id:
                row = _get(cur, key_id)
                if row["status"] in ("disabled", "invalid"):
                    raise HTTPException(409, "That key is %s. Test it or choose another." % row["status"])
                if row["provider"] != "gemini" and not (row["model"] and row["base_url"]):
                    raise HTTPException(400, "Choose a model for this key first (Keys table, Model).")
                last4, provider, model = row["last4"], row["provider"], row["model"]
            cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, status, requested_by, requested_at,
                                                           applied_at, error)
                           VALUES (%s, %s, 'pending', %s, now(), NULL, NULL)
                           ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, status = 'pending',
                                 requested_by = EXCLUDED.requested_by, requested_at = now(), applied_at = NULL, error = NULL""",
                        (agent_key, key_id, str(uid) if uid else None))
            record(cur, action="techdeva.ai_key.assign", resource="ai_key_assignments", resource_id=agent_key,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"agent": agent_key, "key_last4": last4, "platform_key": key_id is None,
                           "provider": provider, "model": model}, tenant_id=None)
        return {"status": "pending", "key_last4": last4}

    return router


# ---------------------------------------------------------------- cron CLI
# Run inside the api container by ops/agent-template/ai-keys-sync.sh:
#   python -m app.ai_keys pending   -> agent \t last4 \t provider \t base_url \t model \t key
#                                      (one line per pending agent; "-" for an empty field,
#                                       provider gemini + "-" model = keep the agent's Gemini model)
#   python -m app.ai_keys applied <agent> ok|failed <live_last4> [error]
#   python -m app.ai_keys live    < lines "agent \t last4 \t running(0/1)"
#   python -m app.ai_keys usage   < JSON lines {day, agent_key, model, ok, rate_limited, overloaded, failed, key_last4}
# `pending` is the only command that prints a key, and only to the script's pipe.

def _cli(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "pending":
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT a.agent_key, a.key_id, k.provider, k.secret, k.last4, k.status, k.base_url, k.model
                             FROM ai_key_assignments a LEFT JOIN ai_keys k ON k.id = a.key_id
                            WHERE a.status = 'pending' ORDER BY a.requested_at""")
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if PROTECTED.match(r["agent_key"]):
                continue
            if r["key_id"] is None:
                value, provider, base, model = (os.environ.get("GEMINI_API_KEY") or "").strip().strip("'\""), "gemini", "-", "-"
            elif r["status"] in ("disabled", "invalid"):
                continue
            else:
                value, provider = _open(r), r["provider"]
                base = r["base_url"] or "-"
                # gemini: the agent keeps its own Gemini model chain unless one is chosen
                model = r["model"] if provider != "gemini" else "-"
                if provider != "gemini" and (base == "-" or not r["model"]):
                    continue
            fields = (r["agent_key"], value[-4:], provider, base, model, value)
            if value and all("\t" not in f and "\n" not in f and f for f in fields):
                sys.stdout.write("\t".join(fields) + "\n")
        return 0
    if cmd == "applied":
        agent, result = argv[2], argv[3]
        live4 = argv[4] if len(argv) > 4 else None
        err = (argv[5] if len(argv) > 5 else None)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE ai_key_assignments SET status = %s, applied_at = CASE WHEN %s = 'applied' THEN now() END,
                                  error = %s WHERE agent_key = %s""",
                        ("applied" if result == "ok" else "failed", "applied" if result == "ok" else "failed",
                         (err or None) and err[:300], agent))
            record(cur, action="techdeva.ai_key.applied", resource="ai_key_assignments", resource_id=agent,
                   actor="system", actor_type="system", status="ok" if result == "ok" else "error",
                   detail={"agent": agent, "live_last4": live4}, error=(err or None) and err[:300], tenant_id=None)
        return 0
    if cmd == "live":
        rows = [l.rstrip("\n").split("\t") for l in sys.stdin if l.strip()]
        with tenant_session(None, "techdeva_admin") as cur:
            for agent, last4, running in rows:
                cur.execute("""INSERT INTO ai_agent_live (agent_key, live_last4, running, seen_at) VALUES (%s, %s, %s, now())
                               ON CONFLICT (agent_key) DO UPDATE SET live_last4 = EXCLUDED.live_last4,
                                     running = EXCLUDED.running, seen_at = now()""",
                            (agent, last4 or None, running == "1"))
        return 0
    if cmd == "usage":
        rows = [json.loads(l) for l in sys.stdin if l.strip()]
        with tenant_session(None, "techdeva_admin") as cur:
            for r in rows:
                cur.execute("""INSERT INTO ai_agent_usage (day, agent_key, model, ok, rate_limited, overloaded, failed, key_last4, updated_at)
                               VALUES (%(day)s, %(agent_key)s, %(model)s, %(ok)s, %(rate_limited)s, %(overloaded)s, %(failed)s, %(key_last4)s, now())
                               ON CONFLICT (day, agent_key, model) DO UPDATE SET ok = EXCLUDED.ok,
                                     rate_limited = EXCLUDED.rate_limited, overloaded = EXCLUDED.overloaded,
                                     failed = EXCLUDED.failed, key_last4 = EXCLUDED.key_last4, updated_at = now()""", r)
        return 0
    sys.stderr.write("usage: python -m app.ai_keys pending|applied|live|usage\n")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
