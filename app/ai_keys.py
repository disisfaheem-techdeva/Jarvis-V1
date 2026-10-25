"""API Manager: AI provider keys, which WhatsApp agent uses which, and how much
each agent used (founder console > API Manager; Faheem/Rafay, 26 Oct 2026).

Until now one GEMINI_API_KEY in the platform .env was copied into every agent,
so every business shared one free-tier quota and nobody could see it run out.
The founder can now:

  * add keys (Gemini, and other providers for later), each stored only as a
    crypto.encrypt_json envelope; the browser only ever sees the last 4 chars;
  * test a key: one model-list request, which costs no generation quota;
  * choose, per agent, which key it runs with (or the platform .env key);
  * see each agent's daily calls, rate-limit hits (429) and overloads (503).

Applying a choice needs root on the host (the key goes into the agent's own
/docker/hermes-<key>/agent.env and the agent is re-created), which the API
container cannot do. So the console records the choice as 'pending' and
ops/agent-template/ai-keys-sync.sh, run by cron every minute, applies it and
reports back through the CLI at the bottom of this file. The same script
reports what each agent really runs with and the daily usage counts.

Never returned, logged or audited: a key's value. Only its last 4 characters.
"""
import hashlib
import json
import os
import re
import sys

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from . import crypto
from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

PROVIDERS = {
    # name: (model-list URL used by Test, how the key is sent)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", "goog"),
    "groq": ("https://api.groq.com/openai/v1/models", "bearer"),
    "openrouter": ("https://openrouter.ai/api/v1/models/user", "bearer"),
    "mistral": ("https://api.mistral.ai/v1/models", "bearer"),
    "cerebras": ("https://api.cerebras.ai/v1/models", "bearer"),
}
# Only Gemini can be given to a WhatsApp agent today: the agents are configured
# for provider gemini (ops/agent-template/apply_config.py, T2).
AGENT_PROVIDERS = ("gemini",)

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


def test_key(provider: str, value: str):
    """One model-list request. Returns (status, error) with status one of
    ok / invalid / rate_limited / error. The error text never contains the key."""
    url, how = PROVIDERS[provider]
    headers = {"x-goog-api-key": value} if how == "goog" else {"Authorization": "Bearer " + value}
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        return "error", "could not reach %s (%s)" % (provider, e.__class__.__name__)
    if r.status_code == 200:
        return "ok", None
    if r.status_code == 429:
        return "rate_limited", "%s says: too many requests (429)" % provider
    if r.status_code in (400, 401, 403):
        return "invalid", "%s refused the key (%d)" % (provider, r.status_code)
    return "error", "%s answered %d" % (provider, r.status_code)


def _agents(cur):
    """Every template agent the platform knows, with its business."""
    cur.execute("""SELECT w.agent_key, w.status::text AS wa_status, w.tenant_id, t.name AS business, t.is_test
                     FROM whatsapp_connections w JOIN tenants t ON t.id = w.tenant_id
                    WHERE w.agent_key IS NOT NULL ORDER BY t.name, w.agent_key""")
    return [dict(r) for r in cur.fetchall() if not PROTECTED.match(r["agent_key"] or "")]


