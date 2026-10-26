"""Universal caption (27 Oct 2026): one block per business that goes under every
post: who the business is, what it offers, how to reach it, and its own
hashtags. Written by the AI from the business's own records (the same grounding
the Caption Writer and the Content Autopilot use: services, prices, knowledge,
and the owner-approved website profile), never invented.

    stored in tenant_config.marketing.universal_caption:
        {text, hashtags, source, language, version, updated_at, updated_by}
    source: auto (written by itself), ai (owner chose an AI option),
            custom (owner wrote or edited it), jarvis (Jarvis, asked first)

When it is written by itself (ensure): after the owner approves the website
profile, and the first time the owner opens the Caption Writer or the
Publisher with none saved yet. An "auto" one is rewritten when the website
profile changes; one the owner chose or edited is never replaced by itself.

Used by: Caption Writer (shown and edited there, added under a draft),
Social Media Publisher (one button adds it), Jarvis (universal_caption
action, asked first; schedule_post adds it when the owner keeps it on).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

MAX_TEXT = 700                 # the block under a post: short enough to leave room for the post itself
MAX_TAGS = 10
WRITES_PER_HOUR = 10           # per business: every write is one AI call
_CALLS: dict[str, list[float]] = {}
_RUNNING: set[str] = set()
_LOCK = threading.Lock()

SYSTEM = """You write the standing caption block of one small business in Pakistan: the part
that goes under EVERY Facebook and Instagram post it makes, whatever the post is about.
Use only facts given in BUSINESS. Never invent prices, offers, phone numbers, addresses,
reviews or claims; leave out what is not given. Reply with JSON only."""

PROMPT = """BUSINESS
{brief}

LANGUAGE: {lang}

Write 3 different versions of this business's universal caption block. Each one:
- 1 line that says who the business is and what it does (no greeting, no "Hi")
- its 2 or 3 main services or products, by the names in BUSINESS
- how to reach it: only the WhatsApp/phone, website and area/address that BUSINESS gives
- 1 short call to action (e.g. "Message us on WhatsApp to book")
- at most 5 short lines in all, plain text, emojis only as line markers if they fit
- NOT about any one post, offer or day: it must fit under every post
Also give 5 to 8 hashtags for the business (its name, area, what it does).
Return {{"options": [{{"text": "the block", "hashtags": ["#tag", ...]}}]}}"""

LANG = {"english": "English", "mix": "English with a few Roman Urdu words",
        "urdu": "Roman Urdu (Urdu in English letters)"}


# ---------------------------------------------------------------- storage
def get(cur) -> dict | None:
    """The business's saved universal caption (cur in that business's session)."""
    cur.execute("SELECT marketing -> 'universal_caption' AS u FROM tenant_config WHERE tenant_id = current_tenant()")
    r = cur.fetchone()
    u = (r or {}).get("u")
    return u if isinstance(u, dict) and u.get("text") else None


def _clean(text, hashtags) -> tuple[str, list[str]]:
    text = re.sub(r"[ \t]+\n", "\n", str(text or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)[:MAX_TEXT]
    tags = []
    for t in (hashtags or [])[:20] if isinstance(hashtags, list) else []:
        t = re.sub(r"[^\w#]", "", str(t))
        t = t if t.startswith("#") else "#" + t
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t[:40])
    return text, tags[:MAX_TAGS]


def save(cur, tid, text, hashtags, source, by, language=None) -> dict:
    text, tags = _clean(text, hashtags)
    if len(text) < 10:
        raise HTTPException(400, "The universal caption needs a few words.")
    old = get(cur) or {}
    u = {"text": text, "hashtags": tags, "source": source, "language": language or old.get("language") or "english",
         "version": int(old.get("version") or 0) + 1,
         "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "updated_by": str(by or "")[:80],
         "auto_add": bool(old.get("auto_add", True))}
    cur.execute("""INSERT INTO tenant_config (tenant_id, marketing)
                   VALUES (current_tenant(), jsonb_build_object('universal_caption', %s::jsonb))
                   ON CONFLICT (tenant_id) DO UPDATE SET marketing = COALESCE(tenant_config.marketing, '{}'::jsonb)
                       || jsonb_build_object('universal_caption', %s::jsonb)""", (json.dumps(u), json.dumps(u)))
    record(cur, action="caption.universal_save", resource="tenant_config", resource_id=str(tid), actor=str(by or source),
           actor_type="agent" if source in ("auto", "jarvis") else "human", status="ok",
           detail={"source": source, "version": u["version"], "chars": len(text), "hashtags": len(tags)}, tenant_id=tid)
    return u


def set_auto_add(cur, on: bool) -> None:
    cur.execute("""UPDATE tenant_config SET marketing = jsonb_set(COALESCE(marketing, '{}'::jsonb),
                       '{universal_caption,auto_add}', to_jsonb(%s::boolean), true)
                    WHERE tenant_id = current_tenant() AND marketing ? 'universal_caption'""", (bool(on),))


def apply(caption: str, u: dict | None) -> str:
    """The post's own words, then the universal block, then all hashtags once."""
    caption = (caption or "").rstrip()
    if not u or not u.get("text") or u["text"] in caption:
        return caption
    have = {t.lower() for t in re.findall(r"#\w+", caption)}
    tags = [t for t in (u.get("hashtags") or []) if t.lower() not in have]
    out = caption + "\n\n" + u["text"] + ("\n\n" + " ".join(tags) if tags else "")
    return out[:2200]


