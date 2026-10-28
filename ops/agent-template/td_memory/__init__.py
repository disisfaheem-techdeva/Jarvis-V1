"""TechDeva td_memory: the WhatsApp agent remembers a returning customer (Rafay, 28 Oct 2026).

Why: the gateway starts a fresh session every day at 04:00 and after 24 hours
of quiet (session_reset), and the agent's memory files are one shared profile,
not one per customer. So a customer who wrote last week was greeted as new.

What it keeps, per customer, in $HERMES_HOME/td_memory/contacts.db (this
agent's own volume, so one business never sees another's customers):
  * the WhatsApp name, first and last time they wrote, how many conversations;
  * their last few exchanges (customer line + agent line, each cut short).

What the model gets: when a customer who wrote in an EARLIER conversation
writes again, a short private card is added to that turn's message
(pre_llm_call; Hermes keeps it out of the saved transcript). Exchanges from
the current conversation are not repeated, the model already has them. A new
customer gets nothing, so no tokens are spent on them. Groups are skipped.

No model call is made here and nothing is written to SOUL.md or the memory
files. Every hook is wrapped: if this plugin fails, the agent replies as before.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Karachi")
KEEP = 8        # exchanges stored per customer
SHOW = 4        # earlier exchanges shown on the card
CUT = 160       # characters kept of each line
# what the gateway wraps around a customer's words before the model sees them
NOISE = re.compile(r"(?is)^\s*\[(the user sent|system:)[^\]]*\]\s*")

_lock = threading.Lock()
_sessions: dict[str, str] = {}   # session_id -> customer key
_groups: dict[str, float] = {}   # customer key -> time last seen writing in a group
_names: dict[str, str] = {}      # customer key -> WhatsApp name, until written


def _db() -> sqlite3.Connection:
    home = os.environ.get("HERMES_HOME") or "/opt/data"
    d = Path(home) / "td_memory"
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(d / "contacts.db"), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS contacts (
        key TEXT PRIMARY KEY, name TEXT, first_seen INTEGER, last_seen INTEGER,
        conversations INTEGER NOT NULL DEFAULT 0, last_session TEXT, recent TEXT NOT NULL DEFAULT '[]')""")
    return con


def _key(user_id) -> str:
    """'2065996709964@lid' / '92300...:12@s.whatsapp.net' -> the bare id."""
    s = str(user_id or "").strip()
    return s.split("@", 1)[0].split(":", 1)[0]


def _cut(text) -> str:
    if not isinstance(text, str):
        return ""
    text = " ".join(NOISE.sub("", text).split())
    return text if len(text) <= CUT else text[:CUT - 1].rstrip() + "…"


def _when(ts: int) -> str:
    then = datetime.fromtimestamp(ts, TZ)
    days = (datetime.now(TZ).date() - then.date()).days
    ago = "today" if days == 0 else "yesterday" if days == 1 else "%d days ago" % days
    return "%s (%s)" % (then.strftime("%d %b %Y"), ago)


def card(row, session_id: str):
    """The private note for the model, or None when there is nothing earlier to recall."""
    name, first_seen, last_seen, conversations, recent = row
    earlier = [x for x in json.loads(recent or "[]") if x.get("s") != session_id][-SHOW:]
    if not earlier and conversations <= 1:
        return None
    lines = ["[Customer memory: a private note for you. Never quote it or say you have notes.]",
             "Returning customer%s. First wrote %s; this is conversation %d. Last wrote %s." % (
                 ", WhatsApp name: %s" % name if name else "", _when(first_seen), conversations, _when(last_seen))]
    if earlier:
        lines.append("What was said before:")
        for x in earlier:
            lines.append("- %s · customer: %s" % (datetime.fromtimestamp(x["t"], TZ).strftime("%d %b"), x["u"]))
            if x.get("a"):
                lines.append("  you: %s" % x["a"])
    lines.append("Greet them as someone you know, and pick up from there if it fits. "
                 "Do not ask again for what they already told you.")
    return "\n".join(lines)


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
    if platform != "whatsapp" or not session_id:
        return None
    k = _key(sender_id)
    if not k:
        return None
    with _lock:
        if time.time() - _groups.get(k, 0) < 120:
            return None
        name = _names.pop(k, None)
        _sessions[str(session_id)] = k
    now = int(time.time())
    con = _db()
    try:
        with con:
            row = con.execute("SELECT name, first_seen, last_seen, conversations, recent, last_session "
                              "FROM contacts WHERE key = ?", (k,)).fetchone()
            if row is None:
                con.execute("INSERT INTO contacts (key, name, first_seen, last_seen, conversations, last_session) "
                            "VALUES (?, ?, ?, ?, 1, ?)", (k, name, now, now, str(session_id)))
                return None
            new_conv = row[5] != str(session_id)
            convs = row[3] + (1 if new_conv else 0)
            note = card((row[0] or name, row[1], row[2], convs, row[4]), str(session_id))
            if new_conv:
                con.execute("UPDATE contacts SET conversations = conversations + 1, last_session = ?, "
                            "last_seen = ?, name = COALESCE(?, name) WHERE key = ?", (str(session_id), now, name, k))
            else:
                con.execute("UPDATE contacts SET last_seen = ?, name = COALESCE(?, name) WHERE key = ?", (now, name, k))
    finally:
        con.close()
    if note:
        logger.info("[TD] memory: returning customer ..%s (conversation %d)", k[-4:], convs)
        return {"context": note}
    return None


def _after(session_id=None, user_message=None, assistant_response=None, platform="", **_kw):
    if platform != "whatsapp" or not session_id:
        return None
    with _lock:
        k = _sessions.get(str(session_id))
    u = _cut(user_message)
    if not k or not u:
        return None
    con = _db()
    try:
        with con:
            row = con.execute("SELECT recent FROM contacts WHERE key = ?", (k,)).fetchone()
            if row is None:
                return None
            recent = json.loads(row[0] or "[]")
            recent.append({"t": int(time.time()), "s": str(session_id), "u": u, "a": _cut(assistant_response)})
            con.execute("UPDATE contacts SET recent = ? WHERE key = ?",
                        (json.dumps(recent[-KEEP:], ensure_ascii=False), k))
    finally:
        con.close()
    return None


def _safe(fn):
    def run(**kw):
        try:
            return fn(**kw)
        except Exception:  # noqa: BLE001 -- memory is a nicety, the reply must still go
            logger.exception("[TD] memory: %s failed", fn.__name__)
            return None
    return run


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _safe(_dispatch))
    ctx.register_hook("pre_llm_call", _safe(_before))
    ctx.register_hook("post_llm_call", _safe(_after))
