"""The white-label partner network's machinery (db/87, db/88).

Three jobs, none of them a web route (those are in app/partner_network.py):

1. MONEY. Every payment a partner's client makes to TechDeva is split by the
   partner's terms: standard 55% partner / 45% TechDeva, negotiated per partner,
   never hard-coded. The split is frozen into partner_ledger at the moment it
   happens, so a later renegotiation never rewrites last month. Refunds and
   chargebacks are new negative rows, never edits.

2. PARTNER SET-UP. Approving an agency starts a provisioning run: terms,
   branding, price books, a self-registration link, the owner's login, the
   website. Each step is idempotent, records its attempts and its last error,
   and is retried by the scheduler with a growing wait. One failing step never
   undoes the ones before it, and never runs twice.

3. CLIENT SET-UP. A partner's new business is created through the same
   provisioning every TechDeva signup uses (persistence.provision_tenant_from_signup),
   with partner_id written in the same INSERT.

Nothing here invents a number. A payment that cannot be split honestly -- no
terms on file, a floor in another currency -- is still recorded, at the standard
split, and marked needs_review with the reason, so it is neither lost nor paid
out until someone has looked at it.
"""
from __future__ import annotations

import os
import secrets
import time
from decimal import ROUND_HALF_UP, Decimal

from .audit import record
from .db import tenant_session

DEFAULT_SHARE_PCT = Decimal(os.environ.get("PARTNER_DEFAULT_SHARE_PCT", "55"))
CENT = Decimal("0.01")
FEE_BEARERS = ("shared", "partner", "techdeva")


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x if x is not None else 0))


