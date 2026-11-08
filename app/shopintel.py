"""Shop intelligence (Phase 8, 9 Nov 2026): the day at the shop, from its own records.

GET /shop/summary?period=today|week|month  (owner only; money)

Every figure is either counted from records or labelled an estimate, and says what
it covers. Nothing is invented and no AI model is involved.

  sales            orders delivered and fully paid in the period (the platform's
                   one definition of a sale, app/dashboard.py METRICS["sales"])
  on udhaar        till bills delivered and not fully paid, made in the period, plus
                   udhaar written in the ledger (db/94) in the period
  collected        payments received in the period
  expenses         expenses dated in the period
  gross profit     ESTIMATE: for sold product lines whose cost is known, line total
                   less quantity x average cost (from purchases and opening stock
                   with a unit cost). Lines with no known cost are left out, and the
                   share of sales the estimate covers is returned beside it.
  after expenses   ESTIMATE: gross profit less expenses, only as good as the coverage
  best sellers     products by quantity and by money, from the period's sales
  low stock        tracked products at or below their warning level, or out
  stock value      ESTIMATE: stock on hand x average cost, for products with a cost
  udhaar / owed    what customers owe the shop, and what the shop owes suppliers
  reorder          SUGGESTION: products that, at the last 14 days' pace, run out
                   within 7 days; how much would cover 14 days
"""
from __future__ import annotations

import datetime as _dt
import math

from fastapi import APIRouter, Cookie, Header, HTTPException

from .auth import SESSION_COOKIE
from .db import tenant_session

PERIODS = ("today", "week", "month")
MONEY_ROLES = ("owner", "techdeva_admin")
PACE_DAYS, RUNOUT_DAYS, COVER_DAYS = 14, 7, 14

# average cost per product: what was paid for stock that came in with a cost
_AVG_COST = """
  SELECT product_id, sum(quantity_delta * unit_cost) / NULLIF(sum(quantity_delta), 0) AS cost
    FROM stock_movements
   WHERE kind IN ('purchase', 'opening') AND unit_cost IS NOT NULL AND quantity_delta > 0
   GROUP BY product_id"""
_ON_HAND = "SELECT product_id, sum(quantity_delta) AS on_hand FROM stock_movements GROUP BY product_id"


def _m(v):
    return round(float(v or 0), 2)


def _q(v):
    return round(float(v or 0), 3)


def _span(period, today):
    if period == "today":
        return today, today
    if period == "week":
        return today - _dt.timedelta(days=6), today
    return today.replace(day=1), today


