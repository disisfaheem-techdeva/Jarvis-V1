"""Safer sign-in (Faheem, 23 Oct 2026).

Lockout. Every email + password sign-in is written to login_attempts (db/73), so
the limits hold across restarts and API processes:
  * 5 wrong passwords for one email (since its last good sign-in) within 15 minutes
    lock that email for 15 minutes;
  * 20 wrong passwords from one address within 15 minutes block that address.
The message is the same whether or not the account exists. A wrong password for an
email that has no account takes as long as one that has (a dummy hash is checked),
so timing cannot tell who is a client either.

Email verification. A person proves an email is theirs with a signed link (24 h)
sent to it. Sent at sign-up, and again on request from the dashboard. Nobody is
locked out for not verifying: the dashboard asks, and the founder can see who has.
Without mail settings on the server (SMTP_HOST, MAIL_FROM) no link can be sent, and
the dashboard says so instead of pretending.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import random
import time

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from .audit import record
from .auth import SESSION_COOKIE, AuthError, _b64u, _secret, _unb64u, hash_password, resolve_scope, verify_password
from .db import tenant_session

EMAIL_FAILS = int(os.environ.get("LOGIN_EMAIL_FAILS", "5"))
IP_FAILS = int(os.environ.get("LOGIN_IP_FAILS", "20"))
WINDOW_MIN = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))
VERIFY_TTL = 24 * 3600
PUBLIC_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "https://api.techdeva.net").strip().rstrip("/")
_DUMMY = None
_SENDS: dict = {}


def client_ip(request) -> str:
    """The visitor's address: Traefik appends it as the last X-Forwarded-For entry."""
    fwd = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    return (fwd[-1] if fwd else (request.client.host if request.client else ""))[:64]


def check_password(password, stored):
    """verify_password, but just as slow when there is no account (no timing leak)."""
    global _DUMMY
    if not stored:
        if _DUMMY is None:
            _DUMMY = hash_password("no-account-" + os.urandom(8).hex())
        verify_password(password or "x", _DUMMY)
        return False
    return verify_password(password, stored)


