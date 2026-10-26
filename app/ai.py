"""The model adapter — one place the platform talks to a language model.

Provider is CONFIGURABLE, never hard-coded. `AI_PROVIDER` selects it and
`AI_MODEL` overrides the model; the default is Anthropic (Claude). A tenant on a
different model must never be silently swapped onto another one, so nothing
outside this file names a vendor.

    AI_PROVIDER=anthropic   ANTHROPIC_API_KEY=...   (default)
    AI_PROVIDER=gemini      GEMINI_API_KEY=...
    AI_PROVIDER=openai      OPENAI_API_KEY=...
    AI_PROVIDER=none        generation disabled (agents park for a human)

--------------------------------------------------------------------------
THE RULE THIS FILE EXISTS TO ENFORCE
--------------------------------------------------------------------------
No key, no network, or an unparseable answer => `AIUnavailable`. The calling
agent then parks the task as `needs_human` and raises an `ops.alert`. It NEVER
falls back to canned copy, and it never ships a half-parsed answer. A client
reading a placeholder caption in their dashboard is worse than an empty slot with
an honest reason next to it.

No new dependencies: `requests` is already pinned in app/requirements.txt.
"""
from __future__ import annotations

import json
import os
import time
import re

import threading

import requests

# Set while generate_json runs, so providers that can be asked for strict JSON are.
_JSON_MODE = threading.local()
_FAST = threading.local()
_LAST = threading.local()     # set by fast(): short, low-thinking replies for a person waiting

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
}
# A model that hangs must not hold a person in front of Jarvis for a minute:
# fail fast and let the chain try the next model.
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "12"))
# Hard wall-clock cap per model call (a slowly trickling reply defeats a read
# timeout), and for the whole chain, so Jarvis always answers or fails fast.
AI_TOTAL_BUDGET = float(os.environ.get("AI_TOTAL_BUDGET", "30"))
_POOL = None


def _run_capped(fn, *a):
    global _POOL
    import concurrent.futures as cf
    if _POOL is None:
        _POOL = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="ai")
    json_on = getattr(_JSON_MODE, "on", False)

    fast_on = getattr(_FAST, "on", False)

    def call():
        _JSON_MODE.on = json_on   # thread-local: carry the caller's modes across
        _FAST.on = fast_on
        try:
            return fn(*a)
        finally:
            _JSON_MODE.on = False
            _FAST.on = False

    fut = _POOL.submit(call)
    try:
        return fut.result(timeout=AI_TIMEOUT + 3)
    except cf.TimeoutError:
        raise requests.Timeout("no complete reply within %ss" % (AI_TIMEOUT + 3))
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "4000"))

# Base URLs are overridable so the platform can be pointed at a gateway, a proxy,
# a self-hosted endpoint, or a stub server during an end-to-end test — without
# any test-only branch existing in production code.
BASE_URLS = {
    "anthropic": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "gemini": os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"),
    "openai": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com"),
}


def base_url(p: str) -> str:
    return os.environ.get(f"{p.upper()}_BASE_URL", BASE_URLS[p]).rstrip("/")


class AIUnavailable(Exception):
    """Generation could not happen honestly. Carries a reason fit to show a human."""


class CreditsUsedUp(AIUnavailable):
    """The business has used this month's AI credits (AI_CREDITS_MODE=lock only)."""


# The provider currently being attempted. generate() sets it while it walks the
# chain, so model_name() and _key() pick the right model and key for whichever
# provider is being tried rather than for the one named in AI_PROVIDER.
_ACTIVE = None
_ACTIVE_MODEL = None


def configured_provider() -> str:
    return os.environ.get("AI_PROVIDER", "anthropic").strip().lower()


def provider() -> str:
    return _ACTIVE or configured_provider()


def model_name() -> str:
    """AI_MODEL names a model on the configured provider, so it must not be
    handed to a fallback provider that has never heard of it."""
    if _ACTIVE_MODEL:
        return _ACTIVE_MODEL
    p = provider()
    per_provider = os.environ.get("AI_MODEL_" + p.upper())
    if per_provider:
        return per_provider.strip()
    if p == configured_provider():
        return os.environ.get("AI_MODEL") or DEFAULT_MODELS.get(p, "")
    return DEFAULT_MODELS.get(p, "")


