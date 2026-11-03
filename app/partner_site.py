"""A partner's public, white-label front: its website, its client sign-up and
the page where a client it invited chooses a password.

ONE template (web/partner_site.html) serves every partner; what differs is the
partner's configuration, read from /public/partner-site/{slug}. No partner gets
its own copy of the code. Nothing on these pages or in these answers names
TechDeva: the client is the partner's customer.

    GET  /p/{slug}                         the website (home, packages, contact)
    GET  /p/{slug}/join/{token}            self-registration (Module 7, method B)
    GET  /p/{slug}/welcome?token=...       a client invited by the partner sets a password
    GET  /p/{slug}/logo
    GET  /public/partner-site/{slug}       the configuration the page renders
    GET  /public/partner-site/{slug}/link/{token}
    POST /public/partner-site/{slug}/join/{token}
    POST /public/partner-site/{slug}/welcome
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from . import partner_engine as eng
from .auth import AuthError, hash_password, verify_setup_token
from .db import tenant_session


def _site(cur, slug):
    cur.execute("""SELECT p.id, p.slug, p.name, b.brand_name, b.headline, b.tagline, b.about,
                          b.primary_color, b.accent_color, b.support_email, b.support_phone,
                          b.support_whatsapp, b.logo_mime IS NOT NULL AS has_logo
                     FROM partners p JOIN partner_branding b ON b.partner_id = p.id
                    WHERE p.slug = %s AND p.status = 'active' AND b.website_published""", (slug,))
    return cur.fetchone()


def _link(cur, pid, token):
    cur.execute("""SELECT l.id, l.price_book_id FROM partner_signup_links l
                    WHERE l.partner_id = %s AND l.token = %s AND l.active
                      AND (l.expires_at IS NULL OR l.expires_at > now())
                      AND (l.max_uses IS NULL OR l.uses < l.max_uses)""", (pid, token))
    return cur.fetchone()


def _email_in(token):
    """The email a setup token names, read WITHOUT trusting it: it only picks
    which account to verify the token against."""
    try:
        raw = token.split(".", 1)[0]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode().split("|", 1)[0]
    except Exception:
        return ""


def make_partner_site_router(P, page) -> APIRouter:
    router = APIRouter()

    for path in ("/p/{slug}", "/p/{slug}/join/{token}", "/p/{slug}/welcome"):
        router.add_api_route(path, lambda: page("partner_site.html"), methods=["GET"],
                             include_in_schema=False)
    # the partner's own console (the agency is TechDeva's customer: this one is TechDeva's)
    for path in ("/partner", "/partner/welcome"):
        router.add_api_route(path, lambda: page("partner.html"), methods=["GET"], include_in_schema=False)

    @router.get("/p/{slug}/logo", include_in_schema=False)
    def logo(slug: str):
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT b.logo_mime, b.logo_bytes FROM partners p
                             JOIN partner_branding b ON b.partner_id = p.id
                            WHERE p.slug = %s AND p.status = 'active' AND b.logo_mime IS NOT NULL""", (slug,))
            r = cur.fetchone()
        if not r:
            raise HTTPException(404, "no logo")
        return Response(bytes(r["logo_bytes"]), media_type=r["logo_mime"],
                        headers={"Cache-Control": "public, max-age=300",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @router.get("/public/partner-site/{slug}")
    def site(slug: str):
        with tenant_session(None, "techdeva_admin") as cur:
            s = _site(cur, slug)
            if not s:
                raise HTTPException(404, "This page is not available.")
            cur.execute("""SELECT b.id, b.name, b.price, b.currency, b.billing_interval, b.trial_days,
                                  pl.max_numbers, pl.max_conversations
                             FROM partner_price_books b JOIN plans pl ON pl.key = b.plan_key
                            WHERE b.partner_id = %s AND b.active ORDER BY b.price""", (s["id"],))
            books = [dict(r) for r in cur.fetchall()]
        out = {k: s[k] for k in ("slug", "headline", "tagline", "about", "primary_color", "accent_color",
                                 "support_email", "support_phone", "support_whatsapp", "has_logo")}
        out["brand_name"] = s["brand_name"] or s["name"]
        with tenant_session(None, "techdeva_admin") as cur:      # the link the site's "Get started" uses
            cur.execute("""SELECT token FROM partner_signup_links WHERE partner_id = %s AND active
                              AND price_book_id IS NULL AND (expires_at IS NULL OR expires_at > now())
                              AND (max_uses IS NULL OR uses < max_uses)
                            ORDER BY created_at LIMIT 1""", (s["id"],))
            ln = cur.fetchone()
        out["join_token"] = ln["token"] if ln else None
        out["packages"] = [{**{k: str(v) if k in ("id", "price") else v for k, v in b.items()}} for b in books]
        return out

    @router.get("/public/partner-site/{slug}/link/{token}")
    def link(slug: str, token: str):
        with tenant_session(None, "techdeva_admin") as cur:
            s = _site(cur, slug)
            ln = _link(cur, s["id"], token) if s else None
        if not ln:
            raise HTTPException(404, "This sign-up link is no longer valid. Ask for a new one.")
        return {"valid": True, "package_id": str(ln["price_book_id"]) if ln["price_book_id"] else None}

    @router.post("/public/partner-site/{slug}/join/{token}")
    def join(slug: str, token: str, request: Request, body: dict = Body(...)):
        """Self-registration through a partner's branded link: the business,
        its login and its subscription, assigned to that partner, in one go."""
        from .account import _rate_ok
        from .security import client_ip
        if (body.get("website_hp") or "").strip():
            return {"ok": True}
        ip = client_ip(request)
        if not _rate_ok("partner-join:" + ip, 5):
            raise HTTPException(429, "Too many sign-ups from this connection. Try again later.")
        pw = body.get("password") or ""
        if len(pw) < 10 or len(set(pw)) < 5:
            raise HTTPException(400, "Use a password of at least 10 characters that is not easy to guess.")
        with tenant_session(None, "techdeva_admin") as cur:
            s = _site(cur, slug)
            ln = _link(cur, s["id"], token) if s else None
        if not ln:
            raise HTTPException(404, "This sign-up link is no longer valid. Ask for a new one.")
        try:
            pw_hash = hash_password(pw)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        try:
            out = eng.provision_client(
                P, str(s["id"]), business_name=body.get("business_name") or "",
                industry=body.get("industry") or "", contact_name=body.get("contact_name") or "",
                contact_email=body.get("contact_email") or "", contact_phone=body.get("contact_phone") or "",
                password_hash=pw_hash, price_book_id=body.get("package_id") or ln["price_book_id"],
                coupon_code=(body.get("coupon_code") or "").strip() or None,
                signup_link_id=str(ln["id"]), actor="self-registration")
        except eng.ClientError as e:
            raise HTTPException(e.status, e.message)
        # the first sign-in waits for an emailed code, as for every new login;
        # this one arrives under the partner's brand
        user = P.find_user_by_email((body.get("contact_email") or "").strip().lower())
        sent = eng.send_client_code(str(s["id"]), dict(user)) if user else {"sent": False, "reason": "failed"}
        return {"ok": True, "business": (body.get("business_name") or "").strip(), "trial": True,
                "verification_required": True, "email_sent": sent["sent"], "login": "/dashboard"}

    @router.post("/public/partner-site/{slug}/resend-code")
    def resend(slug: str, request: Request, body: dict = Body(...)):
        """Same answer whether or not the account exists (no account probing)."""
        from .account import _rate_ok
        from .security import client_ip
        if not _rate_ok("partner-resend:" + client_ip(request), 10):
            raise HTTPException(429, "Too many requests. Try again later.")
        email = (body.get("email") or "").strip().lower()[:200]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT u.id, u.email, u.name, u.email_verified_at, p.id AS partner_id
                             FROM users u JOIN tenants t ON t.id = u.tenant_id
                             JOIN partners p ON p.id = t.partner_id
                            WHERE lower(u.email) = %s AND p.slug = %s AND p.status = 'active'""", (email, slug))
            u = cur.fetchone()
        if u and not u["email_verified_at"]:
            eng.send_client_code(str(u["partner_id"]), dict(u))
        return {"ok": True, "message": "If that email has an account waiting for verification, a code was sent."}

    @router.post("/public/partner-site/{slug}/welcome")
    def welcome(slug: str, request: Request, body: dict = Body(...)):
        """A client the partner created chooses its password. The link is
        single-use (bound to the account's password state) and only works on
        the site of the partner that client belongs to."""
        from .account import _rate_ok
        from .security import client_ip
        if not _rate_ok("partner-welcome:" + client_ip(request), 10):
            raise HTTPException(429, "Too many attempts. Try again later.")
        token, pw = (body.get("token") or "").strip(), body.get("password") or ""
        if len(pw) < 10 or len(set(pw)) < 5:
            raise HTTPException(400, "Use a password of at least 10 characters that is not easy to guess.")
        email = _email_in(token).strip().lower()
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT u.id, u.email, u.password_hash FROM users u
                             JOIN tenants t ON t.id = u.tenant_id
                             JOIN partners p ON p.id = t.partner_id
                            WHERE lower(u.email) = %s AND p.slug = %s""", (email, slug))
            u = cur.fetchone()
        if not u:
            raise HTTPException(400, "This link is not valid, or has already been used.")
        try:
            verify_setup_token(token, u["password_hash"])
            new_hash = hash_password(pw)
        except AuthError as e:
            raise HTTPException(400, "This link is not valid, or has already been used."
                                if e.status != 400 or "password" not in e.message else e.message)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE users SET password_hash = %s, email_verified_at = COALESCE(email_verified_at, now()) "
                        "WHERE id = %s AND password_hash IS NOT DISTINCT FROM %s RETURNING id",
                        (new_hash, u["id"], u["password_hash"]))
            if not cur.fetchone():
                raise HTTPException(409, "This link was just used. Sign in instead.")
        return {"ok": True, "login": "/dashboard"}

    return router
