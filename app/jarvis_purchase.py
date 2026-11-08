"""Jarvis and purchases (Phase 6, 9 Nov 2026; app/purchases.py, db/95).

    "Bilal Traders se 50 kilo cheeni 160 rupay kilo li, 3000 de diye"  -> record_purchase
    "Bilal Traders ko 5000 de diye"                                    -> supplier_payment
    "Bilal Traders ka kitna dena hai?" / "suppliers ka total?"         -> supplier_balance

The model says who, what, how much and at what cost. The platform matches each
item to a product by the till's own rule (name, exact other name, never a guess)
and units the same way (500 g of a product sold by the kg is 0.5), works out the
totals, reads the bill back with what the shop will owe that supplier, and only
after a yes records it once (client_ref). Payments are never more than is owed.
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

from .jarvis_pos import _amount as _qty_unit, _resolve


def _ja():
    from . import jarvis_actions
    return jarvis_actions


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _num(v, what):
    ja = _ja()
    try:
        n = round(float(str(v).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise ja.ActionError(what)
    if n < 0:
        raise ja.ActionError(what)
    return n


def _supplier(tid, role, uid, args, allow_new):
    ja = _ja()
    name = re.sub(r"\s+", " ", str(args.get("supplier") or "")).strip()[:120]
    if not name:
        raise ja.ActionError("which supplier?")
    found = ja._call(tid, role, uid, "GET", "/purchases/suppliers/find?" + urlencode({"name": name})).get("suppliers") or []
    exact = [s for s in found if s["name"].strip().lower() == name.lower()] or found
    if len(exact) == 1:
        return exact[0], name
    if len(exact) > 1:
        raise ja.ActionError("\"%s\" can mean %s. Which one?" % (name, " or ".join(s["name"] for s in exact[:5])))
    if allow_new:
        return None, name
    raise ja.ActionError("%s is not one of your suppliers. Add them on Purchases first" % name)


def describe(tid, role, uid, action, args):
    ja = _ja()
    if action == "supplier_payment":
        s, _ = _supplier(tid, role, uid, args, allow_new=False)
        amt = _num(args.get("amount"), "how much did you pay?")
        if not amt:
            raise ja.ActionError("how much did you pay?")
        if amt - max(s["owed"], 0) > 0.009:
            raise ja.ActionError("you owe %s %s, so %s is more than owed" % (s["name"], _rs(max(s["owed"], 0)), _rs(amt)))
        m = str(args.get("method") or "cash").strip().lower()
        args["_pay"] = {"supplier_id": s["supplier_id"], "amount": amt, "via": "jarvis", "client_ref": "jv" + uuid.uuid4().hex,
                        "method": m if m in ("cash", "bank", "jazzcash", "easypaisa", "card", "cheque", "other") else "cash",
                        "note": str(args.get("note") or "").strip()[:300] or None}
        return "%s ko %s diye likh doon? Dena %s se %s ho jayega." % (s["name"], _rs(amt), _rs(s["owed"]), _rs(s["owed"] - amt))

    s, name = _supplier(tid, role, uid, args, allow_new=True)
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise ja.ActionError("what did you buy, and how much of it?")
    rows = ja._call(tid, role, uid, "GET", "/operations/inventory").get("products") or []
    # the till's own matching and units; stock is not checked (this puts stock IN)
    cat = [dict(r, id=str(r["id"]), kind="product", on_hand=None) for r in rows if r.get("active", True)]
    lines, parts = [], []
    for it in items:
        c, qty = _resolve(cat, it)
        cost, total = it.get("unit_cost"), it.get("line_total")
        if cost in (None, "") and total in (None, ""):
            raise ja.ActionError("what did %s cost? Say the price per %s, or the total for it" % (c["name"], c.get("unit") or "piece"))
        if total not in (None, ""):
            total = _num(total, "what did %s cost?" % c["name"])
            cost = round(total / qty, 2) if qty else 0
        else:
            cost = _num(cost, "what did %s cost?" % c["name"])
            total = round(qty * cost, 2)
        lines.append({"product_id": c["id"], "quantity": qty, "unit_cost": cost, "line_total": total})
        parts.append("%s %s @ %s = %s" % (c["name"], _qty_unit(qty, c.get("unit")), _rs(cost), _rs(total)))
    grand = round(sum(l["line_total"] for l in lines), 2)
    paid = args.get("paid")
    paid = _num(paid, "how much did you pay now?") if paid not in (None, "") else 0
    if paid - grand > 0.009:
        raise ja.ActionError("you paid %s but the bill is %s; say the amounts again" % (_rs(paid), _rs(grand)))
    m = str(args.get("method") or "cash").strip().lower()
    body = {"items": lines, "client_ref": "jv" + uuid.uuid4().hex, "via": "jarvis",
            "supplier_ref": str(args.get("supplier_ref") or "").strip()[:60] or None,
            "note": str(args.get("note") or "").strip()[:300] or None}
    if s:
        body["supplier_id"] = s["supplier_id"]
    else:
        body["supplier_name"] = name
    if paid:
        body["paid_now"] = {"amount": paid, "method": m if m in ("cash", "bank", "jazzcash", "easypaisa", "card", "cheque", "other") else "cash"}
    args["_bill"] = body
    owed = float(s["owed"]) if s else 0.0
    who = s["name"] if s else "%s (naya supplier)" % name
    return "%s se bill: %s. Total %s%s. Stock mein daal kar likh doon? Dena %s se %s ho jayega." % (
        who, ", ".join(parts), _rs(grand), (", %s abhi diye" % _rs(paid)) if paid else "",
        _rs(owed), _rs(owed + grand - paid))


def run(tid, role, uid, action, args):
    ja = _ja()
    if action == "supplier_balance":
        return balance(tid, role, uid, args)
    if action == "supplier_payment":
        p = args.get("_pay")
        if not isinstance(p, dict) or not p.get("client_ref"):
            raise ja.ActionError("that was not read out to you first, so I did not write it; ask me again")
        s = ja._call(tid, role, uid, "POST", "/purchases/payments", p).get("supplier") or {}
        return {"refresh": ["purchases"], "supplier": s}, "Done, payment written. %s ko ab %s dena hai" % (
            s.get("name", "Supplier"), _rs(s.get("owed") or 0))
    b = args.get("_bill")
    if not isinstance(b, dict) or not b.get("client_ref"):
        raise ja.ActionError("that bill was not read out to you first, so I did not record it; ask me again")
    out = ja._call(tid, role, uid, "POST", "/purchases/bills", b)
    bill, s = out.get("bill") or {}, out.get("supplier") or {}
    return {"refresh": ["purchases", "inventory"], "bill": {"id": bill.get("id"), "number": bill.get("number")},
            "supplier": s}, "Done, bill %s for %s is recorded and the stock is in. %s ko ab %s dena hai" % (
        bill.get("number", ""), _rs(bill.get("total") or 0), s.get("name", "Supplier"), _rs(s.get("owed") or 0))


def balance(tid, role, uid, args):
    ja = _ja()
    if not str(args.get("supplier") or "").strip():
        o = ja._call(tid, role, uid, "GET", "/purchases")
        owed = [s for s in (o.get("suppliers") or []) if s["owed"] > 0.009]
        if not owed:
            return {"totals": o.get("totals")}, "Abhi kisi supplier ka kuch dena nahi"
        return {"totals": o.get("totals")}, "Suppliers ko total %s dena hai: %s" % (
            _rs(sum(s["owed"] for s in owed)), ", ".join("%s %s" % (s["name"], _rs(s["owed"])) for s in owed[:4]))
    s, _ = _supplier(tid, role, uid, args, allow_new=False)
    return {"supplier": s}, "%s ko %s dena hai (%d bill%s)" % (s["name"], _rs(s["owed"]), s["bills"], "" if s["bills"] == 1 else "s")
