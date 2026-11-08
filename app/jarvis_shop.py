"""Jarvis answers about the shop (Phase 8, 9 Nov 2026; app/shopintel.py).

    "Jarvis, aaj dukaan ka kya haal hai?"     -> shop_report topic summary
    "aaj kitna profit hua?"                  -> profit
    "kaunsa product sab se zyada bik raha?"  -> best_sellers (week by default)
    "kya kya kam hai?" / "kya mangwana hai?" -> low_stock / reorder
    "stock ki value kitni hai?"              -> stock_value

The model only picks the topic and the period. The words are built here from
GET /shop/summary, and an estimate is always said to be one, with what it covers.
"""
from __future__ import annotations

TOPICS = ("summary", "profit", "best_sellers", "low_stock", "reorder", "stock_value")
WHEN = {"today": "Aaj", "week": "Pichle 7 din", "month": "Is mahine"}


def _rs(v):
    v = float(v)
    return "Rs {:,.0f}".format(v) if v == int(v) else "Rs {:,.2f}".format(v)


def _q(v, unit):
    s = ("%.3f" % float(v)).rstrip("0").rstrip(".")
    return s + (" " + unit if unit and unit != "piece" else "")


def _names(xs, n=4):
    return ", ".join(xs[:n]) + (" aur %d aur" % (len(xs) - n) if len(xs) > n else "")


def _profit(d):
    p = d["profit"]
    if p["gross"] is None:
        if not d["sales"]["count"]:
            return "%s abhi koi sale nahi hui, is liye munafa nahi" % WHEN[d["period"]]
        return ("Munafa abhi nahi bata sakta: jo cheezein biki un ki purchase keemat record nahi. "
                "Purchases mein supplier ka bill likhein, phir main bata doonga")
    cov = int(round((p["coverage"] or 0) * 100))
    s = "%s andaza munafa %s; kharche ke baad %s" % (WHEN[d["period"]], _rs(p["gross"]), _rs(p["after_expenses"]))
    if cov < 100:
        s += ". Yeh andaza sirf %d%% sale par hai jin ki keemat pata hai" % cov
        if p["products_without_cost"]:
            s += " (keemat nahi: %s)" % _names(p["products_without_cost"], 3)
    return s


def say(d, topic):
    when = WHEN[d["period"]]
    if topic == "profit":
        return _profit(d)
    if topic == "best_sellers":
        b = d["best_sellers"]["by_quantity"]
        if not b:
            return "%s abhi koi sale nahi hui" % when
        return "%s sab se zyada biknay wali: %s" % (when, "; ".join(
            "%s %s (%s)" % (x["name"], _q(x["quantity"], x["unit"]), _rs(x["money"])) for x in b[:3]))
    if topic == "low_stock":
        low = d["stock"]["low"]
        if not low:
            return "Koi cheez kam nahi; sab apni warning level se upar hai"
        return "%d cheezein kam hain: %s" % (d["stock"]["low_count"], "; ".join(
            "%s %s" % (x["name"], "khatam" if x["out"] else _q(x["on_hand"], x["unit"]) + " baqi") for x in low[:5]))
    if topic == "reorder":
        r = d["reorder"]["items"]
        if not r:
            return "Pichle %d din ki sale ke hisaab se agle %d din mein kuch khatam nahi hoga" % (
                d["reorder"]["pace_days"], d["reorder"]["runs_out_within"])
        return "Andaza, pichle %d din ki sale par: %s" % (d["reorder"]["pace_days"], "; ".join(
            "%s %s din mein khatam, %s mangwa lein" % (x["name"], ("%.1f" % x["days_left"]).rstrip("0").rstrip("."),
                                                       _q(x["suggest"], x["unit"])) for x in r[:4]))
    if topic == "stock_value":
        s = d["stock"]
        if not s["products_valued"]:
            return "Stock ki value nahi bata sakta: kisi cheez ki purchase keemat record nahi"
        t = "Stock ki andazan value %s (%d cheezon ki, purchase keemat par)" % (_rs(s["value"]), s["products_valued"])
        if s["products_without_cost"]:
            t += "; %d cheezon ki keemat record nahi, woh shamil nahi" % s["products_without_cost"]
        return t
    # summary
    parts = ["%s ki sale %s (%d bill%s)" % (when, _rs(d["sales"]["total"]), d["sales"]["count"],
                                           "" if d["sales"]["count"] == 1 else "s")]
    if d["udhaar_given"]["total"]:
        parts.append("udhaar diya %s" % _rs(d["udhaar_given"]["total"]))
    if d["expenses"]:
        parts.append("kharcha %s" % _rs(d["expenses"]))
    if d["profit"]["gross"] is not None:
        cov = int(round((d["profit"]["coverage"] or 0) * 100))
        parts.append("andaza munafa %s%s" % (_rs(d["profit"]["gross"]), "" if cov >= 100 else " (%d%% sale par)" % cov))
    t = ", ".join(parts)
    low = d["stock"]["low"]
    t += ". " + ("%d cheezein kam: %s" % (d["stock"]["low_count"], _names([x["name"] for x in low])) if low
                 else "Stock theek hai")
    if d["udhaar"]["total"]:
        t += ". Customers ka udhaar %s" % _rs(d["udhaar"]["total"])
    if d["owed_to_suppliers"]["total"]:
        t += "; suppliers ko %s dena hai" % _rs(d["owed_to_suppliers"]["total"])
    return t


def run(tid, role, uid, args):
    from . import jarvis_actions as ja
    topic = str(args.get("topic") or "summary").strip().lower()
    if topic not in TOPICS:
        topic = "summary"
    period = str(args.get("period") or "").strip().lower()
    if period not in WHEN:
        period = "week" if topic == "best_sellers" else "today"
    d = ja._call(tid, role, uid, "GET", "/shop/summary?period=" + period)
    return {"shop": {"period": period, "topic": topic}}, say(d, topic)
