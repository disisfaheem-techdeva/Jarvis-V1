"""Jarvis API v1 (8 Nov 2026; docs/JARVIS-API.md, ADR-003).

One stable, versioned contract for anything that talks to Jarvis: the dashboard
today, a till, a phone app or WhatsApp later. It is a facade over the Jarvis that
already runs in /console (app/console.py, app/jarvis_actions.py,
app/jarvis_memory.py), not a second Jarvis:

  * the turn, the sealed "yes", speech and conversations are answered by the very
    same route functions the dashboard uses, looked up on the app's route table,
    so identity, row-level security, roles, trial locks, AI credits, audit and the
    per-person history behave exactly as they do there;
  * nothing here calls a model provider. The provider stays behind app/ai.py, so
    switching Gemini for Claude or OpenAI changes no client;
  * what changes is the shape: one request and one response format, one error
    format, and a capabilities document a client can read instead of guessing.

The /console routes are untouched and keep serving the current dashboard.
Breaking changes to this contract go to /jarvis/v2, never into v1.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import jarvis_actions as ja
from .auth import SESSION_COOKIE

API = "jarvis/v1"
CONFIRM_TTL = 300          # jarvis_actions.seal's default
TEXT_MAX = 2000            # the same limit console enforces
# console's own answer kinds -> the three a client has to handle
KINDS = {"question": "answer", "confirm": "confirm", "action": "done", "teach": "done",
         "dashboard": "done", "post": "done", "business": "done"}
_SHAPED = {"kind", "reply", "confirm", "summary", "action", "ok", "navigate", "conversation_id"}


class _Routes:
    """The existing Jarvis route functions, found once on the running app."""
    cache: dict = {}

    @classmethod
    def get(cls, app, method, path):
        key = (method, path)
        if key not in cls.cache:
            for r in app.routes:
                if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or ()):
                    cls.cache[key] = r.endpoint
                    break
            else:
                raise HTTPException(503, "Jarvis is not running on this server.")
        return cls.cache[key]


def _error(e: HTTPException, conversation_id=None):
    headers = dict(e.headers or {})
    conv = conversation_id or headers.get("X-Jarvis-Conversation")
    body = {"api": API, "error": {"status": e.status_code, "message": str(e.detail)}}
    if conv:
        body["conversation_id"] = conv
    return JSONResponse(body, status_code=e.status_code, headers=headers)


def shape(out: dict, conversation_id=None) -> dict:
    """console's answer in the v1 response format."""
    intent = (out.get("kind") or "question").strip().lower()
    res = {"api": API, "conversation_id": out.get("conversation_id") or conversation_id,
           "kind": KINDS.get(intent, "answer"), "intent": intent, "reply": out.get("reply") or ""}
    if intent == "confirm":
        res["confirmation"] = {"token": out.get("confirm"), "action": out.get("action"),
                               "summary": out.get("summary") or res["reply"], "expires_in": CONFIRM_TTL}
    elif out.get("action"):
        res["action"] = {"name": out.get("action"), "ok": bool(out.get("ok"))}
    if out.get("navigate"):
        res["navigate"] = out["navigate"]
    extra = {k: v for k, v in out.items() if k not in _SHAPED}
    if extra:
        res["data"] = extra
    return res


def _speech_caps():
    from .speech import capabilities
    return capabilities()


def make_jarvis_api_router():
    router = APIRouter(prefix="/jarvis/v1", tags=["jarvis"])

    def turn(request, response, body, x_tenant_id, x_role, authorization, td_session):
        ask = _Routes.get(request.app, "POST", "/console")
        try:
            out = ask(response, body, x_tenant_id, x_role, authorization, td_session)
        except HTTPException as e:
            return _error(e, response.headers.get("X-Jarvis-Conversation"))
        return shape(out, response.headers.get("X-Jarvis-Conversation"))

    @router.get("")
    def capabilities(request: Request,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What this Jarvis can do, for a client to read instead of hard-coding.
        Signed-in only; it names no model, provider or key."""
        from .console import _identity
        try:
            _identity(x_tenant_id, x_role, authorization, td_session)
        except HTTPException as e:
            return _error(e)
        return {"api": API,
                "kinds": sorted(set(KINDS.values())),
                "actions": sorted(ja.ACTIONS),
                "confirm_first": sorted(ja.CONFIRM & ja.ACTIONS),
                "languages": ["roman-urdu", "ur", "en"],
                # input "server": send recordings to /transcribe; "client": use the browser's own hearing
                "speech": _speech_caps(),
                "limits": {"text_max": TEXT_MAX, "confirmation_ttl_seconds": CONFIRM_TTL}}

    @router.post("/messages")
    def message(request: Request, response: Response, body: dict = Body(...),
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One thing the person said or typed. Answers, asks "shall I?", or does it."""
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return _error(HTTPException(400, "Say or type what you would like."))
        legacy = {"message": text, "voice": bool(body.get("voice"))}
        for k in ("conversation_id", "tenant_id", "image_urls"):
            if body.get(k) is not None:
                legacy[k] = body[k]
        ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
        if isinstance(ctx.get("register"), str) and ctx["register"].strip():
            legacy["till_register"] = ctx["register"].strip()[:40]     # a bill said at a till goes on that till
        return turn(request, response, legacy, x_tenant_id, x_role, authorization, td_session)

    @router.post("/confirmations")
    def confirm(request: Request, response: Response, body: dict = Body(...),
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The person's "yes" to a confirmation: runs exactly what was sealed, once.
        No model is asked again."""
        token = body.get("token")
        if not isinstance(token, str) or not token.strip():
            return _error(HTTPException(400, "Which confirmation? Send its token."))
        legacy = {"confirm": token, "said": str(body.get("said") or "Yes")[:200]}
        for k in ("conversation_id", "tenant_id"):
            if body.get(k) is not None:
                legacy[k] = body[k]
        return turn(request, response, legacy, x_tenant_id, x_role, authorization, td_session)

    @router.post("/transcribe")
    def transcribe(body: dict = Body(...),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What was said in a recording (data URL audio), when this server hears
        (app/speech.py). 501 when the browser's own hearing is used instead."""
        from .console import _identity
        from . import speech as sp
        try:
            _identity(x_tenant_id, x_role, authorization, td_session)
        except HTTPException as e:
            return _error(e)
        try:
            out = sp.transcribe(body.get("audio"), str(body.get("lang") or "")[:5] or None)
        except sp.SpeechError as e:
            return _error(HTTPException(e.status, e.message))
        return {"api": API, "text": out["text"]}

    @router.post("/speech")
    def speech(request: Request, body: dict = Body(...),
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Jarvis's voice for a reply: audio/wav."""
        speak = _Routes.get(request.app, "POST", "/console/speak")
        try:
            return speak({"text": body.get("text") or "", "lang": body.get("lang")},
                         x_tenant_id, x_role, authorization, td_session)
        except HTTPException as e:
            return _error(e)

    @router.get("/conversations")
    def conversations(request: Request, limit: int = 20,
                      x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """This person's conversations in this business, newest first."""
        mine = _Routes.get(request.app, "GET", "/console/conversations")
        try:
            return dict(mine(limit, x_tenant_id, x_role, authorization, td_session), api=API)
        except HTTPException as e:
            return _error(e)

    @router.get("/conversations/{conv_id}")
    def conversation(conv_id: str, request: Request,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One of this person's conversations, with its messages."""
        one = _Routes.get(request.app, "GET", "/console/conversations/{conv_id}")
        try:
            return dict(one(conv_id, x_tenant_id, x_role, authorization, td_session), api=API)
        except HTTPException as e:
            return _error(e)

    return router
