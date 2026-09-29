"""Plan & billing: the client's side of paying TechDeva.

The founder already records fees, invoices and payments (founder.py). This is
what the client sees of that: their trial, their agreed monthly fee, the
invoices TechDeva has SENT them, how to pay, and an "I have paid" button.

A client's "I have paid" is a claim, not a payment. It becomes a payment only
when the founder confirms it against the bank / JazzCash / Easypaisa record.
No card gateway: none of the usual ones serve Pakistan without a merchant
account, and that account (and its keys) is TechDeva's decision to open.

Owner-only on the client side: the business's bill is not staff business.
Prices are never set here; they come from what the founder recorded.
"""
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session
from . import trial

CLIENT_SETTINGS = ("payment_instructions",)
METHODS = ("bank", "jazzcash", "easypaisa", "cash", "other")


def _scope(x_tenant_id, x_role, authorization, session):
    try:
        return resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)


def _owner(x_tenant_id, x_role, authorization, session):
    tid, role, uid = _scope(x_tenant_id, x_role, authorization, session)
    if not tid:
        raise HTTPException(400, "tenant required")
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can see the plan and bills.")
    return tid, role, uid


def _admin(x_tenant_id, x_role, authorization, session):
    _tid, role, uid = _scope(x_tenant_id, x_role, authorization, session)
    if role != "techdeva_admin":
        raise HTTPException(403, "admin only")
    return uid


def _setting(cur, key):
    cur.execute("SELECT value FROM platform_settings WHERE key = %s", (key,))
    r = cur.fetchone()
    return r["value"] if r else ""


