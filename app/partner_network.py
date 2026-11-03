"""White-label partner network: the routes (logic lives in app/partner_engine.py).

    founder   /techdeva/partners/{id}/approve|reject|terminate|terms|provisioning
              /techdeva/partner-network          the whole network on one page
              /techdeva/partner-ledger           review queue, refunds, chargebacks
              /techdeva/partner-payouts          gather, mark paid / failed
    partner   /partner/dashboard                 the partner's Founder Dashboard
              /partner/branding, /partner/domain
              /partner/price-books, /partner/coupons, /partner/signup-links
              /partner/clients (POST)            create a client, end to end
              /partner/ledger, /partner/payouts
    public    /partner/apply                     an agency applies
              /p/{slug}, /p/{slug}/join/{token}  the partner's site and sign-up (app/partner_site.py)

Who may see what is enforced in Postgres (db/87, db/88), not here. These routes
add the rules RLS cannot express: a partner_staff login may look and create
clients but not change prices, brand or money settings; a suspended partner is
refused at the door; a partner may only act on businesses that are its own.
"""
from __future__ import annotations

import base64
import binascii
import re
import socket
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request, Response

from . import partner_engine as eng
from .audit import record
from .auth import AuthError, resolve_scope, token_from, verify_token
from .db import tenant_session

PARTNER_COOKIE = "td_partner"
LOGO_MIMES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DOMAIN = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _j(v):
    """JSON-safe: Decimal -> str (money is never a float), uuid/date -> str."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _j(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _money(raw, name, *, allow_none=False, positive=False):
    if raw is None or raw == "":
        if allow_none:
            return None
        raise HTTPException(400, "%s is required" % name)
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "%s must be a number" % name)
    if d < 0 or (positive and d == 0) or not d.is_finite():
        raise HTTPException(400, "%s must be %s" % (name, "more than 0" if positive else "0 or more"))
    return d


# ------------------------------------------------------------- who is asking
def _founder(x_tenant_id, x_role, authorization, session):
    try:
        _t, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role != "techdeva_admin":
        raise HTTPException(403, "the partner network is managed by TechDeva")
    return uid


def _partner(authorization, cookie, *, owner=False):
    """(partner_id, user_id, role). Refuses a suspended partner, a disabled
    login, and -- with owner=True -- a partner_staff login."""
    token = token_from(authorization, cookie)
    if not token:
        raise HTTPException(401, "sign in to the partner dashboard")
    try:
        claims = verify_token(token)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if claims.get("r") != "partner":
        raise HTTPException(403, "this is not a partner login")
    pid, uid = claims["p"], claims["u"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT p.status, u.role, u.active FROM partners p
                         JOIN partner_users u ON u.partner_id = p.id
                        WHERE p.id = %s AND u.id::text = %s""", (pid, uid))
        r = cur.fetchone()
    if not r or not r["active"]:
        raise HTTPException(403, "this partner login is no longer active")
    if r["status"] != "active":
        raise HTTPException(403, "this partner account is %s. Contact TechDeva." % r["status"])
    if owner and r["role"] != "partner_owner":
        raise HTTPException(403, "only the partner's owner login can change this")
    return pid, uid, r["role"]


def _own_tenant(pid, tid):
    with tenant_session(None, "partner", partner_id=pid) as cur:
        cur.execute("SELECT id, name, status::text AS status FROM tenants WHERE id::text = %s", (tid,))
        t = cur.fetchone()
    if not t:
        raise HTTPException(404, "no such business among your clients")
    return t


