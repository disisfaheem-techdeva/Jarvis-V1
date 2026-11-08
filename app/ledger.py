"""Udhaar ledger (Phase 5, 8 Nov 2026; db/94, ADR-004).

What a customer owes the shop, from two places, added up in one place
(the customer_udhaar view):

    till udhaar   POS bills delivered and not fully paid (app/pos.py), unchanged
    ledger        customer_ledger_entries: opening balances, credit written down
                  with no bill, payments, and adjustments

Rules here:
  * the owner (or a role with Finance, through RoleGuard) writes; money pages are
    owner-only, as elsewhere in the dashboard;
  * entries are append-only (the database refuses an edit): a mistake is undone
    by a reversing entry, and both stay in the statement;
  * a resent entry (same client_ref) is the first entry, never a second;
  * a payment only reduces the ledger part. When the customer also owes on till
    bills and the payment is more than the ledger part, it is refused with the
    two amounts and a pointer to the till: which of the two a payment settles
    first is the owner's rule to make (ADR-004), not the code's to guess;
  * a payment is never more than is owed (no silent advance).
"""
from __future__ import annotations

import datetime as _dt
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

KINDS = ("opening", "credit", "payment", "adjustment")
METHODS = ("cash", "bank", "jazzcash", "easypaisa", "card", "other")
SOURCES = ("manual", "jarvis", "import")
REF = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MONEY_ROLES = ("owner", "techdeva_admin")


class LedgerError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _m(v):
    return round(float(v or 0), 2)


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def _row(r):
    return {"customer_id": str(r["customer_id"]), "name": r["name"], "phone": r["phone"] or None,
            "ledger": _m(r["ledger"]), "till": _m(r["till"]), "balance": _m(r["balance"]),
            "till_bills": int(r["till_bills"]), "entries": int(r["entries"]),
            "last_activity": r["last_activity"]}


def balance(cur, cid):
    cur.execute("SELECT * FROM customer_udhaar WHERE customer_id = %s", (str(cid),))
    r = cur.fetchone()
    if not r:
        raise LedgerError(404, "That customer is not in this business.")
    return _row(r)


def find(cur, name=None, phone=None, limit=8):
    """Customers this name or phone means: a phone (last 10 digits) first, then the
    exact name, then names that contain it. Each with its balance."""
    d = _digits(phone)
    if len(d) >= 7:
        cur.execute("""SELECT * FROM customer_udhaar WHERE right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10)
                       = right(%s, 10) ORDER BY balance DESC LIMIT %s""", (d, limit))
        rows = cur.fetchall()
        if rows:
            return [_row(r) for r in rows]
    n = re.sub(r"\s+", " ", str(name or "")).strip().lower()
    if not n:
        return []
    cur.execute("SELECT * FROM customer_udhaar WHERE lower(btrim(name)) = %s ORDER BY balance DESC LIMIT %s", (n, limit))
    rows = cur.fetchall()
    if not rows:
        cur.execute("SELECT * FROM customer_udhaar WHERE lower(name) LIKE %s ORDER BY balance DESC LIMIT %s",
                    ("%" + n.replace("%", "").replace("_", "") + "%", limit))
        rows = cur.fetchall()
    return [_row(r) for r in rows]


def statement(cur, cid):
    """Every ledger entry and every till udhaar bill, oldest first, with the running balance."""
    b = balance(cur, cid)
    cur.execute("""SELECT id::text, kind, amount, method, note, occurred_on, source, reverses::text, created_at,
                          EXISTS (SELECT 1 FROM customer_ledger_entries r WHERE r.reverses = e.id) AS reversed
                     FROM customer_ledger_entries e WHERE customer_id = %s""", (str(cid),))
    lines = [{"type": "entry", "id": r["id"], "kind": r["kind"], "amount": _m(r["amount"]), "method": r["method"],
              "note": r["note"], "on": r["occurred_on"], "at": r["created_at"], "source": r["source"],
              "reverses": r["reverses"], "reversed": bool(r["reversed"])} for r in cur.fetchall()]
    cur.execute("""SELECT o.id::text, o.number, o.total, o.created_at,
                          COALESCE((SELECT sum(p.amount) FROM payments p WHERE p.invoice_id = o.invoice_id), 0) AS paid
                     FROM orders o WHERE o.customer_id = %s AND o.source = 'pos' AND o.status = 'delivered'
                      AND o.recognised_at IS NULL""", (str(cid),))
    for r in cur.fetchall():
        owed = _m(r["total"]) - _m(r["paid"])
        lines.append({"type": "till_bill", "id": r["id"], "number": r["number"], "amount": round(owed, 2),
                      "total": _m(r["total"]), "paid": _m(r["paid"]),
                      "on": r["created_at"].date() if r["created_at"] else None, "at": r["created_at"]})
    lines.sort(key=lambda x: (str(x["on"]), str(x["at"])))
    run = 0.0
    for x in lines:
        run = round(run + x["amount"], 2)
        x["running"] = run
    return {"customer": b, "lines": lines}