def _key() -> str:
    if provider() == "gemini":
        from .platform_key import gemini_key      # founder console > API Manager > Default key
        return gemini_key()
    env = {"anthropic": "ANTHROPIC_API_KEY",
           "gemini": "GEMINI_API_KEY",
           "openai": "OPENAI_API_KEY"}.get(provider())
    return os.environ.get(env, "").strip() if env else ""


def available() -> bool:
    """True when a real generation call could actually be made."""
    return provider() in DEFAULT_MODELS and bool(_key())


# ---------------------------------------------------------------- transport
def _call_anthropic(system: str, prompt: str) -> str:
    r = requests.post(
        base_url("anthropic") + "/v1/messages",
        headers={"x-api-key": _key(), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model_name(), "max_tokens": AI_MAX_TOKENS, "system": system,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"anthropic_{r.status_code}: {r.text[:200]}")
    blocks = r.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _gemini_fast_thinking(model: str):
    """Least thinking the model allows. Gemini 2.5/3 Flash models reason before
    answering by default, which adds seconds a person waiting on Jarvis can hear.
    Only used in fast mode (the console); other AI work is unchanged."""
    m = (model or "").lower()
    if m.startswith("gemini-2.5") and "pro" not in m:
        return {"thinkingBudget": 0}
    if m.startswith("gemini-3"):
        return {"thinkingLevel": "low"}
    return None


def _call_gemini(system: str, prompt: str) -> str:
    model = model_name()
    url = (f'{base_url("gemini")}/v1beta/models/'
           f"{model}:generateContent")
    fast = getattr(_FAST, "on", False)
    cfg = {"maxOutputTokens": 800 if fast else AI_MAX_TOKENS}
    if getattr(_JSON_MODE, "on", False):
        cfg["responseMimeType"] = "application/json"
    think = _gemini_fast_thinking(model) if fast else None
    if think:
        cfg["thinkingConfig"] = think
    body = {"system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": cfg}
    r = requests.post(url, params={"key": _key()}, json=body, timeout=AI_TIMEOUT)
    if r.status_code == 400 and think and "thinking" in r.text.lower():
        # this model does not take that setting: ask again without it
        cfg.pop("thinkingConfig", None)
        r = requests.post(url, params={"key": _key()}, json=body, timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"gemini_{r.status_code}: {r.text[:200]}")
    cands = r.json().get("candidates", [])
    if not cands:
        raise AIUnavailable("gemini returned no candidates")
    return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])


