"""Set up the business by talking to Jarvis (Phase 9, 9 Nov 2026).

A karyana rarely has a website for the website analyzer (app/website_agent.py) to
read. So the owner tells Jarvis about the shop, by voice or text, in one go or a
bit at a time:

    "Meri dukaan Ali Karyana Store hai, Gulberg Lahore mein. Subah 8 se raat 11
     tak khuli hai. Cheeni 180 rupay kilo, aata 160, ghee 550..."

Each piece is added to what was said before, and the WHOLE account is read again
by the existing reader (app/setup_reader.read_text): every item and price in the
draft must appear in what the owner said, nothing is invented. The draft is kept
as the business's setup suggestion (db/59 setup_suggestions, the same one the
Business & agent page reviews), so nothing is saved until the owner presses Save
there (app/categories setup_apply). After each piece, Jarvis asks for the next
thing it still does not know, one question at a time.
"""
from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

TOLD = "told:"                  # fingerprint prefix of a suggestion built from what the owner said
MAX_TOTAL = 20000               # setup_reader reads at most this much

# what a new business should tell, in the order it is asked; (key, question, words that show it was told)
CHECKLIST = (
    ("type", "Aap ka kaam kya hai: karyana store, dukaan, restaurant, salon, clinic? Ek line mein batayein.", ()),
    ("items", "Aap kya kya bechte hain, aur kis rate par? Jaise: cheeni 180 rupay kilo, aata 160, ghee 550.", ()),
    ("hours", "Dukaan kab khulti aur kab band hoti hai? Koi din band rehti hai?", ()),
    ("location", "Dukaan kahan hai? Area aur shehar batayein.",
     ("location", "address", "area", "located", "shop is in", "near", "lahore", "karachi", "islamabad", "rawalpindi",
      "faisalabad", "multan", "peshawar", "gulberg", "dha", "johar", "model town", "pata", "kahan")),
    ("payments", "Customer kaise payment karte hain: cash, JazzCash, EasyPaisa, bank?",
     ("payment", "cash", "jazzcash", "easypaisa", "bank", "card", "raast")),
    ("delivery", "Kya aap ghar par delivery karte hain? Kahan tak, aur kitne ki?",
     ("delivery", "deliver", "home delivery", "ghar", "pickup", "pick up")),
    ("udhaar", "Kya aap udhaar dete hain, aur kis ko?",
     ("udhaar", "udhar", "credit", "khata", "baad mein")),
)
SHOPS = ("karyana", "shop")


def _blob(draft):
    k = draft.get("knowledge") or []
    return " ".join(str(x.get("title") or "") + " " + str(x.get("body") or "") for x in k if isinstance(x, dict)).lower()


def missing(draft, source_text, business_type):
    """The checklist items not yet told, in order. A business type set already counts."""
    told = (_blob(draft) + " " + str(source_text or "")).lower()
    btype = draft.get("business_type") or business_type
    out = []
    for key, _q, words in CHECKLIST:
        if key == "type" and btype:
            continue
        if key == "items" and (draft.get("services") or []):
            continue
        if key == "hours" and (draft.get("hours") or []):
            continue
        if key == "udhaar" and btype not in SHOPS:
            continue
        if words and any(w in told for w in words):
            continue
        out.append(key)
    return out


def question(key):
    return next(q for k, q, _w in CHECKLIST if k == key)


def _current(cur, tid):
    """The open suggestion built from what the owner told, if there is one."""
    cur.execute("""SELECT id::text, source_text FROM setup_suggestions
                    WHERE tenant_id = %s AND status = 'ready' AND fingerprint LIKE %s
                    ORDER BY created_at DESC LIMIT 1""", (tid, TOLD + "%"))
    return cur.fetchone()


