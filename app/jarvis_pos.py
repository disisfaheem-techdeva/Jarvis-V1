"""Jarvis at the till (Phase 4, 8 Nov 2026): a bill and a stock question, by voice
or text, in Roman Urdu, Urdu or English.

    "Jarvis, dhai kilo cheeni aur do kilo aata ka bill bana do"

The model's only job is WHAT was asked for: for each item, the words said, a
quantity and a unit ({"said": "cheeni", "qty": 2.5, "unit": "kg"}). Everything
that matters for money is ordinary code, run through the till's own endpoints
(loopback, as every Jarvis action is):

  1. each word is matched to a product by the till's own rule (orders.match_item:
     name, then an exact other name, never a guess between two); a word that
     matches nothing, or two products, is asked about;
  2. a unit is turned into the product's unit (500 g of a product sold by the kg
     is 0.5); one that cannot be (2 kg of soap) is asked about;
  3. stock is checked where the product is tracked;
  4. POST /pos/quote prices the bill exactly as a sale would be (list prices,
     the shop's tax) and says whether the till has an open shift;
  5. Jarvis reads that bill and total back and asks "final kar doon?". What the
     model said about prices or totals is never used;
  6. on "yes", the bill is priced again: if anything changed it stops and says
     so; otherwise POST /pos/sale with the reference fixed in step 5, so a
     repeated yes (or a lost connection) returns the first sale, never a second.

Udhaar by voice is not in this phase: it needs the customer's name and phone and,
for a cashier, the owner's or manager's PIN, which the till screen asks for.
"""
from __future__ import annotations

import uuid

from .orders import match_item, _norm

UNITS = {"piece", "kg", "g", "litre", "ml", "dozen", "pack"}
# what people say -> products.unit (db/93)
SAID_UNIT = {"kg": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kgs": "kg", "کلو": "kg",
             "g": "g", "gm": "g", "gram": "g", "grams": "g", "garam": "g", "گرام": "g",
             "litre": "litre", "liter": "litre", "l": "litre", "ltr": "litre", "لیٹر": "litre",
             "ml": "ml", "millilitre": "ml", "milliliter": "ml",
             "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "adad": "piece", "dana": "piece",
             "dozen": "dozen", "darjan": "dozen", "darjon": "dozen", "درجن": "dozen",
             "pack": "pack", "packet": "pack", "packets": "pack", "pkt": "pack", "dabba": "pack", "dibba": "pack"}
# (said unit, product unit) -> multiply the quantity by
CONVERT = {("g", "kg"): 0.001, ("kg", "g"): 1000, ("ml", "litre"): 0.001, ("litre", "ml"): 1000,
           ("dozen", "piece"): 12}
SHOW_UNIT = {"kg": "kg", "g": "g", "litre": "litre", "ml": "ml", "dozen": "dozen", "pack": "pack", "piece": ""}


def _ja():
    from . import jarvis_actions
    return jarvis_actions


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _qty(v):
    return ("%.3f" % v).rstrip("0").rstrip(".")


def _items(args):
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise _ja().ActionError("tell me what goes on the bill, with how much of each")
    if len(items) > 50:
        raise _ja().ActionError("that is too many items for one bill; say up to 50")
    return items


def _resolve(cat, it):
    """One spoken item -> (catalogue entry, quantity in the product's unit). Raises with what to ask."""
    ja = _ja()
    said = str((it or {}).get("said") or (it or {}).get("name") or "").strip()[:80]
    if not said:
        raise ja.ActionError("I did not catch one of the items; say it again")
    c = match_item(cat, said)
    if not c:
        n = _norm(said)
        two = [x["name"] for x in cat if any(_norm(a) == n for a in (x.get("aliases") or ()))]
        if len(two) > 1:
            raise ja.ActionError("\"%s\" can mean %s. Which one?" % (said, " or ".join(two)))
        raise ja.ActionError("\"%s\" is not on your list. Add it under Products first, or say its name as it is listed" % said)
    if c["kind"] != "product":
        raise ja.ActionError("%s is a service; I can only bill products at the till by voice" % c["name"])
    try:
        qty = float(str((it or {}).get("qty", 1)).replace(",", ""))
    except (TypeError, ValueError):
        raise ja.ActionError("how much %s?" % c["name"])
    if not qty > 0:
        raise ja.ActionError("how much %s?" % c["name"])
    said_unit = SAID_UNIT.get(_norm((it or {}).get("unit")))
    unit = c.get("unit")
    if said_unit and unit and said_unit != unit:
        f = CONVERT.get((said_unit, unit))
        if not f:
            raise ja.ActionError("%s is sold by the %s, not by the %s. How many %s?" % (
                c["name"], unit, said_unit, unit if unit != "piece" else "pieces"))
        qty = qty * f
    qty = round(qty, 3)
    if qty > 10000:
        raise ja.ActionError("that is more %s than one bill can hold" % c["name"])
    if c.get("on_hand") is not None and qty > float(c["on_hand"]) + 1e-9:
        raise ja.ActionError("only %s of %s is in stock" % (_amount(float(c["on_hand"]), unit), c["name"]))
    return c, qty