def _r(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# ============================================================== 1. the split
def split(gross, gateway_fee=0, *, partner_share_pct=DEFAULT_SHARE_PCT, fee_bearer="shared",
          min_techdeva_amount=None, floor_currency=None, currency="USD", apply_floor=True,
          fixed_partner_ratio=None) -> dict:
    """Split one money event. Pure: no database, so every rule is testable.

    gross            what the client paid (negative for a refund or chargeback)
    gateway_fee      what the payment provider kept; always a cost (>= 0)
    fee_bearer       'shared'   -> the fee comes off before the split
                     'partner'  -> TechDeva's share is taken from the gross;
                                   the partner's share carries the whole fee
                     'techdeva' -> the partner's share is taken from the gross;
                                   TechDeva carries the whole fee
    min_techdeva_amount / floor_currency
                     TechDeva never takes less than this from one payment, so a
                     partner selling at a deep discount cannot make a client cost
                     TechDeva money. Applied only to payments, only in its own
                     currency. In another currency it is not guessed at: the row
                     is marked for review.
    fixed_partner_ratio
                     a reversal of a payment whose floor applied reverses at that
                     payment's actual ratio, so a refund returns exactly the
                     shares that were taken -- no more, no less.

    partner_amount + techdeva_amount == net, always, to the cent: TechDeva's
    share is computed as the remainder, never rounded independently.
    """
    gross, fee = _d(gross), _d(gateway_fee)
    pct = _d(partner_share_pct)
    if fee < 0:
        raise ValueError("gateway_fee cannot be negative")
    if not (Decimal(0) <= pct <= Decimal(100)):
        raise ValueError("partner_share_pct must be between 0 and 100")
    if fee_bearer not in FEE_BEARERS:
        raise ValueError("fee_bearer must be one of %s" % ", ".join(FEE_BEARERS))
    net = _r(gross - fee)
    share = pct / Decimal(100)
    review, reason, floor_applied = False, None, False

    if fixed_partner_ratio is not None:
        partner = _r(net * _d(fixed_partner_ratio))
    elif fee_bearer == "shared":
        partner = _r(net * share)
    elif fee_bearer == "partner":
        partner = _r(net - _r(gross * (1 - share)))
    else:  # techdeva
        partner = _r(gross * share)
    techdeva = _r(net - partner)

    if apply_floor and gross > 0 and min_techdeva_amount is not None and fixed_partner_ratio is None:
        floor = _d(min_techdeva_amount)
        if floor_currency and floor_currency != currency:
            review, reason = True, ("the minimum TechDeva share is set in %s and this payment is in %s; "
                                    "not converted automatically" % (floor_currency, currency))
        elif techdeva < floor:
            techdeva = min(_r(floor), net)
            partner = _r(net - techdeva)
            floor_applied = True

    if gross > 0 and partner < 0:
        review, reason = True, ("the gateway fee is larger than the partner's share of this payment "
                                "(fee_bearer = partner)")
    return {"gross": _r(gross), "gateway_fee": _r(fee), "net": net,
            "partner_share_pct": pct, "partner_amount": partner, "techdeva_amount": techdeva,
            "floor_applied": floor_applied, "needs_review": review, "review_reason": reason}


def terms_on(cur, partner_id, day):
    """The partner's terms in force on `day` (an admin cursor), or None."""
    cur.execute("""SELECT id, partner_share_pct, min_techdeva_amount, floor_currency, fee_bearer
                     FROM partner_revenue_terms
                    WHERE partner_id = %s AND effective_from <= %s
                    ORDER BY effective_from DESC LIMIT 1""", (str(partner_id), day))
    return cur.fetchone()


def _write_ledger(cur, *, partner_id, tenant_id, kind, source_id, related, s, currency, terms_id,
                  occurred_on, note, actor):
    cur.execute("""INSERT INTO partner_ledger
                     (partner_id, tenant_id, kind, source_id, related_ledger_id, gross, gateway_fee, net,
                      partner_share_pct, partner_amount, techdeva_amount, currency, terms_id,
                      floor_applied, needs_review, review_reason, occurred_on, note, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING
                   RETURNING id""",
                (str(partner_id), str(tenant_id) if tenant_id else None, kind,
                 str(source_id) if source_id else None, str(related) if related else None,
                 s["gross"], s["gateway_fee"], s["net"], s["partner_share_pct"],
                 s["partner_amount"], s["techdeva_amount"], currency,
                 str(terms_id) if terms_id else None, s["floor_applied"], s["needs_review"],
                 s["review_reason"], occurred_on, note or "", actor))
    row = cur.fetchone()
    return str(row["id"]) if row else None


def sync_ledger(limit=500) -> int:
    """Split every confirmed payment of a partner's business that is not split yet.

    Safe to run any number of times: the unique index on (source_id) for
    kind = 'payment' makes a second split of the same payment impossible.
    Only payments recorded after the business joined the partner count.
    """
    made = 0
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT cp.id, cp.tenant_id, cp.amount, cp.currency, cp.paid_on,
                              t.partner_id
                         FROM client_payments cp
                         JOIN tenants t ON t.id = cp.tenant_id
                        WHERE t.partner_id IS NOT NULL
                          AND cp.created_at >= t.partner_assigned_at
                          AND NOT EXISTS (SELECT 1 FROM partner_ledger l
                                           WHERE l.kind = 'payment' AND l.source_id = cp.id)
                        ORDER BY cp.created_at LIMIT %s""", (limit,))
        for p in cur.fetchall():
            t = terms_on(cur, p["partner_id"], p["paid_on"])
            if t:
                s = split(p["amount"], 0, partner_share_pct=t["partner_share_pct"],
                          fee_bearer=t["fee_bearer"], min_techdeva_amount=t["min_techdeva_amount"],
                          floor_currency=t["floor_currency"], currency=p["currency"])
            else:
                s = split(p["amount"], 0)
                s["needs_review"], s["review_reason"] = True, (
                    "no revenue terms on file for this partner on %s; the standard %s%% was used"
                    % (p["paid_on"], DEFAULT_SHARE_PCT))
            lid = _write_ledger(cur, partner_id=p["partner_id"], tenant_id=p["tenant_id"],
                                kind="payment", source_id=p["id"], related=None, s=s,
                                currency=p["currency"], terms_id=t["id"] if t else None,
                                occurred_on=p["paid_on"], note="", actor="partner_engine")
            if lid:
                made += 1
                record(cur, action="partner.ledger.payment", resource="partner_ledger",
                       resource_id=lid, actor="partner_engine", actor_type="system",
                       tenant_id=str(p["tenant_id"]),
                       detail={"payment_id": str(p["id"]), "gross": str(s["gross"]),
                               "partner_amount": str(s["partner_amount"]),
                               "techdeva_amount": str(s["techdeva_amount"]),
                               "needs_review": s["needs_review"]})
    return made


def record_reversal(*, ledger_id, amount, kind="refund", gateway_fee=0, note="", actor=None) -> dict:
    """A refund or chargeback against one split payment: a new negative row.

    The reversal is split by the terms of the ORIGINAL payment (or its actual
    ratio when a floor applied), not today's terms, so a refund gives back the
    shares that were taken. Refunding more than was paid, less what was already
    refunded, is refused.
    """
    if kind not in ("refund", "chargeback"):
        raise ValueError("kind must be refund or chargeback")
    amount, fee = _d(amount), _d(gateway_fee)
    if amount <= 0:
        raise ValueError("amount must be positive (it is recorded as a negative row)")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT l.*, t.fee_bearer FROM partner_ledger l
                         LEFT JOIN partner_revenue_terms t ON t.id = l.terms_id
                        WHERE l.id = %s FOR UPDATE OF l""", (str(ledger_id),))
        orig = cur.fetchone()
        if not orig or orig["kind"] != "payment":
            raise LookupError("no such payment in the ledger")
        cur.execute("""SELECT COALESCE(-SUM(gross), 0) AS back FROM partner_ledger
                        WHERE related_ledger_id = %s AND kind IN ('refund','chargeback')""",
                    (str(ledger_id),))
        already = _d(cur.fetchone()["back"])
        if amount + already > _d(orig["gross"]):
            raise ValueError("refunding %s would exceed the payment of %s (already reversed: %s)"
                             % (amount, orig["gross"], already))
        ratio = None
        if orig["floor_applied"] and _d(orig["net"]) != 0:
            ratio = _d(orig["partner_amount"]) / _d(orig["net"])
        s = split(-amount, fee, partner_share_pct=orig["partner_share_pct"],
                  fee_bearer=orig["fee_bearer"] or "shared", apply_floor=False,
                  fixed_partner_ratio=ratio, currency=orig["currency"])
        lid = _write_ledger(cur, partner_id=orig["partner_id"], tenant_id=orig["tenant_id"], kind=kind,
                            source_id=None, related=ledger_id, s=s, currency=orig["currency"],
                            terms_id=orig["terms_id"], occurred_on=time.strftime("%Y-%m-%d"),
                            note=note, actor=actor)
        record(cur, action="partner.ledger.%s" % kind, resource="partner_ledger", resource_id=lid,
               actor=actor, tenant_id=str(orig["tenant_id"]) if orig["tenant_id"] else None,
               detail={"against": str(ledger_id), "amount": str(amount), "fee": str(fee),
                       "partner_amount": str(s["partner_amount"]),
                       "techdeva_amount": str(s["techdeva_amount"])})
    return {"id": lid, **{k: str(v) if isinstance(v, Decimal) else v for k, v in s.items()}}


