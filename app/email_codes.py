"""Email verification by 6-digit code (Faheem, 25 Oct 2026).

A new account proves its email before the first sign-in: /signup emails a code,
the person types it in, and only then does /auth/login hand out a session. Every
account that existed before db/79 was marked verified there, so nobody who could
sign in before is locked out now. PIN logins (@pin.invalid) have no inbox and are
never asked.

The code itself is never stored: only HMAC-SHA256 of it, keyed from SESSION_SECRET
(auth._secret) and bound to the user, compared with hmac.compare_digest. A code
lives 10 minutes and dies after 5 wrong tries; a new one kills the older ones.
At most 1 code a minute and 5 an hour per account, counted in email_codes, so the
limits hold across restarts and API processes.

Delivery goes through account.send_mail unchanged. It raises on failure; here that
becomes sent=False, so no answer ever says an email went out when it did not.

The signed 24 h link from app/security.py (GET /auth/verify-email) still works for
links sent before this change.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from .audit import record
from .auth import _secret
from .db import tenant_session

PURPOSE = "verify_email"
TTL_MIN = 10
MAX_TRIES = 5
RESEND_GAP_S = 60
RESEND_PER_HOUR = 5
# guesses at /auth/verify-email, per email and per address, like the login throttle
VERIFY_PER_EMAIL = 10
VERIFY_PER_IP = 30
VERIFY_WINDOW = 900
GENERIC_BAD = "invalid or expired code"
GENERIC_SENT = "If that email has an account waiting for verification, a code was sent."
_hits: dict = {}


class TooSoon(Exception):
    def __init__(self, wait):
        super().__init__("wait %d s" % wait)
        self.wait = wait


def exempt(email) -> bool:
    """PIN logins have no inbox: they are never asked to verify."""
    return (email or "").strip().lower().endswith("@pin.invalid")


def _key():
    return hmac.new(_secret(), b"email-code", hashlib.sha256).digest()


def hash_code(user_id, code) -> str:
    return hmac.new(_key(), ("%s|%s" % (user_id, code)).encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    return "%06d" % secrets.randbelow(10 ** 6)


def _rate_ok(key, limit, window=VERIFY_WINDOW):
    now = time.time()
    hits = [t for t in _hits.get(key, []) if now - t < window]
    ok = len(hits) < limit
    if ok:
        hits.append(now)
    _hits[key] = hits
    if len(_hits) > 10000:
        for k in [k for k, v in list(_hits.items()) if not v or now - v[-1] > window]:
            _hits.pop(k, None)
    return ok


def _user(cur, email):
    cur.execute("SELECT id, tenant_id, name, email, email_verified_at FROM users "
                "WHERE lower(email) = lower(%s)", ((email or "").strip(),))
    return cur.fetchone()


def is_verified(user_id) -> bool:
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT email_verified_at FROM users WHERE id = %s", (str(user_id),))
        r = cur.fetchone()
    return bool(r and r["email_verified_at"])


def mark_verified(cur, user, how):
    cur.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()) WHERE id = %s",
                (str(user["id"]),))
    record(cur, action="auth.email_verified", resource="users", resource_id=str(user["id"]),
           actor=str(user["id"]), status="ok", detail={"by": how}, tenant_id=user.get("tenant_id"))


def issue(cur, user_id, purpose=PURPOSE) -> str:
    """A fresh code for this account; older unused ones stop working. Raises TooSoon."""
    cur.execute("""SELECT count(*) AS n,
                          max(created_at) AS last,
                          now() AS now
                     FROM email_codes
                    WHERE user_id = %s AND purpose = %s AND created_at > now() - interval '1 hour'""",
                (str(user_id), purpose))
    r = cur.fetchone()
    if r["last"] is not None:
        gap = (r["now"] - r["last"]).total_seconds()
        if gap < RESEND_GAP_S:
            raise TooSoon(int(RESEND_GAP_S - gap) + 1)
    if r["n"] >= RESEND_PER_HOUR:
        raise TooSoon(3600)
    cur.execute("UPDATE email_codes SET used_at = now() WHERE user_id = %s AND purpose = %s AND used_at IS NULL",
                (str(user_id), purpose))
    code = new_code()
    cur.execute("INSERT INTO email_codes (user_id, purpose, code_hash, expires_at) "
                "VALUES (%s, %s, %s, now() + make_interval(mins => %s))",
                (str(user_id), purpose, hash_code(user_id, code), TTL_MIN))
    return code


def check(cur, user_id, code, purpose=PURPOSE) -> bool:
    """True once, for the live code. A wrong guess counts against that code."""
    code = (code or "").strip()
    cur.execute("""SELECT id, code_hash, attempts FROM email_codes
                    WHERE user_id = %s AND purpose = %s AND used_at IS NULL
                      AND expires_at > now() AND attempts < %s
                    ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (str(user_id), purpose, MAX_TRIES))
    row = cur.fetchone()
    if not row:
        return False
    good = len(code) == 6 and code.isdigit() and hmac.compare_digest(row["code_hash"], hash_code(user_id, code))
    if good:
        cur.execute("UPDATE email_codes SET used_at = now() WHERE id = %s", (row["id"],))
    else:
        cur.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
    return good


def _body(name, code):
    return ("Hello %s,\n\nYour TechDeva verification code is:\n\n    %s\n\n"
            "It expires in %d minutes.\n\n"
            "If this wasn't you, ignore this email.\n\nTechDeva" % (name or "there", code, TTL_MIN))


