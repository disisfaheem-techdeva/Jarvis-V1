"""Forgot password.

A person who forgot their password asks for a link with their email. The
answer is the same whether or not the account exists, so this page cannot be
used to find out who is a client.

The link is the same one-time setup link /auth/set-password already accepts
(auth.issue_setup_token): signed, one hour, and dead the moment the password
changes. It is never stored.

Delivery: by email when the server has SMTP settings (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD, MAIL_FROM). Without them the request waits in the
founder console, where TechDeva makes a fresh link and sends it by WhatsApp.
Nothing pretends an email went out when it did not.
"""
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request

from .audit import record
from .auth import AuthError, SESSION_COOKIE, issue_setup_token, resolve_scope
from .db import tenant_session

RESET_TTL = 3600
PUBLIC_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "https://api.techdeva.net").strip().rstrip("/")
_hits: dict = {}


def _rate_ok(key, limit, window=3600):
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


def _ip(request):
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return fwd[-1] if fwd else (request.client.host if request.client else "?")


def mail_configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"))


def send_mail(to, subject, text, html=None, from_name=None, reply_to=None):
    """Send one email: plain text, plus an HTML version when given (clients that
    cannot show HTML show the text). Raises on failure; the caller decides.

    from_name / reply_to (white-label partners): the mail shows the partner's
    brand as the sender's name and replies go to the partner's support address.
    The sending ADDRESS stays MAIL_FROM: sending as the partner's own domain
    needs that domain's SPF/DKIM, which is a per-partner DNS step."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER") or ""
    pw = os.environ.get("SMTP_PASSWORD") or ""
    msg = EmailMessage()
    if from_name:
        from email.utils import formataddr, parseaddr
        msg["From"] = formataddr((from_name, parseaddr(os.environ["MAIL_FROM"])[1]))
    else:
        msg["From"] = os.environ["MAIL_FROM"]
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as s:
            if user:
                s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, pw)
            s.send_message(msg)


def reset_link(email, current_hash):
    return "%s/reset?token=%s" % (PUBLIC_BASE, issue_setup_token(email, current_hash, ttl=RESET_TTL))


def _find_user(cur, email):
    cur.execute("SELECT id, tenant_id, name, email, password_hash FROM users "
                "WHERE lower(email) = lower(%s)", (email,))
    return cur.fetchone()


def make_account_router():
    router = APIRouter()

    @router.post("/auth/forgot-password")
    def forgot(request: Request, body: dict = Body(...)):
        email = str(body.get("email") or "").strip().lower()[:200]
        same = {"ok": True, "message": "If that email has an account, a reset link is on its way. "
                                       "If nothing arrives, TechDeva will send it to you on WhatsApp."}
        if "@" not in email or "." not in email.split("@")[-1]:
            raise HTTPException(400, "Enter the email you sign in with.")
        if not _rate_ok("ip:" + _ip(request), 10) or not _rate_ok("em:" + email, 3):
            raise HTTPException(429, "Too many requests. Please try again in an hour.")
        with tenant_session(None, "techdeva_admin") as cur:
            u = _find_user(cur, email)
            if not u:
                return same
            delivered = "pending"
            if mail_configured():
                try:
                    send_mail(u["email"], "Reset your TechDeva password",
                              "Hello %s,\n\nSomeone (hopefully you) asked to reset the password for "
                              "your TechDeva dashboard. Choose a new one here within the next hour:\n\n%s\n\n"
                              "If you did not ask, ignore this email; your password stays as it is.\n\n"
                              "TechDeva" % (u["name"] or "there", reset_link(u["email"], u["password_hash"])))
                    delivered = "email"
                except Exception as e:                     # noqa: BLE001 - fall back to the founder
                    print("[account] reset email failed: %s" % type(e).__name__, flush=True)
            cur.execute("INSERT INTO password_reset_requests (user_id, tenant_id, email, delivered) "
                        "VALUES (%s, %s, %s, %s) RETURNING id",
                        (u["id"], u["tenant_id"], u["email"], delivered))
            rid = cur.fetchone()["id"]
            record(cur, action="auth.reset_requested", resource="password_reset_requests",
                   resource_id=str(rid), actor=str(u["id"]), status="ok",
                   detail={"delivered": delivered}, tenant_id=u["tenant_id"])
        return same

    def _admin(x_tenant_id, x_role, authorization, session):
        try:
            _tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        if role != "techdeva_admin":
            raise HTTPException(403, "admin only")
        return uid

    @router.get("/techdeva/password-resets")
    def pending(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT r.id, r.email, r.delivered, r.created_at, r.handled_at,
                                  u.name, t.name AS business
                             FROM password_reset_requests r
                             LEFT JOIN users u ON u.id = r.user_id
                             LEFT JOIN tenants t ON t.id = r.tenant_id
                            WHERE r.created_at > now() - interval '14 days'
                            ORDER BY (r.delivered = 'pending') DESC, r.created_at DESC LIMIT 30""")
            rows = [dict(r) for r in cur.fetchall()]
        return {"requests": rows, "email_configured": mail_configured()}

    @router.post("/techdeva/password-resets/{rid}/link")
    def make_link(rid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A fresh one-hour link for the founder to send the person on WhatsApp."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, email, tenant_id FROM password_reset_requests WHERE id = %s", (rid,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "No such request.")
            u = _find_user(cur, r["email"])
            if not u:
                raise HTTPException(404, "That account no longer exists.")
            cur.execute("UPDATE password_reset_requests SET delivered = 'handled', handled_at = now() "
                        "WHERE id = %s AND delivered = 'pending'", (rid,))
            record(cur, action="auth.reset_link_made", resource="password_reset_requests",
                   resource_id=rid, actor=str(uid), status="ok", detail={}, tenant_id=r["tenant_id"])
        return {"link": reset_link(u["email"], u["password_hash"]), "expires_in": RESET_TTL,
                "email": u["email"]}

    return router
