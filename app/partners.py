"""White-label partners: the agencies that sell TechDeva under their own name.

Two audiences, one module:

* **The founder** (`/techdeva/partners/...`) approves an agency, writes down what
  it pays for each plan, creates its logins, and says which businesses it brought.
* **The partner** (`/partner/...`) signs in and sees its own businesses.

What a partner may see is decided in the database, not here (db/87): a partner
session sets `app.partner_id` and nothing else, so `is_admin()` is false and
`current_tenant()` is NULL. Every tenant-scoped table therefore returns nothing.
A partner sees the *list* of businesses it brought and no data inside any of
them — not one lead, message, appointment or connected ad account. Helping a
customer means being given that customer's own login, or the founder acting for
them, both of which are already audited paths.

Partner logins are a separate identity space from `users`: an agency is not a
business, and a token for one must never resolve to the other. `resolve_scope`
refuses a partner token outright, so no existing route can be reached with one.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request, Response

from .audit import record
from .auth import (AuthError, hash_password, issue_setup_token, issue_token,
                   resolve_scope, token_from, verify_setup_token, verify_token)
from .db import tenant_session
from .security import check_password, client_ip, lock_message, locked_for, record_attempt

PARTNER_COOKIE = "td_partner"
PARTNER_TTL = 12 * 3600


# ----------------------------------------------------------------- who is asking
def _founder(x_tenant_id, x_role, authorization, session):
    try:
        _tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role != "techdeva_admin":
        raise HTTPException(403, "partners are managed by TechDeva")
    return uid


def _partner(authorization, cookie):
    """The signed-in agency, or 401. Returns (partner_id, partner_user_id)."""
    token = token_from(authorization, cookie)
    if not token:
        raise HTTPException(401, "sign in to the partner dashboard")
    try:
        claims = verify_token(token)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if claims.get("r") != "partner":
        raise HTTPException(403, "this is not a partner login")
    return claims["p"], claims["u"]


def _active(pid):
    """Refuse a suspended or unapproved agency at the door, every request."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT name, status FROM partners WHERE id = %s", (str(pid),))
        row = cur.fetchone()
    if not row:
        raise HTTPException(403, "this partner account no longer exists")
    if row["status"] != "active":
        raise HTTPException(403, "this partner account is %s. Contact TechDeva." % row["status"])
    return row


