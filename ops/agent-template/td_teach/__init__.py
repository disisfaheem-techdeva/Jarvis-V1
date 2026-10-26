"""TechDeva td_teach: the owner teaches their WhatsApp agent over WhatsApp.

    /sikho <lesson>   the lesson is held, and the owner is asked to confirm
    /haan             the held lesson is saved for the platform to pick up
    /nahi             the held lesson is dropped

Only the owner can run these: add-teach.sh puts the owner's number (phone and
WhatsApp LID forms) in the gateway's whatsapp.allow_admin_from, and with that
list set the gateway refuses every slash command except /help and /whoami to
anyone else. The commands never reach the model (no quota, nothing a customer
could steer).

A confirmed lesson is appended to $HERMES_HOME/td_teach/inbox.jsonl. The host
cron ops/agent-template/teach-sync.sh moves it into the platform's
agent_lessons table, which writes it into this agent's SOUL.md (TECHDEVA-
LESSONS block) and shows it on the dashboard. Nothing here edits SOUL.md: the
platform's list stays the one source, so the dashboard can remove a lesson.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

MAX = 600


def _dir() -> Path:
    home = os.environ.get("HERMES_HOME") or "/opt/data"
    d = Path(home) / "td_teach"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pending_path() -> Path:
    return _dir() / "pending.json"


def _sikho(raw_args: str) -> str:
    text = " ".join(str(raw_args or "").split())
    if not text:
        return ("Sabaq ke saath likhein, jaise:\n/sikho Hum Sunday ko band hote hain\n\n"
                "Ek message mein ek baat. Lambi hidayat dashboard par \"Full guide\" mein daalein.")
    if len(text) > MAX:
        return "Yeh %d harf ka hai; ek sabaq %d harf tak ho sakta hai. Chhota kar ke dobara bhejein." % (len(text), MAX)
    tmp = _pending_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"text": text, "at": int(time.time())}), encoding="utf-8")
    os.replace(tmp, _pending_path())
    return ("Yeh sabaq save karoon?\n\n\"%s\"\n\nSave ke liye /haan likhein, chhorne ke liye /nahi." % text)


def _haan(raw_args: str) -> str:
    p = _pending_path()
    try:
        held = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "Koi sabaq save ke liye nahi hai. Pehle /sikho <sabaq> likhein."
    if time.time() - int(held.get("at") or 0) > 3600:
        p.unlink(missing_ok=True)
        return "Woh sabaq ek ghante se purana ho gaya. Dobara /sikho <sabaq> likhein."
    entry = {"id": uuid.uuid4().hex, "text": held["text"], "at": int(time.time())}
    with open(_dir() / "inbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    p.unlink(missing_ok=True)
    return "Save ho gaya. 1-2 minute mein agent yeh seekh lega, aur dashboard par \"What your agent has been taught\" mein dikhega."


def _nahi(raw_args: str) -> str:
    p = _pending_path()
    if p.exists():
        p.unlink(missing_ok=True)
        return "Theek hai, woh sabaq chhor diya."
    return "Koi sabaq save ke liye nahi tha."


def register(ctx):
    ctx.register_command("sikho", _sikho, description="Owner: teach your agent one rule", args_hint="<sabaq>")
    ctx.register_command("haan", _haan, description="Owner: save the lesson you just sent")
    ctx.register_command("nahi", _nahi, description="Owner: drop the lesson you just sent")
