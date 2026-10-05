"""Voice: the AI phone agent's records and settings (5 Oct 2026).

Calls are answered by app/voice_bridge.py (Asterisk -> Gemini Live). This is
the dashboard side: the call log with transcripts and outcomes, and each
number's settings.

  - A number is wired to a phone line on the server, so adding one is for
    TechDeva's admin; the business owner turns it on or off and sets the
    greeting, the assistant's name and the minute limits.
  - Staff can read the call log and mark a call reviewed; settings are the
    owner's.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity

OUTCOMES = ("interested", "booked", "ordered", "callback", "not_interested", "wrong_number", "question_answered", "unknown")
OWNER = ("owner", "techdeva_admin")


def digits(n):
    return re.sub(r"\D", "", str(n or ""))


def _number_row(r):
    d = dict(r)
    d["id"] = str(d["id"])
    d.pop("tenant_id", None)
    return d


def make_voice_router():
    router = APIRouter()

    def who(x_tenant_id, x_role, authorization, td_session):
        return _identity(x_tenant_id, x_role, authorization, td_session)

    @router.get("/calls")
    def calls(limit: int = 100, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT id::text, direction, from_number, to_number, status, started_at, duration_s,
                                  caller_name, outcome, summary, follow_up, reviewed, lead_id::text,
                                  jsonb_array_length(transcript) AS turns
                             FROM calls ORDER BY started_at DESC LIMIT %s""", (max(1, min(int(limit or 100), 300)),))
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT count(*) FILTER (WHERE started_at >= date_trunc('day', now())) AS today,
                                  COALESCE(sum(duration_s) FILTER (WHERE started_at >= date_trunc('day', now())), 0) AS seconds_today,
                                  count(*) FILTER (WHERE started_at >= date_trunc('month', now())) AS month,
                                  count(*) FILTER (WHERE started_at >= date_trunc('month', now())
                                                   AND outcome IN ('interested','booked','ordered','callback')) AS month_interested,
                                  count(*) FILTER (WHERE NOT reviewed AND outcome IN ('interested','callback')) AS to_follow_up
                             FROM calls""")
            s = {k: int(v) for k, v in cur.fetchone().items()}
            cur.execute("SELECT * FROM voice_numbers ORDER BY created_at")
            nums = [_number_row(r) for r in cur.fetchall()]
        return {"calls": rows, "summary": s, "numbers": nums}

    @router.get("/calls/{cid}")
    def one(cid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not re.match(r"^[0-9a-f-]{36}$", cid):
            raise HTTPException(404, "That call is not in this business.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT * FROM calls WHERE id = %s::uuid", (cid,))
            r = cur.fetchone()
        if not r:
            raise HTTPException(404, "That call is not in this business.")
        d = dict(r)
        for k in ("id", "tenant_id", "number_id", "lead_id"):
            d[k] = str(d[k]) if d.get(k) else None
        return d

    @router.post("/calls/{cid}/review")
    def review(cid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None), authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A person checked the call: correct the outcome, add a note, mark it done."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not re.match(r"^[0-9a-f-]{36}$", cid):
            raise HTTPException(404, "That call is not in this business.")
        sets, args = ["reviewed = %s"], [bool(body.get("reviewed", True))]
        if body.get("outcome") is not None:
            if body["outcome"] not in OUTCOMES:
                raise HTTPException(400, "Unknown outcome.")
            sets.append("outcome = %s"); args.append(body["outcome"])
        if body.get("follow_up") is not None:
            sets.append("follow_up = %s"); args.append(str(body["follow_up"]).strip()[:300])
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE calls SET " + ", ".join(sets) + " WHERE id = %s::uuid RETURNING id", args + [cid])
            if not cur.fetchone():
                raise HTTPException(404, "That call is not in this business.")
            record(cur, action="call.review", resource="calls", resource_id=cid, actor=str(uid),
                   detail={k: body.get(k) for k in ("outcome", "reviewed") if k in body})
        return {"ok": True}

    @router.post("/calls/numbers")
    def add_number(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Connect a phone number to this business (TechDeva admin: the line is set up on the server)."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if role != "techdeva_admin":
            raise HTTPException(403, "A number is connected by TechDeva, after the phone line is set up on the server.")
        did = digits(body.get("did"))
        if not 7 <= len(did) <= 15:
            raise HTTPException(400, "Write the number with its country code, e.g. 92 42 3576 1234.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT tenant_id::text FROM voice_numbers WHERE did = %s", (did,))
            r = cur.fetchone()
            if r and r["tenant_id"] != str(tid):
                raise HTTPException(409, "That number is already connected to another business.")
            if r:
                raise HTTPException(409, "That number is already connected to this business.")
            cur.execute("""INSERT INTO voice_numbers (tenant_id, did, label) VALUES (%s, %s, %s) RETURNING *""",
                        (tid, did, str(body.get("label") or "")[:80]))
            n = _number_row(cur.fetchone())
            record(cur, action="voice.number_add", resource="voice_numbers", resource_id=n["id"], actor=str(uid),
                   tenant_id=tid, detail={"did_last4": did[-4:]})
        return n

    @router.put("/calls/numbers/{nid}")
    def set_number(nid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None), authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner's settings for one number."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if role not in OWNER:
            raise HTTPException(403, "Only the owner can change the phone agent's settings.")
        if not re.match(r"^[0-9a-f-]{36}$", nid):
            raise HTTPException(404, "That number is not in this business.")
        sets, args = [], []
        if "enabled" in body:
            sets.append("enabled = %s"); args.append(bool(body["enabled"]))
        if "assistant_name" in body:
            nm = re.sub(r"\s+", " ", str(body["assistant_name"] or "")).strip()[:40]
            if not nm:
                raise HTTPException(400, "Give the assistant a name.")
            sets.append("assistant_name = %s"); args.append(nm)
        if "greeting" in body:
            sets.append("greeting = %s"); args.append(str(body["greeting"] or "").strip()[:400])
        if "label" in body:
            sets.append("label = %s"); args.append(str(body["label"] or "").strip()[:80])
        for k, lo, hi in (("max_call_minutes", 1, 30), ("max_minutes_per_day", 0, 2000)):
            if k in body:
                try:
                    v = int(body[k])
                except (TypeError, ValueError):
                    raise HTTPException(400, "%s must be a number." % k)
                if not lo <= v <= hi:
                    raise HTTPException(400, "%s must be between %d and %d." % (k, lo, hi))
                sets.append(k + " = %s"); args.append(v)
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE voice_numbers SET " + ", ".join(sets) + ", updated_at = now() WHERE id = %s::uuid RETURNING *",
                        args + [nid])
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That number is not in this business.")
            record(cur, action="voice.number_settings", resource="voice_numbers", resource_id=nid, actor=str(uid),
                   detail={k: body[k] for k in body if k != "greeting"})
        return _number_row(r)

    return router
