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
    # gemini-2.5-flash-lite is "no longer available to new users" (Google, 26 Oct 2026): new keys 404 on it
    "gemini": {"label": "Google Gemini", "base_url": None, "model": "gemini-3.5-flash-lite"},
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


# Models a WhatsApp agent can chat with (the list Test brings back also holds
# speech, image and embedding models), and the default picked when the
# founder does not choose: the first of these the key really has.
NOT_CHAT = re.compile(r"(?i)(tts|whisper|embed|image|imagen|veo|aqa|guard|vision-only|audio|live|computer-use|robotics|omni)")
PREFERRED = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash",
               "gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash"],
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3-32b",
             "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.1-8b-instant"],
}


def chat_models(ids):
    return [m for m in (ids or []) if not NOT_CHAT.search(m)]


def pick_model(provider, ids):
    ids = chat_models(ids)
    low = {m.lower(): m for m in ids}
    for p in PREFERRED.get(provider, []):
        if p.lower() in low:
            return low[p.lower()]
    return ids[0] if ids else PROVIDERS[provider]["model"]


# ---------------------------------------------------------------- voice notes
# One Groq key (Whisper) transcribes every template agent's voice notes:
# platform_settings ai_stt_key / ai_stt_model; ai-keys-sync.sh writes it to each
# agent as GROQ_API_KEY and turns stt on (stt_config.py).
STT_PREFERRED = ["whisper-large-v3-turbo", "whisper-large-v3", "distil-whisper-large-v3-en"]


def stt_models(ids):
    return [m for m in (ids or []) if "whisper" in m.lower()]


def pick_stt_model(ids):
    ms = stt_models(ids)
    low = {m.lower(): m for m in ms}
    for p in STT_PREFERRED:
        if p in low:
            return low[p]
    # a key may list its speech model under another name ("Whisper", which 404s on
    # /audio/transcriptions); the standard Groq name answers (checked 26 Oct 2026)
    return "whisper-large-v3-turbo"


def _voice(cur):
    cur.execute("SELECT key, value FROM platform_settings WHERE key IN ('ai_stt_key', 'ai_stt_model', 'ai_stt_agents')")
    d = {r["key"]: r["value"] for r in cur.fetchall()}
    return {"key_id": d.get("ai_stt_key") or None, "model": d.get("ai_stt_model") or None,
            "agents": [a for a in (d.get("ai_stt_agents") or "").split(",") if a]}


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


# ---------------------------------------------------------------- test bench + defaults by plan
# Phase 2 of the Hermes training framework. The bench runs customer questions
# through a COPY of an agent with a chosen key (ops/agent-template/model_bench.py,
# cron); defaults say which key paying and trial businesses' agents should use.
DEFAULT_QUESTIONS = [
    "Assalam o alaikum, aap ke packages ya services kya hain aur price kitni hai?",
    "Hi, what do you offer and where are you located?",
    "کیا آپ کل صبح کھلے ہیں؟",
    "Mujhe booking karni hai, kya process hai?",
    "Kya aap free mein kaam karte hain?",
]
BENCH_MAX_Q = 8
DEFAULT_KEYS = ("ai_key_default_paying", "ai_key_default_trial")


def _usable(row):
    return row["status"] not in ("disabled", "invalid") and (row["provider"] == "gemini" or (row["model"] and row["base_url"]))


def _passed_bench(cur, key_id):
    """A key may be the default for many agents only after a full pass on the bench in the last 7 days."""
    cur.execute("""SELECT 1 FROM ai_model_tests WHERE key_id IS NOT DISTINCT FROM %s AND status = 'done'
                      AND passed = total AND total > 0 AND finished_at > now() - interval '7 days' LIMIT 1""", (key_id,))
    return cur.fetchone() is not None


def _defaults(cur):
    cur.execute("SELECT key, value FROM platform_settings WHERE key = ANY(%s)", (list(DEFAULT_KEYS),))
    d = {r["key"]: (r["value"] or None) for r in cur.fetchall()}
    return {"paying": d.get("ai_key_default_paying"), "trial": d.get("ai_key_default_trial")}


# ---------------------------------------------------------------- On/Off and auto-hold
# The founder switches an agent off/on (console), and an agent whose key keeps
# hitting its limit with no answer is put on hold by the platform until the
# key's quota resets (db/84 agent_power; applied by ai-keys-sync.sh).
HOLD_HITS = 3                # refused calls in 15 minutes, with none answered


def _autohold_on(cur):
    cur.execute("SELECT value FROM platform_settings WHERE key = 'ai_autohold'")
    r = cur.fetchone()
    return bool(r and r["value"] == "1")