def _call_openai(system: str, prompt: str) -> str:
    r = requests.post(
        base_url("openai") + "/v1/chat/completions",
        headers={"Authorization": "Bearer " + _key()},
        json={"model": model_name(), "max_tokens": AI_MAX_TOKENS,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": prompt}]},
        timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"openai_{r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- resilience
# One provider is a single point of failure for the entire AI workforce, and the
# most common way it fails is the dullest: the account runs out of credit. When
# that happened here, every agent failed, retried, dead-lettered, and nothing
# said why. The chain below fails over instead, and when it cannot, it says what
# it tried and what each one answered.

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY"}

# How long to stop asking a provider that just told us no. An exhausted account
# will still be exhausted in a second; hammering it wastes the whole task's time
# budget on a question already answered.
_COOLDOWN = {"credit": 900, "auth": 900, "rate": 60, "unreachable": 60, "bad_response": 10}

_DOWN = {}          # provider -> {"until": ts, "why": str, "reason": str}


def _key_for(p: str) -> str:
    if p == "gemini":
        from .platform_key import gemini_key
        return gemini_key()
    return os.environ.get(_KEY_ENV.get(p, ""), "").strip()


def classify(message: str) -> str:
    """What kind of no was that? The answer decides how long to wait."""
    m = (message or "").lower()
    if any(w in m for w in ("credit", "quota", "billing", "insufficient",
                            "exceeded your current", "payment")):
        return "credit"
    if any(w in m for w in ("_401", "_403", "unauthorized", "invalid api key",
                            "invalid x-api-key", "permission")):
        return "auth"
    if "_429" in m or "rate limit" in m or "overloaded" in m:
        return "rate"
    if any(w in m for w in ("unreachable", "timeout", "_500", "_502", "_503", "_504")):
        return "unreachable"
    return "bad_response"


def providers() -> list:
    """The order to try, keys permitting.

    AI_PROVIDERS is explicit. Otherwise the configured provider goes first and
    every other provider that has a key follows it, because a key that exists is
    a fallback somebody already paid for.
    """
    raw = os.environ.get("AI_PROVIDERS", "").strip()
    if raw:
        chain = [x.strip().lower() for x in raw.split(",") if x.strip()]
    else:
        first = configured_provider()
        chain = [first] + [x for x in DEFAULT_MODELS if x != first]
    seen, out = set(), []
    for x in chain:
        if x in _TRANSPORT and x not in seen and _key_for(x):
            seen.add(x)
            out.append(x)
    return out


def chain_entries() -> list:
    """The (provider, model) pairs to try, in order.

    A provider is not the only thing that goes out of action -- one model can be
    rate limited upstream while the next is fine, which is the normal state of
    the free tiers. AI_MODEL_CHAIN spells the order out:

        AI_MODEL_CHAIN=openai=gpt-4o,openai=google/gemma-4-31b-it:free

    With no chain set this falls back to one entry per provider that has a key.
    """
    raw = os.environ.get("AI_MODEL_CHAIN", "").strip()
    if not raw:
        return [(x, None) for x in providers()]
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        prov, _, model = item.partition("=")
        prov = prov.strip().lower()
        if prov in _TRANSPORT and _key_for(prov):
            out.append((prov, model.strip() or None))
    return out


# The breaker has to be shared, not per-process. The worker learns a model is
# out of credit; the monitor, running its own one-off python, would otherwise
# see a clean slate and report all clear -- which is precisely the shape of the
# outage this work exists to stop. Redis already runs here, so the state lives
# there and expires on its own. If Redis is unreachable the in-process dict
# still works; the breaker degrades, it does not fail.
def _redis():
    try:
        import redis
        return redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                              socket_timeout=2, decode_responses=True)
    except Exception:
        return None


def _mark_down(key: str, why: str):
    reason = classify(why)
    ttl = _COOLDOWN.get(reason, 30)
    _DOWN[key] = {"until": time.time() + ttl, "why": why[:200], "reason": reason}
    r = _redis()
    if r:
        try:
            r.setex("ai:down:" + key, int(ttl),
                    json.dumps({"reason": reason, "why": why[:200]}))
        except Exception:
            pass


def _resting(key: str):
    d = _DOWN.get(key)
    if d and d["until"] > time.time():
        return d
    _DOWN.pop(key, None)
    r = _redis()
    if r:
        try:
            raw = r.get("ai:down:" + key)
            if raw:
                ttl = r.ttl("ai:down:" + key)
                d = json.loads(raw)
                return {"until": time.time() + max(int(ttl or 0), 0),
                        "why": d.get("why", ""), "reason": d.get("reason", "unknown")}
        except Exception:
            pass
    return None


def health() -> dict:
    """What the AI layer can honestly say about itself right now."""
    entries = chain_entries()
    out = []
    for p, m in entries:
        key = "%s:%s" % (p, m or "default")
        rest = _resting(key)
        out.append({"provider": p, "model": m or DEFAULT_MODELS.get(p),
                    "state": "resting" if rest else "ready",
                    "reason": rest["reason"] if rest else None,
                    "last_error": rest["why"] if rest else None,
                    "retry_in_seconds": int(rest["until"] - time.time()) if rest else 0})
    usable = [x for x in out if x["state"] == "ready"]
    return {"configured": configured_provider(),
            "chain": out,
            "usable": len(usable),
            "status": "ok" if usable else ("degraded" if out else "unconfigured")}


_TRANSPORT = {"anthropic": _call_anthropic, "gemini": _call_gemini, "openai": _call_openai}


def generate(system: str, prompt: str) -> str:
    """Raw text generation. Raises AIUnavailable rather than returning anything fake.
    Every call is counted against the business's AI credits (app/usage.py)."""
    global _ACTIVE
    if configured_provider() == "none":
        raise AIUnavailable("generation disabled (AI_PROVIDER=none)")
    from . import usage
    why = usage.check()
    if why:
        raise CreditsUsedUp(why)
    t0 = time.time()
    try:
        text = _generate(system, prompt)
    except AIUnavailable as e:
        usage.charge("text", False, ms=(time.time() - t0) * 1000, chars_in=len(system) + len(prompt), error=str(e))
        raise
    p, _, m = (last_model() or ":").partition(":")
    usage.charge("text", True, provider=p or None, model=m or None, ms=(time.time() - t0) * 1000,
                 chars_in=len(system) + len(prompt), chars_out=len(text))
    return text


def _generate(system: str, prompt: str) -> str:
    global _ACTIVE

    global _ACTIVE_MODEL
    chain = chain_entries()
    if not chain:
        raise AIUnavailable("no AI provider has an API key configured")

    tried, skipped = [], []
    started = time.time()
    for p, m in chain:
        if time.time() - started > AI_TOTAL_BUDGET:
            skipped.append("out of time after %ds" % AI_TOTAL_BUDGET)
            break
        key = "%s:%s" % (p, m or "default")
        rest = _resting(key)
        if rest:
            skipped.append(f"{key} resting ({rest['reason']}, {int(rest['until'] - time.time())}s)")
            continue
        _ACTIVE, _ACTIVE_MODEL = p, m
        try:
            text = _run_capped(_TRANSPORT[p], system, prompt)
            if not (text or "").strip():
                raise AIUnavailable(f"{key} returned an empty response")
            if (p, m) != chain[0]:
                print(f"[ai] served by fallback {key}", flush=True)
            _LAST.key = "%s:%s" % (p, m or DEFAULT_MODELS.get(p))
            return text
        except requests.RequestException as e:
            why = f"{key} unreachable: {type(e).__name__}"
            _mark_down(key, why)
            tried.append(why)
        except AIUnavailable as e:
            _mark_down(key, str(e))
            tried.append(str(e))
        finally:
            _ACTIVE, _ACTIVE_MODEL = None, None

    # Every provider said no. Say which, and what each one said: an agent that
    # dies with "AI unavailable" and no reason is how a 22-day outage happens.
    detail = "; ".join(tried + skipped) or "no provider was in a state to try"
    raise AIUnavailable("every AI provider refused: " + detail)


# ------------------------------------------------------------- JSON output
def _extract_json(text: str):
    """Pull the JSON object out of a reply, tolerating ```json fences and prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise AIUnavailable("model did not return parseable JSON")


class fast:
    """with ai.fast(): ... -- a person is waiting (Jarvis): least thinking, short output."""
    def __enter__(self):
        _FAST.on = True
        return self

    def __exit__(self, *exc):
        _FAST.on = False
        return False


def last_model():
    """Which provider:model answered the last call on this thread (for timing)."""
    return getattr(_LAST, "key", None)


def generate_json(system: str, prompt: str, required_keys: tuple = (), _retry=True) -> dict:
    """Generation that must come back as a JSON object with the expected shape.

    One corrective retry, then give up honestly. Half-understood output is never
    passed on to a client — the agent parks it for a human instead.
    """
    _JSON_MODE.on = True
    try:
        text = generate(system, prompt)
    finally:
        _JSON_MODE.on = False
    try:
        data = _extract_json(text)
        if not isinstance(data, dict):
            raise AIUnavailable("model returned JSON that is not an object")
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise AIUnavailable(f"model response missing keys: {', '.join(missing)}")
        return data
    except AIUnavailable:
        if not _retry:
            raise
        return generate_json(
            system,
            prompt + "\n\nYour previous reply could not be parsed. Reply with ONLY a "
                     "single valid JSON object and no other text, no markdown fences.",
            required_keys, _retry=False)