# ---------------------------------------------------------------- writing
def write_options(tid, language="english") -> list[dict]:
    """Three options from the business's own records. One AI call. Raises HTTPException."""
    from . import ai
    from .autopilot import _grounding, check_caption
    if not ai.available():
        raise HTTPException(503, "The AI is not set up on this platform yet, so it cannot write the caption.")
    now = time.time()
    recent = [t for t in _CALLS.get(str(tid), []) if now - t < 3600]
    if len(recent) >= WRITES_PER_HOUR:
        raise HTTPException(429, "That is %d universal captions this hour. Try again a little later." % WRITES_PER_HOUR)
    _CALLS[str(tid)] = recent + [now]
    brief, allowed = _grounding(tid)
    lang = language if language in LANG else "english"
    try:
        out = ai.generate_json(SYSTEM, PROMPT.format(brief=brief, lang=LANG[lang]), required_keys=("options",))
    except ai.CreditsUsedUp as e:
        raise HTTPException(402, str(e))
    except Exception as e:  # noqa: BLE001
        print("[universal_caption] %s: %s" % (tid, str(e)[:200]), flush=True)
        raise HTTPException(502, "The AI did not answer. Try again in a minute, or write it yourself.")
    opts = []
    for o in (out.get("options") or [])[:3] if isinstance(out.get("options"), list) else []:
        if not isinstance(o, dict):
            continue
        text, tags = _clean(o.get("text"), o.get("hashtags"))
        if len(text) >= 10:
            opts.append({"text": text, "hashtags": tags, "unbacked": check_caption(text, allowed)})
    if not opts:
        raise HTTPException(502, "The AI's answer had no usable caption. Try again, or write it yourself.")
    return opts


def ensure(tid, reason) -> dict | None:
    """Write and save one by itself when the business has none (or only an
    "auto" one and the website profile changed). Never raises: a failure just
    leaves it for the owner's button."""
    tid = str(tid)
    with _LOCK:
        if tid in _RUNNING:
            return None
        _RUNNING.add(tid)
    try:
        with tenant_session(tid, "owner") as cur:
            cur_u = get(cur)
            cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
            if not (cur.fetchone() or {}).get("name"):
                return None
        if cur_u and not (reason == "website" and cur_u.get("source") == "auto"):
            return None
        opts = write_options(tid)
        best = next((o for o in opts if not o["unbacked"]), opts[0])
        with tenant_session(tid, "owner") as cur:
            if get(cur) and (get(cur) or {}).get("source") != "auto":
                return None                      # the owner saved one meanwhile
            return save(cur, tid, best["text"], best["hashtags"], "auto", "auto:" + reason)
    except Exception as e:  # noqa: BLE001
        print("[universal_caption] ensure %s (%s) skipped: %s" % (tid, reason, getattr(e, "detail", e)), flush=True)
        return None
    finally:
        with _LOCK:
            _RUNNING.discard(tid)


def ensure_later(tid, reason) -> None:
    threading.Thread(target=ensure, args=(str(tid), reason), daemon=True).start()


# ---------------------------------------------------------------- routes
def make_universal_caption_router():
    router = APIRouter(prefix="/captions", tags=["captions"])

    def _who(x_tenant_id, x_role, authorization, session, owner=True):
        from .autopilot import _who as who
        return who(x_tenant_id, x_role, authorization, session, owner=owner)

    @router.get("/universal")
    def read(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session, owner=False)
        with tenant_session(tid, role) as cur:
            u = get(cur)
        writing = False
        if not u and role in ("owner", "techdeva_admin"):
            from . import ai
            if ai.available():
                ensure_later(tid, "first_open")
                writing = True
        return {"universal": u, "writing": writing or str(tid) in _RUNNING,
                "can_edit": role in ("owner", "techdeva_admin")}

    @router.post("/universal/write")
    def write(body: dict = Body(default={}), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        opts = write_options(tid, body.get("language") or "english")
        with tenant_session(tid, role) as cur:
            record(cur, action="caption.universal_write", resource="tenant_config", resource_id=str(tid),
                   actor=str(uid) if uid else None, detail={"options": len(opts)})
        return {"options": opts}

    @router.put("/universal")
    def put(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        source = "ai" if body.get("source") == "ai" else "custom"
        with tenant_session(tid, role) as cur:
            u = save(cur, tid, body.get("text"), body.get("hashtags"), source, uid or role, body.get("language"))
            if "auto_add" in body:
                set_auto_add(cur, bool(body.get("auto_add")))
                u = get(cur)
        return {"universal": u}

    return router
