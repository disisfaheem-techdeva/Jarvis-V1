"""The client portal API — login, trial status, the analysis report, and the
deliverables the client approves or rejects.

This is the half of the product the *client* touches during the 7-day trial.
Everything here reads through the same RLS-scoped persistence layer as the rest
of the platform; nothing bypasses tenant isolation.

Route map:
  POST /auth/login                          email + password -> signed session
  POST /auth/logout                         clear the session cookie
  GET  /me                                  who am I, which tenant
  GET  /portal/summary                      trial countdown + work counters
  GET  /portal/report                       the ONE combined analysis report
  GET  /portal/deliverables                 everything the agents produced
  POST /portal/deliverables/{id}/approve    releases a task parked at the gate
  POST /portal/deliverables/{id}/reject     closes it with a recorded reason

Two rules are enforced here rather than merely documented:
  1. Approving money/public-posting work requires a REAL logged-in user. With
     header-stub auth (AUTH_REQUIRED=false) there is no user id, so approval is
     refused — it cannot be waved through by an unauthenticated caller.
  2. Nothing is ever fabricated. No report yet -> {"status": "pending"}, not a
     placeholder score.
"""
from __future__ import annotations

import os
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, Cookie, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from .auth import (AuthError, SESSION_COOKIE, SESSION_TTL_SECONDS, auth_required,
                   issue_token, resolve_scope, verify_password, revoke_sessions,
                   hash_password, verify_setup_token, _unb64u)
from .audit import record
from .db import tenant_session

# Per-process brute-force throttle on login. Not a substitute for a WAF/fail2ban
# at the edge, but it stops a trivially scripted password guess against /auth/login.
_LOGIN_FAILS: dict[str, list] = {}
MAX_LOGIN_FAILS = int(os.environ.get("MAX_LOGIN_FAILS", "10"))
LOGIN_WINDOW = int(os.environ.get("LOGIN_FAIL_WINDOW", "900"))   # 15 minutes


def _throttled(key: str) -> bool:
    now = time.time()
    hits = [t for t in _LOGIN_FAILS.get(key, []) if now - t < LOGIN_WINDOW]
    _LOGIN_FAILS[key] = hits
    return len(hits) >= MAX_LOGIN_FAILS


def _record_fail(key: str):
    _LOGIN_FAILS.setdefault(key, []).append(time.time())


class LoginIn(BaseModel):
    email: str
    password: str


class SetPasswordIn(BaseModel):
    token: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class AccountIn(BaseModel):
    name: str | None = None
    business_name: str | None = None


class RejectIn(BaseModel):
    reason: str


# --------------------------------------------------------------- onboarding
# The client's own description of their business. creative.py and hermes.py
# already read this row; until now nothing could write it, so every tenant
# looked identical to the agents. Steps are independent on purpose - a client
# fills them in over several sittings and the dashboard shows what is left.

ONBOARDING_STEPS = (
    ("brand",          "Your name, logo and colours"),
    ("services",       "What you sell"),
    ("price_list",     "What it costs"),
    ("working_hours",  "When you are open"),
    ("geo",            "Where you are"),
    ("hermes_persona", "How your AI should sound"),
    ("booking_rules",  "How appointments work"),
)


def onboarding_state(cfg: dict) -> dict:
    done, missing = [], []
    for key, label in ONBOARDING_STEPS:
        v = (cfg or {}).get(key)
        filled = bool(v) if not isinstance(v, (list, dict, str)) else len(v) > 0
        (done if filled else missing).append({"key": key, "label": label})
    total = len(ONBOARDING_STEPS)
    return {"done": done, "missing": missing, "complete": len(done),
            "total": total, "percent": round(100 * len(done) / total)}


class ConfigIn(BaseModel):
    services: list | None = None
    price_list: list | dict | None = None
    departments: list | None = None
    booking_rules: dict | None = None
    brand: dict | None = None
    geo: dict | None = None
    working_hours: dict | None = None
    hermes_persona: str | None = None
    automation: str | None = None
    # workspace settings (db/66_workspace_settings.sql)
    timezone: str | None = None
    currency: str | None = None
    language: str | None = None

    @field_validator("timezone")
    @classmethod
    def _real_timezone(cls, v):
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a real time zone such as Asia/Karachi")
        return v

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v):
        if v is not None and not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("currency must be a 3-letter code in capitals, such as PKR")
        return v

    @field_validator("language")
    @classmethod
    def _known_language(cls, v):
        if v is not None and v not in ("en", "ur"):
            raise ValueError("language must be en or ur")
        return v