def make_partner_network_router(P) -> APIRouter:
    router = APIRouter()

    # ===================================================== the founder's side
    @router.post("/techdeva/partners/{pid}/approve")
    def approve(pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias="td_session")):
        """Approve an agency and set it up automatically, there and then.
        A step that fails is retried by the scheduler; the answer says which."""
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE partners SET status = 'active', updated_at = now(),
                                  approved_at = COALESCE(approved_at, now()), decided_reason = ''
                            WHERE id = %s AND status IN ('pending','active','suspended')
                            RETURNING name""", (pid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "no such partner, or it was rejected / terminated")
            run_id = eng.start_provisioning(cur, pid)
            record(cur, action="partner.approved", resource="partner", resource_id=pid, actor=uid,
                   detail={"name": row["name"], "run": run_id})
        result = eng.run_provisioning(run_id)
        return {"id": pid, "status": "active", "provisioning": result, "run_id": run_id,
                **_provisioning_view(pid)}

    def _provisioning_view(pid):
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT id, status, attempts, last_error, next_try_at, finished_at
                             FROM partner_provisioning_runs WHERE partner_id = %s
                            ORDER BY created_at DESC LIMIT 1""", (pid,))
            run = cur.fetchone()
            steps = []
            if run:
                cur.execute("""SELECT step, status, attempts, last_error, detail, done_at
                                 FROM partner_provisioning_steps WHERE run_id = %s ORDER BY position""",
                            (run["id"],))
                steps = [dict(s) for s in cur.fetchall()]
                for s in steps:   # the link token belongs on the partner's own screen, not in a log view
                    s["detail"] = {k: v for k, v in (s["detail"] or {}).items() if k != "token"}
        return {"run": _j(dict(run)) if run else None, "steps": _j(steps)}

    @router.get("/techdeva/partners/{pid}/provisioning")
    def provisioning(pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias="td_session")):
        _founder(x_tenant_id, x_role, authorization, td_session)
        return _provisioning_view(pid)

    @router.post("/techdeva/partners/{pid}/provisioning/retry")
    def provisioning_retry(pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                           authorization: str | None = Header(None),
                           td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT status FROM partners WHERE id = %s", (pid,))
            p = cur.fetchone()
            if not p or p["status"] != "active":
                raise HTTPException(409, "only an active partner can be set up")
            run_id = eng.start_provisioning(cur, pid)
            cur.execute("UPDATE partner_provisioning_runs SET attempts = 0 WHERE id = %s", (run_id,))
            record(cur, action="partner.provisioning.retry", resource="partner", resource_id=pid, actor=uid)
        return {"result": eng.run_provisioning(run_id), **_provisioning_view(pid)}

    def _decide(pid, status, reason, uid, allowed_from):
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE partners SET status = %s, decided_reason = %s, updated_at = now(),
                                  terminated_at = CASE WHEN %s = 'terminated' THEN now() ELSE terminated_at END
                            WHERE id = %s AND status = ANY(%s) RETURNING name""",
                        (status, reason, status, pid, list(allowed_from)))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "this partner cannot be moved to %s from its current state" % status)
            record(cur, action="partner.%s" % status, resource="partner", resource_id=pid, actor=uid,
                   detail={"name": row["name"], "reason": reason})
        return {"id": pid, "status": status}

    @router.post("/techdeva/partners/{pid}/reject")
    def reject(pid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None), authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        return _decide(pid, "rejected", (body.get("reason") or "").strip()[:500], uid, ("pending",))

    @router.post("/techdeva/partners/{pid}/terminate")
    def terminate(pid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias="td_session")):
        """End a partnership. Its clients keep running and keep being served:
        who owns them now is a contract question, answered by
        /techdeva/partners/{new}/clients with reassign: true, never by this."""
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        reason = (body.get("reason") or "").strip()[:500]
        if not reason:
            raise HTTPException(400, "say why the partnership ends")
        return _decide(pid, "terminated", reason, uid, ("active", "suspended"))

    @router.post("/techdeva/partners/{pid}/terms")
    def set_terms(pid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias="td_session")):
        """The split for this partner from a date. A new deal is a new row: what
        was already split stays split at the old terms."""
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        pct = _money(body.get("partner_share_pct"), "partner_share_pct")
        if pct > 100:
            raise HTTPException(400, "partner_share_pct cannot be more than 100")
        floor = _money(body.get("min_techdeva_amount"), "min_techdeva_amount", allow_none=True)
        fcur = (body.get("floor_currency") or "").strip().upper() or None
        if floor is not None and not (fcur and re.match(r"^[A-Z]{3}$", fcur)):
            raise HTTPException(400, "a minimum needs its currency, e.g. USD")
        bearer = body.get("fee_bearer") or "shared"
        if bearer not in eng.FEE_BEARERS:
            raise HTTPException(400, "fee_bearer must be shared, partner or techdeva")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM partners WHERE id = %s", (pid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such partner")
            cur.execute("""INSERT INTO partner_revenue_terms
                             (partner_id, partner_share_pct, min_techdeva_amount, floor_currency,
                              fee_bearer, effective_from, notes, created_by)
                           VALUES (%s,%s,%s,%s,%s, COALESCE(%s::date, current_date), %s, %s)
                           ON CONFLICT (partner_id, effective_from) DO UPDATE
                             SET partner_share_pct = EXCLUDED.partner_share_pct,
                                 min_techdeva_amount = EXCLUDED.min_techdeva_amount,
                                 floor_currency = EXCLUDED.floor_currency,
                                 fee_bearer = EXCLUDED.fee_bearer, notes = EXCLUDED.notes
                           RETURNING id, effective_from""",
                        (pid, pct, floor, fcur if floor is not None else None, bearer,
                         body.get("effective_from") or None, (body.get("notes") or "").strip(), uid))
            row = cur.fetchone()
            record(cur, action="partner.terms", resource="partner", resource_id=pid, actor=uid,
                   detail={"partner_share_pct": str(pct), "techdeva_share_pct": str(100 - pct),
                           "min_techdeva_amount": str(floor) if floor is not None else None,
                           "floor_currency": fcur, "fee_bearer": bearer})
        return _j({"id": row["id"], "effective_from": row["effective_from"], "partner_share_pct": pct,
                   "techdeva_share_pct": 100 - pct, "min_techdeva_amount": floor, "fee_bearer": bearer})

    @router.get("/techdeva/partner-network")
    def network(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias="td_session")):
        """The Master Admin view of the network. Money per currency, never added
        across currencies."""
        _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT status, count(*) AS n FROM partners GROUP BY status")
            by_status = {r["status"]: r["n"] for r in cur.fetchall()}
            cur.execute("""SELECT count(*) AS n FROM tenants
                            WHERE partner_id IS NOT NULL AND NOT COALESCE(is_test,false)""")
            clients = cur.fetchone()["n"]
            cur.execute("""SELECT currency,
                                  SUM(gross) FILTER (WHERE kind = 'payment')        AS collected,
                                  -SUM(gross) FILTER (WHERE kind <> 'payment')      AS reversed,
                                  SUM(partner_amount)                                AS partner_earned,
                                  SUM(techdeva_amount)                               AS techdeva_earned,
                                  SUM(partner_amount) FILTER (WHERE payout_id IS NULL AND NOT needs_review)
                                                                                     AS awaiting_payout,
                                  count(*) FILTER (WHERE needs_review)               AS to_review
                             FROM partner_ledger GROUP BY currency ORDER BY currency""")
            money = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT currency, count(*) AS n, SUM(amount) AS amount FROM partner_payouts
                            WHERE status = 'pending' GROUP BY currency""")
            pending = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT p.id, p.code, p.name, p.slug, p.status, p.country,
                                  (SELECT count(*) FROM tenants t WHERE t.partner_id = p.id
                                     AND NOT COALESCE(t.is_test,false)) AS clients,
                                  (SELECT status FROM partner_provisioning_runs r WHERE r.partner_id = p.id
                                    ORDER BY created_at DESC LIMIT 1) AS setup,
                                  (SELECT partner_share_pct FROM partner_revenue_terms rt
                                    WHERE rt.partner_id = p.id AND rt.effective_from <= current_date
                                    ORDER BY effective_from DESC LIMIT 1) AS partner_share_pct
                             FROM partners p ORDER BY p.created_at DESC""")
            partners = [dict(r) for r in cur.fetchall()]
        return _j({"partners_by_status": by_status, "partner_clients": clients, "money": money,
                   "pending_payouts": pending, "partners": partners})

    @router.get("/techdeva/partner-ledger")
    def ledger_admin(review: bool = False, partner_id: str | None = None,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias="td_session")):
        _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT l.*, p.name AS partner, t.name AS business
                             FROM partner_ledger l JOIN partners p ON p.id = l.partner_id
                             LEFT JOIN tenants t ON t.id = l.tenant_id
                            WHERE (NOT %s OR l.needs_review)
                              AND (%s::uuid IS NULL OR l.partner_id = %s::uuid)
                            ORDER BY l.created_at DESC LIMIT 500""", (review, partner_id, partner_id))
            return {"entries": _j([dict(r) for r in cur.fetchall()])}

    @router.post("/techdeva/partner-ledger/{lid}/clear-review")
    def clear_review(lid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        note = (body.get("note") or "").strip()
        if not note:
            raise HTTPException(400, "write what was checked")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE partner_ledger SET needs_review = false,
                                  note = trim(both ' ' from note || ' | reviewed: ' || %s)
                            WHERE id = %s AND needs_review RETURNING id""", (note, lid))
            if not cur.fetchone():
                raise HTTPException(404, "no such entry waiting for review")
            record(cur, action="partner.ledger.reviewed", resource="partner_ledger", resource_id=lid,
                   actor=uid, detail={"note": note})
        return {"id": lid, "needs_review": False}

    @router.post("/techdeva/partner-ledger/{lid}/reverse")
    def reverse(lid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        amount = _money(body.get("amount"), "amount", positive=True)
        fee = _money(body.get("gateway_fee") or 0, "gateway_fee")
        try:
            return eng.record_reversal(ledger_id=lid, amount=amount, kind=body.get("kind") or "refund",
                                       gateway_fee=fee, note=(body.get("note") or "").strip(), actor=uid)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/techdeva/partner-payouts/run")
    def payouts_run(body: dict = Body(default={}), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        eng.sync_ledger()
        return {"created": eng.create_payouts(actor=uid, partner_id=body.get("partner_id"))}

    @router.get("/techdeva/partner-payouts")
    def payouts_admin(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias="td_session")):
        _founder(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT o.*, p.name AS partner, p.code FROM partner_payouts o
                             JOIN partners p ON p.id = o.partner_id ORDER BY o.created_at DESC LIMIT 300""")
            return {"payouts": _j([dict(r) for r in cur.fetchall()])}

    @router.post("/techdeva/partner-payouts/{oid}/mark")
    def payout_mark(oid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None), authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias="td_session")):
        uid = _founder(x_tenant_id, x_role, authorization, td_session)
        status = body.get("status")
        if status == "paid" and not (body.get("reference") or "").strip():
            raise HTTPException(400, "a paid payout needs the bank / transfer reference")
        if status == "failed" and not (body.get("reason") or "").strip():
            raise HTTPException(400, "say why the payout failed")
        try:
            return eng.mark_payout(oid, status, reference=(body.get("reference") or "").strip(),
                                   reason=(body.get("reason") or "").strip() or None, actor=uid)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/techdeva/partners/{pid}/domain/verify")
    def domain_verify(pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias="td_session")):
        """A partner's domain is verified when it resolves to TechDeva's server
        (PARTNER_DOMAIN_TARGETS, comma-separated IPs). Pointing a domain at us
        is something only its owner can do. The certificate and routing for a
        verified domain is the host's job (see docs/PARTNERS.md)."""
        _founder(x_tenant_id, x_role, authorization, td_session)
        return _verify_domain(pid)

    # ===================================================== the partner's side
    @router.post("/partner/apply")
    def apply(request: Request, body: dict = Body(...)):
        """PUBLIC: an agency applies (Module 1, method A). It waits as 'pending'
        until TechDeva approves; nothing is set up before that."""
        from .account import _rate_ok
        from .security import client_ip
        if (body.get("website_hp") or "").strip():
            return {"ok": True}                              # honeypot
        if not _rate_ok("partner-apply:" + client_ip(request), 5):
            raise HTTPException(429, "Too many applications from this connection. Try again later.")
        name = (body.get("company") or "").strip()[:120]
        email = (body.get("email") or "").strip().lower()[:200]
        if len(name) < 2 or "@" not in email:
            raise HTTPException(400, "Please give your company name and a work email.")
        from .subdomains import RESERVED, VALID, slug as _slug
        base = _slug(name)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM partners WHERE lower(contact_email) = %s AND status = 'pending'", (email,))
            if cur.fetchone():
                return {"ok": True, "status": "pending"}          # already applied; say nothing more
            cur.execute("SELECT slug FROM partners WHERE slug LIKE %s", (base + "%",))
            taken = {r["slug"] for r in cur.fetchall()}
            cand, n = base, 1
            while cand in taken or cand in RESERVED or cand in ("partner", "partners") or not VALID.match(cand):
                n += 1
                cand = "%s-%d" % (base[:28], n)
            cur.execute("""INSERT INTO partners (name, slug, status, country, contact_name, contact_email,
                                                 contact_phone, website, notes, applied_via)
                           VALUES (%s,%s,'pending',%s,%s,%s,%s,%s,%s,'applied') RETURNING id, code""",
                        (name, cand, (body.get("country") or "").strip()[:60],
                         (body.get("contact_name") or "").strip()[:120], email,
                         (body.get("phone") or "").strip()[:40], (body.get("website") or "").strip()[:200],
                         (body.get("message") or "").strip()[:2000]))
            row = cur.fetchone()
            record(cur, action="partner.applied", resource="partner", resource_id=str(row["id"]),
                   actor="public", detail={"company": name})
        return {"ok": True, "status": "pending", "reference": row["code"]}

    @router.get("/partner/dashboard")
    def dashboard(authorization: str | None = Header(None),
                  td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """The partner's Founder Dashboard (Module 3). Everything read through
        the partner's own RLS scope; money per currency."""
        pid, _uid, role = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("SELECT id, code, name, slug, status, approved_at FROM partners")
            me = cur.fetchone()
            cur.execute("""SELECT count(*) AS total,
                                  count(*) FILTER (WHERE status::text = 'trial')  AS trial,
                                  count(*) FILTER (WHERE status::text = 'active') AS active,
                                  count(*) FILTER (WHERE status::text = 'paused') AS paused
                             FROM tenants WHERE NOT COALESCE(is_test,false)""")
            clients = cur.fetchone()
            cur.execute("""SELECT status, count(*) AS n FROM partner_client_subscriptions GROUP BY status""")
            subs = {r["status"]: r["n"] for r in cur.fetchall()}
            cur.execute("""SELECT currency,
                                  SUM(CASE WHEN billing_interval = 'year' THEN price / 12 ELSE price END) AS mrr
                             FROM partner_client_subscriptions WHERE status = 'active'
                            GROUP BY currency""")
            mrr = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT currency,
                                  SUM(gross) FILTER (WHERE kind = 'payment')                   AS collected,
                                  SUM(gross) FILTER (WHERE kind = 'payment'
                                     AND occurred_on >= date_trunc('month', current_date))      AS collected_this_month,
                                  -SUM(gross) FILTER (WHERE kind <> 'payment')                 AS refunded,
                                  SUM(partner_amount)                                           AS your_earnings,
                                  SUM(techdeva_amount)                                          AS platform_share,
                                  SUM(partner_amount) FILTER (WHERE payout_id IS NULL AND NOT needs_review)
                                                                                                AS awaiting_payout,
                                  SUM(partner_amount) FILTER (WHERE needs_review)               AS under_review
                             FROM partner_ledger GROUP BY currency ORDER BY currency""")
            money = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT id, amount, currency, status, reference, created_at, paid_at
                             FROM partner_payouts ORDER BY created_at DESC LIMIT 10""")
            payouts = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT partner_share_pct, min_techdeva_amount, floor_currency, fee_bearer, effective_from
                             FROM partner_revenue_terms WHERE effective_from <= current_date
                            ORDER BY effective_from DESC LIMIT 1""")
            terms = cur.fetchone()
            cur.execute("""SELECT to_char(created_at, 'YYYY-MM') AS month, count(*) AS n
                             FROM tenants WHERE NOT COALESCE(is_test,false)
                              AND created_at > now() - interval '12 months' GROUP BY 1 ORDER BY 1""")
            growth = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT token, label, uses, active FROM partner_signup_links ORDER BY created_at")
            links = [dict(r) for r in cur.fetchall()]
        for ln in links:
            ln["url"] = "%s/join/%s" % (eng.partner_base_url(me["slug"]), ln["token"])
        return _j({"partner": dict(me), "your_role": role, "clients": dict(clients),
                   "subscriptions": subs, "mrr": mrr, "money": money, "payouts": payouts,
                   "terms": dict(terms) if terms else None, "client_growth": growth,
                   "signup_links": links, "website": eng.partner_base_url(me["slug"])})

    @router.post("/partner/logout")
    def logout(response: Response, authorization: str | None = Header(None),
               td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """End this partner login on the server too (db/62), not just in the browser."""
        from .auth import sign_out
        sign_out(token_from(authorization, td_partner))
        response.delete_cookie(PARTNER_COOKIE, path="/")
        return {"ok": True}

    # ---------------------------------------------------------------- branding
    @router.get("/partner/branding")
    def branding_get(authorization: str | None = Header(None),
                     td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT brand_name, headline, tagline, about, primary_color, accent_color,
                                  support_email, support_phone, support_whatsapp, website_published,
                                  logo_mime IS NOT NULL AS has_logo FROM partner_branding""")
            b = cur.fetchone()
            cur.execute("SELECT domain, status, token, checked_at, last_error FROM partner_domains")
            d = cur.fetchone()
        return _j({"branding": dict(b) if b else None, "domain": dict(d) if d else None})

    @router.put("/partner/branding")
    def branding_put(body: dict = Body(...), authorization: str | None = Header(None),
                     td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        fields, vals = [], []
        for k, lim in (("brand_name", 80), ("headline", 160), ("tagline", 200), ("about", 4000),
                       ("support_email", 200), ("support_phone", 40), ("support_whatsapp", 40)):
            if k in body:
                v = (body.get(k) or "").strip()[:lim]
                if k == "brand_name" and len(v) < 2:
                    raise HTTPException(400, "the brand needs a name")
                if k == "support_email" and v and "@" not in v:
                    raise HTTPException(400, "support_email does not look like an email")
                fields.append(k); vals.append(v)
        for k in ("primary_color", "accent_color"):
            if k in body:
                v = (body.get(k) or "").strip() or None
                if v and not HEX.match(v):
                    raise HTTPException(400, "%s must look like #1a2b3c" % k)
                fields.append(k); vals.append(v)
        if "website_published" in body:
            fields.append("website_published"); vals.append(bool(body["website_published"]))
        if not fields:
            raise HTTPException(400, "nothing to change")
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("UPDATE partner_branding SET %s, updated_at = now() RETURNING partner_id"
                        % ", ".join("%s = %%s" % f for f in fields), vals)
            if not cur.fetchone():
                raise HTTPException(409, "your brand is still being set up; try again in a minute")
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="partner.branding", resource="partner", resource_id=pid, actor=uid,
                   detail={"fields": fields})
        return {"ok": True, "changed": fields}

    @router.put("/partner/branding/logo")
    def logo_put(body: dict = Body(...), authorization: str | None = Header(None),
                 td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        mime = (body.get("mime") or "").strip()
        if mime not in LOGO_MIMES:
            raise HTTPException(400, "logo must be PNG, JPEG, WebP or SVG")
        try:
            raw = base64.b64decode(body.get("data_base64") or "", validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "logo data is not valid base64")
        if not raw or len(raw) > 1048576:
            raise HTTPException(400, "logo must be between 1 byte and 1 MB")
        if mime == "image/svg+xml" and re.search(rb"<script|javascript:|on[a-z]+\s*=", raw, re.I):
            raise HTTPException(400, "that SVG contains script; upload a plain image")
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""UPDATE partner_branding SET logo_mime = %s, logo_bytes = %s, updated_at = now()
                            RETURNING partner_id""", (mime, raw))
            if not cur.fetchone():
                raise HTTPException(409, "your brand is still being set up; try again in a minute")
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="partner.logo", resource="partner", resource_id=pid, actor=uid,
                   detail={"mime": mime, "bytes": len(raw)})
        return {"ok": True, "bytes": len(raw)}

    @router.put("/partner/domain")
    def domain_put(body: dict = Body(...), authorization: str | None = Header(None),
                   td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """Ask for an own domain. It starts 'pending'; only a DNS check flips it
        to verified -- a partner cannot write that itself (db/88)."""
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        dom = (body.get("domain") or "").strip().lower().rstrip(".")
        dom = re.sub(r"^https?://", "", dom).split("/")[0]
        if not DOMAIN.match(dom) or dom.endswith("techdeva.net"):
            raise HTTPException(400, "give a domain you own, e.g. app.youragency.com")
        tok = eng.new_token()[:24]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT partner_id FROM partner_domains WHERE domain = %s", (dom,))
            r = cur.fetchone()
            if r and str(r["partner_id"]) != pid:
                raise HTTPException(409, "that domain is already connected to another account")
            cur.execute("""INSERT INTO partner_domains (partner_id, domain, token)
                           VALUES (%s,%s,%s)
                           ON CONFLICT (partner_id) DO UPDATE SET domain = EXCLUDED.domain, status = 'pending',
                                  token = EXCLUDED.token, last_error = NULL, checked_at = NULL, updated_at = now()""",
                        (pid, dom, tok))
            record(cur, action="partner.domain.requested", resource="partner", resource_id=pid, actor=uid,
                   detail={"domain": dom})
        import os
        targets = [t.strip() for t in os.environ.get("PARTNER_DOMAIN_TARGETS", "").split(",") if t.strip()]
        return {"domain": dom, "status": "pending",
                "instructions": ("Point %s at our server with an A record to %s, then press Verify."
                                 % (dom, " or ".join(targets) if targets else "the address TechDeva gives you"))}

    @router.post("/partner/domain/verify")
    def domain_verify_partner(authorization: str | None = Header(None),
                              td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner, owner=True)
        return _verify_domain(pid)

    def _verify_domain(pid):
        import os
        targets = {t.strip() for t in os.environ.get("PARTNER_DOMAIN_TARGETS", "").split(",") if t.strip()}
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT domain FROM partner_domains WHERE partner_id = %s", (pid,))
            r = cur.fetchone()
        if not r:
            raise HTTPException(404, "no domain requested")
        if not targets:
            status, err = "failed", "PARTNER_DOMAIN_TARGETS is not configured on the server"
        else:
            try:
                ips = {a[4][0] for a in socket.getaddrinfo(r["domain"], None)}
                status, err = (("verified", None) if ips & targets else
                               ("failed", "%s points to %s, not to our server" % (r["domain"], ", ".join(sorted(ips)))))
            except socket.gaierror:
                status, err = "failed", "%s does not resolve yet (DNS can take up to an hour)" % r["domain"]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE partner_domains SET status = %s, last_error = %s, checked_at = now(),
                                  updated_at = now() WHERE partner_id = %s""", (status, err, pid))
            record(cur, action="partner.domain.%s" % status, resource="partner", resource_id=pid,
                   actor="partner_engine", actor_type="system", detail={"domain": r["domain"], "error": err})
        return {"domain": r["domain"], "status": status, "error": err}

    # ------------------------------------------------------------ price books
    @router.get("/partner/price-books")
    def books_get(authorization: str | None = Header(None),
                  td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT b.id, b.plan_key, b.name, b.price, b.currency, b.billing_interval,
                                  b.trial_days, b.active FROM partner_price_books b ORDER BY b.price""")
            books = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT partner_share_pct, min_techdeva_amount, floor_currency, fee_bearer
                             FROM partner_revenue_terms WHERE effective_from <= current_date
                            ORDER BY effective_from DESC LIMIT 1""")
            t = cur.fetchone()
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT key, name, max_numbers, max_conversations FROM plans ORDER BY sort")
            plans = [dict(r) for r in cur.fetchall()]
        for b in books:   # what the partner keeps from one payment at this price
            if t:
                s = eng.split(b["price"], partner_share_pct=t["partner_share_pct"], fee_bearer=t["fee_bearer"],
                              min_techdeva_amount=t["min_techdeva_amount"], floor_currency=t["floor_currency"],
                              currency=b["currency"])
                b["you_keep"], b["platform_share"], b["floor_applies"] = (
                    s["partner_amount"], s["techdeva_amount"], s["floor_applied"])
        return _j({"price_books": books, "plans": plans})

    @router.post("/partner/price-books")
    def books_post(body: dict = Body(...), authorization: str | None = Header(None),
                   td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        return _save_book(pid, uid, None, body)

    @router.put("/partner/price-books/{bid}")
    def books_put(bid: str, body: dict = Body(...), authorization: str | None = Header(None),
                  td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        return _save_book(pid, uid, bid, body)

    def _save_book(pid, uid, bid, body):
        name = (body.get("name") or "").strip()[:60]
        plan = (body.get("plan_key") or "").strip()
        price = _money(body.get("price"), "price")
        cur_ = (body.get("currency") or "USD").strip().upper()
        interval = body.get("billing_interval") or "month"
        trial = int(body.get("trial_days") or 0)
        if len(name) < 2:
            raise HTTPException(400, "give the package a name")
        if not re.match(r"^[A-Z]{3}$", cur_):
            raise HTTPException(400, "currency must be a 3-letter code, e.g. USD")
        if interval not in ("month", "year"):
            raise HTTPException(400, "billing_interval must be month or year")
        if not 0 <= trial <= 90:
            raise HTTPException(400, "trial_days must be between 0 and 90")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM plans WHERE key = %s", (plan,))
            if not cur.fetchone():
                raise HTTPException(400, "no such TechDeva plan")
        with tenant_session(None, "partner", partner_id=pid) as cur:
            try:
                if bid:
                    cur.execute("""UPDATE partner_price_books SET name=%s, plan_key=%s, price=%s, currency=%s,
                                          billing_interval=%s, trial_days=%s,
                                          active=COALESCE(%s, active), updated_at=now()
                                    WHERE id::text = %s RETURNING id""",
                                (name, plan, price, cur_, interval, trial, body.get("active"), bid))
                else:
                    cur.execute("""INSERT INTO partner_price_books (partner_id, plan_key, name, price, currency,
                                                                   billing_interval, trial_days)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                                (pid, plan, name, price, cur_, interval, trial))
            except Exception as e:
                if "partner_price_books_name" in str(e):
                    raise HTTPException(409, "you already have a package with that name")
                raise
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "no such package")
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="partner.price_book", resource="partner", resource_id=pid, actor=uid,
                   detail={"package": name, "plan": plan, "price": str(price), "currency": cur_})
        return {"id": str(row["id"])}

    # ------------------------------------------------------------------ coupons
    @router.get("/partner/coupons")
    def coupons_get(authorization: str | None = Header(None),
                    td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("SELECT * FROM partner_coupons ORDER BY created_at DESC")
            return {"coupons": _j([dict(r) for r in cur.fetchall()])}

    @router.post("/partner/coupons")
    def coupons_post(body: dict = Body(...), authorization: str | None = Header(None),
                     td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        code = (body.get("code") or "").strip().upper()
        pct = _money(body.get("percent_off"), "percent_off", allow_none=True)
        amt = _money(body.get("amount_off"), "amount_off", allow_none=True)
        if not re.match(r"^[A-Z0-9][A-Z0-9_-]{2,30}$", code):
            raise HTTPException(400, "code: 3-31 letters, numbers, - or _")
        if (pct is None) == (amt is None):
            raise HTTPException(400, "give either percent_off or amount_off")
        if pct is not None and not 0 < pct <= 100:
            raise HTTPException(400, "percent_off must be between 0 and 100")
        with tenant_session(None, "partner", partner_id=pid) as cur:
            try:
                cur.execute("""INSERT INTO partner_coupons (partner_id, code, percent_off, amount_off,
                                                            max_uses, expires_at)
                               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (pid, code, pct, amt, body.get("max_uses") or None, body.get("expires_at") or None))
            except Exception as e:
                if "partner_coupons_code" in str(e):
                    raise HTTPException(409, "you already have that coupon code")
                raise
            cid = str(cur.fetchone()["id"])
        with tenant_session(None, "techdeva_admin") as cur:
            record(cur, action="partner.coupon", resource="partner", resource_id=pid, actor=uid,
                   detail={"code": code})
        return {"id": cid, "code": code}

    @router.post("/partner/coupons/{cid}/deactivate")
    def coupons_off(cid: str, authorization: str | None = Header(None),
                    td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner, owner=True)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("UPDATE partner_coupons SET active = false WHERE id::text = %s RETURNING id", (cid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such coupon")
        return {"id": cid, "active": False}

    # ------------------------------------------------------------ signup links
    @router.get("/partner/signup-links")
    def links_get(authorization: str | None = Header(None),
                  td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("SELECT slug FROM partners")
            slug = cur.fetchone()["slug"]
            cur.execute("SELECT * FROM partner_signup_links ORDER BY created_at")
            links = [dict(r) for r in cur.fetchall()]
        for ln in links:
            ln["url"] = "%s/join/%s" % (eng.partner_base_url(slug), ln["token"])
        return {"links": _j(links)}

    @router.post("/partner/signup-links")
    def links_post(body: dict = Body(default={}), authorization: str | None = Header(None),
                   td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        book = body.get("price_book_id") or None
        with tenant_session(None, "partner", partner_id=pid) as cur:
            if book:
                cur.execute("SELECT 1 FROM partner_price_books WHERE id::text = %s AND active", (book,))
                if not cur.fetchone():
                    raise HTTPException(400, "no such active package")
            tok = eng.new_token()
            cur.execute("""INSERT INTO partner_signup_links (partner_id, token, label, price_book_id, max_uses,
                                                             expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (pid, tok, (body.get("label") or "").strip()[:80], book,
                         body.get("max_uses") or None, body.get("expires_at") or None))
            lid = str(cur.fetchone()["id"])
            cur.execute("SELECT slug FROM partners")
            slug = cur.fetchone()["slug"]
        return {"id": lid, "token": tok, "url": "%s/join/%s" % (eng.partner_base_url(slug), tok)}

    @router.post("/partner/signup-links/{lid}/deactivate")
    def links_off(lid: str, authorization: str | None = Header(None),
                  td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner, owner=True)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("UPDATE partner_signup_links SET active = false WHERE id::text = %s RETURNING id", (lid,))
            if not cur.fetchone():
                raise HTTPException(404, "no such link")
        return {"id": lid, "active": False}

    # ------------------------------------------------------------------ clients
    @router.post("/partner/clients")
    def client_create(body: dict = Body(...), authorization: str | None = Header(None),
                      td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """Module 5, method A: the partner adds a business; it is created end
        to end, and its owner gets a branded invitation to choose a password."""
        pid, uid, _r = _partner(authorization, td_partner)
        try:
            return eng.provision_client(
                P, pid, business_name=body.get("business_name") or "", industry=body.get("industry") or "",
                contact_name=body.get("contact_name") or "", contact_email=body.get("contact_email") or "",
                contact_phone=body.get("contact_phone") or "", price_book_id=body.get("price_book_id"),
                coupon_code=body.get("coupon_code"), actor="partner:" + uid)
        except eng.ClientError as e:
            raise HTTPException(e.status, e.message)

    @router.get("/partner/clients/{tid}")
    def client_get(tid: str, authorization: str | None = Header(None),
                   td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """One client: its status and subscription. Not its leads, chats or
        bookings -- those are the business's, not the agency's."""
        pid, _u, _r = _partner(authorization, td_partner)
        t = _own_tenant(pid, tid)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT plan_key, list_price, discount, price, currency, billing_interval, status,
                                  trial_ends_at, current_period_end, created_at
                             FROM partner_client_subscriptions WHERE tenant_id::text = %s""", (tid,))
            s = cur.fetchone()
            cur.execute("""SELECT occurred_on, kind, gross, partner_amount, currency FROM partner_ledger
                            WHERE tenant_id::text = %s ORDER BY occurred_on DESC LIMIT 24""", (tid,))
            pays = [dict(r) for r in cur.fetchall()]
        return _j({"business": dict(t), "subscription": dict(s) if s else None, "payments": pays})

    @router.post("/partner/clients/{tid}/cancel")
    def client_cancel(tid: str, authorization: str | None = Header(None),
                      td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        """The partner ends a client's subscription. The business is not deleted:
        the platform's own trial/retention rules decide what happens to its data."""
        pid, uid, _r = _partner(authorization, td_partner, owner=True)
        _own_tenant(pid, tid)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE partner_client_subscriptions SET status = 'cancelled', updated_at = now()
                            WHERE tenant_id::text = %s AND partner_id = %s AND status <> 'cancelled'
                            RETURNING id""", (tid, pid))
            if not cur.fetchone():
                raise HTTPException(409, "already cancelled, or no subscription")
            record(cur, action="partner.client.cancelled", resource="tenant", resource_id=tid, actor=uid,
                   tenant_id=tid, detail={"partner_id": pid})
        return {"tenant_id": tid, "subscription": "cancelled"}

    # -------------------------------------------------------------------- money
    @router.get("/partner/ledger")
    def ledger_partner(authorization: str | None = Header(None),
                       td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT id, tenant_id, kind, gross, gateway_fee, net, partner_share_pct,
                                  partner_amount, techdeva_amount, currency, floor_applied, needs_review,
                                  review_reason, payout_id, occurred_on
                             FROM partner_ledger ORDER BY occurred_on DESC, created_at DESC LIMIT 500""")
            return {"entries": _j([dict(r) for r in cur.fetchall()])}

    @router.get("/partner/payouts")
    def payouts_partner(authorization: str | None = Header(None),
                        td_partner: str | None = Cookie(None, alias=PARTNER_COOKIE)):
        pid, _u, _r = _partner(authorization, td_partner)
        with tenant_session(None, "partner", partner_id=pid) as cur:
            cur.execute("""SELECT id, amount, currency, status, method, reference, failure_reason,
                                  created_at, paid_at FROM partner_payouts ORDER BY created_at DESC""")
            return {"payouts": _j([dict(r) for r in cur.fetchall()])}

    return router
