"""POS routes (3 Nov 2026). The logic is in pos.py, pos_shift.py, pos_refund.py
and pos_settings.py; this file only resolves who is asking, opens one
tenant-scoped transaction per request and turns refusals into HTTP errors.

Every money route runs in ONE transaction: a refusal raised anywhere rolls the
whole request back, so a half-made sale, refund or close never lands.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import date

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request

from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity
from .orders import MONEY_ROLES, OrderError
from .security import client_ip
from . import pos, pos_refund, pos_settings as cfg, pos_shift
from .pos_refund import RefundError
from .pos_shift import ShiftError

ERRORS = (OrderError, cfg.PosSettingsError, ShiftError, RefundError)
# POS is a separate product: its own address, on only for the businesses that
# take it (db/91). Everything under /pos/* except /pos/status is refused for a
# business where it is off, so it never shows up half-working.
POS_URL = (os.environ.get("POS_PUBLIC_URL") or "https://pos.techdeva.net").rstrip("/")
OFF = "POS is not on for this business. Ask TechDeva to turn it on."


def enabled(cur) -> bool:
    cur.execute("SELECT enabled FROM pos_settings WHERE tenant_id = current_tenant()")
    r = cur.fetchone()
    return bool(r and r["enabled"])
UUID = re.compile(r"^[0-9a-f-]{36}$")


def who(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
        authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
    if role not in ("owner", "operator", "techdeva_admin"):
        raise HTTPException(403, "The till is for the business's own team.")
    return {"tid": tid, "role": role, "uid": uid}


@contextmanager
def session(me, approval=None, gate=True):
    """A tenant transaction plus the context every POS function takes."""
    try:
        with tenant_session(me["tid"], me["role"]) as cur:
            if gate and not enabled(cur):
                raise HTTPException(403, OFF)
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

    # ------------------------------------------------------------ on / off
    @router.get("/pos/status")
    def status(me: dict = Depends(who)):
        """For the dashboard: show 'Open POS' only where it is on."""
        with session(me, gate=False) as (cur, ctx):
            return {"enabled": enabled(cur), "url": POS_URL}

    @router.get("/techdeva/pos")
    def founder_list(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        from .founder import _admin
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT t.id::text, t.name, t.status::text AS status, t.industry,
                                  COALESCE(s.enabled, false) AS enabled, s.enabled_at,
                                  (SELECT count(*) FROM orders o WHERE o.tenant_id = t.id AND o.source = 'pos') AS sales
                             FROM tenants t LEFT JOIN pos_settings s ON s.tenant_id = t.id
                            WHERE NOT COALESCE(t.is_test, false)
                            ORDER BY COALESCE(s.enabled, false) DESC, t.name""")
            return {"url": POS_URL, "businesses": [dict(r) for r in cur.fetchall()]}

    @router.put("/techdeva/pos/{tid}")
    def founder_set(tid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """TechDeva turns the POS on or off for one business."""
        from .audit import record
        from .founder import _admin
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        if not UUID.match(tid):
            raise HTTPException(404, "That business does not exist.")
        on = bool(body.get("enabled"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT name FROM tenants WHERE id = %s::uuid", (tid,))
            t = cur.fetchone()
            if not t:
                raise HTTPException(404, "That business does not exist.")
            cur.execute("""INSERT INTO pos_settings (tenant_id, enabled, enabled_by, enabled_at) VALUES (%s, %s, %s, now())
                           ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled,
                                  enabled_by = EXCLUDED.enabled_by, enabled_at = EXCLUDED.enabled_at""",
                        (tid, on, str(uid or "") or None))
            record(cur, action="pos.enable" if on else "pos.disable", resource="pos_settings", resource_id=tid,
                   actor=str(uid or ""), detail={"business": t["name"]}, tenant_id=tid)
        return {"id": tid, "enabled": on}

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
        # the address our proxy saw, never the caller-written first entry (security.client_ip)
        ip = client_ip(request) or "?"
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

    @router.post("/pos/quote")
    def quote(body: dict = Body(...), me: dict = Depends(who)):
        """The price of a bill before it is made (Jarvis reads it out first). Writes nothing."""
        with session(me) as (cur, ctx):
            q = pos.quote(cur, body.get("items"), ctx=ctx)
            open_ = pos_shift.current(cur, body.get("register")) is not None
            return {**q, "register": pos_shift._register(body.get("register")),
                    "can_sell": open_ or not ctx["settings"].get("require_shift", True)}

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
