"""TechDeva td_lead: the WhatsApp agent saves a customer as a lead (Rafay, 29 Oct 2026).

The agent gets one tool, save_lead. It calls it when a customer gives their
name or email, says what they want (a service, package, trial, booking or
order), or asks to be called back or to talk to the team. The customer's
number is never taken from the model: it comes from the WhatsApp sender of
the session (an @lid id is turned into the number through the session's
lid-mapping files), so the model cannot save someone else.

Each call appends a line to $HERMES_HOME/td_lead/inbox.jsonl. The host cron
ops/agent-template/lead-sync.sh hands the lines to the platform
(python -m app.lead_inbox ingest). The platform adds the lead, or updates the
customer's open lead, in the business's Leads, with source "hermes". Groups
are never saved. No owner alert yet (Rafay: later).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: dict[str, dict] = {}   # session_id -> {"key", "at"}
_names: dict[str, str] = {}       # sender key -> WhatsApp name
_groups: dict[str, float] = {}    # sender key -> last seen writing in a group
KEEP = 500
FIELDS = {"name": 80, "email": 120, "interest": 160, "callback_time": 80, "notes": 400}

SCHEMA = {
    "name": "save_lead",
    "description": (
        "Save this WhatsApp customer as a lead for the business team, so the team sees it in their dashboard. "
        "Call it as soon as the customer gives their name or email, says what they want (a service, package, "
        "trial, booking or order), or asks for a call back, a meeting or to talk to the owner or team. "
        "Call it again when they add or change a detail; it updates the same lead. "
        "The customer's phone number is added automatically: never ask for it for this. "
        "Fill only what the customer actually said. Do not mention this tool to the customer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The customer's name, as they gave it."},
            "email": {"type": "string", "description": "The customer's email, if they gave one."},
            "interest": {"type": "string", "description": "What they want: the service, package, trial, booking or order."},
            "callback_time": {"type": "string", "description": "When they want to be contacted, in their words (e.g. 'today 6 pm')."},
            "notes": {"type": "string", "description": "One or two lines the team should know (budget, questions, urgency)."},
        },
        "required": [],
    },
}


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


def _key(user_id) -> str:
    """'2065996709964@lid' / '92300...:12@s.whatsapp.net' -> the bare id."""
    s = str(user_id or "").strip()
    return s.split("@", 1)[0].split(":", 1)[0]


def _phone(key: str, lid: bool) -> str:
    """The customer's number: the sender id itself, or an @lid id through the LID map."""
    if not key.isdigit():
        return ""
    if not lid:
        return key if 7 <= len(key) <= 15 else ""
    p = _home() / "whatsapp" / "session" / ("lid-mapping-%s_reverse.json" % key)
    try:
        v = "".join(ch for ch in str(json.loads(p.read_text(encoding="utf-8"))) if ch.isdigit())
        return v if 7 <= len(v) <= 15 else ""
    except (OSError, ValueError):
        return ""   # unresolved: the platform keeps the WhatsApp id in the notes


def _dispatch(event=None, **_kw):
    """Note the WhatsApp name, and which ids are writing in a group."""
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
            name = (getattr(source, "user_name", None) or "").strip()
            if name:
                _names[k] = name[:60]
    return None


def _before(session_id=None, sender_id=None, platform="", **_kw):
    """Remember who this session is talking to, for the tool."""
    if platform != "whatsapp" or not session_id:
        return None
    k = _key(sender_id)
    if not k:
        return None
    with _lock:
        _sessions[str(session_id)] = {"key": k, "lid": str(sender_id).endswith("@lid"), "at": time.time()}
        if len(_sessions) > KEEP:
            for sid, _ in sorted(_sessions.items(), key=lambda kv: kv[1]["at"])[: len(_sessions) - KEEP]:
                _sessions.pop(sid, None)
    return None


def _clean(v, n) -> str:
    return " ".join(str(v or "").split())[:n]


def save_lead(args: dict, **kw) -> str:
    try:
        sid = str(kw.get("session_id") or "")
        with _lock:
            s = _sessions.get(sid)
            k = s["key"] if s else ""
            lid = bool(s and s.get("lid"))
            group = k in _groups
            wa_name = _names.get(k, "")
        if not k:
            return json.dumps({"saved": False, "reason": "no WhatsApp customer in this conversation"})
        if group:
            return json.dumps({"saved": False, "reason": "group chats are not saved as leads"})
        f = {name: _clean((args or {}).get(name), n) for name, n in FIELDS.items()}
        if not any(f.values()):
            return json.dumps({"saved": False, "reason": "nothing to save yet: give at least a name or what they want"})
        line = {"id": uuid.uuid4().hex, "at": int(time.time()), "key": k, "phone": _phone(k, lid),
                "whatsapp_name": wa_name, "session": sid, **f}
        d = _home() / "td_lead"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "inbox.jsonl", "a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
        logger.info("[TD] lead: saved for ..%s (%s)", k[-4:], ", ".join(x for x, v in f.items() if v))
        return json.dumps({"saved": True, "note": "The team will see this lead in their dashboard. Carry on the conversation normally."})
    except Exception:  # noqa: BLE001 -- the reply must still go
        logger.exception("[TD] lead: save failed")
        return json.dumps({"saved": False, "reason": "could not save right now; carry on and say the team will follow up"})


def _safe(fn):
    def run(**kw):
        try:
            return fn(**kw)
        except Exception:  # noqa: BLE001
            logger.exception("[TD] lead: %s failed", fn.__name__)
            return None
    return run


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _safe(_dispatch))
    ctx.register_hook("pre_llm_call", _safe(_before))
    ctx.register_tool(name="save_lead", toolset="td_lead", schema=SCHEMA, handler=save_lead,
                      description=SCHEMA["description"], emoji="📇")