def _quota_reset(provider):
    """When a free daily quota comes back: Gemini resets at midnight Pacific time;
    for any other provider try again in an hour."""
    import datetime
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(datetime.timezone.utc)
    if provider != "gemini":
        return now + datetime.timedelta(hours=1)
    pt = now.astimezone(ZoneInfo("America/Los_Angeles"))
    nxt = (pt + datetime.timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
    return nxt.astimezone(datetime.timezone.utc)


def _set_power(cur, agent, want, by, auto=False, reason=None, resume_at=None):
    cur.execute("""INSERT INTO agent_power (agent_key, want, auto, reason, resume_at, status, requested_by, requested_at,
                                            applied_at, error)
                   VALUES (%s, %s, %s, %s, %s, 'pending', %s, now(), NULL, NULL)
                   ON CONFLICT (agent_key) DO UPDATE SET want = EXCLUDED.want, auto = EXCLUDED.auto, reason = EXCLUDED.reason,
                         resume_at = EXCLUDED.resume_at, status = 'pending', requested_by = EXCLUDED.requested_by,
                         requested_at = now(), applied_at = NULL, error = NULL""",
                (agent, want, auto, reason, resume_at, by))


def autohold(rows):
    """rows from ai_recent.py: put on hold every agent whose key keeps hitting its limit."""
    out = []
    with tenant_session(None, "techdeva_admin") as cur:
        if not _autohold_on(cur):
            return out
        agents = {a["agent_key"]: a for a in _agents(cur)}
        cur.execute("SELECT agent_key, want FROM agent_power")
        power = {r["agent_key"]: r["want"] for r in cur.fetchall()}
        cur.execute("SELECT last4, provider FROM ai_keys")
        prov = {r["last4"]: r["provider"] for r in cur.fetchall()}
        for r in rows:
            agent = r.get("agent_key")
            if agent not in agents or PROTECTED.match(agent or "") or power.get(agent) == "off":
                continue
            if r.get("limited", 0) < HOLD_HITS or r.get("ok", 0) > 0:
                continue
            cur.execute("""SELECT 1 FROM ai_key_assignments WHERE agent_key = %s AND status = 'pending'
                           UNION ALL SELECT 1 FROM ai_key_switches WHERE agent_key = %s AND at > now() - interval '30 minutes'""",
                        (agent, agent))
            if cur.fetchone():
                continue                                    # just moved to another key: give it the chance
            provider = prov.get(r.get("live_last4"), "gemini")
            until = _quota_reset(provider)
            why = "AI key ...%s hit its limit %d times in 15 minutes with no answer" % (r.get("live_last4"), r.get("limited", 0))
            _set_power(cur, agent, "off", "auto-hold", auto=True, reason=why, resume_at=until)
            record(cur, action="techdeva.agent.autohold", resource="agent_power", resource_id=agent, actor="auto-hold",
                   actor_type="system", status="ok", detail={"agent": agent, "why": why, "resume_at": until.isoformat()},
                   tenant_id=None)
            t = until.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Karachi")).strftime("%d %b %H:%M")
            _hold_incident(cur, None, "agent.on_hold", "%s on hold: AI limit reached" % agent,
                           "%s. No replies are sent while it is on hold; it switches back on at %s (Pakistan time). "
                           "Give it another key in API Manager and press On to bring it back sooner." % (why, t))
            _hold_incident(cur, agents[agent]["tenant_id"], "agent.on_hold", "Your WhatsApp agent is paused",
                           "Its AI service reached today's limit, so it is not replying for now (customers' messages "
                           "stay in your WhatsApp). It starts again by itself at %s." % t)
            out.append("%s on hold until %s" % (agent, until.isoformat()))
    return out


def _upsert_incident(cur, tenant_id, kind, severity, title, detail):
    """One open incident per business and kind (a database rule): a new event
    updates it (title = the latest, detail = latest first, older lines kept)."""
    cur.execute("""SELECT id, detail FROM incidents WHERE tenant_id IS NOT DISTINCT FROM %s AND kind = %s
                    AND status = 'open' FOR UPDATE""", (tenant_id, kind))
    r = cur.fetchone()
    if r:
        lines = [detail] + [l for l in (r["detail"] or "").split(chr(10)) if l and l != detail]
        cur.execute("""UPDATE incidents SET title = %s, detail = %s, severity = %s, last_seen_at = now(),
                              seen_count = seen_count + 1 WHERE id = %s""",
                    (title, chr(10).join(lines)[:3000], severity, r["id"]))
    else:
        cur.execute("INSERT INTO incidents (tenant_id, kind, severity, title, detail) VALUES (%s, %s, %s, %s, %s)",
                    (tenant_id, kind, severity, title, detail))


def _hold_incident(cur, tenant_id, kind, title, detail):
    _upsert_incident(cur, tenant_id, kind, "warn", title, detail)


def _resolve_hold_incidents(cur, agent, tenant_id):
    """This business's hold notice closes when its agent is back; the founder's
    closes when no agent is on hold any more."""
    if tenant_id:
        cur.execute("""UPDATE incidents SET status = 'resolved', resolved_at = now()
                        WHERE kind = 'agent.on_hold' AND status = 'open' AND tenant_id = %s""", (tenant_id,))
    cur.execute("SELECT 1 FROM agent_power WHERE want = 'off' AND auto LIMIT 1")
    if not cur.fetchone():
        cur.execute("""UPDATE incidents SET status = 'resolved', resolved_at = now()
                        WHERE kind = 'agent.on_hold' AND status = 'open' AND tenant_id IS NULL""")

def _autoswitch_on(cur):
    cur.execute("SELECT value FROM platform_settings WHERE key = 'ai_key_autoswitch'")
    r = cur.fetchone()
    return bool(r and r["value"] == "1")


LIMIT_HITS = 3              # refused calls in 15 minutes ...
SWITCH_GAP = "30 minutes"   # ... with none answered moves the agent, at most once per this


def autoswitch(rows):
    """rows: {agent_key, ok, limited, live_last4} for the last 15 minutes (ai_recent.py).
    An agent whose key keeps hitting its limit and answered nothing is moved to the
    next key in rotation that has passed the test bench and is not itself hitting
    its limit. Every move, and every time no spare key is left, opens a founder
    incident. Returns lines describing what was done."""
    out = []
    with tenant_session(None, "techdeva_admin") as cur:
        if not _autoswitch_on(cur):
            return out
        hot = {r.get("live_last4") for r in rows if r.get("limited", 0) >= LIMIT_HITS}
        cur.execute("""SELECT * FROM ai_keys WHERE in_pool AND status NOT IN ('disabled','invalid','rate_limited')
                        ORDER BY pool_order, created_at""")
        pool = [dict(k) for k in cur.fetchall() if _usable(k) and _passed_bench(cur, k["id"])]
        known = {a["agent_key"] for a in _agents(cur)}
        for r in rows:
            agent = r.get("agent_key")
            if agent not in known or PROTECTED.match(agent or ""):
                continue
            if r.get("limited", 0) < LIMIT_HITS or r.get("ok", 0) > 0:
                continue
            cur.execute("SELECT 1 FROM ai_key_switches WHERE agent_key = %s AND at > now() - interval '" + SWITCH_GAP + "'", (agent,))
            if cur.fetchone():
                continue
            cur.execute("SELECT status FROM ai_key_assignments WHERE agent_key = %s", (agent,))
            a = cur.fetchone()
            if a and a["status"] == "pending":
                continue
            used = {}
            cur.execute("SELECT live_last4, count(*) AS n FROM ai_agent_live WHERE running GROUP BY 1")
            for u in cur.fetchall():
                used[u["live_last4"]] = u["n"]
            spare = [k for k in pool if k["last4"] != r.get("live_last4") and k["last4"] not in hot]
            spare.sort(key=lambda k: (k["pool_order"], used.get(k["last4"], 0)))
            if not spare:
                _incident(cur, "ai_key.pool_empty", "critical", "No spare AI key for %s" % agent,
                          "Its key (...%s) keeps hitting its limit and no key in rotation is free and tested. "
                          "Add a key (API Manager), run the test bench, and put it in rotation." % r.get("live_last4"))
                out.append("%s: no spare key" % agent)
                continue
            k = spare[0]
            cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, status, requested_by, requested_at, applied_at, error)
                           VALUES (%s, %s, 'pending', 'auto-switch', now(), NULL, NULL)
                           ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, status = 'pending',
                                 requested_by = 'auto-switch', requested_at = now(), applied_at = NULL, error = NULL""",
                        (agent, k["id"]))
            why = "%d calls refused for a limit, none answered, in 15 minutes" % r.get("limited", 0)
            cur.execute("""INSERT INTO ai_key_switches (agent_key, from_last4, to_key_id, to_last4, reason)
                           VALUES (%s, %s, %s, %s, %s)""", (agent, r.get("live_last4"), k["id"], k["last4"], why))
            record(cur, action="techdeva.ai_key.autoswitch", resource="ai_key_assignments", resource_id=agent,
                   actor="auto-switch", actor_type="system", status="ok",
                   detail={"agent": agent, "from_last4": r.get("live_last4"), "to_last4": k["last4"], "why": why},
                   tenant_id=None)
            _incident(cur, "ai_key.switched", "warn", "%s moved to key %s" % (agent, k["label"]),
                      "Key ...%s: %s. Moved to %s (...%s); it applies within a minute." % (
                          r.get("live_last4"), why, k["label"], k["last4"]))
            used[k["last4"]] = used.get(k["last4"], 0) + 1
            out.append("%s: ...%s -> %s ...%s" % (agent, r.get("live_last4"), k["label"], k["last4"]))
    return out


def _incident(cur, kind, severity, title, detail):
    """A founder incident (no business): one open per kind, updated by each new event."""
    _upsert_incident(cur, None, kind, severity, title, detail)


# ---------------------------------------------------------------- automatic mode
# The founder only adds keys (platform_settings ai_auto, on unless set to 0):
#   * Groq keys: the first working one transcribes voice notes for every agent;
#   * Google keys: each new one is put on the test bench by itself (two short
#     questions on the TechDeva agent); once it passes it goes into rotation
#     and every agent not on a working, tested key is moved to the key with the
#     fewest agents. Limits are then handled by auto-switch and auto-hold.
AUTO_QUESTIONS = DEFAULT_QUESTIONS[:2]


def _auto_on(cur):
    cur.execute("SELECT value FROM platform_settings WHERE key = 'ai_auto'")
    r = cur.fetchone()
    return not (r and r["value"] == "0")


def _set_setting(cur, key, value):
    cur.execute("""INSERT INTO platform_settings (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (key, value))


