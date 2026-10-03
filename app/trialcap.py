"""Free-trial limits (Faheem, 3 Oct 2026).

During the free trial a business gets:
  * TRIAL_MESSAGES (50) customer messages answered by its WhatsApp agent. At
    the limit the agent is paused (its containers are stopped by the host's
    trial-guard script, ops/agent-template/trial-guard.sh) until the business
    pays; then it runs again and its chosen plan's limits apply.
  * TRIAL_AI_PICTURES (5) AI pictures in total (app/aidesign.py).

A "customer message" is a message a customer sent on WhatsApp (not the
agent's own replies, not system nudges), counted from the agent's own chat
store through its pairing sidecar. Only template-built agents (agent_requests)
are ever paused; the clinic and Hermes are not part of this.

The count is kept in trial_usage, because a paused agent cannot be asked."""
import os
import time
import urllib.parse

from fastapi import APIRouter, Cookie, Header

from .auth import SESSION_COOKIE
from .db import tenant_session

TRIAL_MESSAGES = int(os.environ.get("TRIAL_MESSAGES", "50"))
TRIAL_AI_PICTURES = int(os.environ.get("TRIAL_AI_PICTURES", "5"))
TTL = 300
_CACHE: dict = {}


def is_trial(cur, tid):
    cur.execute("SELECT status::text AS s FROM tenants WHERE id::text = %s", (str(tid),))
    r = cur.fetchone()
    return bool(r and r["s"] in ("trial", "paused"))


def _count(pairctl_url):
    """Customer messages on one number, or None when the agent cannot be asked."""
    from .plans import _pairctl_get
    try:
        chats = _pairctl_get(pairctl_url, "/chats").get("chats") or []
        n = 0
        for c in chats:
            if not c.get("messages"):
                continue
            msgs = _pairctl_get(pairctl_url, "/chats/" + urllib.parse.quote(str(c["session_id"]), safe="")).get("messages") or []
            n += sum(1 for m in msgs if m.get("role") == "user" and not str(m.get("content") or "").startswith("[System"))
        return n
    except Exception:  # noqa: BLE001 -- unreachable agent: unknown, never guessed
        return None


def trial_messages(tid, fresh=False):
    """{used, cap, paused, counted_at, exact} for a business on trial, else None."""
    tid = str(tid)
    with tenant_session(None, "techdeva_admin") as cur:
        if not is_trial(cur, tid):
            return None
        cur.execute("SELECT messages, paused, counted_at FROM trial_usage WHERE tenant_id::text = %s", (tid,))
        last = cur.fetchone()
        cur.execute("SELECT pairctl_url FROM whatsapp_connections WHERE tenant_id::text = %s", (tid,))
        urls = [r["pairctl_url"] for r in cur.fetchall() if r["pairctl_url"]]
    hit = _CACHE.get(tid)
    if hit and hit[0] > time.time() and not fresh:
        return hit[1]
    counts = [_count(u) for u in urls]
    known = [c for c in counts if c is not None]
    used = sum(known)
    if last and (None in counts or not urls):
        used = max(used, last["messages"])          # a paused agent cannot be asked: keep the last count
    paused = bool(last and last["paused"])
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO trial_usage (tenant_id, messages, paused, counted_at) VALUES (%s, %s, %s, now())
                       ON CONFLICT (tenant_id) DO UPDATE SET messages = GREATEST(trial_usage.messages, EXCLUDED.messages),
                       counted_at = now() RETURNING messages, paused, counted_at""", (tid, used, paused))
        row = cur.fetchone()
    out = {"used": row["messages"], "cap": TRIAL_MESSAGES, "paused": row["paused"],
           "counted_at": row["counted_at"].isoformat(), "exact": None not in counts}
    _CACHE[tid] = (time.time() + TTL, out)
    return out


def guard_list():
    """For the host's trial-guard script: every template agent and whether it
    should run. run=None means 'unknown, change nothing'."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT r.agent_key, r.tenant_id::text AS tid, t.status::text AS status
                         FROM agent_requests r JOIN tenants t ON t.id = r.tenant_id
                        WHERE r.status = 'ready' AND r.agent_key ~ '^[a-z0-9][a-z0-9-]{2,60}$'""")
        rows = [dict(r) for r in cur.fetchall()]
    out, seen = [], {}
    for r in rows:
        if r["status"] not in ("trial", "paused"):
            with tenant_session(None, "techdeva_admin") as cur:      # paid: never paused for the trial
                cur.execute("UPDATE trial_usage SET paused = false WHERE tenant_id::text = %s AND paused", (r["tid"],))
            out.append({"key": r["agent_key"], "run": True, "why": "paid"})
            continue
        if r["tid"] not in seen:
            seen[r["tid"]] = trial_messages(r["tid"], fresh=True)
        t = seen[r["tid"]]
        over = t and t["used"] >= t["cap"]
        if over and not t["paused"]:
            with tenant_session(None, "techdeva_admin") as cur:
                cur.execute("UPDATE trial_usage SET paused = true WHERE tenant_id::text = %s", (r["tid"],))
                from .audit import record
                record(cur, action="trial.agent_paused", resource="agent_requests", resource_id=r["agent_key"],
                       actor="trial-guard", actor_type="system", status="ok", tenant_id=r["tid"],
                       detail={"messages": t["used"], "cap": t["cap"]}, error=None)
            t["paused"] = True
        out.append({"key": r["agent_key"], "run": False if over else (True if t else None),
                    "why": "trial limit reached (%s of %s)" % (t["used"], t["cap"]) if over else "trial"})
    return out


def make_trialcap_router():
    router = APIRouter()

    @router.get("/techdeva/trial-guard")
    def guard(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        from .tdwork import _admin
        _admin(x_tenant_id, x_role, authorization, td_session)
        return {"agents": guard_list(), "trial_messages": TRIAL_MESSAGES}

    return router