# ============================================================== payouts
def create_payouts(actor="partner_engine", partner_id=None) -> list[dict]:
    """Gather each partner's unpaid, reviewed earnings into a payout to send.

    One payout per (partner, currency). Rows marked needs_review are left out
    until a person clears them. A payout is only created when the balance is
    positive: a partner whose refunds exceed its earnings is not "paid" a
    negative amount, and those rows wait for the next positive balance.
    """
    made = []
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT l.partner_id, l.currency, SUM(l.partner_amount) AS due
                         FROM partner_ledger l JOIN partners p ON p.id = l.partner_id
                        WHERE l.payout_id IS NULL AND NOT l.needs_review
                          AND p.status IN ('active','suspended')
                          AND (%s::uuid IS NULL OR l.partner_id = %s::uuid)
                        GROUP BY l.partner_id, l.currency
                       HAVING SUM(l.partner_amount) > 0""",
                    (str(partner_id) if partner_id else None, str(partner_id) if partner_id else None))
        for g in cur.fetchall():
            cur.execute("""INSERT INTO partner_payouts (partner_id, amount, currency, created_by)
                           VALUES (%s,%s,%s,%s) RETURNING id""",
                        (str(g["partner_id"]), _r(_d(g["due"])), g["currency"], actor))
            pid = str(cur.fetchone()["id"])
            cur.execute("""UPDATE partner_ledger SET payout_id = %s
                            WHERE partner_id = %s AND currency = %s AND payout_id IS NULL
                              AND NOT needs_review""", (pid, str(g["partner_id"]), g["currency"]))
            record(cur, action="partner.payout.created", resource="partner_payout", resource_id=pid,
                   actor=actor, actor_type="system" if actor == "partner_engine" else "human",
                   detail={"partner_id": str(g["partner_id"]), "amount": str(_r(_d(g["due"]))),
                           "currency": g["currency"]})
            made.append({"id": pid, "partner_id": str(g["partner_id"]),
                         "amount": str(_r(_d(g["due"]))), "currency": g["currency"]})
    return made


def mark_payout(payout_id, status, *, reference="", reason=None, actor=None) -> dict:
    """Record what happened to a payout. A failed or cancelled payout releases its
    ledger rows, so they are gathered into the next one instead of being lost."""
    if status not in ("paid", "failed", "cancelled"):
        raise ValueError("status must be paid, failed or cancelled")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id, status, partner_id FROM partner_payouts WHERE id = %s FOR UPDATE",
                    (str(payout_id),))
        p = cur.fetchone()
        if not p:
            raise LookupError("no such payout")
        if p["status"] != "pending":
            raise ValueError("this payout is already %s" % p["status"])
        cur.execute("""UPDATE partner_payouts
                          SET status = %s, reference = %s, failure_reason = %s, updated_at = now(),
                              paid_at = CASE WHEN %s = 'paid' THEN now() END
                        WHERE id = %s""", (status, reference or "", reason, status, str(payout_id)))
        if status != "paid":
            cur.execute("UPDATE partner_ledger SET payout_id = NULL WHERE payout_id = %s", (str(payout_id),))
        record(cur, action="partner.payout.%s" % status, resource="partner_payout",
               resource_id=str(payout_id), actor=actor,
               detail={"partner_id": str(p["partner_id"]), "reference": reference, "reason": reason})
    return {"id": str(payout_id), "status": status}


# ===================================================== 2. partner provisioning
STEPS = ("revenue_terms", "branding", "price_books", "signup_link", "owner_login", "website")
MAX_ATTEMPTS = 6


def new_token() -> str:
    return secrets.token_urlsafe(24)


def start_provisioning(cur, partner_id) -> str:
    """Open (or reuse) the partner's set-up run. `cur` is an admin cursor."""
    cur.execute("""SELECT id FROM partner_provisioning_runs
                    WHERE partner_id = %s AND status <> 'done'""", (str(partner_id),))
    r = cur.fetchone()
    if r:
        run_id = str(r["id"])
        cur.execute("""UPDATE partner_provisioning_runs SET status = 'pending', next_try_at = now(),
                              updated_at = now() WHERE id = %s""", (run_id,))
    else:
        cur.execute("""INSERT INTO partner_provisioning_runs (partner_id) VALUES (%s) RETURNING id""",
                    (str(partner_id),))
        run_id = str(cur.fetchone()["id"])
    for i, step in enumerate(STEPS):
        cur.execute("""INSERT INTO partner_provisioning_steps (run_id, partner_id, step, position)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (run_id, str(partner_id), step, i))
    return run_id


def _step_revenue_terms(cur, p):
    cur.execute("SELECT 1 FROM partner_revenue_terms WHERE partner_id = %s", (p["id"],))
    if not cur.fetchone():
        cur.execute("""INSERT INTO partner_revenue_terms (partner_id, partner_share_pct, notes, created_by)
                       VALUES (%s,%s,'standard terms, set at approval','partner_engine')""",
                    (p["id"], DEFAULT_SHARE_PCT))
    return {"partner_share_pct": str(DEFAULT_SHARE_PCT)}


def _step_branding(cur, p):
    cur.execute("""INSERT INTO partner_branding (partner_id, brand_name, support_email, support_phone)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (partner_id) DO NOTHING""",
                (p["id"], p["name"][:80], p["contact_email"] or "", p["contact_phone"] or ""))
    return {"brand_name": p["name"][:80]}


def _step_price_books(cur, p):
    """A ready-to-sell price list: one entry per TechDeva plan that has a USD
    price, at that price. The partner changes them; this only means the partner
    can sell on day one."""
    cur.execute("SELECT count(*) AS n FROM partner_price_books WHERE partner_id = %s", (p["id"],))
    if cur.fetchone()["n"]:
        return {"created": 0}
    cur.execute("SELECT key, name, price_usd FROM plans WHERE price_usd IS NOT NULL ORDER BY sort")
    made = 0
    for pl in cur.fetchall():
        cur.execute("""INSERT INTO partner_price_books (partner_id, plan_key, name, price, currency, trial_days)
                       VALUES (%s,%s,%s,%s,'USD',7) ON CONFLICT DO NOTHING""",
                    (p["id"], pl["key"], pl["name"], pl["price_usd"]))
        made += 1
    return {"created": made}


def _step_signup_link(cur, p):
    cur.execute("SELECT token FROM partner_signup_links WHERE partner_id = %s AND active LIMIT 1", (p["id"],))
    r = cur.fetchone()
    if r:
        return {"token": r["token"]}
    tok = new_token()
    cur.execute("""INSERT INTO partner_signup_links (partner_id, token, label)
                   VALUES (%s,%s,'Main registration link')""", (p["id"], tok))
    return {"token": tok}


def _step_owner_login(cur, p):
    """The agency's first login, from the contact email it applied with. Its
    password is chosen through a one-time link -- emailed when mail is set up;
    otherwise the founder hands it over from the console (never stored)."""
    email = (p["contact_email"] or "").strip().lower()
    if "@" not in email:
        return {"skipped": "no contact email on the application"}
    cur.execute("SELECT id, password_hash FROM partner_users WHERE lower(btrim(email)) = %s", (email,))
    u = cur.fetchone()
    if not u:
        cur.execute("""INSERT INTO partner_users (partner_id, email, name, role)
                       VALUES (%s,%s,%s,'partner_owner') RETURNING id, password_hash""",
                    (p["id"], email, (p["contact_name"] or "")[:120]))
        u = cur.fetchone()
    if u["password_hash"]:
        return {"user_id": str(u["id"]), "invite": "already has a password"}
    sent = send_partner_invite(p, email)
    return {"user_id": str(u["id"]), "invite_emailed": sent}


def _step_website(cur, p):
    cur.execute("UPDATE partner_branding SET website_published = true, updated_at = now() WHERE partner_id = %s",
                (p["id"],))
    return {"path": "/p/%s" % p["slug"]}


_STEP_FN = {"revenue_terms": _step_revenue_terms, "branding": _step_branding,
            "price_books": _step_price_books, "signup_link": _step_signup_link,
            "owner_login": _step_owner_login, "website": _step_website}


def run_provisioning(run_id) -> str:
    """Run every unfinished step of one set-up, in order, each in its own
    transaction. A failing step stops the run (later steps may depend on it),
    records its error and schedules a retry with a growing wait."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""UPDATE partner_provisioning_runs SET status = 'running', attempts = attempts + 1,
                              updated_at = now()
                        WHERE id = %s AND status IN ('pending','failed') RETURNING partner_id, attempts""",
                    (str(run_id),))
        r = cur.fetchone()
    if not r:
        return "not_due"
    pid, attempts = str(r["partner_id"]), r["attempts"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT step FROM partner_provisioning_steps WHERE run_id = %s AND status <> 'done' "
                    "ORDER BY position", (str(run_id),))
        todo = [x["step"] for x in cur.fetchall()]
    for step in todo:
        try:
            with tenant_session(None, "techdeva_admin") as cur:
                cur.execute("SELECT * FROM partners WHERE id = %s", (pid,))
                p = cur.fetchone()
                p = {**p, "id": str(p["id"])}
                if p["status"] != "active":
                    raise RuntimeError("partner is %s, not active" % p["status"])
                detail = _STEP_FN[step](cur, p)
                import json as _json
                cur.execute("""UPDATE partner_provisioning_steps
                                  SET status = 'done', attempts = attempts + 1, last_error = NULL,
                                      detail = %s::jsonb, done_at = now()
                                WHERE run_id = %s AND step = %s""",
                            (_json.dumps(detail, default=str), str(run_id), step))
        except Exception as e:  # noqa: BLE001 -- recorded, retried, never swallowed silently
            msg = ("%s: %s" % (type(e).__name__, e))[:500]
            wait = min(60 * (2 ** attempts), 6 * 3600)
            final = attempts >= MAX_ATTEMPTS
            with tenant_session(None, "techdeva_admin") as cur:
                cur.execute("""UPDATE partner_provisioning_steps SET status = 'failed',
                                      attempts = attempts + 1, last_error = %s
                                WHERE run_id = %s AND step = %s""", (msg, str(run_id), step))
                cur.execute("""UPDATE partner_provisioning_runs
                                  SET status = 'failed', last_error = %s, updated_at = now(),
                                      next_try_at = CASE WHEN %s THEN 'infinity'::timestamptz
                                                         ELSE now() + make_interval(secs => %s) END
                                WHERE id = %s""", (step + " -> " + msg, final, wait, str(run_id)))
                record(cur, action="partner.provisioning.failed", resource="partner", resource_id=pid,
                       actor="partner_engine", actor_type="system", status="error",
                       detail={"step": step, "attempt": attempts, "gave_up": final}, error=msg)
            print("[partners] set-up of %s stopped at %s: %s" % (pid, step, msg), flush=True)
            return "failed"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""UPDATE partner_provisioning_runs SET status = 'done', last_error = NULL,
                              finished_at = now(), updated_at = now() WHERE id = %s""", (str(run_id),))
        record(cur, action="partner.provisioning.done", resource="partner", resource_id=pid,
               actor="partner_engine", actor_type="system")
    return "done"


