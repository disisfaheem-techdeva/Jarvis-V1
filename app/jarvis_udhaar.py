"""Jarvis and the udhaar ledger (Phase 5, 8 Nov 2026; app/ledger.py, db/94).

    "Jarvis, Ahmed ke account mein 1,500 rupees udhaar likh do"   -> udhaar_add
    "Ahmed ne 1,000 rupees de diye hain"                          -> udhaar_payment
    "Ahmed ka kitna baqi hai?" / "total udhaar kitna hai?"        -> customer_balance

The model only says who (the name as said, a phone if said), how much and a
note. Everything else is the ledger's own endpoints (loopback, like every Jarvis
action): which customer that is (two Ahmeds -> "which one?", settled by the last
digits of the phone), the balance before and after, the payment rule (a payment
settles only the ledger part; till bills are collected on the till), and the
words said back. A yes writes exactly the sealed entry, once (client_ref).
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode


def _ja():
    from . import jarvis_actions
    return jarvis_actions


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def _who(c):
    d = _digits(c.get("phone"))
    return c["name"] + (" (…%s)" % d[-4:] if len(d) >= 4 else "")


def _amount(args):
    ja = _ja()
    try:
        a = round(float(str(args.get("amount")).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise ja.ActionError("how much?")
    if a <= 0:
        raise ja.ActionError("how much?")
    return a


def _pick(tid, role, uid, args, allow_new):
    """(customer row or None for a new one, name, phone). Raises with the question to ask."""
    ja = _ja()
    name = re.sub(r"\s+", " ", str(args.get("customer") or "")).strip()[:120]
    phone = str(args.get("phone") or "").strip()[:40]
    d = _digits(phone)
    if not name and len(d) < 7:
        raise ja.ActionError("whose account?")
    q = {"name": name}
    if len(d) >= 7:
        q["phone"] = d
    found = ja._call(tid, role, uid, "GET", "/ledger/find?" + urlencode(q)).get("customers") or []
    if 3 <= len(d) < 7:                               # "1234 wala": the last digits of the phone
        found = [c for c in found if _digits(c.get("phone")).endswith(d)]
    exact = [c for c in found if c["name"].strip().lower() == name.lower()] or found
    if len(exact) == 1:
        return exact[0], name, phone
    if len(exact) > 1:
        raise ja.ActionError("there are %d customers called %s: %s. Which one? Say the last 4 digits of the phone" % (
            len(exact), name, ", ".join(_who(c) + " " + _rs(c["balance"]) + " baqi" for c in exact[:5])))
    if allow_new and len(d) >= 7:
        return None, name, phone
    raise ja.ActionError("%s is not in your customers. Say their phone number to add them" % (name or phone))


def describe(tid, role, uid, action, args):
    """The question, and args["_entry"] filled with exactly what a yes will write."""
    ja = _ja()
    amt = _amount(args)
    c, name, phone = _pick(tid, role, uid, args, allow_new=(action == "udhaar_add"))
    note = re.sub(r"\s+", " ", str(args.get("note") or "")).strip()[:300] or None
    entry = {"kind": "credit" if action == "udhaar_add" else "payment", "amount": amt, "note": note,
             "client_ref": "jv" + uuid.uuid4().hex, "via": "jarvis"}
    if action == "udhaar_payment":
        m = str(args.get("method") or "cash").strip().lower()
        entry["method"] = m if m in ("cash", "bank", "jazzcash", "easypaisa", "card", "other") else "cash"
    if c:
        entry["customer_id"] = c["customer_id"]
    else:
        entry["customer_name"], entry["customer_phone"] = name, phone
    args["_entry"] = entry
    if action == "udhaar_add":
        if not c:
            return "%s is new. Add them with phone …%s and write %s udhaar on their account?" % (
                name, _digits(phone)[-4:], _rs(amt))
        return "%s ke account mein %s udhaar likh doon? Baqi %s se %s ho jayega." % (
            _who(c), _rs(amt), _rs(c["balance"]), _rs(c["balance"] + amt))
    ledger, till = max(float(c["ledger"]), 0.0), float(c["till"])
    if amt - ledger > 0.009:
        if till > 0:
            raise ja.ActionError("%s owes %s on the ledger and %s on till bills. I can write at most %s here; "
                                 "collect the till bills on the till (Bills, Udhaar)" % (
                                     _who(c), _rs(ledger), _rs(till), _rs(ledger)))
        raise ja.ActionError("%s owes %s, so %s is more than owed" % (_who(c), _rs(ledger), _rs(amt)))
    return "%s se %s wusool likh doon? Baqi %s se %s ho jayega." % (
        _who(c), _rs(amt), _rs(c["balance"]), _rs(c["balance"] - amt))


def run(tid, role, uid, action, args):
    ja = _ja()
    if action == "customer_balance":
        return balance(tid, role, uid, args)
    e = args.get("_entry")
    if not isinstance(e, dict) or not e.get("client_ref"):
        raise ja.ActionError("that was not read out to you first, so I did not write it; ask me again")
    out = ja._call(tid, role, uid, "POST", "/ledger/entries", e)
    c = out.get("customer") or {}
    what = "udhaar written" if e["kind"] == "credit" else "payment written"
    return {"refresh": ["udhaar"], "entry": out.get("entry"), "customer": c}, (
        "Done, %s. %s ka baqi ab %s" % (what, _who(c) if c.get("name") else "Their", _rs(c.get("balance") or 0)))


def balance(tid, role, uid, args):
    ja = _ja()
    name = str(args.get("customer") or "").strip()
    if not name and len(_digits(args.get("phone"))) < 7:
        o = ja._call(tid, role, uid, "GET", "/ledger")
        t, top = o.get("totals") or {}, [c for c in (o.get("customers") or []) if c["balance"] > 0][:3]
        if not t.get("customers_owing"):
            return {"totals": t}, "Abhi kisi ka udhaar baqi nahi"
        return {"totals": t}, "Total udhaar %s, %d customer%s. Sab se zyada: %s" % (
            _rs(t["owed"]), t["customers_owing"], "" if t["customers_owing"] == 1 else "s",
            ", ".join("%s %s" % (_who(c), _rs(c["balance"])) for c in top))
    c, _, _ = _pick(tid, role, uid, args, allow_new=False)
    parts = []
    if c["ledger"]:
        parts.append("%s ledger" % _rs(c["ledger"]))
    if c["till"]:
        parts.append("%s on %d till bill%s" % (_rs(c["till"]), c["till_bills"], "" if c["till_bills"] == 1 else "s"))
    return {"customer": c}, "%s: %s baqi%s" % (_who(c), _rs(c["balance"]), (" (" + ", ".join(parts) + ")") if parts else "")