def _html_body(name, code):
    """The same message as _body, laid out like the dashboard (violet on near-white).
    Email clients ignore <style> and web fonts often, so everything is inline and in tables."""
    from html import escape
    hi = escape(name) if name else "there"
    font = "'Geist',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><title>Your TechDeva code</title></head>'
        '<body style="margin:0;padding:0;background:#fbfaff">'
        '<div style="display:none;max-height:0;overflow:hidden">Your TechDeva code is %(code)s. It expires in %(ttl)d minutes.</div>'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#fbfaff">'
        '<tr><td align="center" style="padding:32px 16px">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="max-width:440px;background:#ffffff;border:1px solid #e8e7ed;border-radius:16px">'
        # wordmark
        '<tr><td style="padding:28px 32px 20px;border-bottom:1px solid #e8e7ed">'
        '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        '<td style="width:28px;height:28px;background:#6d28d9;border-radius:8px;color:#ffffff;'
        'font:700 13px/28px %(font)s;text-align:center">TD</td>'
        '<td style="padding-left:10px;font:700 20px/28px %(font)s;color:#15131d;letter-spacing:-0.3px">'
        'Tech<span style="color:#6d28d9">Deva</span></td>'
        '</tr></table></td></tr>'
        # message
        '<tr><td style="padding:24px 32px 0;font:400 15px/22px %(font)s;color:#15131d">Hi %(hi)s,</td></tr>'
        '<tr><td style="padding:14px 32px 0;font:400 15px/22px %(font)s;color:#15131d">Your verification code is:</td></tr>'
        # the code
        '<tr><td style="padding:16px 32px 0">'
        '<div style="background:#ede9fe;border:1px solid #ddd6fe;border-radius:14px;padding:20px 8px;text-align:center;'
        "font:700 34px/40px 'Geist Mono',ui-monospace,Menlo,Consolas,monospace;letter-spacing:8px;color:#5b21b6\">"
        '%(code)s</div></td></tr>'
        '<tr><td style="padding:12px 32px 0;font:400 13px/20px %(font)s;color:#4b4859">'
        'It expires in %(ttl)d minutes. Never share this code with anyone.</td></tr>'
        '<tr><td style="padding:14px 32px 26px;font:400 15px/22px %(font)s;color:#15131d">'
        "If you did not try to sign in, you can ignore this email.</td></tr>"
        # footer
        '<tr><td style="padding:18px 32px 24px;border-top:1px solid #e8e7ed;text-align:center;'
        'font:400 12px/18px %(font)s;color:#615d70">'
        '&copy; TechDeva &middot; AI Business Operating System<br>'
        '<a href="https://techdeva.net" style="color:#6d28d9;text-decoration:none">techdeva.net</a></td></tr>'
        '</table></td></tr></table></body></html>'
    ) % {"code": escape(code), "hi": hi, "ttl": TTL_MIN, "font": font}


def send_code(user) -> dict:
    """Make a code and email it. {"sent": bool, "reason": None|"no_mail"|"failed"|"too_soon", "wait": s}"""
    from .account import mail_configured, send_mail
    if not mail_configured():
        return {"sent": False, "reason": "no_mail", "wait": 0}
    try:
        with tenant_session(None, "techdeva_admin") as cur:
            code = issue(cur, user["id"])
    except TooSoon as e:
        return {"sent": False, "reason": "too_soon", "wait": e.wait}
    try:
        send_mail(user["email"], "Your TechDeva code: %s" % code, _body(user.get("name"), code),
                  html=_html_body(user.get("name"), code))
    except Exception as e:  # noqa: BLE001 - the caller says so honestly
        print("[email_codes] code email failed: %s" % type(e).__name__, flush=True)
        return {"sent": False, "reason": "failed", "wait": 0}
    return {"sent": True, "reason": None, "wait": RESEND_GAP_S}


def _ip(request):
    from .security import client_ip
    return client_ip(request)


def make_email_codes_router():
    router = APIRouter()

    @router.post("/auth/verify-email")
    def verify(request: Request, body: dict = Body(...)):
        """{email, code} -> the email is confirmed. One answer for every failure."""
        email = str(body.get("email") or "").strip().lower()[:200]
        code = str(body.get("code") or "").strip()[:12]
        if not _rate_ok("vip:" + _ip(request), VERIFY_PER_IP) or not _rate_ok("vem:" + email, VERIFY_PER_EMAIL):
            raise HTTPException(429, "too many attempts — try again later")
        with tenant_session(None, "techdeva_admin") as cur:
            u = _user(cur, email) if email else None
            if not u or exempt(u["email"]):
                raise HTTPException(400, GENERIC_BAD)
            if not check(cur, u["id"], code):
                ok = False
            else:
                mark_verified(cur, dict(u), "code")
                ok = True
        if not ok:      # outside the block: the attempt counter above is committed
            raise HTTPException(400, GENERIC_BAD)
        return {"ok": True, "verified": True}

    @router.post("/auth/resend-code")
    def resend(request: Request, background: BackgroundTasks, body: dict = Body(...)):
        """{email} -> the same answer whether or not the account exists. The email goes
        out after the answer, so timing does not tell who has an account either."""
        email = str(body.get("email") or "").strip().lower()[:200]
        same = {"ok": True, "message": GENERIC_SENT, "retry_after": RESEND_GAP_S}
        if not _rate_ok("rip:" + _ip(request), VERIFY_PER_IP, 3600):
            raise HTTPException(429, "too many requests — try again later")
        if "@" not in email:
            return same
        with tenant_session(None, "techdeva_admin") as cur:
            u = _user(cur, email)
        if u and not u["email_verified_at"] and not exempt(u["email"]):
            background.add_task(send_code, dict(u))
        return same

    return router