# ======================================================== branded emails
def _branding(cur, partner_id):
    cur.execute("""SELECT b.brand_name, b.support_email, p.slug, p.name
                     FROM partners p LEFT JOIN partner_branding b ON b.partner_id = p.id
                    WHERE p.id = %s""", (str(partner_id),))
    return cur.fetchone()


def partner_base_url(slug) -> str:
    base = os.environ.get("PARTNER_SITE_BASE") or os.environ.get("OAUTH_REDIRECT_BASE") or "https://api.techdeva.net"
    return "%s/p/%s" % (base.rstrip("/"), slug)


def send_partner_invite(p, email) -> bool:
    """TechDeva -> the agency (the agency is TechDeva's own customer, so this
    one is TechDeva-branded on purpose)."""
    from .account import mail_configured, send_mail
    from .auth import issue_setup_token
    if not mail_configured():
        return False
    tok = issue_setup_token(email, None, ttl=72 * 3600)
    link = "%s/partner/welcome?token=%s" % (partner_base_url(p["slug"]).rsplit("/p/", 1)[0], tok)
    try:
        send_mail(email, "Your TechDeva partner account is ready",
                  "Hello %s,\n\nYour partner account for %s is approved and set up.\n"
                  "Choose your password here (the link works for 72 hours):\n%s\n\n"
                  "Your public website: %s\n\nTechDeva" % (
                      p.get("contact_name") or "", p["name"], link, partner_base_url(p["slug"])))
        return True
    except Exception as e:  # noqa: BLE001
        print("[partners] invite not sent: %s" % type(e).__name__, flush=True)
        return False