def _customer(cur, body, actor):
    """The customer an entry is for: by id, or by name and phone (made if new)."""
    if body.get("customer_id"):
        return balance(cur, body["customer_id"])
    name = re.sub(r"\s+", " ", str(body.get("customer_name") or "")).strip()[:120]
    phone = str(body.get("customer_phone") or "").strip()[:40]
    if not name:
        raise LedgerError(400, "Whose account? Choose the customer, or give their name.")
    found = find(cur, name, phone)
    if len(_digits(phone)) >= 7 and found:
        return found[0]
    same = [f for f in found if f["name"].strip().lower() == name.lower()]
    if same and not phone:
        if len(same) > 1:
            raise LedgerError(409, "There are %d customers called %s. Choose one, or give the phone number." % (len(same), name))
        return same[0]
    cur.execute("INSERT INTO customers (tenant_id, name, phone, source) VALUES (current_tenant(), %s, %s, 'udhaar') "
                "RETURNING id", (name, phone or None))
    cid = cur.fetchone()["id"]
    record(cur, action="customer.create", resource="customers", resource_id=str(cid), actor=actor,
           detail={"name": name, "via": "udhaar"})
    return balance(cur, cid)


def add_entry(cur, body, *, actor, source="manual"):
    """One entry. Returns {entry, customer, duplicate}."""
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise LedgerError(400, "Choose opening balance, credit, payment or adjustment.")
    if source not in SOURCES:
        raise LedgerError(400, "Unknown source.")
    ref = body.get("client_ref")
    if ref is not None and not REF.match(str(ref)):
        raise LedgerError(400, "The entry reference is not valid. Reload the page.")
    if ref:
        cur.execute("SELECT id::text, customer_id FROM customer_ledger_entries WHERE client_ref = %s", (ref,))
        dup = cur.fetchone()
        if dup:
            return {"entry": _entry(cur, dup["id"]), "customer": balance(cur, dup["customer_id"]), "duplicate": True}
    try:
        amt = round(float(str(body.get("amount")).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise LedgerError(400, "The amount must be a number.")
    if kind == "adjustment":
        if amt == 0:
            raise LedgerError(400, "An adjustment cannot be zero.")
    elif amt <= 0:
        raise LedgerError(400, "The amount must be more than zero.")
    if abs(amt) > 100000000:
        raise LedgerError(400, "That amount is too large.")
    method = str(body.get("method") or "").strip().lower() or None
    if kind == "payment":
        method = method or "cash"
        if method not in METHODS:
            raise LedgerError(400, "Paid how? cash, bank, JazzCash, EasyPaisa, card or other.")
    else:
        method = None
    on = body.get("occurred_on")
    if on:
        try:
            on = _dt.date.fromisoformat(str(on)[:10])
        except ValueError:
            raise LedgerError(400, "The date must look like 2026-11-08.")
        if on > _dt.date.today() + _dt.timedelta(days=1):
            raise LedgerError(400, "That date is in the future.")
    note = re.sub(r"\s+", " ", str(body.get("note") or "")).strip()[:300] or None
    c = _customer(cur, body, actor)
    # one customer at a time: two payments arriving together are checked one after the other
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('udhaar:' || %s))", (c["customer_id"],))
    c = balance(cur, c["customer_id"])
    if kind == "payment":
        if amt - max(c["ledger"], 0) > 0.009:
            if c["till"] > 0:
                raise LedgerError(409, (
                    "%s owes %s on the ledger and %s on %d till bill%s. A payment here settles only the ledger, so the "
                    "most it can be is %s. Collect the rest on the till (Bills, Udhaar)." % (
                        c["name"], _rs(max(c["ledger"], 0)), _rs(c["till"]), c["till_bills"],
                        "" if c["till_bills"] == 1 else "s", _rs(max(c["ledger"], 0)))))
            raise LedgerError(409, "%s owes %s, so a payment of %s is more than owed." % (
                c["name"], _rs(max(c["ledger"], 0)), _rs(amt)))
        amt = -amt
    cur.execute("""INSERT INTO customer_ledger_entries (tenant_id, customer_id, kind, amount, method, note, occurred_on,
                                                        source, client_ref, created_by)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s, COALESCE(%s, (now() AT TIME ZONE 'Asia/Karachi')::date),
                           %s, %s, %s) RETURNING id::text""",
                (c["customer_id"], kind, amt, method, note, on, source, ref or None, actor))
    eid = cur.fetchone()["id"]
    record(cur, action="udhaar." + kind, resource="customer_ledger_entries", resource_id=eid, actor=actor,
           detail={"customer": c["name"], "amount": amt, "source": source})
    return {"entry": _entry(cur, eid), "customer": balance(cur, c["customer_id"]), "duplicate": False}


def reverse(cur, eid, reason, *, actor):
    """Undo an entry by adding its opposite. Both stay in the statement."""
    e = _entry(cur, eid)
    if e["reverses"]:
        raise LedgerError(409, "That entry is itself a correction. Add a new entry instead.")
    cur.execute("SELECT 1 FROM customer_ledger_entries WHERE reverses = %s", (eid,))
    if cur.fetchone():
        raise LedgerError(409, "That entry was already reversed.")
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:250]
    if not reason:
        raise LedgerError(400, "Say why it is being reversed.")
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('udhaar:' || %s))", (e["customer_id"],))
    cur.execute("""INSERT INTO customer_ledger_entries (tenant_id, customer_id, kind, amount, note, source, reverses, created_by)
                   VALUES (current_tenant(), %s, 'adjustment', %s, %s, 'manual', %s, %s) RETURNING id::text""",
                (e["customer_id"], -e["amount"], "Reversed: " + reason, eid, actor))
    rid = cur.fetchone()["id"]
    record(cur, action="udhaar.reverse", resource="customer_ledger_entries", resource_id=rid, actor=actor,
           detail={"reverses": eid, "amount": -e["amount"], "reason": reason})
    return {"entry": _entry(cur, rid), "customer": balance(cur, e["customer_id"])}