def describe(tid, role, uid, text, fresh=False):
    """Add what the owner just said, read everything said so far, keep it as the suggestion to review."""
    from .setup_reader import read_text, compare
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(text) < 3:
        raise HTTPException(400, "Tell me about your business: what you sell and at what price, your hours, where you are.")
    with tenant_session(tid, role) as cur:
        cur.execute("SELECT slug FROM business_categories WHERE active ORDER BY sort_order")
        slugs = [r["slug"] for r in cur.fetchall()]
        cur.execute("SELECT business_type FROM tenants WHERE id = %s", (tid,))
        btype = (cur.fetchone() or {}).get("business_type")
        prev = None if fresh else _current(cur, tid)
    so_far = ((prev["source_text"] + "\n") if prev else "") + text
    if len(so_far) > MAX_TOTAL:
        raise HTTPException(400, "That is a lot to read at once. Save what is ready on Business & agent, then tell me the rest.")
    draft = read_text(so_far, slugs)                       # never invents: every item must be in what was said
    gaps = missing(draft, so_far, btype)
    src = [{"kind": "told", "ref": "what you told Jarvis", "ok": True, "note": None}]
    with tenant_session(tid, role) as cur:
        if prev:
            cur.execute("""UPDATE setup_suggestions SET draft = %s, source_text = %s, sources = %s
                            WHERE id = %s::uuid RETURNING id::text""",
                        (json.dumps(draft), so_far, json.dumps(src), prev["id"]))
        else:
            # only the newest suggestion is offered: older ready ones step aside
            cur.execute("UPDATE setup_suggestions SET status = 'dismissed', decided_at = now() WHERE tenant_id = %s AND status = 'ready'",
                        (tid,))
            cur.execute("""INSERT INTO setup_suggestions (tenant_id, sources, fingerprint, draft, source_text, status)
                           VALUES (%s, %s, %s, %s, %s, 'ready') RETURNING id::text""",
                        (tid, json.dumps(src), TOLD + str(uuid.uuid4()), json.dumps(draft), so_far))
        sid = cur.fetchone()["id"]
        record(cur, action="business.setup_told", resource="setup_suggestions", resource_id=sid, actor=str(uid or ""),
               detail={"items": len(draft.get("services") or []), "hours": len(draft.get("hours") or []),
                       "notes": len(draft.get("knowledge") or []), "missing": gaps})
    d = compare(tid, {**{"services": [], "hours": [], "knowledge": [], "dropped": []}, **draft})
    return {"suggestion_id": sid, "draft": d, "missing": gaps,
            "next_question": question(gaps[0]) if gaps else None}


def summary(out):
    """What Jarvis says back: what it has, where to check it, and the next question."""
    d = out["draft"]
    parts = []
    if d.get("business_type"):
        parts.append({"karyana": "karyana store", "shop": "dukaan"}.get(d["business_type"], d["business_type"]))
    n = len(d.get("services") or [])
    if n:
        priced = sum(1 for s in d["services"] if s.get("price") is not None)
        parts.append("%d cheez%s (%d ke rate)" % (n, "" if n == 1 else "ein", priced))
    if d.get("hours"):
        open_days = [h for h in d["hours"] if not h.get("closed")]
        if open_days:
            h = open_days[0]
            parts.append("timings %s se %s" % (h.get("opens"), h.get("closes")))
    k = len(d.get("knowledge") or [])
    if k:
        parts.append("%d aur baatein" % k)
    t = ("Likh liya: " + ", ".join(parts) + ". ") if parts else "Abhi koi cheez saaf samajh nahi aayi. "
    if d.get("dropped"):
        t += "Kuch cheezein chhor di jo aap ki baat mein saaf nahi thi. "
    t += "Business & agent par check karke Save karein; Save se pehle kuch nahi badla."
    if out["next_question"]:
        t += " Ab batayein: " + out["next_question"]
    else:
        t += " Mujhe zaroori sab kuch mil gaya."
    return t


def make_setup_talk_router(identity):
    router = APIRouter(tags=["business-type"])

    @router.post("/business-profile/setup/describe")
    def setup_describe(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What the owner says about the business, added to what they said before and read
        into the setup suggestion to review. Saves nothing to the business."""
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the owner sets up the business.")
        out = describe(tid, role, uid, body.get("text"), fresh=bool(body.get("fresh")))
        return dict(out, reply=summary(out))

    return router
