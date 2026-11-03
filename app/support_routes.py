"""Support agent over HTTP (app/support_agent.py). Backend only; no screen yet.

A business (its own dashboard, console._identity scope):
  GET  /support            its open problems and what was fixed
  POST /support/run        check this business now                  (owner)
  POST /support/fix        {key}: press a problem's fix              (owner)
  POST /support/ask        {question, history}: ask Support          (any login that can see the dashboard)

The founder (admin only):
  GET  /techdeva/support            the platform's problems + every business with open ones
  GET  /techdeva/support/{tenant}   one business, including internal (TechDeva-only) problems
  POST /techdeva/support/run        {tenant_id?}: check now (one business, or everything)
  POST /techdeva/support/fix        {tenant_id?, key}
  POST /techdeva/support/ask        {question, history, tenant_id?}
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from . import support_agent as sa
from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .founder import _admin


def _tenant(value):
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise HTTPException(400, "That business id is not valid.")


def _question(body):
    q = str((body or {}).get("question") or "").strip()
    if not q:
        raise HTTPException(400, "Write a question.")
    history = (body or {}).get("history") or []
    return q[:1000], [h for h in history if isinstance(h, dict)][-6:]


def _do_fix(tid, key, by, founder):
    try:
        return sa.fix(tid, str(key or ""), by, founder=founder)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(409, str(e))


def _audit(tid, action, uid, detail):
    with tenant_session(None, "techdeva_admin") as cur:
        record(cur, action=action, resource="support_findings", actor=str(uid) if uid else None,
               actor_type="human", detail=detail, tenant_id=tid)


def make_support_router(identity):
    """identity: console._identity, the same scope rules as every other dashboard read."""
    router = APIRouter(prefix="/support", tags=["support"])

    def owner_only(role):
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can do this.")

    @router.get("")
    def get_support(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        return sa.overview(tid)

    @router.post("/run")
    def run_now(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        owner_only(role)
        out = sa.run_scope(tid)
        return {**out, **sa.overview(tid)}

    @router.post("/fix")
    def press_fix(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        owner_only(role)
        what = _do_fix(tid, body.get("key"), "Owner", founder=False)
        _audit(tid, "support.fix", uid, {"key": body.get("key"), "what": what})
        return {"done": what, **sa.overview(tid)}

    @router.post("/ask")
    def ask(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
            x_role: str | None = Header(None), authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        q, history = _question(body)
        return {"answer": sa.ask(tid, q, history)}

    return router


def make_founder_support_router():
    router = APIRouter(prefix="/techdeva/support", tags=["support"])

    def admin(x_tenant_id, x_role, authorization, session):
        return _admin(x_tenant_id, x_role, authorization, session)

    @router.get("")
    def summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        admin(x_tenant_id, x_role, authorization, td_session)
        return sa.founder_summary()

    @router.post("/run")
    def run_now(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        admin(x_tenant_id, x_role, authorization, td_session)
        tid = _tenant(body.get("tenant_id"))
        if tid:
            return {**sa.run_scope(tid), **sa.overview(tid, founder=True)}
        return {**sa.run_all(), **sa.founder_summary()}

    @router.post("/fix")
    def press_fix(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = admin(x_tenant_id, x_role, authorization, td_session)
        tid = _tenant(body.get("tenant_id"))
        what = _do_fix(tid, body.get("key"), "TechDeva", founder=True)
        _audit(tid, "support.fix", uid, {"key": body.get("key"), "what": what})
        return {"done": what, **(sa.overview(tid, founder=True) if tid else sa.founder_summary())}

    @router.post("/ask")
    def ask(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
            x_role: str | None = Header(None), authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        admin(x_tenant_id, x_role, authorization, td_session)
        q, history = _question(body)
        return {"answer": sa.ask(_tenant(body.get("tenant_id")), q, history, founder=True)}

    @router.get("/{tenant_id}")
    def one_business(tenant_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        admin(x_tenant_id, x_role, authorization, td_session)
        return sa.overview(_tenant(tenant_id), founder=True)

    return router