def make_ai_keys_router() -> APIRouter:
    router = APIRouter()

    @router.get("/techdeva/api-keys")
    def overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT id, provider, label, owner_note, last4, status, last_test_at, last_error,
                                  created_at, disabled_at FROM ai_keys ORDER BY created_at""")
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
            key["week"] = {f: sum((week.get(x) or {}).get(f, 0) for x in used_by)
                           for f in ("ok", "rate_limited", "overloaded", "failed")}
        return {"keys": keys, "agents": agents, "usage": usage, "platform_key_last4": plat,
                "providers": list(PROVIDERS), "agent_providers": list(AGENT_PROVIDERS),
                "encryption": crypto.enabled()}

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
        if len(value) < 20 or len(value) > 300 or re.search(r"\s", value):
            raise HTTPException(400, "That does not look like an API key. Paste the whole key, with no spaces.")
        status, error = test_key(provider, value)
        fp = _fingerprint(value)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT label FROM ai_keys WHERE fingerprint = %s", (fp,))
            dup = cur.fetchone()
            if dup:
                raise HTTPException(409, "This key is already saved as \"%s\"." % dup["label"])
            cur.execute("""INSERT INTO ai_keys (provider, label, owner_note, secret, last4, fingerprint,
                                                status, last_test_at, last_error, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
                           RETURNING id, last4, status""",
                        (provider, label, note, json.dumps(_seal(provider, value)), value[-4:], fp,
                         status, error, str(uid) if uid else None))
            row = dict(cur.fetchone())
            record(cur, action="techdeva.ai_key.add", resource="ai_keys", resource_id=str(row["id"]),
                   actor=str(uid) if uid else None, status="ok",
                   detail={"provider": provider, "label": label, "last4": row["last4"], "test": status},
                   tenant_id=None)
        return {"id": str(row["id"]), "last4": row["last4"], "status": status, "error": error}

    def _get(cur, key_id):
        cur.execute("SELECT * FROM ai_keys WHERE id = %s", (key_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such key.")
        return dict(row)

    @router.post("/techdeva/api-keys/{key_id}/test")
    def test(key_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
        status, error = test_key(row["provider"], _open(row))
        if row["status"] == "disabled":
            status = "disabled"          # a test does not switch a key back on
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE ai_keys SET status = %s, last_error = %s, last_test_at = now(), updated_at = now()
                            WHERE id = %s""", (status, error, key_id))
            record(cur, action="techdeva.ai_key.test", resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok" if status in ("ok", "disabled") else "error",
                   detail={"last4": row["last4"], "result": status}, tenant_id=None)
        return {"status": status, "error": error}

    @router.put("/techdeva/api-keys/{key_id}")
    def edit(key_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None), authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            row = _get(cur, key_id)
            label = str(body.get("label") or row["label"]).strip()[:80]
            disabled = body.get("disabled")
            if disabled is True:
                cur.execute("""SELECT count(*) AS n FROM ai_key_assignments WHERE key_id = %s""", (key_id,))
                if cur.fetchone()["n"]:
                    raise HTTPException(409, "Agents are set to use this key. Give them another key first.")
                cur.execute("""UPDATE ai_keys SET label = %s, status = 'disabled', disabled_at = now(), updated_at = now()
                                WHERE id = %s""", (label, key_id))
            elif disabled is False:
                cur.execute("""UPDATE ai_keys SET label = %s, status = 'untested', disabled_at = NULL, updated_at = now()
                                WHERE id = %s""", (label, key_id))
            else:
                cur.execute("UPDATE ai_keys SET label = %s, updated_at = now() WHERE id = %s", (label, key_id))
            record(cur, action="techdeva.ai_key.edit", resource="ai_keys", resource_id=key_id,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"last4": row["last4"], "label": label, "disabled": disabled}, tenant_id=None)
        return {"ok": True}

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
            last4 = _platform_last4()
            if key_id:
                row = _get(cur, key_id)
                if row["provider"] not in AGENT_PROVIDERS:
                    raise HTTPException(400, "WhatsApp agents can only use a Gemini key for now.")
                if row["status"] in ("disabled", "invalid"):
                    raise HTTPException(409, "That key is %s. Test it or choose another." % row["status"])
                last4 = row["last4"]
            cur.execute("""INSERT INTO ai_key_assignments (agent_key, key_id, status, requested_by, requested_at,
                                                           applied_at, error)
                           VALUES (%s, %s, 'pending', %s, now(), NULL, NULL)
                           ON CONFLICT (agent_key) DO UPDATE SET key_id = EXCLUDED.key_id, status = 'pending',
                                 requested_by = EXCLUDED.requested_by, requested_at = now(), applied_at = NULL, error = NULL""",
                        (agent_key, key_id, str(uid) if uid else None))
            record(cur, action="techdeva.ai_key.assign", resource="ai_key_assignments", resource_id=agent_key,
                   actor=str(uid) if uid else None, status="ok",
                   detail={"agent": agent_key, "key_last4": last4, "platform_key": key_id is None}, tenant_id=None)
        return {"status": "pending", "key_last4": last4}

    return router


# ---------------------------------------------------------------- cron CLI
# Run inside the api container by ops/agent-template/ai-keys-sync.sh:
#   python -m app.ai_keys pending                 -> agent \t last4 \t key   (one line per pending agent)
#   python -m app.ai_keys applied <agent> ok|failed <live_last4> [error]
#   python -m app.ai_keys live    < lines "agent \t last4 \t running(0/1)"
#   python -m app.ai_keys usage   < JSON lines {day, agent_key, model, ok, rate_limited, overloaded, failed, key_last4}
# `pending` is the only command that prints a key, and only to the script's pipe.

def _cli(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "pending":
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT a.agent_key, a.key_id, k.provider, k.secret, k.last4, k.status
                             FROM ai_key_assignments a LEFT JOIN ai_keys k ON k.id = a.key_id
                            WHERE a.status = 'pending' ORDER BY a.requested_at""")
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if PROTECTED.match(r["agent_key"]):
                continue
            if r["key_id"] is None:
                value = (os.environ.get("GEMINI_API_KEY") or "").strip().strip("'\"")
            elif r["status"] in ("disabled", "invalid"):
                continue
            else:
                value = _open(r)
            if value and "\t" not in value and "\n" not in value:
                sys.stdout.write("%s\t%s\t%s\n" % (r["agent_key"], value[-4:], value))
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
