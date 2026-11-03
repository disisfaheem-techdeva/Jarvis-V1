"""POS routes (3 Nov 2026). The logic is in pos.py, pos_shift.py, pos_refund.py
and pos_settings.py; this file only resolves who is asking, opens one
tenant-scoped transaction per request and turns refusals into HTTP errors.

Every money route runs in ONE transaction: a refusal raised anywhere rolls the
whole request back, so a half-made sale, refund or close never lands.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request

from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity
from .orders import MONEY_ROLES, OrderError
from . import pos, pos_refund, pos_settings as cfg, pos_shift
from .pos_refund import RefundError
from .pos_shift import ShiftError

ERRORS = (OrderError, cfg.PosSettingsError, ShiftError, RefundError)
UUID = re.compile(r"^[0-9a-f-]{36}$")


def who(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
        authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
    if role not in ("owner", "operator", "techdeva_admin"):
        raise HTTPException(403, "The till is for the business's own team.")
    return {"tid": tid, "role": role, "uid": uid}


@contextmanager
def session(me, approval=None):
    """A tenant transaction plus the context every POS function takes."""
    try:
        with tenant_session(me["tid"], me["role"]) as cur:
            ctx = {**me, "settings": cfg.get(cur), "approval": approval}
            yield cur, ctx
    except ERRORS as e:
        raise HTTPException(e.status, e.message)


def _oid(oid):
    if not UUID.match(oid):
        raise HTTPException(404, "That bill is not in this business.")


def _owner(me, what):
    if me["role"] not in MONEY_ROLES:
        raise HTTPException(403, "%s is for the owner." % what)


def make_pos_router():
    router = APIRouter()

    # ------------------------------------------------------------ setup
    @router.get("/pos/catalogue")
    def catalogue(me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
            t = cur.fetchone()
            cur.execute("SELECT name FROM users WHERE id::text = %s", (str(me["uid"] or ""),))
            u = cur.fetchone()
            return {"business": t["name"] if t else None, "me": u["name"] if u else None, "role": me["role"],
                    "items": pos.till_catalogue(cur), "is_owner": me["role"] in MONEY_ROLES,
                    "methods": list(pos.METHODS), "settings": ctx["settings"]}

    @router.get("/pos/settings")
    def get_settings(me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            return ctx["settings"]

    @router.put("/pos/settings")
    def put_settings(body: dict = Body(...), me: dict = Depends(who)):
        _owner(me, "Changing the till settings")
        with session(me) as (cur, ctx):
            return cfg.save(cur, body, actor=me["uid"])

    @router.post("/pos/manager-pin")
    def manager_pin(request: Request, body: dict = Body(...), me: dict = Depends(who)):
        """A manager's PIN typed at a cashier's till: a 5-minute approval for one action."""
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
        try:
            return cfg.approve(me["tid"], body.get("pin"), str(body.get("action") or ""), ip=ip, asked_by=me["uid"])
        except cfg.PosSettingsError as e:
            raise HTTPException(e.status, e.message)

    # ------------------------------------------------------------ shift
    @router.get("/pos/shift")
    def shift_now(register: str = "Till 1", me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            s = pos_shift.current(cur, register)
            return {"shift": pos_shift.report(cur, s, me["role"]) if s else None,
                    "required": ctx["settings"]["require_shift"]}

    @router.post("/pos/shift/open")
    def shift_open(body: dict = Body(...), me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            s = pos_shift.open_shift(cur, body.get("register"), body.get("opening_float"), actor=me["uid"])
            return pos_shift.report(cur, s, me["role"])

    @router.post("/pos/shift/cash")
    def shift_cash(body: dict = Body(...), me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            pos_shift.cash_move(cur, body.get("register"), body.get("kind"), body.get("amount"), body.get("reason"),
                                actor=me["uid"])
            return pos_shift.report(cur, pos_shift.current(cur, body.get("register")), me["role"])

    @router.post("/pos/shift/close")
    def shift_close(body: dict = Body(...), me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            return pos_shift.close_shift(cur, body.get("register"), body.get("counted_cash"), body.get("notes"),
                                         actor=me["uid"], role=me["role"])

    @router.get("/pos/shifts")
    def shifts(limit: int = 30, me: dict = Depends(who)):
        _owner(me, "The shift history")
        with session(me) as (cur, ctx):
            return {"shifts": pos_shift.history(cur, limit)}

    @router.get("/pos/shifts/{sid}")
    def one_shift(sid: str, me: dict = Depends(who)):
        if not UUID.match(sid):
            raise HTTPException(404, "That shift is not in this business.")
        with session(me) as (cur, ctx):
            return pos_shift.report(cur, pos_shift.load(cur, sid), me["role"])

    # ------------------------------------------------------------ sales
    @router.get("/pos/today")
    def today(me: dict = Depends(who)):
        with session(me) as (cur, ctx):
            return {**pos.today(cur, me["role"]), "open_bills": pos.open_bills(cur)}

    @router.get("/pos/bills")
    def bills(day: str | None = None, q: str | None = None, status: str | None = None, me: dict = Depends(who)):
        if day:
            try:
                day = date.fromisoformat(day[:10]).isoformat()
            except ValueError:
                raise HTTPException(400, "The day is YYYY-MM-DD.")
        with session(me) as (cur, ctx):
            return {"bills": pos.bills(cur, day=day, q=(q or "").strip() or None, status=status)}

    @router.post("/pos/sale")
    def sale(body: dict = Body(...), me: dict = Depends(who)):
        with session(me, body.get("approval")) as (cur, ctx):
            shift = pos_shift.require(cur, body.get("register"), ctx["settings"])
            return pos.sale(cur, body, ctx=ctx, shift=shift)

    @router.get("/pos/bills/{oid}")
    def bill(oid: str, me: dict = Depends(who)):
        _oid(oid)
        with session(me) as (cur, ctx):
            pos._pos_order(cur, oid)
            return pos.receipt(cur, oid, ctx)

    @router.post("/pos/bills/{oid}/items")
    def add(oid: str, body: dict = Body(...), me: dict = Depends(who)):
        _oid(oid)
        with session(me, body.get("approval")) as (cur, ctx):
            return pos.add_items(cur, oid, body.get("items"), ctx=ctx)

    @router.post("/pos/bills/{oid}/lines/{item_id}/remove")
    def remove_line(oid: str, item_id: str, body: dict = Body(default={}), me: dict = Depends(who)):
        _oid(oid)
        _oid(item_id)
        with session(me, body.get("approval")) as (cur, ctx):
            return pos.void_line(cur, oid, item_id, body.get("reason"), ctx=ctx)

    @router.post("/pos/bills/{oid}/kitchen")
    def kitchen(oid: str, me: dict = Depends(who)):
        _oid(oid)
        with session(me) as (cur, ctx):
            return pos.kitchen_ticket(cur, oid, ctx=ctx)

    @router.post("/pos/bills/{oid}/settle")
    def settle(oid: str, body: dict = Body(...), me: dict = Depends(who)):
        _oid(oid)
        with session(me, body.get("approval")) as (cur, ctx):
            shift = pos_shift.require(cur, body.get("register"), ctx["settings"])
            return pos.settle(cur, oid, body.get("payments"), ctx=ctx, shift=shift, credit=bool(body.get("credit")))

    @router.post("/pos/bills/{oid}/collect")
    def collect(oid: str, body: dict = Body(...), me: dict = Depends(who)):
        _oid(oid)
        with session(me) as (cur, ctx):
            shift = pos_shift.require(cur, body.get("register"), ctx["settings"])
            return pos.collect(cur, oid, body.get("payments"), ctx=ctx, shift=shift)

    @router.post("/pos/bills/{oid}/cancel")
    def cancel(oid: str, body: dict = Body(...), me: dict = Depends(who)):
        _oid(oid)
        with session(me, body.get("approval")) as (cur, ctx):
            return pos.cancel(cur, oid, body.get("reason"), ctx=ctx)

    @router.post("/pos/bills/{oid}/refund")
    def refund(oid: str, body: dict = Body(...), me: dict = Depends(who)):
        _oid(oid)
        with session(me, body.get("approval")) as (cur, ctx):
            approver = pos._authority(ctx, "refund", "A refund needs the owner's or a manager's PIN.")
            method = str(body.get("method") or "cash")
            shift = pos_shift.require(cur, body.get("register"), ctx["settings"]) if method == "cash" else \
                pos_shift.current(cur, body.get("register"))
            r = pos_refund.refund(cur, oid, lines=body.get("lines"), method=method, reason=body.get("reason"),
                                  restock=body.get("restock", True), shift=shift, actor=me["uid"], approved_by=approver)
            return {**pos.receipt(cur, oid, ctx), "refund": r}

    return router