def _entry(cur, eid):
    cur.execute("""SELECT id::text, customer_id::text, kind, amount, method, note, occurred_on, source,
                          reverses::text, created_at FROM customer_ledger_entries WHERE id::text = %s""", (str(eid),))
    r = cur.fetchone()
    if not r:
        raise LedgerError(404, "That entry is not in this business.")
    return dict(r, amount=_m(r["amount"]))


def overview(cur, q=None, everyone=False, limit=300):
    where, args = ([] if everyone else ["(balance <> 0 OR entries > 0)"]), []
    if q:
        d = _digits(q)
        if len(d) >= 4:
            where.append("regexp_replace(coalesce(phone, ''), '\\D', '', 'g') LIKE %s")
            args.append("%" + d + "%")
        else:
            where.append("lower(name) LIKE %s")
            args.append("%" + str(q).strip().lower().replace("%", "").replace("_", "") + "%")
    sql = "SELECT * FROM customer_udhaar" + (" WHERE " + " AND ".join(where) if where else "")
    cur.execute(sql + " ORDER BY balance DESC, name LIMIT %s", args + [limit])
    rows = [_row(r) for r in cur.fetchall()]
    cur.execute("""SELECT COALESCE(sum(balance) FILTER (WHERE balance > 0), 0) AS owed,
                          count(*) FILTER (WHERE balance > 0) AS owing,
                          COALESCE(sum(ledger), 0) AS ledger, COALESCE(sum(till), 0) AS till FROM customer_udhaar""")
    t = cur.fetchone()
    return {"customers": rows, "totals": {"owed": _m(t["owed"]), "customers_owing": int(t["owing"]),
                                           "ledger": _m(t["ledger"]), "till": _m(t["till"])}}


# ------------------------------------------------------------------ routes
def make_ledger_router(identity):
    router = APIRouter(prefix="/ledger", tags=["udhaar"])

    def who(x_tenant_id, x_role, authorization, session):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, session)
        if role not in MONEY_ROLES:
            raise HTTPException(403, "Udhaar is for the owner. Ask the owner, or a role with Finance.")
        return tid, role, uid

    def run(fn, x_tenant_id, x_role, authorization, session):
        tid, role, uid = who(x_tenant_id, x_role, authorization, session)
        try:
            with tenant_session(tid, role) as cur:
                return fn(cur, str(uid or ""))
        except LedgerError as e:
            raise HTTPException(e.status, e.message)

    @router.get("")
    def ledger_overview(q: str | None = None, all: int = 0,
                        x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                        authorization: str | None = Header(None),
                        td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Who owes what: every customer with a balance or an entry, most owed first, and the totals."""
        return run(lambda cur, uid: overview(cur, (q or "").strip()[:80] or None, bool(all)),
                   x_tenant_id, x_role, authorization, td_session)

    @router.get("/find")
    def ledger_find(name: str | None = None, phone: str | None = None,
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The customers a name or phone means (Jarvis asks which one when it is more than one)."""
        return run(lambda cur, uid: {"customers": find(cur, (name or "")[:120], (phone or "")[:40])},
                   x_tenant_id, x_role, authorization, td_session)

    @router.get("/customers/{cid}")
    def ledger_statement(cid: str,
                         x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                         authorization: str | None = Header(None),
                         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One customer's statement: entries and till udhaar bills with the running balance."""
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", cid):
            raise HTTPException(404, "That customer is not in this business.")
        return run(lambda cur, uid: statement(cur, cid), x_tenant_id, x_role, authorization, td_session)

    @router.post("/entries")
    def ledger_add(body: dict = Body(...),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Write one entry down: opening balance, credit, payment or adjustment."""
        src = "jarvis" if body.get("via") == "jarvis" else "manual"
        return run(lambda cur, uid: add_entry(cur, body, actor=uid, source=src),
                   x_tenant_id, x_role, authorization, td_session)

    @router.post("/entries/{eid}/reverse")
    def ledger_reverse(eid: str, body: dict = Body(default={}),
                       x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Undo an entry with its opposite; both stay in the history."""
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", eid):
            raise HTTPException(404, "That entry is not in this business.")
        return run(lambda cur, uid: reverse(cur, eid, body.get("reason"), actor=uid),
                   x_tenant_id, x_role, authorization, td_session)

    return router
