"""Jarvis's conversations, stored (24 Oct 2026; db/75).

Every message the person sends and every answer Jarvis gives is written to
jarvis_messages under a conversation that belongs to one business AND one person.
The history the model reads comes from here, not from the browser, and the page
reloads the conversation after a refresh.

Isolation: row-level security limits rows to the business; every function here
also requires the conversation's user_id to be the signed-in person, so a team
member can never open the owner's chats (or another business's, which RLS
already refuses).
"""
from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .auth import SESSION_COOKIE
from .db import tenant_session

HISTORY_TURNS = 8


def is_person(uid):
    """Only a real login keeps conversations (service tokens and dev headers do not)."""
    try:
        uuid.UUID(str(uid))
        return True
    except (ValueError, TypeError):
        return False


def _title(text):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return (t[:60] + "…") if len(t) > 60 else t


def open_conversation(tid, role, uid, conv_id=None, first_text=""):
    """The person's conversation: the one asked for (if it is theirs) or a new one."""
    with tenant_session(tid, role) as cur:
        if conv_id:
            cur.execute("SELECT id::text FROM jarvis_conversations WHERE id::text = %s AND user_id = %s",
                        (str(conv_id), str(uid)))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That conversation is not yours, or it was deleted.")
            return r["id"]
        cur.execute("""INSERT INTO jarvis_conversations (tenant_id, user_id, title) VALUES (current_tenant(), %s, %s)
                       RETURNING id::text""", (str(uid), _title(first_text)))
        return cur.fetchone()["id"]


def add_message(tid, role, conv_id, who, text, kind=None, meta=None):
    with tenant_session(tid, role) as cur:
        cur.execute("""INSERT INTO jarvis_messages (conversation_id, tenant_id, role, text, kind, meta)
                       VALUES (%s, current_tenant(), %s, %s, %s, %s::jsonb)""",
                    (conv_id, who, str(text or "")[:4000], kind, json.dumps(meta or {}, default=str)))
        cur.execute("UPDATE jarvis_conversations SET updated_at = now(), "
                    "title = CASE WHEN title = '' AND %s = 'user' THEN %s ELSE title END WHERE id = %s",
                    (who, _title(text), conv_id))


def history(tid, role, conv_id, n=HISTORY_TURNS):
    """The last turns, oldest first, in the shape console._history reads."""
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT role, text FROM (SELECT id, role, text FROM jarvis_messages WHERE conversation_id = %s
                        ORDER BY id DESC LIMIT %s) m ORDER BY id""", (conv_id, n))
        return [{"role": r["role"], "text": r["text"]} for r in cur.fetchall()]


def make_jarvis_memory_router(identity):
    """identity: console._identity, so the same scope rules apply as for /console."""
    router = APIRouter(prefix="/console/conversations", tags=["console"])

    def who(x_tenant_id, x_role, authorization, session):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, session)
        if not is_person(uid):
            raise HTTPException(401, "Sign in to keep conversations with Jarvis.")
        return tid, role, uid

    @router.get("")
    def mine(limit: int = 20, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """This person's conversations in this business, newest first."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT c.id::text, c.title, c.created_at, c.updated_at,
                                  (SELECT count(*) FROM jarvis_messages m WHERE m.conversation_id = c.id) AS messages
                             FROM jarvis_conversations c WHERE c.user_id = %s
                            ORDER BY c.updated_at DESC LIMIT %s""", (str(uid), max(1, min(int(limit), 100))))
            return {"conversations": [dict(r) for r in cur.fetchall()]}

    @router.get("/{conv_id}")
    def one(conv_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One conversation's messages (the newest 200), if it is this person's."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id::text, title, updated_at FROM jarvis_conversations WHERE id::text = %s AND user_id = %s",
                        (conv_id, str(uid)))
            conv = cur.fetchone()
            if not conv:
                raise HTTPException(404, "That conversation is not yours, or it was deleted.")
            cur.execute("""SELECT role, text, kind, meta, created_at FROM (
                             SELECT * FROM jarvis_messages WHERE conversation_id = %s ORDER BY id DESC LIMIT 200) m
                           ORDER BY id""", (conv_id,))
            return {"conversation": dict(conv), "messages": [dict(r) for r in cur.fetchall()]}

    @router.post("/{conv_id}/notes")
    def note(conv_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A turn answered in the page without the AI (a "No" to a question Jarvis asked)."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        open_conversation(tid, role, uid, conv_id)
        said, answer = str(body.get("said") or "").strip()[:500], str(body.get("reply") or "").strip()[:1000]
        if not said or not answer:
            raise HTTPException(400, "Nothing to note.")
        add_message(tid, role, conv_id, "user", said, "local")
        add_message(tid, role, conv_id, "jarvis", answer, "local")
        return {"ok": True}

    @router.delete("/{conv_id}")
    def forget(conv_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Delete one of this person's conversations."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("DELETE FROM jarvis_conversations WHERE id::text = %s AND user_id = %s RETURNING id", (conv_id, str(uid)))
            if not cur.fetchone():
                raise HTTPException(404, "That conversation is not yours, or it was deleted.")
        return {"ok": True}

    return router