def send_client_invite(partner_id, email, business, token) -> bool:
    """Partner -> its client: the partner's brand as the sender's name, replies
    to the partner's support address, and no TechDeva name in the text."""
    from .account import mail_configured, send_mail
    if not mail_configured():
        return False
    with tenant_session(None, "techdeva_admin") as cur:
        b = _branding(cur, partner_id)
    brand = (b["brand_name"] or b["name"]) if b else "Your provider"
    link = "%s/welcome?token=%s" % (partner_base_url(b["slug"]), token)
    try:
        send_mail(email, "Your %s account for %s" % (brand, business),
                  "Hello,\n\nYour %s workspace for %s is ready.\n"
                  "Choose your password here (the link works for 72 hours):\n%s\n\n%s" % (
                      brand, business, link, brand),
                  from_name=brand, reply_to=(b["support_email"] or None) if b else None)
        return True
    except Exception as e:  # noqa: BLE001
        print("[partners] client invite not sent: %s" % type(e).__name__, flush=True)
        return False


def send_client_code(partner_id, user) -> dict:
    """The sign-in verification code for a partner's client, under the
    partner's brand. The platform's own code email (app/email_codes.py) says
    TechDeva, which a partner's client must never see. Same code store, same
    checks (/auth/verify-email verifies it; its answers never name TechDeva)."""
    from .account import mail_configured, send_mail
    from . import email_codes as ec
    if not mail_configured():
        return {"sent": False, "reason": "no_mail", "wait": 0}
    try:
        with tenant_session(None, "techdeva_admin") as cur:
            code = ec.issue(cur, user["id"])
            b = _branding(cur, partner_id)
    except ec.TooSoon as e:
        return {"sent": False, "reason": "too_soon", "wait": e.wait}
    brand = (b["brand_name"] or b["name"]) if b else "Your provider"
    try:
        send_mail(user["email"], "Your %s code: %s" % (brand, code),
                  "Hello %s,\n\nYour sign-in code for %s is: %s\n\nIt works for %d minutes. "
                  "If you did not ask for it, ignore this email.\n\n%s" % (
                      user.get("name") or "", brand, code, ec.TTL_MIN, brand),
                  from_name=brand, reply_to=(b["support_email"] or None) if b else None)
    except Exception as e:  # noqa: BLE001
        print("[partners] code email failed: %s" % type(e).__name__, flush=True)
        return {"sent": False, "reason": "failed", "wait": 0}
    return {"sent": True, "reason": None, "wait": ec.RESEND_GAP_S}


