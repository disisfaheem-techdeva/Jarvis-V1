"""Team PIN login (Faheem, 29 Sep 2026: "they should log in by entering the PIN
provided by the owner", as in the Beauty Aesthetic CRM).

The owner gives each team member a login with a 4-6 digit PIN and an access
level:
    staff  -> role 'operator' (no money pages, no settings)
    full   -> role 'owner'    (everything the owner sees)

Where the PIN is typed:
  * the business's shared screen: the owner presses "Use this device for PIN
    login" once; the browser keeps a signed, httpOnly device cookie naming the
    business, and /pin shows that business's PIN pad;
  * a team member's own phone: /pin asks for the business code first.

A PIN is a short secret, so the protection is in the limits, not the hash:
weak PINs (1111, 1234, ...) are refused, a PIN is unique inside its business,
and wrong tries lock that screen for 15 minutes after 5 misses (and the whole
business after 30). Every try is recorded in pin_attempts.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request, Response

from .audit import record
from .auth import (AuthError, SESSION_COOKIE, _b64u, _secret, _unb64u, hash_password,
                   issue_token, resolve_scope, verify_password)
from .db import tenant_session

DEVICE_COOKIE = "td_device"
DEVICE_TTL = 365 * 86400
PIN_SESSION_TTL = 12 * 3600
IP_FAILS, TENANT_FAILS, WINDOW_MIN = 5, 30, 15
ACCESS = {"staff": "operator", "full": "owner"}


def _weak(pin):
    if len(set(pin)) == 1:
        return True
    digits = [int(c) for c in pin]
    steps = {digits[i + 1] - digits[i] for i in range(len(digits) - 1)}
    if steps in ({1}, {-1}):
        return True
    return pin in {"1212", "6969", "2580", "0852", "1122", "112233", "121212", "123123", "654321", "123456"}


def check_pin(pin):
    pin = str(pin or "").strip()
    if not re.fullmatch(r"\d{4,6}", pin):
        raise HTTPException(400, "A PIN is 4 to 6 digits.")
    if _weak(pin):
        raise HTTPException(400, "That PIN is too easy to guess (like 1111 or 1234). Choose another.")
    return pin


def _device_token(tenant_id):
    payload = _b64u(json.dumps({"t": str(tenant_id), "exp": int(time.time()) + DEVICE_TTL}).encode())
    sig = _b64u(hmac.new(_secret(), ("device|" + payload).encode(), hashlib.sha256).digest())
    return payload + "." + sig


def _device_tenant(tok):
    try:
        payload, sig = (tok or "").split(".", 1)
        good = _b64u(hmac.new(_secret(), ("device|" + payload).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(_unb64u(payload))
        return d["t"] if d.get("exp", 0) > time.time() else None
    except Exception:
        return None


def _ip(request):
    """The visitor's address, behind Traefik and Cloudflare (app/client_ip.py)."""
    from .client_ip import client_ip
    return client_ip(request) or "?"


def _business(cur, tenant_id=None, code=None):
    if tenant_id:
        cur.execute("SELECT id, name FROM tenants WHERE id = %s", (tenant_id,))
    else:
        cur.execute("SELECT id, name FROM tenants WHERE login_code = %s", (str(code or "").strip().upper(),))
    return cur.fetchone()


def _owner(*a):
    try:
        tid, role, uid = resolve_scope(*a)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can manage team logins.")
    return tid, role, uid


BASE_DOMAIN = os.environ.get("TENANT_BASE_DOMAIN", "techdeva.net").strip().lower().lstrip(".")


def subdomain_of(request):
    """'drirfan' for a request to drirfan.techdeva.net, else None."""
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0]
    host = host.strip().lower().split(":")[0]
    if not BASE_DOMAIN or not host.endswith("." + BASE_DOMAIN):
        return None
    label = host[: -len(BASE_DOMAIN) - 1]
    return label if re.fullmatch(r"[a-z][a-z0-9-]{1,30}[a-z0-9]", label) else None