def _bench_agent(cur, agents):
    """Where new keys are tried: TechDeva's own agent, else the first running one."""
    cur.execute("SELECT value FROM platform_settings WHERE key = 'home_tenant_id'")
    h = cur.fetchone()
    home = h["value"] if h else None
    for a in agents:
        if home and str(a["tenant_id"]) == home:
            return a["agent_key"]
    running = [a for a in agents if (a.get("live") or {}).get("running")]
    return (running or agents or [{}])[0].get("agent_key")


def auto_balance():
    out = []
    with tenant_session(None, "techdeva_admin") as cur:
        if not _auto_on(cur):
            return out
        cur.execute("SELECT * FROM ai_keys ORDER BY created_at")
        keys = [dict(k) for k in cur.fetchall()]
        agents = _agents(cur)
        cur.execute("SELECT * FROM ai_agent_live")
        live = {r["agent_key"]: dict(r) for r in cur.fetchall()}
        for a in agents:
            a["live"] = live.get(a["agent_key"])

        # voice notes: the first working Groq key, for every agent
        groq = [k for k in keys if k["provider"] == "groq" and k["status"] == "ok"]
        v = _voice(cur)
        if groq and (v["key_id"] not in {str(k["id"]) for k in groq} or v["agents"]):
            k = next((k for k in groq if str(k["id"]) == v["key_id"]), groq[0])
            model = pick_stt_model(((k["test_detail"] or {}).get("stt_models")))
            _set_setting(cur, "ai_stt_key", str(k["id"]))
            _set_setting(cur, "ai_stt_model", model)
            _set_setting(cur, "ai_stt_agents", "")
            out.append("voice notes: %s (%s) for every agent" % (k["label"], model))
        elif not groq and v["key_id"]:
            _set_setting(cur, "ai_stt_key", "")
            out.append("voice notes off: no working Groq key")

        # Google keys: an untested one goes on the bench by itself
        bench_agent = _bench_agent(cur, agents)
        gem = [k for k in keys if k["provider"] == "gemini" and k["status"] == "ok" and k["model"]]
        for k in gem:
            if _passed_bench(cur, k["id"]):
                continue
            cur.execute("""SELECT 1 FROM ai_model_tests WHERE key_id = %s AND (status IN ('pending','running')
                              OR requested_at > now() - interval '6 hours')""", (k["id"],))
            if cur.fetchone() or not bench_agent:
                continue
            cur.execute("SELECT 1 FROM ai_model_tests WHERE agent_key = %s AND status IN ('pending','running')", (bench_agent,))
            if cur.fetchone():
                continue
            cur.execute("""INSERT INTO ai_model_tests (agent_key, key_id, model, questions, total, requested_by)
                           VALUES (%s, %s, %s, %s, %s, 'automatic')""",
                        (bench_agent, k["id"], "(agent's Gemini chain)", json.dumps(AUTO_QUESTIONS), len(AUTO_QUESTIONS)))
            out.append("bench queued for %s on %s" % (k["label"], bench_agent))
            break                                   # one at a time: the bench takes a few minutes

        # tested Google keys: in rotation, and agents spread across them
        ready = [k for k in gem if _passed_bench(cur, k["id"])]
        for i, k in enumerate(ready):
            if not k["in_pool"]:
                cur.execute("UPDATE ai_keys SET in_pool = true, pool_order = %s, updated_at = now() WHERE id = %s",
                            (i + 1, k["id"]))
                out.append("%s in rotation" % k["label"])
        if not ready:
            return out
        cur.execute("SELECT agent_key, key_id, status FROM ai_key_assignments")
        assign = {r["agent_key"]: dict(r) for r in cur.fetchall()}
        ready_ids = {str(k["id"]) for k in ready}
        load = {str(k["id"]): 0 for k in ready}
        for a in assign.values():
            if str(a["key_id"] or "") in load:
                load[str(a["key_id"])] += 1
        for a in agents:
            ak = a["agent_key"]
            cur_a = assign.get(ak)
            if cur_a and (cur_a["status"] == "pending" or str(cur_a["key_id"] or "") in ready_ids):
                continue
            cur.execute("SELECT 1 FROM ai_key_switches WHERE agent_key = %s AND at > now() - interval '30 minutes'", (ak,))
            if cur.fetchone():
                continue
            kid = min(load, key=lambda x: (load[x], [str(k["id"]) for k in ready].index(x)))
            cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, model, status, requested_by, requested_at, applied_at, error)
                           VALUES (%s, %s, NULL, 'pending', 'automatic', now(), NULL, NULL)
                           ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, model = NULL, status = 'pending',
                                 requested_by = 'automatic', requested_at = now(), applied_at = NULL, error = NULL""", (ak, kid))
            load[kid] += 1
            label = next(k["label"] for k in ready if str(k["id"]) == kid)
            record(cur, action="techdeva.ai_key.auto_assign", resource="ai_key_assignments", resource_id=ak,
                   actor="automatic", actor_type="system", status="ok", detail={"agent": ak, "key": label}, tenant_id=None)
            out.append("%s -> %s" % (ak, label))
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
                                  base_url, model, test_detail, in_pool, pool_order, created_at, disabled_at
                             FROM ai_keys ORDER BY created_at""")
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
            cur.execute("""SELECT id, agent_key, key_id, model, status, passed, total, results, error, requested_at, finished_at
                             FROM ai_model_tests ORDER BY requested_at DESC LIMIT 30""")
            benches = [dict(r) for r in cur.fetchall()]
            defaults = _defaults(cur)
            for k in keys:
                k["bench_passed"] = _passed_bench(cur, k["id"])
            platform_bench_passed = _passed_bench(cur, None)
            voice = _voice(cur)
            auto_on = _auto_on(cur)
            autoswitch_on = _autoswitch_on(cur)
            cur.execute("SELECT agent_key, from_last4, to_last4, reason, at FROM ai_key_switches ORDER BY at DESC LIMIT 20")
            switches = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM agent_power")
            power = {r["agent_key"]: dict(r) for r in cur.fetchall()}
            autohold_on = _autohold_on(cur)
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
            a["power"] = power.get(k)
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
                "agent_providers": list(PROVIDERS), "encryption": crypto.enabled(),
                "benches": benches, "defaults": defaults, "default_questions": DEFAULT_QUESTIONS,
                "platform_bench_passed": platform_bench_passed, "autohold": autohold_on,
                "autoswitch": autoswitch_on, "switches": switches, "voice": voice, "auto": auto_on}

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
        typed = str(body.get("model") or "").strip()
        model = _model(provider, typed) if typed else None
        status, error, detail = test_key(provider, value, base_url, model)
        if detail.get("sample"):
            detail["stt_models"] = stt_models(detail["sample"])
            detail["sample"] = chat_models(detail["sample"])
        if not model:
            model = pick_model(provider, detail.get("sample")) if status == "ok" else PROVIDERS[provider]["model"]
            if detail.get("sample"):
                detail["model_listed"] = model in detail["sample"]
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
        if detail.get("sample"):
            detail["stt_models"] = stt_models(detail["sample"])
            detail["sample"] = chat_models(detail["sample"])
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
        want_model = str(body.get("model") or "").strip() or None
        if want_model and not MODEL_RE.match(want_model):
            raise HTTPException(400, "That is not a model name.")
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
                if row["provider"] != "gemini" and not ((want_model or row["model"]) and row["base_url"]):
                    raise HTTPException(400, "Choose a model for this key first (Keys table, Model).")
                listed = ((row["test_detail"] or {}).get("sample")) or []
                if want_model and listed and want_model not in listed:
                    raise HTTPException(400, "%s does not have the model %s. Test the key to refresh its list." % (row["label"], want_model))
                last4, provider, model = row["last4"], row["provider"], want_model or row["model"]
            elif want_model:
                raise HTTPException(400, "The platform key keeps the agent's own Gemini models.")
            cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, model, status, requested_by, requested_at,
                                                           applied_at, error)
                           VALUES (%s, %s, %s, 'pending', %s, now(), NULL, NULL)
                           ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, model = EXCLUDED.model, status = 'pending',
                                 requested_by = EXCLUDED.requested_by, requested_at = now(), applied_at = NULL, error = NULL""",
                        (agent_key, key_id, want_model, str(uid) if uid else None))
            record(cur, action="techdeva.ai_key.assign", resource="ai_key_assignments", resource_id=agent_key,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"agent": agent_key, "key_last4": last4, "platform_key": key_id is None,
                           "provider": provider, "model": model}, tenant_id=None)
        return {"status": "pending", "key_last4": last4}

    @router.put("/techdeva/agent-power/{agent_key}")
    def power(agent_key: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
              x_role: str | None = Header(None), authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Switch a WhatsApp agent off or on (applied within a minute)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        if PROTECTED.match(agent_key):
            raise HTTPException(403, "This agent is managed by hand and cannot be switched here.")
        on = bool(body.get("on"))
        with tenant_session(None, "techdeva_admin") as cur:
            if agent_key not in {a["agent_key"] for a in _agents(cur)}:
                raise HTTPException(404, "No such agent.")
            _set_power(cur, agent_key, "on" if on else "off", str(uid) if uid else "founder",
                       reason=None if on else "switched off by the founder")
            record(cur, action="techdeva.agent.power", resource="agent_power", resource_id=agent_key,
                   actor=str(uid) if uid else None, status="ok", detail={"agent": agent_key, "on": on}, tenant_id=None)
        return {"agent": agent_key, "want": "on" if on else "off", "status": "pending"}

    @router.put("/techdeva/ai-autohold")
    def set_autohold(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        on = bool(body.get("on"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO platform_settings (key, value) VALUES ('ai_autohold', %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", ("1" if on else "0",))
            record(cur, action="techdeva.agent.autohold_setting", resource="platform_settings", resource_id="ai_autohold",
                   actor=str(uid) if uid else None, status="ok", detail={"on": on}, tenant_id=None)
        return {"on": on}

    @router.put("/techdeva/ai-autoswitch")
    def set_autoswitch(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        on = bool(body.get("on"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO platform_settings (key, value) VALUES ('ai_key_autoswitch', %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", ("1" if on else "0",))
            record(cur, action="techdeva.ai_key.autoswitch_setting", resource="platform_settings",
                   resource_id="ai_key_autoswitch", actor=str(uid) if uid else None, status="ok", detail={"on": on},
                   tenant_id=None)
        return {"on": on}

    @router.put("/techdeva/ai-pool/{key_id}")
    def set_pool(key_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None), authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Put a key in rotation (the auto-switcher may move agents onto it) and its place in the order."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
            in_pool = bool(body.get("in_pool", row["in_pool"]))
            try:
                order = int(body.get("pool_order", row["pool_order"]))
            except (TypeError, ValueError):
                raise HTTPException(400, "The order is a number.")
            if in_pool and not _usable(row):
                raise HTTPException(409, "That key cannot run an agent (off, refused, or no model chosen).")
            cur.execute("UPDATE ai_keys SET in_pool = %s, pool_order = %s, updated_at = now() WHERE id = %s",
                        (in_pool, max(0, min(order, 999)), key_id))
            record(cur, action="techdeva.ai_key.pool", resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"last4": row["last4"], "in_pool": in_pool, "order": order}, tenant_id=None)
        return {"in_pool": in_pool, "pool_order": order}

    @router.put("/techdeva/ai-auto")
    def set_auto(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        on = bool(body.get("on"))
        with tenant_session(None, "techdeva_admin") as cur:
            _set_setting(cur, "ai_auto", "1" if on else "0")
            record(cur, action="techdeva.ai_key.auto_setting", resource="platform_settings", resource_id="ai_auto",
                   actor=str(uid) if uid else None, status="ok", detail={"on": on}, tenant_id=None)
        return {"on": on}

    @router.put("/techdeva/ai-voice")
    def set_voice(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The Groq key that transcribes every agent's voice notes (null = voice notes off).
        Applied by ai-keys-sync.sh within a minute; each agent restarts once."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        key_id = body.get("key_id") or None
        with tenant_session(None, "techdeva_admin") as cur:
            model = ""
            if key_id:
                row = _get(cur, key_id)
                if row["provider"] != "groq":
                    raise HTTPException(400, "Voice notes need a Groq key (Whisper).")
                if row["status"] in ("disabled", "invalid"):
                    raise HTTPException(409, "That key is %s. Test it or choose another." % row["status"])
                listed = (row["test_detail"] or {}).get("stt_models")
                model = str(body.get("model") or "").strip() or pick_stt_model(listed)
                if listed and model not in listed and model not in STT_PREFERRED:
                    raise HTTPException(400, "%s does not have %s." % (row["label"], model))
            only = body.get("agents")
            if only is not None:
                known = {a["agent_key"] for a in _agents(cur)}
                only = [str(a) for a in (only or []) if str(a) in known]
            pairs = [("ai_stt_key", key_id or ""), ("ai_stt_model", model)]
            if only is not None:
                pairs.append(("ai_stt_agents", ",".join(only)))
            for k, v in pairs:
                cur.execute("""INSERT INTO platform_settings (key, value) VALUES (%s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (k, v))
            record(cur, action="techdeva.ai_key.voice", resource="platform_settings", resource_id="ai_stt_key",
                   actor=str(uid) if uid else None, status="ok", detail={"key_id": key_id, "model": model, "agents": only},
                   tenant_id=None)
        return {"key_id": key_id, "model": model or None, "agents": only}

    @router.post("/techdeva/ai-bench")
    def bench(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Queue a bench run: these questions, through a copy of this agent, on this key
        (key_id null = the platform key). Uses one request of the key's quota per question."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        agent_key = str(body.get("agent_key") or "")
        key_id = body.get("key_id") or None
        qs = [" ".join(str(q).split()) for q in (body.get("questions") or DEFAULT_QUESTIONS)]
        qs = [q for q in qs if q][:BENCH_MAX_Q]
        if not qs or any(len(q) > 300 for q in qs):
            raise HTTPException(400, "Give 1 to %d questions of up to 300 characters." % BENCH_MAX_Q)
        if PROTECTED.match(agent_key):
            raise HTTPException(403, "This agent is managed by hand and cannot be tested here.")
        with tenant_session(None, "techdeva_admin") as cur:
            if agent_key not in {a["agent_key"] for a in _agents(cur)}:
                raise HTTPException(404, "No such agent.")
            model = None
            if key_id:
                row = _get(cur, key_id)
                if not _usable(row):
                    raise HTTPException(409, "That key cannot run an agent (off, refused, or no model chosen).")
                model = row["model"] if row["provider"] != "gemini" else "(agent's Gemini chain)"
            cur.execute("SELECT 1 FROM ai_model_tests WHERE agent_key = %s AND status IN ('pending','running')", (agent_key,))
            if cur.fetchone():
                raise HTTPException(409, "A test for this agent is already waiting or running.")
            cur.execute("""INSERT INTO ai_model_tests (agent_key, key_id, model, questions, total, requested_by)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                        (agent_key, key_id, model or "(platform Gemini key)", json.dumps(qs), len(qs), str(uid) if uid else None))
            tid = str(cur.fetchone()["id"])
            record(cur, action="techdeva.ai_key.bench", resource="ai_model_tests", resource_id=tid,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"agent": agent_key, "key_id": key_id, "questions": len(qs)}, tenant_id=None)
        return {"id": tid, "status": "pending"}

    @router.put("/techdeva/ai-key-defaults")
    def set_defaults(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Which key paying and trial businesses' agents should run on ("" = the platform key)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            out = {}
            for group, setting in (("paying", "ai_key_default_paying"), ("trial", "ai_key_default_trial")):
                if group not in body:
                    continue
                kid = body.get(group) or ""
                if kid:
                    row = _get(cur, kid)
                    if not _usable(row):
                        raise HTTPException(409, "%s cannot run an agent (off, refused, or no model chosen)." % row["label"])
                cur.execute("""INSERT INTO platform_settings (key, value) VALUES (%s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (setting, kid))
                out[group] = kid or None
            record(cur, action="techdeva.ai_key.defaults", resource="platform_settings", resource_id="ai_key_default",
                   actor=str(uid) if uid else None, status="ok", detail=out, tenant_id=None)
        return {"ok": True, "defaults": out}

    @router.post("/techdeva/ai-key-defaults/apply")
    def apply_defaults(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Put every paying (or trial) business's agent on its group's default key.
        A key that has not fully passed the bench in the last 7 days is refused:
        one bad key must not take every agent of a group down at once."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        group = str(body.get("group") or "")
        if group not in ("paying", "trial"):
            raise HTTPException(400, "Choose paying or trial.")
        with tenant_session(None, "techdeva_admin") as cur:
            kid = _defaults(cur)[group]
            if kid:
                row = _get(cur, kid)
                if not _usable(row):
                    raise HTTPException(409, "The %s default key cannot run an agent now." % group)
            if not _passed_bench(cur, kid):
                raise HTTPException(409, "Run the test bench with this key first (all answers must pass, within 7 days). "
                                         "Then apply it to every %s agent." % group)
            cur.execute("SELECT agent_key, key_id, status FROM ai_key_assignments")
            cur_assign = {r["agent_key"]: r for r in cur.fetchall()}
            moved = []
            for a in _agents(cur):
                if a["paying"] != (group == "paying"):
                    continue
                c = cur_assign.get(a["agent_key"])
                if c and str(c["key_id"] or "") == str(kid or "") and c["status"] in ("pending", "applied"):
                    continue
                if not c and not kid:
                    continue                                    # already on the platform key
                cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, status, requested_by, requested_at, applied_at, error)
                               VALUES (%s, %s, 'pending', %s, now(), NULL, NULL)
                               ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, status = 'pending',
                                     requested_by = EXCLUDED.requested_by, requested_at = now(), applied_at = NULL, error = NULL""",
                            (a["agent_key"], kid, str(uid) if uid else None))
                moved.append(a["agent_key"])
            record(cur, action="techdeva.ai_key.apply_defaults", resource="ai_key_assignments", resource_id=group,
                   actor=str(uid) if uid else None, status="ok", detail={"group": group, "key_id": kid, "agents": moved},
                   tenant_id=None)
        return {"group": group, "agents": moved}

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
            cur.execute("""SELECT a.agent_key, a.key_id, k.provider, k.secret, k.last4, k.status, k.base_url,
                                  coalesce(a.model, k.model) AS model
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
                # gemini: the chosen model becomes the agent's first one (new keys cannot use
                # gemini-2.5-flash-lite); "-" keeps the agent's own chain
                model = r["model"] or "-"
                if provider != "gemini" and (base == "-" or not r["model"]):
                    continue
            fields = (r["agent_key"], value[-4:], provider, base, model, value)
            if value and all("\t" not in f and "\n" not in f and f for f in fields):
                sys.stdout.write("\t".join(fields) + "\n")
        return 0
    if cmd == "power-pending":
        # agent \t on|off, for ai-keys-sync.sh; an auto hold whose time has come is switched back on first
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE agent_power SET want = 'on', status = 'pending', requested_by = 'auto-resume',
                                  requested_at = now(), applied_at = NULL, error = NULL
                            WHERE auto AND want = 'off' AND resume_at IS NOT NULL AND resume_at <= now()""")
            cur.execute("SELECT agent_key, want FROM agent_power WHERE status = 'pending' ORDER BY requested_at")
            for r in cur.fetchall():
                if not PROTECTED.match(r["agent_key"]):
                    sys.stdout.write("%s\t%s\n" % (r["agent_key"], r["want"]))
        return 0
    if cmd == "power-applied":
        agent, result = argv[2], argv[3]
        err = argv[4] if len(argv) > 4 else None
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE agent_power SET status = %s, applied_at = CASE WHEN %s THEN now() END, error = %s
                            WHERE agent_key = %s RETURNING want""", ("applied" if result == "ok" else "failed",
                                                                     result == "ok", err, agent))
            r = cur.fetchone()
            if r and r["want"] == "on" and result == "ok":
                cur.execute("SELECT tenant_id FROM whatsapp_connections WHERE agent_key = %s LIMIT 1", (agent,))
                w = cur.fetchone()
                _resolve_hold_incidents(cur, agent, w["tenant_id"] if w else None)
            record(cur, action="techdeva.agent.power_applied", resource="agent_power", resource_id=agent, actor="system",
                   actor_type="system", status="ok" if result == "ok" else "error",
                   detail={"agent": agent, "want": r["want"] if r else None}, error=err, tenant_id=None)
        return 0
    if cmd == "autohold":
        rows = [json.loads(l) for l in sys.stdin if l.strip()]
        for line in autohold(rows):
            print(line)
        return 0
    if cmd == "autoswitch":
        rows = [json.loads(l) for l in sys.stdin if l.strip()]
        for line in autoswitch(rows):
            print(line)
        return 0
    if cmd == "auto-balance":
        for line in auto_balance():
            print(line)
        return 0
    if cmd == "stt-agents":
        # the agents that get voice notes, one per line (nothing = every agent)
        with tenant_session(None, "techdeva_admin") as cur:
            for a in _voice(cur)["agents"]:
                sys.stdout.write(a + "\n")
        return 0
    if cmd == "stt-key":
        # last4 \t model \t key of the voice-notes key (nothing = off); for ai-keys-sync.sh only
        with tenant_session(None, "techdeva_admin") as cur:
            v = _voice(cur)
            if not v["key_id"]:
                return 0
            cur.execute("SELECT * FROM ai_keys WHERE id = %s", (v["key_id"],))
            r = cur.fetchone()
        if not r or r["status"] in ("disabled", "invalid") or r["provider"] != "groq":
            return 0
        value = _open(dict(r))
        model = v["model"] or "whisper-large-v3-turbo"
        if value and "\t" not in value and "\n" not in value:
            sys.stdout.write("%s\t%s\t%s\n" % (value[-4:], model, value))
        return 0
    if cmd == "bench-next":
        # one queued bench run, as a JSON line for model_bench.py (contains the key: pipe only)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE ai_model_tests SET status = 'failed', error = 'stopped (took too long)', finished_at = now()
                            WHERE status = 'running' AND requested_at < now() - interval '45 minutes'""")
            cur.execute("""SELECT t.id, t.agent_key, t.questions, k.provider, k.base_url, k.model, k.secret, k.status AS kstatus
                             FROM ai_model_tests t LEFT JOIN ai_keys k ON k.id = t.key_id
                            WHERE t.status = 'pending' ORDER BY t.requested_at LIMIT 1 FOR UPDATE OF t SKIP LOCKED""")
            r = cur.fetchone()
            if not r:
                return 0
            r = dict(r)
            cur.execute("UPDATE ai_model_tests SET status = 'running' WHERE id = %s", (r["id"],))
        if PROTECTED.match(r["agent_key"]):
            return 0
        if r["provider"] is None:
            job = {"provider": "gemini", "base_url": None, "model": None,
                   "key": (os.environ.get("GEMINI_API_KEY") or "").strip().strip("'\"")}
        else:
            job = {"provider": r["provider"], "base_url": r["base_url"], "model": r["model"], "key": _open(r)}
        job.update({"id": str(r["id"]), "agent_key": r["agent_key"], "questions": r["questions"]})
        sys.stdout.write(json.dumps(job) + "\n")
        return 0
    if cmd == "bench-done":
        tid = argv[2]
        data = json.loads(sys.stdin.read() or "{}")
        results = data.get("results") or []
        passed = sum(1 for x in results if x.get("ok"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE ai_model_tests SET status = %s, results = %s, passed = %s, total = %s, error = %s,
                                  finished_at = now() WHERE id = %s""",
                        ("failed" if data.get("error") else "done", json.dumps(results), passed, len(results) or None,
                         (data.get("error") or None) and str(data["error"])[:500], tid))
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