def make_partners_router() -> APIRouter:
    router = APIRouter()

    # ========================================================== founder's side
    @router.post("/techdeva/partners")
    def create_partner(body: dict = Body(...),
                       x_tenant_id: str | None = Header(None),
                       x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        name = (body.get("name") or "").strip()
        slug = (body.get("slug") or "").strip().lower()
        if not name:
            raise HTTPException(400, "the agency needs a name")
        if not slug:
            raise HTTPException(400, "the agency needs an address name (slug)")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM partners WHERE slug = %s", (slug,))
            if cur.fetchone():
                raise HTTPException(409, "that address name is taken")
            try:
                cur.execute("""INSERT INTO partners (name, slug, country, contact_name,
                                                     contact_email, contact_phone, notes)
                               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, status""",
                            (name[:120], slug,
                             (body.get("country") or "").strip()[:60],
                             (body.get("contact_name") or "").strip()[:120],
                             (body.get("contact_email") or "").strip()[:200],
                             (body.get("contact_phone") or "").strip()[:40],
                             (body.get("notes") or "").strip()))
            except Exception as e:                       # the CHECK on slug, mostly
                raise HTTPException(400, "that address name cannot be used: "
                                         "letters, numbers and hyphens, 3-32 characters")
            row = cur.fetchone()
            record(cur, action="partner.created", resource="partner",
                   resource_id=str(row["id"]), actor=uid,
                   detail={"name": name, "slug": slug})
        return {"id": str(row["id"]), "name": name, "slug": slug, "status": row["status"]}

    @router.get("/techdeva/partners")
    def list_partners(x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias="td_session")):
        _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""
                SELECT p.id, p.name, p.slug, p.status, p.country, p.contact_name,
                       p.contact_email, p.contact_phone, p.created_at,
                       (SELECT count(*) FROM tenants t
                         WHERE t.partner_id = p.id AND NOT COALESCE(t.is_test,false)) AS clients,
                       (SELECT count(*) FROM partner_users u
                         WHERE u.partner_id = p.id AND u.active) AS logins
                  FROM partners p ORDER BY p.created_at""")
            partners = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT partner_id, plan_key, wholesale_price, currency,
                                  upside_share_pct, effective_from
                             FROM partner_agreements ORDER BY effective_from DESC""")
            terms = [dict(r) for r in cur.fetchall()]
        by_partner = {}
        for t in terms:
            by_partner.setdefault(str(t["partner_id"]), []).append(t)
        for p in partners:
            p["id"] = str(p["id"])
            p["agreements"] = by_partner.get(p["id"], [])
        return {"partners": partners}

    @router.post("/techdeva/partners/{pid}/status")
    def set_status(pid: str, body: dict = Body(...),
                   x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        status = (body.get("status") or "").strip()
        if status not in {"pending", "active", "suspended"}:
            raise HTTPException(400, "status must be pending, active or suspended")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE partners SET status = %s, updated_at = now() "
                        "WHERE id = %s RETURNING name", (status, pid))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "no such partner")
            record(cur, action="partner.status", resource="partner", resource_id=pid,
                   actor=uid, detail={"status": status, "name": row["name"]})
        # Suspension bites on the partner's next request (_active), and their
        # customers are untouched: a suspended agency does not close a business.
        return {"id": pid, "status": status}

    @router.post("/techdeva/partners/{pid}/agreement")
    def set_agreement(pid: str, body: dict = Body(...),
                      x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias="td_session")):
        """What this agency pays TechDeva for one plan, from a date.

        A new rate is a new row, never an edit: what a partner was charged last
        month must still be readable next year.
        """
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        plan_key = (body.get("plan_key") or "").strip()
        price = body.get("wholesale_price")
        if price is None:
            raise HTTPException(400, "wholesale_price is required")
        try:
            price = float(price)
        except (TypeError, ValueError):
            raise HTTPException(400, "wholesale_price must be a number")
        if price < 0:
            raise HTTPException(400, "wholesale_price cannot be negative")
        share = float(body.get("upside_share_pct") or 0)
        if not 0 <= share <= 100:
            raise HTTPException(400, "upside_share_pct must be between 0 and 100")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM plans WHERE key = %s", (plan_key,))
            if not cur.fetchone():
                raise HTTPException(400, "no such plan")
            cur.execute("SELECT 1 FROM partners WHERE id = %s", (pid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such partner")
            cur.execute("""INSERT INTO partner_agreements
                             (partner_id, plan_key, wholesale_price, currency,
                              upside_share_pct, effective_from, notes)
                           VALUES (%s,%s,%s,%s,%s, COALESCE(%s, current_date), %s)
                           ON CONFLICT (partner_id, plan_key, effective_from)
                           DO UPDATE SET wholesale_price = EXCLUDED.wholesale_price,
                                         currency = EXCLUDED.currency,
                                         upside_share_pct = EXCLUDED.upside_share_pct,
                                         notes = EXCLUDED.notes
                           RETURNING id, effective_from""",
                        (pid, plan_key, price,
                         (body.get("currency") or "USD").strip().upper()[:3],
                         share, body.get("effective_from") or None,
                         (body.get("notes") or "").strip()))
            row = cur.fetchone()
            record(cur, action="partner.agreement", resource="partner", resource_id=pid,
                   actor=uid, detail={"plan": plan_key, "wholesale_price": price,
                                      "upside_share_pct": share})
        return {"id": str(row["id"]), "plan_key": plan_key,
                "wholesale_price": price, "effective_from": row["effective_from"]}

    @router.post("/techdeva/partners/{pid}/users")
    def add_partner_user(pid: str, body: dict = Body(...),
                         x_tenant_id: str | None = Header(None),
                         x_role: str | None = Header(None),
                         authorization: str | None = Header(None),
                         td_session: str | None = Cookie(None, alias="td_session")):
        """Create a login for the agency and return a one-time link to set its
        password. No password is ever chosen for them or sent in plain text."""
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        email = (body.get("email") or "").strip().lower()
        if "@" not in email:
            raise HTTPException(400, "a real email address is required")
        role = (body.get("role") or "partner_owner").strip()
        if role not in {"partner_owner", "partner_staff"}:
            raise HTTPException(400, "role must be partner_owner or partner_staff")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM partners WHERE id = %s", (pid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such partner")
            cur.execute("SELECT 1 FROM partner_users WHERE lower(btrim(email)) = %s", (email,))
            if cur.fetchone():
                raise HTTPException(409, "that email already has a partner login")
            cur.execute("""INSERT INTO partner_users (partner_id, email, name, role)
                           VALUES (%s,%s,%s,%s) RETURNING id""",
                        (pid, email, (body.get("name") or "").strip()[:120], role))
            row = cur.fetchone()
            record(cur, action="partner.user_created", resource="partner",
                   resource_id=pid, actor=uid, detail={"email": email, "role": role})
        return {"id": str(row["id"]), "email": email, "role": role,
                "set_password_token": issue_setup_token(email, None)}

    @router.post("/techdeva/partners/{pid}/clients")
    def assign_client(pid: str, body: dict = Body(...),
                      x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias="td_session")):
        """Say that this agency brought this business.

        A business already belonging to a different agency is NOT moved unless
        the caller says `reassign: true`. Who owns a customer is the part of a
        partnership that ends up in a dispute, so it never happens by accident.
        """
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        tid = (body.get("tenant_id") or "").strip()
        if not tid:
            raise HTTPException(400, "tenant_id is required")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM partners WHERE id = %s", (pid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such partner")
            cur.execute("SELECT id, name, partner_id FROM tenants WHERE id = %s", (tid,))
            t = cur.fetchone()
            if not t:
                raise HTTPException(404, "no such business")
            before = str(t["partner_id"]) if t["partner_id"] else None
            if before and before != pid and not body.get("reassign"):
                raise HTTPException(409, "that business already belongs to another agency. "
                                         "Send reassign: true to move it.")
            cur.execute("UPDATE tenants SET partner_id = %s WHERE id = %s", (pid, tid))
            record(cur, action="partner.client_assigned", resource="tenant",
                   resource_id=tid, actor=uid, tenant_id=tid,
                   detail={"partner_id": pid, "previous_partner_id": before,
                           "business": t["name"]})
        return {"tenant_id": tid, "partner_id": pid, "previous_partner_id": before}

    # ========================================================== partner's side
    @router.post("/partner/login")
    def partner_login(request: Request, response: Response, body: dict = Body(...)):
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        ip = client_ip(request)
        wait, which = locked_for(email, ip)
        if wait:
            raise HTTPException(429, lock_message(wait, which))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT u.id, u.partner_id, u.password_hash, u.active, u.role,
                                  p.status AS partner_status, p.name AS partner_name
                             FROM partner_users u JOIN partners p ON p.id = u.partner_id
                            WHERE lower(btrim(u.email)) = %s""", (email,))
            u = cur.fetchone()
        ok = check_password(password, u["password_hash"] if u else None)
        if not ok or not u or not u["active"]:
            record_attempt(email, ip, False)
            raise HTTPException(401, "wrong email or password")
        if u["partner_status"] != "active":
            record_attempt(email, ip, False)
            raise HTTPException(403, "this partner account is %s. Contact TechDeva."
                                     % u["partner_status"])
        record_attempt(email, ip, True)
        token = issue_token(tenant_id=None, user_id=str(u["id"]), role="partner",
                            partner_id=str(u["partner_id"]), ttl=PARTNER_TTL)
        response.set_cookie(PARTNER_COOKIE, token, httponly=True, secure=True,
                            samesite="lax", max_age=PARTNER_TTL, path="/")
        return {"partner": u["partner_name"], "role": u["role"], "token": token}

    @router.post("/partner/set-password")
    def partner_set_password(body: dict = Body(...)):
        """Use the one-time link from TechDeva to choose a password.

        The link is bound to the account's current password state, so it stops
        working the moment a password is set (app/auth.py)."""
        token = (body.get("token") or "").strip()
        password = body.get("password") or ""
        with tenant_session(None, "techdeva_admin") as cur:
            # the email is inside the token; read it without trusting it yet
            cur.execute("SELECT id, email, password_hash FROM partner_users")
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            try:
                email = verify_setup_token(token, r["password_hash"])
            except AuthError:
                continue
            if email != (r["email"] or "").strip().lower():
                continue
            try:
                new_hash = hash_password(password)
            except AuthError as e:
                raise HTTPException(e.status, e.message)
            with tenant_session(None, "techdeva_admin") as cur:
                cur.execute("UPDATE partner_users SET password_hash = %s, updated_at = now() "
                            "WHERE id = %s", (new_hash, str(r["id"])))
                record(cur, action="partner.password_set", resource="partner",
                       resource_id=str(r["id"]), actor=str(r["id"]), actor_type="human")
            return {"ok": True, "email": email}
        raise HTTPException(400, "this link is not valid, or has already been used")

    @router.get("/partner/me")
    def partner_me(authorization: str | None = Header(None),
                   td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid = _partner(authorization, td_partner)
        _active(pid)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("SELECT id, name, slug, status, country FROM partners")
            me = cur.fetchone()
            cur.execute("""SELECT plan_key, wholesale_price, currency, upside_share_pct,
                                  effective_from
                             FROM partner_agreements
                            WHERE effective_from <= current_date
                            ORDER BY plan_key, effective_from DESC""")
            terms = [dict(r) for r in cur.fetchall()]
        # one row per plan: the rate in force today
        latest = {}
        for t in terms:
            latest.setdefault(t["plan_key"], t)
        return {"partner": {"id": str(me["id"]), "name": me["name"], "slug": me["slug"],
                            "status": me["status"], "country": me["country"]},
                "user_id": uid,
                "agreements": list(latest.values())}

    @router.get("/partner/clients")
    def partner_clients(authorization: str | None = Header(None),
                        td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """The businesses this agency brought — the list, not what is inside them.

        Row-level security is what makes that true: this session carries only
        `app.partner_id`, so every tenant-scoped table is empty to it.
        """
        pid, _uid = _partner(authorization, td_partner)
        _active(pid)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT id, name, industry, status::text AS status,
                                  subdomain, created_at, trial_ends_at
                             FROM tenants
                            WHERE NOT COALESCE(is_test, false)
                            ORDER BY created_at DESC""")
            clients = [dict(r) for r in cur.fetchall()]
        for c in clients:
            c["id"] = str(c["id"])
        return {"clients": clients, "count": len(clients)}

    return router