def summary(cur, period="today"):
    from .dashboard import _tz
    if period not in PERIODS:
        period = "today"
    tz = _tz(cur)
    cur.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,))
    today = cur.fetchone()["d"]
    s, e = _span(period, today)
    p = {"tz": tz, "s": s, "e": e}
    out = {"period": period, "start": str(s), "end": str(e), "timezone": tz}

    cur.execute("""SELECT COALESCE(sum(total), 0) AS v, count(*) AS n FROM orders
                    WHERE recognised_at IS NOT NULL AND (recognised_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s""", p)
    r = cur.fetchone()
    out["sales"] = {"total": _m(r["v"]), "count": int(r["n"])}

    cur.execute("""SELECT COALESCE(sum(o.total - COALESCE(pp.paid, 0)), 0) AS v, count(*) AS n FROM orders o
                     LEFT JOIN (SELECT invoice_id, sum(amount) AS paid FROM payments GROUP BY invoice_id) pp
                            ON pp.invoice_id = o.invoice_id
                    WHERE o.source = 'pos' AND o.status = 'delivered' AND o.recognised_at IS NULL
                      AND (o.created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s""", p)
    r = cur.fetchone()
    cur.execute("""SELECT COALESCE(sum(amount), 0) AS v, count(*) AS n FROM customer_ledger_entries
                    WHERE kind = 'credit' AND occurred_on BETWEEN %(s)s AND %(e)s""", p)
    l = cur.fetchone()
    out["udhaar_given"] = {"total": _m(float(r["v"]) + float(l["v"])), "till_bills": int(r["n"]), "ledger_entries": int(l["n"])}

    cur.execute("""SELECT COALESCE(sum(amount), 0) AS v FROM payments
                    WHERE (paid_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s""", p)
    out["collected"] = _m(cur.fetchone()["v"])
    cur.execute("SELECT COALESCE(sum(amount), 0) AS v FROM expenses WHERE spent_on BETWEEN %(s)s AND %(e)s", p)
    out["expenses"] = _m(cur.fetchone()["v"])

    # sold lines in the period, with each product's average cost where it is known
    cur.execute("""WITH ac AS (""" + _AVG_COST + """)
                   SELECT i.product_id::text AS pid, COALESCE(p.name, i.description) AS name, p.unit,
                          sum(i.quantity) AS qty, sum(i.line_total) AS money, max(ac.cost) AS cost
                     FROM order_items i JOIN orders o ON o.id = i.order_id
                     LEFT JOIN products p ON p.id = i.product_id
                     LEFT JOIN ac ON ac.product_id = i.product_id
                    WHERE o.recognised_at IS NOT NULL
                      AND (o.recognised_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s
                    GROUP BY 1, 2, 3""", p)
    lines = cur.fetchall()
    covered = cogs = line_money = 0.0
    no_cost = []
    for x in lines:
        money = float(x["money"] or 0)
        line_money += money
        if x["pid"] and x["cost"] is not None:
            covered += money
            cogs += float(x["qty"]) * float(x["cost"])
        elif x["pid"]:
            no_cost.append(x["name"])
    gross = round(covered - cogs, 2)
    out["profit"] = {"estimate": True, "gross": gross if covered else None,
                     "after_expenses": round(gross - out["expenses"], 2) if covered else None,
                     "covered_sales": _m(covered), "item_sales": _m(line_money),
                     "coverage": round(covered / line_money, 3) if line_money else None,
                     "products_without_cost": sorted(no_cost)[:10]}

    best = [x for x in lines if x["pid"]]
    out["best_sellers"] = {
        "by_quantity": [{"product_id": x["pid"], "name": x["name"], "unit": x["unit"], "quantity": _q(x["qty"]),
                         "money": _m(x["money"])} for x in sorted(best, key=lambda x: -float(x["qty"]))[:5]],
        "by_money": [{"product_id": x["pid"], "name": x["name"], "unit": x["unit"], "quantity": _q(x["qty"]),
                      "money": _m(x["money"])} for x in sorted(best, key=lambda x: -float(x["money"] or 0))[:5]]}

    # stock: on hand, value at average cost, low and out
    cur.execute("""WITH ac AS (""" + _AVG_COST + """), oh AS (""" + _ON_HAND + """)
                   SELECT p.id::text AS pid, p.name, p.unit, p.low_stock_threshold AS low,
                          COALESCE(oh.on_hand, 0) AS on_hand, ac.cost
                     FROM products p LEFT JOIN oh ON oh.product_id = p.id LEFT JOIN ac ON ac.product_id = p.id
                    WHERE p.active AND (p.track_stock OR oh.on_hand IS NOT NULL)""")
    stock = cur.fetchall()
    value = 0.0
    valued = unvalued = 0
    low = []
    for x in stock:
        oh = float(x["on_hand"] or 0)
        if oh > 0 and x["cost"] is not None:
            value += oh * float(x["cost"])
            valued += 1
        elif oh > 0:
            unvalued += 1
        if oh <= 0 or (x["low"] is not None and oh <= float(x["low"])):
            low.append({"product_id": x["pid"], "name": x["name"], "unit": x["unit"], "on_hand": _q(oh),
                        "warn_at": _q(x["low"]) if x["low"] is not None else None, "out": oh <= 0})
    low.sort(key=lambda x: (not x["out"], x["name"]))
    out["stock"] = {"value": _m(value), "estimate": True, "products_valued": valued,
                    "products_without_cost": unvalued, "low": low[:20], "low_count": len(low)}

    # who owes whom
    cur.execute("SELECT COALESCE(sum(balance) FILTER (WHERE balance > 0), 0) AS v, count(*) FILTER (WHERE balance > 0) AS n FROM customer_udhaar")
    r = cur.fetchone()
    out["udhaar"] = {"total": _m(r["v"]), "customers": int(r["n"])}
    cur.execute("SELECT COALESCE(sum(owed) FILTER (WHERE owed > 0), 0) AS v, count(*) FILTER (WHERE owed > 0) AS n FROM supplier_balances")
    r = cur.fetchone()
    out["owed_to_suppliers"] = {"total": _m(r["v"]), "suppliers": int(r["n"])}

    out["reorder"] = reorder(cur, tz, today)
    return out


def reorder(cur, tz, today):
    """At the last PACE_DAYS' pace, what runs out within RUNOUT_DAYS. A suggestion."""
    since = today - _dt.timedelta(days=PACE_DAYS - 1)
    cur.execute("""WITH sold AS (
                     SELECT i.product_id, sum(i.quantity) AS qty FROM order_items i JOIN orders o ON o.id = i.order_id
                      WHERE i.product_id IS NOT NULL AND o.status = 'delivered'
                        AND (o.created_at AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s
                      GROUP BY 1), oh AS (""" + _ON_HAND + """)
                   SELECT p.id::text AS pid, p.name, p.unit, sold.qty, COALESCE(oh.on_hand, 0) AS on_hand
                     FROM sold JOIN products p ON p.id = sold.product_id LEFT JOIN oh ON oh.product_id = p.id
                    WHERE p.active AND (p.track_stock OR oh.on_hand IS NOT NULL)""",
                {"tz": tz, "s": since, "e": today})
    out = []
    for x in cur.fetchall():
        rate = float(x["qty"]) / PACE_DAYS
        oh = max(float(x["on_hand"] or 0), 0.0)
        if rate <= 0:
            continue
        days = oh / rate
        if days < RUNOUT_DAYS:
            need = rate * COVER_DAYS - oh
            out.append({"product_id": x["pid"], "name": x["name"], "unit": x["unit"], "on_hand": _q(oh),
                        "per_day": round(rate, 2), "days_left": round(days, 1),
                        "suggest": float(math.ceil(need)) if need > 0 else 0.0})
    out.sort(key=lambda x: x["days_left"])
    return {"suggestion": True, "pace_days": PACE_DAYS, "runs_out_within": RUNOUT_DAYS, "cover_days": COVER_DAYS,
            "items": out[:15]}


def make_shop_router(identity):
    router = APIRouter(prefix="/shop", tags=["shop"])

    @router.get("/summary")
    def shop_summary(period: str = "today",
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The shop's day (or week, or month so far): sales, profit estimate, stock, udhaar, what to reorder."""
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if role not in MONEY_ROLES:
            raise HTTPException(403, "The shop's figures are for the owner.")
        if period not in PERIODS:
            raise HTTPException(400, "Choose today, week or month.")
        with tenant_session(tid, role) as cur:
            return summary(cur, period)

    return router