def make_portal_router(P) -> APIRouter:
    """Build the portal router around an existing Persistence instance (one Redis
    connection for the whole API process)."""
    router = APIRouter()

    # ---------------------------------------------------------------- identity
    def identity(x_tenant_id, x_role, authorization, cookie):
        try:
            return resolve_scope(x_tenant_id, x_role, authorization, cookie)
        except AuthError as e:
            raise HTTPException(e.status, e.message)

    # ------------------------------------------------------------------- login
    @router.post("/auth/login")
    def login(body: LoginIn, response: Response, request: Request):
        from . import security
        key = body.email.strip().lower()
        ip = security.client_ip(request)
        try:                       # the lockout lives in the database (db/73), across restarts
            wait, which = security.locked_for(key, ip)
        except Exception as e:     # noqa: BLE001 -- database away: the in-memory limit still holds
            print("[login] lockout check skipped: %s" % type(e).__name__, flush=True)
            wait, which = 0, None
        if wait:
            raise HTTPException(429, security.lock_message(wait, which))
        if _throttled(key):
            raise HTTPException(429, "too many failed attempts — try again later")

        user = P.find_user_by_email(body.email)
        # one generic message for both branches: never reveal whether an account exists
        # (and just as slow without an account, so timing does not tell either)
        if not security.check_password(body.password, user.get("password_hash") if user else None):
            _record_fail(key)
            try:
                security.record_attempt(key, ip, False, user)
            except Exception:      # noqa: BLE001
                pass
            raise HTTPException(401, "invalid email or password")

        _LOGIN_FAILS.pop(key, None)
        try:
            security.record_attempt(key, ip, True, user)
        except Exception:          # noqa: BLE001
            pass
        # right password, email not confirmed yet: no session until the 6-digit code
        # (app/email_codes.py). A fresh code goes out when the resend limits allow.
        from . import email_codes
        if not email_codes.exempt(user["email"]) and not email_codes.is_verified(user["id"]):
            sent = email_codes.send_code(dict(user))
            return JSONResponse(status_code=403, content={
                "detail": "email not verified", "verification_required": True,
                "email": user["email"], "email_sent": sent["sent"], "retry_after": sent["wait"]})
        P.touch_login(user["id"])
        token = issue_token(tenant_id=user["tenant_id"], user_id=user["id"],
                            role=user["role"])
        # httpOnly so page JavaScript can never read it; Secure in production (HTTPS).
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_SECONDS,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true")
        return {"token": token, "role": user["role"],
                "tenant_id": str(user["tenant_id"]) if user["tenant_id"] else None,
                "name": user["name"], "expires_in": SESSION_TTL_SECONDS}

    @router.post("/auth/logout")
    def logout(response: Response, authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Sign out: the cookie goes, and this login stops working on the server too."""
        from .auth import sign_out, token_from
        ended = sign_out(token_from(authorization, td_session))
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True, "ended": ended}

    @router.get("/me")
    def me(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        from .trial import locked
        return {"tenant_id": tid, "role": role, "user_id": uid,
                "authenticated": uid is not None, "auth_enforced": auth_required(),
                "locked": role != "techdeva_admin" and locked(tid)}

    # ------------------------------------------------------------- trial + report
    @router.get("/portal/summary")
    def summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        return P.portal_summary(tid)

    @router.get("/portal/report")
    def report(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        rep = P.latest_report(tid)
        if not rep:
            # honest: the analysis has not produced anything yet. No placeholder scores.
            return {"status": "pending",
                    "message": "Your analysis is still running — nothing to show yet."}
        return rep


    # ------------------------------------------------------------------ config
    @router.get("/portal/config")
    def get_config(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        cfg = P.load_config(tid) or {}
        return {"config": cfg, "onboarding": onboarding_state(cfg)}

    @router.put("/portal/config")
    def put_config(body: ConfigIn,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "only the owner can change business settings")
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        if not patch:
            raise HTTPException(400, "nothing to save")
        cfg = P.save_config(tid, patch) or {}
        return {"config": cfg, "onboarding": onboarding_state(cfg), "saved": sorted(patch)}

    # ----------------------------------------------------- first password
    @router.post("/auth/set-password")
    def set_password(body: SetPasswordIn):
        """Choose a password from a one-time setup link.

        The link carries no authority of its own beyond the account it names:
        it is HMAC-signed, expires, and is bound to the account's current
        password state so it stops working the moment it is used.
        """
        token = (body.token or "").strip()
        pw = body.password or ""
        if len(pw) < 10:
            raise HTTPException(400, "password must be at least 10 characters")
        raw, _, _sig = token.partition(".")
        try:
            claimed_email = _unb64u(raw).decode().split("|")[0]
        except Exception:
            raise HTTPException(400, "malformed setup link")
        user = P.find_user_by_email(claimed_email)
        if not user:
            # never confirm or deny which accounts exist
            raise HTTPException(400, "this link is not valid")
        try:
            email = verify_setup_token(token, user.get("password_hash"))
        except AuthError as e:
            raise HTTPException(e.status or 400, e.message)
        if not P.set_user_password(email, hash_password(pw)):
            raise HTTPException(400, "this link is not valid")
        # record() takes a cursor; the old positional call raised after the
        # password was already saved, so every reset answered 500.
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="auth.password_set", resource="users",
                   resource_id=str(user.get("id")), actor=str(user.get("id")),
                   status="ok", detail={}, tenant_id=user.get("tenant_id"))
            # the link reached this inbox, so the email is proven (app/email_codes.py)
            from .email_codes import mark_verified
            mark_verified(cur, dict(user), "setup_link")
        return {"ok": True}

    # --------------------------------------------------- change own password
    def _fresh_session(response, user):
        """After revoking, keep THIS device signed in with a new login."""
        token = issue_token(tenant_id=user["tenant_id"], user_id=user["id"], role=user["role"])
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL_SECONDS,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true")
        return token

    @router.post("/auth/sign-out-everywhere")
    def sign_out_everywhere(response: Response, x_tenant_id: str | None = Header(None),
                            x_role: str | None = Header(None), authorization: str | None = Header(None),
                            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Every other device where this person is signed in is signed out now;
        this one stays signed in."""
        _tid, _role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not uid:
            raise HTTPException(401, "Sign in again first.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, tenant_id, role FROM users WHERE id::text = %s", (str(uid),))
            user = cur.fetchone()
            if not user:
                raise HTTPException(404, "Your account was not found.")
            revoke_sessions(cur, user["id"])
            record(cur, action="auth.sign_out_everywhere", resource="users", resource_id=str(user["id"]),
                   actor=str(user["id"]), actor_type="human", status="ok", detail={},
                   tenant_id=_audit_tenant(cur, user["tenant_id"]))
        time.sleep(1.1)       # the new login must be newer than the cut-off (whole seconds)
        return {"ok": True, "token": _fresh_session(response, user)}

    @router.post("/auth/change-password")
    def change_password(body: ChangePasswordIn, response: Response, x_tenant_id: str | None = Header(None),
                        x_role: str | None = Header(None), authorization: str | None = Header(None),
                        td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A signed-in person changes their own password (founder, owner or staff
        with an email login). The current password is required, so a session
        left open on a shared computer cannot be used to take the account."""
        _tid, _role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not uid:
            raise HTTPException(401, "Sign in again first.")
        key = "chg:%s" % uid
        if _throttled(key):
            raise HTTPException(429, "Too many wrong tries. Wait a few minutes and try again.")
        new = body.new_password or ""
        if len(new) < 10:
            raise HTTPException(400, "The new password must be at least 10 characters.")
        if new == body.current_password:
            raise HTTPException(400, "The new password must be different from the current one.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, tenant_id, email, role, password_hash FROM users WHERE id::text = %s", (str(uid),))
            user = cur.fetchone()
        if not user or not user["email"]:
            raise HTTPException(400, "This login has no password (it signs in with a PIN).")
        if not verify_password(body.current_password or "", user["password_hash"]):
            _record_fail(key)
            raise HTTPException(400, "The current password is not right.")
        _LOGIN_FAILS.pop(key, None)
        if not P.set_user_password(user["email"], hash_password(new)):
            raise HTTPException(500, "The password was not changed. Try again.")
        with tenant_session(None, "techdeva_admin") as cur:
            revoke_sessions(cur, user["id"])       # a new password signs out every other device
            record(cur, action="auth.password_change", resource="users", resource_id=str(user["id"]),
                   actor=str(user["id"]), actor_type="human", status="ok", detail={"signed_out_elsewhere": True},
                   tenant_id=_audit_tenant(cur, user["tenant_id"]))
        time.sleep(1.1)
        return {"ok": True, "token": _fresh_session(response, user)}

    # ------------------------------------------------------ my name / business name
    def _audit_tenant(cur, tenant_id):
        """Where a person's own account change is logged: their business, or for
        the founder (no business) TechDeva's own business. The log needs one."""
        if tenant_id:
            return tenant_id
        from .tdwork import _home
        h = _home(cur)
        return h["id"] if h else None

    def _me_row(cur, uid):
        cur.execute("SELECT u.id, u.tenant_id, u.name, u.email, u.role, u.staff_id, t.name AS business_name "
                    "FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id WHERE u.id::text = %s", (str(uid),))
        return cur.fetchone()

    @router.get("/auth/account")
    def account(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not uid:
            raise HTTPException(401, "Sign in again first.")
        with tenant_session(None, "techdeva_admin") as cur:
            u = _me_row(cur, uid)
        if not u:
            raise HTTPException(404, "Your account was not found.")
        return {"name": u["name"] or "", "email": u["email"] or "", "role": u["role"],
                "business_name": u["business_name"], "has_password": bool(u["email"]),
                "can_rename_business": u["role"] == "owner" and u["tenant_id"] is not None}

    @router.put("/auth/account")
    def save_account(body: AccountIn, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A signed-in person changes their own name; the owner can also rename
        the business (its web address stays the same)."""
        _tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not uid:
            raise HTTPException(401, "Sign in again first.")
        name = " ".join((body.name or "").split()) if body.name is not None else None
        biz = " ".join((body.business_name or "").split()) if body.business_name is not None else None
        if name is not None and not 2 <= len(name) <= 80:
            raise HTTPException(400, "Your name must be 2 to 80 characters.")
        if biz is not None and not 2 <= len(biz) <= 120:
            raise HTTPException(400, "The business name must be 2 to 120 characters.")
        with tenant_session(None, "techdeva_admin") as cur:
            u = _me_row(cur, uid)
            if not u:
                raise HTTPException(404, "Your account was not found.")
            changed = {}
            if name is not None and name != (u["name"] or ""):
                cur.execute("UPDATE users SET name = %s WHERE id = %s", (name, u["id"]))
                if u["staff_id"]:      # the HR record shows the same person
                    cur.execute("UPDATE staff SET name = %s WHERE id = %s AND tenant_id = %s",
                                (name, u["staff_id"], u["tenant_id"]))
                changed["name"] = name
            if biz is not None and biz != (u["business_name"] or ""):
                if u["role"] != "owner" or u["tenant_id"] is None:
                    raise HTTPException(403, "Only the business owner can rename the business.")
                cur.execute("UPDATE tenants SET name = %s WHERE id = %s", (biz, u["tenant_id"]))
                changed["business_name"] = biz
            if changed:
                record(cur, action="auth.account_edit", resource="users", resource_id=str(u["id"]),
                       actor=str(u["id"]), actor_type="human", status="ok",
                       detail={"changed": sorted(changed), "business_from": u["business_name"]
                               if "business_name" in changed else None},
                       tenant_id=_audit_tenant(cur, u["tenant_id"]))
            u = _me_row(cur, uid)
        return {"ok": True, "changed": sorted(changed), "name": u["name"], "business_name": u["business_name"]}

    # ------------------------------------------------------------------ growth
    @router.get("/portal/growth")
    def growth(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The research and growth plan this tenant actually has.

        AnalysisAgent and GrowthPlannerAgent have been running and writing to
        the context store for a while, but nothing read it back, so a client
        could not see their own analysis. This reads what is stored. It does
        not generate, summarise or pad it, and absent work is reported absent.
        """
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        ctx = P.load_context(tid) or {}
        pipeline = [dict(r) for r in (P.growth_pipeline(tid) or [])]
        analysis = ctx.get("latest_analysis")
        plan = ctx.get("current_content_plan")
        return {"analysis": analysis,
                "content_plan": plan,
                "pipeline": pipeline,
                "has_analysis": analysis is not None,
                "has_plan": plan is not None,
                "missing": [k for k, v in (("analysis", analysis),
                                           ("content_plan", plan)) if v is None]}

    # ------------------------------------------------------------- deliverables
    @router.get("/portal/deliverables")
    def deliverables(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        items = P.list_deliverables(tid)
        return {"deliverables": items,
                "awaiting_approval": sum(1 for d in items
                                         if d["status"] == "awaiting_approval")}

    def _approver(tid, role, uid):
        """Approving irreversible work needs a real, named human. Enforced here AND
        by the deliverable_approval_complete CHECK in the database."""
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in {"owner", "techdeva_admin"}:
            raise HTTPException(403, "only the account owner can approve or reject")
        if not uid:
            raise HTTPException(403, "approval requires a logged-in user "
                                     "(set AUTH_REQUIRED=true and sign in)")
        return uid

    @router.post("/portal/deliverables/{deliverable_id}/approve")
    def approve(deliverable_id: str,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        uid = _approver(tid, role, uid)
        out = P.approve_deliverable(tid, deliverable_id, uid)
        if not out.get("approved"):
            raise HTTPException(409, out.get("reason", "could not approve"))
        return out

    @router.post("/portal/deliverables/{deliverable_id}/reject")
    def reject(deliverable_id: str, body: RejectIn,
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        uid = _approver(tid, role, uid)
        reason = (body.reason or "").strip()
        if not reason:
            raise HTTPException(400, "a rejection needs a reason")
        out = P.reject_deliverable(tid, deliverable_id, uid, reason)
        if not out.get("rejected"):
            raise HTTPException(409, out.get("reason", "could not reject"))
        return out

    return router
