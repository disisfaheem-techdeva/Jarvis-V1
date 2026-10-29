"""TechDeva td_close: Hermes ends chats that go nowhere (Rafay, 29 Oct 2026).

Every Hermes reply is one model call (about 2,300 input tokens). A customer who
is not interested, or is only passing time, keeps those calls coming. This
plugin lets the agent stop that, with no extra model call:

  * end_chat, a tool: the agent says one short goodbye and calls it. CLOSE_DELAY
    seconds later (after that goodbye has gone out), the WhatsApp bridge closes
    the chat for CLOSE_HOURS: the customer's messages are not handed to the
    agent, so they cost nothing. After that, Hermes answers again. The owner
    sees the chat as closed in the dashboard Inbox and can turn it on.
  * After MINUTES of one conversation, every turn carries a note asking the
    agent to say goodbye and call end_chat if the customer has still shown no
    real interest.
  * When the customer is only signing off ("ok bus", "ni", "shukriya"), the
    note asks for one short goodbye with no further question. Before this,
    Hermes answered every "ok" with "kya aur madad chahiye?" and kept the chat
    going.

The notes ride on the current user message only (pre_llm_call context), never
on the stored history. Groups are left alone. Each close is appended to
$HERMES_HOME/td_close/closed.jsonl, so the savings can be counted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MINUTES = 10          # how long a conversation may go nowhere
CLOSE_HOURS = 24      # how long a closed chat stays closed
CLOSE_DELAY = 20      # seconds: the goodbye goes out first
GAP = 30 * 60         # silence after which a new conversation starts
KEEP = 2000

_lock = threading.Lock()
_sessions: dict[str, dict] = {}   # session_id -> {"key", "lid", "at"}
_convs: dict[str, dict] = {}      # sender key -> {"start", "last"}
_groups: dict[str, float] = {}    # sender key -> last seen writing in a group

# A sign-off needs a closing word; the fillers may only come with one. A bare
# "ok" or "haan" can be a yes to Hermes' question, so it is never a sign-off.
CLOSERS = {"bye", "allah", "khuda", "hafiz", "shukriya", "thanks", "thank", "thx", "bus", "bas", "tc"}
FILLERS = {"ok", "okay", "okk", "theek", "thik", "hai", "ha", "ji", "jee", "ni", "nai", "nahi", "nhi", "no",
           "you", "acha", "achha", "g", "sir", "bhai", "and"}

SCHEMA = {
    "name": "end_chat",
    "description": (
        "Close this WhatsApp chat for 24 hours after your goodbye, so you stop answering a customer who is going "
        "nowhere. Use it only when the customer clearly is not interested (said no, has no budget, turned down the "
        "trial), or is wasting time (flirting, jokes, off-topic or repeated nonsense), or is abusive or spam. Never "
        "use it when they asked about a price, booking, order or trial, or gave their details: those are customers. "
        "In the same reply, write one short, polite goodbye in their language (that they can message any time they "
        "need the business). Do not mention closing or this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "enum": ["not_interested", "time_wasting", "abusive", "spam"],
                       "description": "Why the chat is being closed."},
        },
        "required": ["reason"],
    },
}


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


def _key(user_id) -> str:
    s = str(user_id or "").strip()
    return s.split("@", 1)[0].split(":", 1)[0]


def is_sign_off(text) -> bool:
    """'ok bus', 'shukriya 🙏', 'Allah hafiz' -> True; 'ok', 'haan', a question or anything longer -> False."""
    t = re.sub(r"[^\w\s]", " ", str(text or "").lower(), flags=re.UNICODE)
    words = t.split()
    if not words or len(words) > 5:
        return False
    return any(w in CLOSERS for w in words) and all(w in CLOSERS or w in FILLERS for w in words)


def _dispatch(event=None, **_kw):
    source = getattr(event, "source", None)
    if getattr(getattr(source, "platform", None), "value", None) != "whatsapp":
        return None
    k = _key(getattr(source, "user_id", None))
    if not k:
        return None
    with _lock:
        if getattr(source, "chat_type", "dm") != "dm":
            _groups[k] = time.time()
        else:
            _groups.pop(k, None)
    return None


def _before(session_id=None, sender_id=None, platform="", user_message="", **_kw):
    if platform != "whatsapp" or not session_id:
        return None
    k = _key(sender_id)
    if not k:
        return None
    now = time.time()
    with _lock:
        if now - _groups.get(k, 0) < 120:
            return None
        _sessions[str(session_id)] = {"key": k, "lid": str(sender_id).endswith("@lid"), "at": now}
        c = _convs.get(k)
        if not c or now - c["last"] > GAP:
            c = {"start": now}
        c["last"] = now
        _convs[k] = c
        for d in (_sessions, _convs):
            if len(d) > KEEP:
                for old, _ in sorted(d.items(), key=lambda kv: kv[1].get("at", kv[1].get("last", 0)))[: len(d) - KEEP]:
                    d.pop(old, None)
        minutes = int((now - c["start"]) / 60)
    notes = []
    if is_sign_off(user_message):
        notes.append("[Chat note: the customer is only signing off. Reply with one short, warm goodbye. "
                     "Do not ask a question or offer anything more.]")
    if minutes >= MINUTES:
        notes.append("[Chat note: this conversation has run %d minutes. If the customer has still shown no real "
                     "interest (no question about a price, booking, order or trial, no details given) or is wasting "
                     "time, say one short polite goodbye now and call end_chat. If they are interested, carry on "
                     "normally.]" % minutes)
    return {"context": "\n".join(notes)} if notes else None


def _bridge_close(jid: str, reason: str, key: str):
    time.sleep(CLOSE_DELAY)
    body = json.dumps({"chatId": jid, "minutes": CLOSE_HOURS * 60, "kind": "closed"}).encode()
    port = os.environ.get("WHATSAPP_BRIDGE_PORT") or "3000"
    req = urllib.request.Request("http://127.0.0.1:%s/takeover" % port, data=body, method="POST",
                                 headers={"content-type": "application/json",
                                          "x-internal-key": os.environ.get("HERMES_INTERNAL_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("[TD] close: bridge refused for ..%s: %s", key[-4:], e)
        ok = False
    try:
        d = _home() / "td_close"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "closed.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": int(time.time()), "key": key, "reason": reason, "ok": ok}) + "\n")
    except OSError:
        pass
    if ok:
        logger.info("[TD] close: ..%s closed for %dh (%s)", key[-4:], CLOSE_HOURS, reason)


def end_chat(args: dict, **kw) -> str:
    try:
        sid = str(kw.get("session_id") or "")
        with _lock:
            s = _sessions.get(sid)
        if not s:
            return json.dumps({"closed": False, "reason": "no WhatsApp customer in this conversation"})
        k = s["key"]
        if k in _groups:
            return json.dumps({"closed": False, "reason": "group chats are not closed"})
        reason = str((args or {}).get("reason") or "not_interested")
        if reason not in ("not_interested", "time_wasting", "abusive", "spam"):
            reason = "not_interested"
        jid = k + ("@lid" if s["lid"] else "@s.whatsapp.net")
        threading.Thread(target=_bridge_close, args=(jid, reason, k), daemon=True).start()
        with _lock:
            _convs.pop(k, None)
        return json.dumps({"closed": True, "note": "Your goodbye in this reply is the last message this customer "
                                                   "gets for now. Keep it short and polite."})
    except Exception:  # noqa: BLE001 -- the reply must still go
        logger.exception("[TD] close: end_chat failed")
        return json.dumps({"closed": False, "reason": "could not close; just say goodbye"})


def _safe(fn):
    def run(**kw):
        try:
            return fn(**kw)
        except Exception:  # noqa: BLE001
            logger.exception("[TD] close: %s failed", fn.__name__)
            return None
    return run


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _safe(_dispatch))
    ctx.register_hook("pre_llm_call", _safe(_before))
    ctx.register_tool(name="end_chat", toolset="td_close", schema=SCHEMA, handler=end_chat,
                      description=SCHEMA["description"], emoji="👋")