# ======================================================= 3. client set-up
class ClientError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def _price(cur, partner_id, price_book_id, coupon_code):
    """The price a new client starts on: (book, list, discount, price, coupon_id)."""
    if price_book_id:
        cur.execute("""SELECT * FROM partner_price_books WHERE id = %s AND partner_id = %s AND active""",
                    (str(price_book_id), str(partner_id)))
    else:
        cur.execute("""SELECT * FROM partner_price_books WHERE partner_id = %s AND active
                        ORDER BY price LIMIT 1""", (str(partner_id),))
    book = cur.fetchone()
    if not book:
        raise ClientError("no active price on this partner's price list", 400)
    listp, discount, coupon_id = _d(book["price"]), Decimal(0), None
    if coupon_code:
        cur.execute("""SELECT * FROM partner_coupons WHERE partner_id = %s AND code = %s AND active
                          AND (expires_at IS NULL OR expires_at > now())
                          AND (max_uses IS NULL OR uses < max_uses) FOR UPDATE""",
                    (str(partner_id), coupon_code.strip().upper()))
        c = cur.fetchone()
        if not c:
            raise ClientError("that coupon is not valid", 400)
        discount = _r(listp * _d(c["percent_off"]) / 100) if c["percent_off"] else min(_d(c["amount_off"]), listp)
        coupon_id = c["id"]
    return book, _r(listp), _r(discount), _r(max(listp - discount, Decimal(0))), coupon_id