def _amount(q, unit):
    u = SHOW_UNIT.get(unit or "", "")
    return _qty(q) + ((" " + u) if u else "")


def describe_bill(tid, role, uid, args):
    """The bill read back and the question. Fills args["_bill"] with exactly what a
    yes will sell, so the seal carries it (jarvis_actions.seal is made from args)."""
    ja = _ja()
    if str(args.get("payment") or "cash").lower() not in ("cash", ""):
        raise ja.ActionError("by voice I can make cash bills for now; for udhaar or card, finish it on the till screen")
    cat = ja._call(tid, role, uid, "GET", "/pos/catalogue").get("items") or []
    picked = {}
    for it in _items(args):
        c, qty = _resolve(cat, it)
        picked[c["id"]] = (c, round(picked.get(c["id"], (c, 0))[1] + qty, 3))
    for c, qty in picked.values():          # the same item said twice: one line, stock checked on the sum
        if c.get("on_hand") is not None and qty > float(c["on_hand"]) + 1e-9:
            raise ja.ActionError("only %s of %s is in stock" % (_amount(float(c["on_hand"]), c.get("unit")), c["name"]))
    register = str(args.get("register") or "").strip()[:40] or None
    items = [{"product_id": cid, "quantity": q} for cid, (c, q) in picked.items()]
    q = ja._call(tid, role, uid, "POST", "/pos/quote", {"items": items, "register": register})
    if not q.get("can_sell"):
        raise ja.ActionError("%s has no open shift. Open the shift on the till first" % (q.get("register") or "The till"))
    args["_bill"] = {"items": items, "total": q["total"], "register": q.get("register"),
                     "client_ref": "jv" + uuid.uuid4().hex}
    parts = []
    for l in q["lines"]:
        u = SHOW_UNIT.get(l.get("unit") or "", "")
        parts.append("%s %s%s %s" % (l["name"], _qty(l["quantity"]), (" " + u) if u else "", _rs(l["line_total"])))
    tax = ""
    if q.get("tax"):
        tax = (" plus tax %s" if q.get("tax_mode") == "exclusive" else " (tax %s included)") % _rs(q["tax"])
    return "Bill: %s.%s Total %s. Final kar doon?" % (", ".join(parts), tax, _rs(q["total"]))


def sell(tid, role, uid, args):
    """The yes: price it again, then one sale with the sealed reference."""
    ja = _ja()
    b = args.get("_bill")
    if not isinstance(b, dict) or not b.get("items") or not b.get("client_ref"):
        raise ja.ActionError("that bill was not read out to you first, so I did not make it; ask me again")
    q = ja._call(tid, role, uid, "POST", "/pos/quote", {"items": b["items"], "register": b.get("register")})
    if abs(float(q["total"]) - float(b["total"])) > 0.009:
        raise ja.ActionError("a price changed since I read the bill out (now %s, it was %s), so I did not make it. "
                             "Ask me again" % (_rs(q["total"]), _rs(b["total"])))
    out = ja._call(tid, role, uid, "POST", "/pos/sale", {
        "client_ref": b["client_ref"], "register": b.get("register"), "items": b["items"],
        "payments": [{"method": "cash", "amount": q["total"]}]})
    return {"navigate": None, "refresh": ["orders", "inventory"],
            "sale": {k: out.get(k) for k in ("id", "number", "total", "status", "duplicate")}}, None


def done_text(args):
    b = args.get("_bill") or {}
    return "Done, the bill for %s is made. Print it from the till." % _rs(b.get("total") or 0)


def stock(tid, role, uid, args):
    """How much is left, from the stock ledger. The answer is worded here, never by the model."""
    ja = _ja()
    said = str(args.get("item") or "").strip()[:80]
    if not said:
        raise ja.ActionError("which item?")
    rows = ja._call(tid, role, uid, "GET", "/operations/inventory").get("products") or []
    cat = [dict(r, id=str(r["id"]), kind="product") for r in rows if r.get("active", True)]
    c = match_item(cat, said)
    if not c:
        n = _norm(said)
        two = [x["name"] for x in cat if any(_norm(a) == n for a in (x.get("aliases") or ()))]
        if len(two) > 1:
            raise ja.ActionError("\"%s\" can mean %s. Which one?" % (said, " or ".join(two)))
        raise ja.ActionError("\"%s\" is not on your products list" % said)
    u = SHOW_UNIT.get(c.get("unit") or "", "")
    if not c.get("track_stock"):
        return {"item": c["name"], "on_hand": None}, "%s ka stock track nahi hota. Turn on Track stock for it under Inventory" % c["name"]
    on = float(c.get("on_hand") or 0)
    low = c.get("low_stock_threshold")
    warn = " Yeh kam hai, reorder kar lein." if low is not None and on <= float(low) else ""
    return {"item": c["name"], "on_hand": on}, "%s: %s%s in stock.%s" % (c["name"], _qty(on), (" " + u) if u else "", warn)