def locked_for(email, ip):
    """Seconds this email or address must wait (0 = may try), and which."""
    email = (email or "").strip().lower()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""WITH last_ok AS (SELECT max(at) AS t FROM login_attempts WHERE lower(email) = %s AND ok)
                       SELECT count(*) AS n, min(at) AS first, max(at) AS last FROM (
                         SELECT at FROM login_attempts
                          WHERE lower(email) = %s AND NOT ok AND at > now() - make_interval(mins => %s)
                            AND at > COALESCE((SELECT t FROM last_ok), '-infinity')
                          ORDER BY at DESC LIMIT %s) f""", (email, email, WINDOW_MIN, EMAIL_FAILS))
        e = cur.fetchone()
        cur.execute("""SELECT count(*) AS n, max(at) AS last FROM login_attempts
                        WHERE ip = %s AND ip <> '' AND NOT ok AND at > now() - make_interval(mins => %s)""", (ip, WINDOW_MIN))
        i = cur.fetchone()
        cur.execute("SELECT now() AS now")
        now = cur.fetchone()["now"]
    if e["n"] >= EMAIL_FAILS:
        return max(1, int(WINDOW_MIN * 60 - (now - e["last"]).total_seconds())), "email"
    if i["n"] >= IP_FAILS:
        return max(1, int(WINDOW_MIN * 60 - (now - i["last"]).total_seconds())), "ip"
    return 0, None


def record_attempt(email, ip, ok, user=None):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO login_attempts (email, ip, ok, user_id, tenant_id) VALUES (%s, %s, %s, %s, %s)",
                    ((email or "").strip().lower()[:200], ip or "", bool(ok),
                     str(user["id"]) if user else None, str(user["tenant_id"]) if user and user.get("tenant_id") else None))
        if random.random() < 0.01:          # keep 30 days
            cur.execute("DELETE FROM login_attempts WHERE at < now() - interval '30 days'")


def lock_message(seconds, which):
    mins = max(1, (seconds + 59) // 60)
    if which == "ip":
        return "Too many wrong passwords from this connection. Try again in %d minute%s." % (mins, "" if mins == 1 else "s")
    return ("Too many wrong passwords for this email. Try again in %d minute%s, or use \"Forgot password\" "
            "to set a new one." % (mins, "" if mins == 1 else "s"))


# ------------------------------------------------------------------ verification
def _vkey():
    return hmac.new(_secret(), b"email-verification", hashlib.sha256).digest()


def verify_token_for(email, ttl=VERIFY_TTL):
    payload = "verify|%s|%d" % (email.strip().lower(), int(time.time()) + ttl)
    sig = _b64u(hmac.new(_vkey(), payload.encode(), hashlib.sha256).digest())
    return "%s.%s" % (_b64u(payload.encode()), sig)


def email_from_token(token):
    raw, _, sig = (token or "").partition(".")
    try:
        payload = _unb64u(raw).decode()
    except Exception:  # noqa: BLE001
        return None
    want = _b64u(hmac.new(_vkey(), payload.encode(), hashlib.sha256).digest())
    if not sig or not hmac.compare_digest(sig, want):
        return None
    kind, _, rest = payload.partition("|")
    email, _, exp = rest.rpartition("|")
    if kind != "verify" or not exp.isdigit() or int(exp) < time.time():
        return None
    return email


def send_verification(user):
    """Email the link. Returns "email" when it went, "no_mail" when the server cannot send, "failed"."""
    from .account import mail_configured, send_mail
    if not mail_configured():
        return "no_mail"
    link = "%s/auth/verify-email?token=%s" % (PUBLIC_BASE, verify_token_for(user["email"]))
    try:
        send_mail(user["email"], "Confirm your email for TechDeva",
                  "Hello %s,\n\nPlease confirm this is your email by opening this link within 24 hours:\n\n%s\n\n"
                  "It keeps your account safe: password reset links are only sent to a confirmed email.\n\n"
                  "If you did not sign up for TechDeva, ignore this email.\n\nTechDeva" % (user.get("name") or "there", link))
        return "email"
    except Exception as e:  # noqa: BLE001
        print("[security] verification email failed: %s" % type(e).__name__, flush=True)
        return "failed"


def make_security_router():
    router = APIRouter()

    def _me(x_tenant_id, x_role, authorization, session):
        try:
            _tid, _role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        if not uid:
            raise HTTPException(401, "Sign in again first.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, tenant_id, name, email, email_verified_at FROM users WHERE id::text = %s", (str(uid),))
            u = cur.fetchone()
        if not u:
            raise HTTPException(404, "Your account was not found.")
        return dict(u)

    @router.get("/auth/security")
    def status(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Is my email confirmed, and can the server send the link?"""
        from .account import mail_configured
        u = _me(x_tenant_id, x_role, authorization, td_session)
        pin_only = (u["email"] or "").endswith("@pin.invalid")
        return {"email": None if pin_only else u["email"], "verified": bool(u["email_verified_at"]),
                "verified_at": u["email_verified_at"], "needs_verification": not pin_only and not u["email_verified_at"],
                "can_send": mail_configured()}

    @router.post("/auth/verify-email/send")
    def resend(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        u = _me(x_tenant_id, x_role, authorization, td_session)
        if (u["email"] or "").endswith("@pin.invalid"):
            raise HTTPException(400, "This login uses a PIN, not an email.")
        if u["email_verified_at"]:
            return {"ok": True, "already": True}
        now = time.time()
        hits = [t for t in _SENDS.get(str(u["id"]), []) if now - t < 3600]
        if len(hits) >= 3:
            raise HTTPException(429, "Three links were sent in the last hour. Check your inbox and spam folder.")
        how = send_verification(u)
        if how == "no_mail":
            raise HTTPException(503, "This server cannot send emails yet. TechDeva will confirm your email with you.")
        if how != "email":
            raise HTTPException(502, "The email could not be sent just now. Try again in a few minutes.")
        _SENDS[str(u["id"])] = hits + [now]
        return {"ok": True, "sent_to": u["email"]}

    @router.get("/auth/verify-email", include_in_schema=False)
    def confirm(token: str = ""):
        """The link in the email. Marks the email confirmed and opens the dashboard."""
        email = email_from_token(token)
        if not email:
            return RedirectResponse("/dashboard?verified=expired", status_code=302)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE users SET email_verified_at = COALESCE(email_verified_at, now())
                            WHERE lower(email) = %s RETURNING id, tenant_id""", (email,))
            u = cur.fetchone()
            if u:
                record(cur, action="auth.email_verified", resource="users", resource_id=str(u["id"]),
                       actor=str(u["id"]), status="ok", detail={}, tenant_id=u["tenant_id"])
        return RedirectResponse("/dashboard?verified=%s" % ("1" if u else "expired"), status_code=302)

    return router