def make_billing_router():
    router = APIRouter()

    # ----------------------------------------------------------- client side
    @router.get("/billing")
    def summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        # client_invoices / client_accounts / platform_settings are admin-only
        # tables; every query below is pinned to this one tenant by id.
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT t.status::text AS status, t.trial_ends_at,
                                  GREATEST(0, CEIL(EXTRACT(EPOCH FROM (t.trial_ends_at - now())) / 86400))::int AS days_left,
                                  COALESCE(a.monthly_fee, 0) AS monthly_fee, COALESCE(a.currency, 'PKR') AS currency,
                                  a.next_invoice_on
                             FROM tenants t LEFT JOIN client_accounts a ON a.tenant_id = t.id
                            WHERE t.id = %s""", (tid,))
            t = cur.fetchone()
            if not t:
                raise HTTPException(404, "no such business")
            cur.execute("""SELECT id, amount, currency, status, description, issued_on, due_on, paid_at
                             FROM client_invoices WHERE tenant_id = %s AND status IN ('sent','paid')
                            ORDER BY issued_on DESC, created_at DESC LIMIT 24""", (tid,))
            invoices = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT id, invoice_id, amount, method, reference, status, reason, created_at, decided_at
                             FROM payment_claims WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 24""", (tid,))
            claims = [dict(r) for r in cur.fetchall()]
            how = _setting(cur, "payment_instructions")
        t = dict(t)
        t["monthly_fee"] = float(t["monthly_fee"])
        for i in invoices:
            i["amount"] = float(i["amount"])
            i["claim"] = next((c["status"] for c in claims if c["invoice_id"] == i["id"]), None)
        for c in claims:
            c["amount"] = float(c["amount"])
        t["locked"] = trial.locked(tid)
        t["trial_days"] = trial.TRIAL_DAYS
        return {"account": t, "invoices": invoices, "claims": claims,
                "payment_instructions": how,
                "due": sum(i["amount"] for i in invoices if i["status"] == "sent")}

    @router.post("/billing/invoices/{invoice_id}/paid")
    def claim(invoice_id: str, body: dict = Body(...),
              x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        method = str(body.get("method") or "").strip().lower()
        if method not in METHODS:
            raise HTTPException(400, "Say how you paid: bank, JazzCash, Easypaisa, cash or other.")
        ref = str(body.get("reference") or "").strip()[:120]
        if method != "cash" and len(ref) < 3:
            raise HTTPException(400, "Add the transaction ID or reference so TechDeva can find the payment.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT amount, status FROM client_invoices WHERE id = %s AND tenant_id = %s",
                        (invoice_id, tid))
            inv = cur.fetchone()
        if not inv:
            raise HTTPException(404, "That bill is not yours or does not exist.")
        if inv["status"] != "sent":
            raise HTTPException(409, "That bill is not waiting for payment.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM payment_claims WHERE invoice_id = %s AND status = 'pending'", (invoice_id,))
            if cur.fetchone():
                raise HTTPException(409, "You already told us about this payment. TechDeva is checking it.")
            cur.execute("""INSERT INTO payment_claims (tenant_id, invoice_id, amount, method, reference, note, claimed_by)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (invoice_id, inv["amount"], method, ref,
                         str(body.get("note") or "").strip()[:500], str(uid) if uid else None))
            cid = cur.fetchone()["id"]
            record(cur, action="billing.claim", resource="payment_claims", resource_id=str(cid),
                   actor=str(uid), status="ok", detail={"method": method, "amount": float(inv["amount"])})
        return {"ok": True, "id": str(cid), "status": "pending"}

    @router.post("/billing/paid")
    def claim_open(body: dict = Body(...),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """'I have paid' with no bill yet: the trial ended and the owner paid the
        monthly fee straight away. Still a claim until the founder confirms it."""
        tid, role, uid = _owner(x_tenant_id, x_role, authorization, td_session)
        method = str(body.get("method") or "").strip().lower()
        if method not in METHODS:
            raise HTTPException(400, "Say how you paid: bank, JazzCash, Easypaisa, cash or other.")
        ref = str(body.get("reference") or "").strip()[:120]
        if method != "cash" and len(ref) < 3:
            raise HTTPException(400, "Add the transaction ID or reference so TechDeva can find the payment.")
        try:
            amount = float(str(body.get("amount") or "0").replace(",", ""))
        except ValueError:
            amount = 0
        if amount <= 0:
            raise HTTPException(400, "Enter the amount you paid.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM payment_claims WHERE invoice_id IS NULL AND status = 'pending'")
            if cur.fetchone():
                raise HTTPException(409, "You already told us about a payment. TechDeva is checking it.")
            cur.execute("""INSERT INTO payment_claims (tenant_id, amount, method, reference, note, claimed_by)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s) RETURNING id""",
                        (amount, method, ref, str(body.get("note") or "").strip()[:500], str(uid) if uid else None))
            cid = cur.fetchone()["id"]
            record(cur, action="billing.claim", resource="payment_claims", resource_id=str(cid),
                   actor=str(uid), status="ok", detail={"method": method, "amount": amount, "invoice": None})
        return {"ok": True, "id": str(cid), "status": "pending"}

    # ---------------------------------------------------------- founder side
    @router.get("/techdeva/payment-claims")
    def claims(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT c.id, c.tenant_id, t.name AS business, c.invoice_id, c.amount, c.method,
                                  c.reference, c.note, c.status, c.reason, c.created_at, c.decided_at,
                                  i.description AS invoice
                             FROM payment_claims c JOIN tenants t ON t.id = c.tenant_id
                             LEFT JOIN client_invoices i ON i.id = c.invoice_id
                            ORDER BY (c.status = 'pending') DESC, c.created_at DESC LIMIT 40""")
            rows = [dict(r) for r in cur.fetchall()]
            how = _setting(cur, "payment_instructions")
        for r in rows:
            r["amount"] = float(r["amount"])
        return {"claims": rows, "payment_instructions": how}

    def _decide(cur, claim_id, uid, ok, reason=None):
        cur.execute("""UPDATE payment_claims SET status = %s, decided_by = %s, decided_at = now(), reason = %s
                        WHERE id = %s AND status = 'pending'
                        RETURNING tenant_id, invoice_id, amount, method, reference""",
                    ("confirmed" if ok else "rejected", str(uid), reason, claim_id))
        c = cur.fetchone()
        if not c:
            raise HTTPException(409, "That claim is not waiting for a decision.")
        return c

    @router.post("/techdeva/payment-claims/{claim_id}/confirm")
    def confirm(claim_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The money arrived: write the payment, mark the bill paid, and a
        client still on trial becomes active."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            c = _decide(cur, claim_id, uid, True)
            cur.execute("""INSERT INTO client_payments (tenant_id, amount, method, reference, notes)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (c["tenant_id"], c["amount"], c["method"], c["reference"],
                         "confirmed from the client's payment claim"))
            pid = cur.fetchone()["id"]
            if c["invoice_id"]:
                cur.execute("UPDATE client_invoices SET status = 'paid', paid_at = now(), updated_at = now() "
                            "WHERE id = %s AND status = 'sent'", (c["invoice_id"],))
            cur.execute("UPDATE tenants SET status = 'active' WHERE id = %s AND status = 'trial'", (c["tenant_id"],))
            trial.forget(c["tenant_id"])
            record(cur, action="billing.claim_confirmed", resource="payment_claims", resource_id=claim_id,
                   actor=uid, status="ok", detail={"payment_id": str(pid), "amount": float(c["amount"])},
                   tenant_id=c["tenant_id"])
        return {"ok": True, "payment_id": str(pid)}

    @router.post("/techdeva/payment-claims/{claim_id}/reject")
    def reject(claim_id: str, body: dict = Body(default={}),
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        reason = str(body.get("reason") or "").strip()[:300] or "TechDeva could not find this payment."
        with tenant_session(None, "techdeva_admin") as cur:
            c = _decide(cur, claim_id, uid, False, reason)
            record(cur, action="billing.claim_rejected", resource="payment_claims", resource_id=claim_id,
                   actor=uid, status="ok", detail={"reason": reason}, tenant_id=c["tenant_id"])
        return {"ok": True}

    @router.post("/techdeva/invoices/{invoice_id}/paid")
    def invoice_paid(invoice_id: str, body: dict = Body(default={}),
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Money arrived without a claim (the client paid and said nothing)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        method = str(body.get("method") or "bank").strip().lower()[:40]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE client_invoices SET status = 'paid', paid_at = now(), updated_at = now() "
                        "WHERE id = %s AND status = 'sent' RETURNING tenant_id, amount", (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(409, "Only a sent invoice can be marked paid.")
            cur.execute("""INSERT INTO client_payments (tenant_id, amount, method, reference, notes)
                           VALUES (%s, %s, %s, %s, 'invoice marked paid') RETURNING id""",
                        (inv["tenant_id"], inv["amount"], method, str(body.get("reference") or "")[:200]))
            pid = cur.fetchone()["id"]
            cur.execute("UPDATE payment_claims SET status = 'confirmed', decided_by = %s, decided_at = now() "
                        "WHERE invoice_id = %s AND status = 'pending'", (str(uid), invoice_id))
            cur.execute("UPDATE tenants SET status = 'active' WHERE id = %s AND status = 'trial'", (inv["tenant_id"],))
            trial.forget(inv["tenant_id"])
            record(cur, action="techdeva.invoice.paid", resource="client_invoices", resource_id=invoice_id,
                   actor=uid, status="ok", detail={"payment_id": str(pid)}, tenant_id=inv["tenant_id"])
        return {"ok": True, "payment_id": str(pid)}

    @router.post("/techdeva/clients/{tenant_id}/trial")
    def extend_trial(tenant_id: str, body: dict = Body(default={}),
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Give a business more trial days (it unlocks at once if it was locked)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        try:
            days = int(body.get("days") or 7)
        except (TypeError, ValueError):
            days = 0
        if not 1 <= days <= 60:
            raise HTTPException(400, "Extend by 1 to 60 days.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE tenants SET trial_ends_at = GREATEST(now(), COALESCE(trial_ends_at, now()))
                                                + make_interval(days => %s)
                            WHERE id = %s AND status = 'trial' RETURNING trial_ends_at""", (days, tenant_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "Only a business on trial can get more trial days.")
            record(cur, action="techdeva.trial.extend", resource="tenants", resource_id=tenant_id,
                   actor=uid, status="ok", detail={"days": days}, tenant_id=tenant_id)
        trial.forget(tenant_id)
        return {"ok": True, "trial_ends_at": row["trial_ends_at"]}

    @router.post("/techdeva/clients/{tenant_id}/activate")
    def activate(tenant_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Mark a business as a paying client by hand (never locked by the trial)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE tenants SET status = 'active' WHERE id = %s AND status <> 'active' RETURNING id",
                        (tenant_id,))
            if not cur.fetchone():
                raise HTTPException(409, "That business is already active (or does not exist).")
            record(cur, action="techdeva.client.activate", resource="tenants", resource_id=tenant_id,
                   actor=uid, status="ok", detail={}, tenant_id=tenant_id)
        trial.forget(tenant_id)
        return {"ok": True}

    @router.put("/techdeva/settings/{key}")
    def put_setting(key: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        if key not in CLIENT_SETTINGS:
            raise HTTPException(404, "unknown setting")
        value = str(body.get("value") or "").strip()[:2000]
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO platform_settings (key, value) VALUES (%s, %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                        (key, value))
            record(cur, action="techdeva.setting", resource="platform_settings", resource_id=key,
                   actor=uid, status="ok", detail={"length": len(value)})
        return {"ok": True}

    return router