def provision_client(P, partner_id, *, business_name, industry="", contact_name="", contact_email,
                     contact_phone="", password_hash=None, price_book_id=None, coupon_code=None,
                     signup_link_id=None, actor=None) -> dict:
    """Create a partner's client, end to end. Returns the tenant and what happened.

    The business itself is created by the platform's own signup provisioning,
    with partner_id in the same INSERT. Then the subscription and the client's
    account fee (so /billing shows what it really owes), its plan (so its limits
    are the plan's), and either its login (self-registration, password given) or
    a branded invitation to choose one (created by the partner).
    """
    email = (contact_email or "").strip().lower()
    if "@" not in email:
        raise ClientError("a real email address is required")
    if len((business_name or "").strip()) < 2:
        raise ClientError("the business needs a name")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT status FROM partners WHERE id = %s", (str(partner_id),))
        p = cur.fetchone()
        if not p or p["status"] != "active":
            raise ClientError("this partner is not active", 403)
        if P.find_user_by_email(email):
            raise ClientError("that email is already registered", 409)
        _price(cur, partner_id, price_book_id, coupon_code)          # validate before creating anything

    signup_id = P.create_signup(business_name.strip(), industry or "", None, {}, [], email, contact_phone or None)
    tenant_id = P.provision_tenant_from_signup(signup_id, owner_name=contact_name or None,
                                              password_hash=password_hash, partner_id=str(partner_id))
    invite_token, invited = None, False
    with tenant_session(None, "techdeva_admin") as cur:
        book, listp, discount, price, coupon_id = _price(cur, partner_id, price_book_id, coupon_code)
        cur.execute("""INSERT INTO partner_client_subscriptions
                         (partner_id, tenant_id, price_book_id, plan_key, list_price, discount, price,
                          currency, billing_interval, coupon_id, status, trial_ends_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'trial',
                               now() + make_interval(days => %s))
                       ON CONFLICT (tenant_id) DO NOTHING""",
                    (str(partner_id), tenant_id, book["id"], book["plan_key"], listp, discount, price,
                     book["currency"], book["billing_interval"], coupon_id, int(book["trial_days"] or 0)))
        if int(book["trial_days"] or 0) > 0:
            # the partner sold this trial length; app/trial.py locks a business
            # at tenants.trial_ends_at, so that is where it must be written
            cur.execute("UPDATE tenants SET trial_ends_at = now() + make_interval(days => %s) WHERE id = %s",
                        (int(book["trial_days"]), tenant_id))
        if coupon_id:
            cur.execute("UPDATE partner_coupons SET uses = uses + 1 WHERE id = %s", (coupon_id,))
        if signup_link_id:
            cur.execute("UPDATE partner_signup_links SET uses = uses + 1 WHERE id = %s", (str(signup_link_id),))
        cur.execute("""INSERT INTO client_accounts (tenant_id, monthly_fee, currency, started_on, plan_key,
                                                    contact, notes)
                       VALUES (%s,%s,%s,current_date,%s,%s,%s)
                       ON CONFLICT (tenant_id) DO UPDATE SET monthly_fee = EXCLUDED.monthly_fee,
                              currency = EXCLUDED.currency, plan_key = EXCLUDED.plan_key""",
                    (tenant_id, price if book["billing_interval"] == "month" else _r(price / 12),
                     book["currency"], book["plan_key"], email, "partner client"))
        if not password_hash:
            cur.execute("""INSERT INTO users (tenant_id, name, email, role)
                           VALUES (%s,%s,%s,'owner') ON CONFLICT (email) DO NOTHING""",
                        (tenant_id, contact_name or business_name, email))
        record(cur, action="partner.client.provisioned", resource="tenant", resource_id=tenant_id,
               actor=actor, tenant_id=tenant_id,
               detail={"partner_id": str(partner_id), "price": str(price), "currency": book["currency"],
                       "plan": book["plan_key"], "self_registered": password_hash is not None})
    if not password_hash:
        from .auth import issue_setup_token
        invite_token = issue_setup_token(email, None, ttl=72 * 3600)
        invited = send_client_invite(partner_id, email, business_name.strip(), invite_token)
    return {"tenant_id": tenant_id, "signup_id": signup_id, "price": str(price),
            "currency": book["currency"], "plan_key": book["plan_key"],
            "invite_emailed": invited, "invite_token": None if invited else invite_token}


# ============================================================ the scheduler
_last_payout_day = None


def run_due() -> int:
    """Called by the scheduler each tick: split new payments, push set-ups that
    are due, and once a day gather payouts (PARTNER_AUTO_PAYOUTS, default on --
    a payout is a statement to send, not money moved; moving money needs a
    provider, see docs/PARTNERS.md)."""
    global _last_payout_day
    done = sync_ledger()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT id FROM partner_provisioning_runs
                        WHERE status IN ('pending','failed') AND next_try_at <= now()
                        ORDER BY created_at LIMIT 20""")
        due = [str(r["id"]) for r in cur.fetchall()]
    for rid in due:
        if run_provisioning(rid) == "done":
            done += 1
    today = time.strftime("%Y-%m-%d")
    if os.environ.get("PARTNER_AUTO_PAYOUTS", "true").lower() not in ("0", "false", "no") \
            and _last_payout_day != today:
        done += len(create_payouts())
        _last_payout_day = today
    return done