def host_tenant(request):
    """The business whose own address this request came to, if any."""
    sub = subdomain_of(request)
    if not sub:
        return None
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM tenants WHERE subdomain = %s", (sub,))
        r = cur.fetchone()
    return str(r["id"]) if r else None


def _pin_taken(cur, tid, pin, except_user=None):
    cur.execute("SELECT id, pin_hash FROM users WHERE tenant_id = %s AND pin_hash IS NOT NULL", (tid,))
    return any(verify_password(pin * 2, r["pin_hash"]) for r in cur.fetchall() if str(r["id"]) != str(except_user or ""))


def _code(cur, tid):
    cur.execute("SELECT login_code FROM tenants WHERE id = %s", (tid,))
    code = (cur.fetchone() or {}).get("login_code")
    while not code:
        cand = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        cur.execute("UPDATE tenants SET login_code = %s WHERE id = %s AND login_code IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM tenants WHERE login_code = %s) RETURNING login_code",
                    (cand, tid, cand))
        row = cur.fetchone()
        code = row["login_code"] if row else None
        if not code:
            cur.execute("SELECT login_code FROM tenants WHERE id = %s", (tid,))
            code = (cur.fetchone() or {}).get("login_code")
    return code


def make_pins_router():
    router = APIRouter()

    # ------------------------------------------------------------ PIN pad
    def _pad_business(code, td_device, request=None):
        # this screen's business: a business code typed on it wins (so a
        # computer once used as one business's reception screen can still log
        # in to another business); else the device's, else the address's
        # (drirfan.techdeva.net). A code still needs that business's PIN.
        tid = None if code else (_device_tenant(td_device) or (host_tenant(request) if request is not None else None))
        with tenant_session(None, "techdeva_admin") as cur:
            b = _business(cur, tenant_id=tid) if tid else (_business(cur, code=code) if code else None)
            if not b:
                return None, None, None
            cur.execute("SELECT brand, geo FROM tenant_config WHERE tenant_id = %s", (b["id"],))
            cfg = cur.fetchone() or {}
            cur.execute("SELECT updated_at FROM brand_assets WHERE kind = 'logo' AND tenant_id = %s", (b["id"],))
            logo = cur.fetchone()
        return b, (cfg, logo), bool(tid)

    @router.get("/auth/pin-info")
    def pin_info(request: Request, code: str | None = None, td_device: str | None = Cookie(None, alias=DEVICE_COOKIE)):
        """Which business this PIN screen is for (the device's, or the code's),
        and how to dress it: that business's own logo, colour and tagline."""
        b, extra, device = _pad_business(code, td_device, request)
        if not b:
            if code:
                raise HTTPException(404, "No business has that code. Check it with the owner.")
            return {"device": False}
        cfg, logo = extra
        brand = cfg.get("brand") if isinstance(cfg.get("brand"), dict) else {}
        geo = cfg.get("geo") if isinstance(cfg.get("geo"), dict) else {}
        accent = str((brand.get("colors") or {}).get("accent") or "")
        where = ", ".join(x for x in (geo.get("area"), geo.get("city")) if isinstance(x, str) and x.strip())
        return {"device": device, "business": b["name"],
                "tagline": (brand.get("tagline") or where or "Staff login")[:80],
                "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else None,
                "logo": ("/auth/pin-logo?v=%d" % int(logo["updated_at"].timestamp())
                         + ("&code=" + str(code).strip().upper() if not device and code else "")) if logo else None}

    @router.get("/auth/pin-logo")
    def pin_logo(request: Request, code: str | None = None, td_device: str | None = Cookie(None, alias=DEVICE_COOKIE)):
        """The business's logo for its PIN screen (before anyone is signed in)."""
        b, _x, _d = _pad_business(code, td_device, request)
        if not b:
            raise HTTPException(404, "Not found.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT mime, bytes FROM brand_assets WHERE kind = 'logo' AND tenant_id = %s", (b["id"],))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Not found.")
        return Response(content=bytes(row["bytes"]), media_type=row["mime"],
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @router.post("/auth/pin")
    def pin_login(request: Request, response: Response, body: dict = Body(...),
                  td_device: str | None = Cookie(None, alias=DEVICE_COOKIE)):
        pin = str(body.get("pin") or "").strip()
        if not re.fullmatch(r"\d{4,6}", pin):
            raise HTTPException(400, "Enter your 4 to 6 digit PIN.")
        ip = _ip(request)
        with tenant_session(None, "techdeva_admin") as cur:
            code = str(body.get("code") or "").strip()
            tid = None if code else (_device_tenant(td_device) or host_tenant(request))
            b = _business(cur, tenant_id=tid) if tid else _business(cur, code=code or None)
            if not b:
                raise HTTPException(400, "Enter your business code first (ask the owner).")
            tid = str(b["id"])
            cur.execute("""SELECT count(*) FILTER (WHERE ip = %s) AS by_ip, count(*) AS all_
                             FROM pin_attempts WHERE tenant_id = %s AND NOT ok
                              AND at > now() - make_interval(mins => %s)""", (ip, tid, WINDOW_MIN))
            f = cur.fetchone()
            if f["by_ip"] >= IP_FAILS or f["all_"] >= TENANT_FAILS:
                raise HTTPException(429, "Too many wrong PINs. Wait 15 minutes, or ask the owner.")
            cur.execute("SELECT id, name, role::text AS role, pin_hash FROM users "
                        "WHERE tenant_id = %s AND pin_hash IS NOT NULL", (tid,))
            who = next((u for u in cur.fetchall() if verify_password(pin * 2, u["pin_hash"])), None)
            cur.execute("INSERT INTO pin_attempts (tenant_id, user_id, ip, ok) VALUES (%s, %s, %s, %s)",
                        (tid, who["id"] if who else None, ip, bool(who)))
            if who:
                record(cur, action="auth.pin_login", resource="users", resource_id=str(who["id"]),
                       actor=str(who["id"]), status="ok", detail={"device": bool(_device_tenant(td_device))}, tenant_id=tid)
        # raised only after the block has committed: raising inside it would roll
        # the failed attempt back, and the lockout would never count anything
        if not who:
            left = IP_FAILS - f["by_ip"] - 1
            tail = ("" if left > 3 else " 1 try left." if left == 1 else
                    " This screen is now locked for 15 minutes." if left <= 0 else " %d tries left." % left)
            raise HTTPException(401, "Wrong PIN." + tail)
        token = issue_token(tenant_id=tid, user_id=str(who["id"]), role=who["role"], ttl=PIN_SESSION_TTL)
        import os
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=PIN_SESSION_TTL,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true")
        return {"ok": True, "name": who["name"], "role": who["role"]}

    # ------------------------------------------------------- owner manages
    @router.get("/team/logins")
    def logins(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE),
               td_device: str | None = Cookie(None, alias=DEVICE_COOKIE)):
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            code = _code(cur, tid)
            cur.execute("""SELECT u.id, u.name, u.role::text AS role, u.staff_id, u.pin_set_at,
                                  (u.pin_hash IS NOT NULL) AS has_pin, (u.email NOT LIKE '%%@pin.invalid') AS has_email,
                                  (SELECT max(at) FROM pin_attempts a WHERE a.user_id = u.id AND a.ok) AS last_pin_login
                             FROM users u WHERE u.tenant_id = %s ORDER BY (u.role = 'owner') DESC, u.name""", (tid,))
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT s.id, s.name, s.role FROM staff s WHERE s.tenant_id = %s AND s.active
                            AND NOT EXISTS (SELECT 1 FROM users u WHERE u.staff_id = s.id) ORDER BY s.name""", (tid,))
            staff = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT subdomain FROM tenants WHERE id = %s", (tid,))
            sub = (cur.fetchone() or {}).get("subdomain")
        for u in users:
            u["access"] = "full" if u["role"] == "owner" else "staff"
            u["me"] = str(u["id"]) == str(uid)
        return {"code": code, "logins": users, "staff_without_login": staff,
                "address": ("https://%s.%s/login" % (sub, BASE_DOMAIN)) if sub else None,
                "this_device": _device_tenant(td_device) == str(tid)}

    @router.post("/team/logins")
    def add_login(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        pin = check_pin(body.get("pin"))
        access = str(body.get("access") or "staff")
        if access not in ACCESS:
            raise HTTPException(400, "Access must be staff or full.")
        staff_id = str(body.get("staff_id") or "").strip() or None
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:80]
        with tenant_session(None, "techdeva_admin") as cur:
            if staff_id:
                cur.execute("SELECT name FROM staff WHERE id = %s AND tenant_id = %s", (staff_id, tid))
                s = cur.fetchone()
                if not s:
                    raise HTTPException(404, "That team member is not in this business.")
                cur.execute("SELECT 1 FROM users WHERE staff_id = %s", (staff_id,))
                if cur.fetchone():
                    raise HTTPException(409, "%s already has a login. Change their PIN instead." % s["name"])
                name = name or s["name"]
            if len(name) < 2:
                raise HTTPException(400, "Whose login is this? Pick a team member or type a name.")
            if _pin_taken(cur, tid, pin):
                raise HTTPException(409, "Someone in your team already uses that PIN. Choose another.")
            new_id = uuid.uuid4()
            cur.execute("""INSERT INTO users (id, tenant_id, name, email, role, pin_hash, pin_set_at, staff_id)
                           VALUES (%s, %s, %s, %s, %s, %s, now(), %s) RETURNING id""",
                        (str(new_id), tid, name, "pin+%s@pin.invalid" % new_id, ACCESS[access],
                         hash_password(pin * 2), staff_id))
            record(cur, action="team.login_add", resource="users", resource_id=str(new_id), actor=str(uid),
                   status="ok", detail={"name": name, "access": access}, tenant_id=tid)
        return {"ok": True, "id": str(new_id), "name": name, "access": access}

    @router.put("/team/logins/{user_id}/pin")
    def set_pin(user_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Give (or change) a PIN - also for the owner's own account."""
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        pin = check_pin(body.get("pin"))
        with tenant_session(None, "techdeva_admin") as cur:
            if _pin_taken(cur, tid, pin, except_user=user_id):
                raise HTTPException(409, "Someone in your team already uses that PIN. Choose another.")
            cur.execute("UPDATE users SET pin_hash = %s, pin_set_at = now() WHERE id = %s AND tenant_id = %s RETURNING name",
                        (hash_password(pin * 2), user_id, tid))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That login is not in this business.")
            record(cur, action="team.pin_set", resource="users", resource_id=user_id, actor=str(uid),
                   status="ok", detail={"name": r["name"]}, tenant_id=tid)
        return {"ok": True}

    @router.delete("/team/logins/{user_id}/pin")
    def remove_pin(user_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Stop a PIN working (someone left). The person's history stays."""
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE users SET pin_hash = NULL, pin_set_at = now() WHERE id = %s AND tenant_id = %s "
                        "AND pin_hash IS NOT NULL RETURNING name", (user_id, tid))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That login has no PIN.")
            record(cur, action="team.pin_removed", resource="users", resource_id=user_id, actor=str(uid),
                   status="ok", detail={"name": r["name"]}, tenant_id=tid)
        return {"ok": True}

    @router.post("/team/device")
    def this_device(response: Response, body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Make this browser the business's PIN screen (or stop it being one)."""
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        import os
        if body.get("off"):
            response.delete_cookie(DEVICE_COOKIE)
            return {"ok": True, "device": False}
        response.set_cookie(DEVICE_COOKIE, _device_token(tid), httponly=True, samesite="lax", max_age=DEVICE_TTL,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true")
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="team.device_set", resource="tenants", resource_id=tid, actor=str(uid),
                   status="ok", detail={}, tenant_id=tid)
        return {"ok": True, "device": True}

    return router
