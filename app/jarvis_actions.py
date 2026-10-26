"""Jarvis doing things, not only answering.

Every action runs through the platform's OWN endpoints (a loopback HTTP call
with a two-minute token for exactly the caller's tenant and role), never
through SQL written here. So a Jarvis booking is validated, permission-checked,
fenced by RLS, audited and trial-locked exactly like the same booking made by
hand on the Appointments page, and Jarvis can never do more than the person
talking to it could do themselves.

Money, removal and anything the public will see (an expense, a cancellation,
a scheduled post) is not done on the first sentence: Jarvis asks "shall I?"
and gets back a sealed, signed, 5-minute confirmation that only the same
person in the same business can redeem.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time

import requests
from fastapi import HTTPException

from .auth import _secret, issue_token
from .db import tenant_session

SELF_URL = os.environ.get("JARVIS_SELF_URL", "http://127.0.0.1:8000").rstrip("/")

SECTIONS = ("overview", "leads", "customers", "appointments", "calendar", "dailyentry",
            "revenue", "invoices", "expenses", "dayclose", "pnl", "services", "products",
            "packages", "catalogue", "inventory", "integrations", "reports", "settings",
            "console", "posts", "billing", "team", "whatsapp", "inbox", "needshuman",
            "marketing", "attendance", "hr", "hermes", "ai", "bizagent", "orders", "calls")

# What needs a "yes" first.
CONFIRM = {"add_expense", "cancel_appointment", "schedule_post", "publish_post",
           "record_order_payment", "cancel_order"}
# Dashboard Customizer (app/dashcustom.py): always asked first, owner only
DASH_OPS = {"dashboard_hide": "disable", "dashboard_show": "enable", "dashboard_reorder": "reorder",
            "dashboard_reset": "reset", "dashboard_set": "set"}
CONFIRM |= set(DASH_OPS)
ACTIONS = {"navigate", "book_appointment", "reschedule_appointment", "cancel_appointment",
           "complete_appointment", "add_customer", "add_lead", "add_expense", "schedule_post",
           "publish_post", "update_order", "record_order_payment", "cancel_order", "write_campaign",
           "add_followup", "complete_followup"} | set(DASH_OPS)
_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_USED = {}   # signature -> expiry of confirmations already used (one API process)


class ActionError(Exception):
    """A reason fit to say to the owner."""


# ------------------------------------------------------------ sealed confirm
def seal(tid, uid, role, action, args, ttl=300):
    raw = json.dumps({"t": str(tid), "u": str(uid or ""), "r": role, "a": action, "g": args,
                      "e": int(time.time()) + ttl}, separators=(",", ":"), default=str).encode()
    p = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    s = base64.urlsafe_b64encode(hmac.new(_secret(), b"jarvis:" + p.encode(),
                                          hashlib.sha256).digest()).decode().rstrip("=")
    return p + "." + s


def unseal(token, tid, uid, role):
    try:
        p, s = str(token).split(".", 1)
        want = base64.urlsafe_b64encode(hmac.new(_secret(), b"jarvis:" + p.encode(),
                                                 hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(want, s):
            raise ValueError
        d = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except Exception:
        raise HTTPException(400, "That confirmation is not valid. Ask me again.")
    if d.get("t") != str(tid) or d.get("u") != str(uid or "") or d.get("r") != role:
        raise HTTPException(403, "That confirmation belongs to someone else.")
    now = time.time()
    if d.get("e", 0) < now:
        raise HTTPException(410, "That took too long, so I did not do it. Ask me again.")
    # One "yes" does one thing: a double click must not record an expense twice.
    for k in [k for k, e in _USED.items() if e < now]:
        _USED.pop(k, None)
    if s in _USED:
        raise HTTPException(409, "That is already done.")
    _USED[s] = d["e"]
    return d["a"], d.get("g") or {}


# ------------------------------------------------------------ loopback
def _call(tid, role, uid, method, path, body=None):
    tok = issue_token(tenant_id=tid, user_id=str(uid or "jarvis"), role=role, ttl=120)
    try:
        r = requests.request(method, SELF_URL + path, json=body, timeout=25,
                             headers={"Authorization": "Bearer " + tok})
    except requests.RequestException:
        raise ActionError("the platform did not answer me, so nothing was changed")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        why = data.get("detail") if isinstance(data, dict) else None
        if isinstance(why, list):  # pydantic validation errors
            why = "; ".join(str(x.get("msg", x)) for x in why)[:200]
        raise ActionError(str(why or "it was refused (HTTP %d)" % r.status_code))
    return data


# ------------------------------------------------------------ lookups
def _find(cur, table, name, cols="id, name"):
    name = (name or "").strip()
    if not name:
        return None
    cur.execute("SELECT %s FROM %s WHERE name ILIKE %%s LIMIT 1" % (cols, table), (name,))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT %s FROM %s WHERE name ILIKE %%s ORDER BY length(name) LIMIT 1"
                    % (cols, table), ("%" + name + "%",))
        row = cur.fetchone()
    return row


def _appt(cur, appt_id):
    cur.execute("""SELECT a.id, a.status, a.service,
                          to_char(a.starts_at AT TIME ZONE 'Asia/Karachi', 'Dy DD Mon, HH12:MI AM') AS at,
                          c.name AS customer
                     FROM appointments a LEFT JOIN customers c ON c.id = a.customer_id
                    WHERE a.id::text = %s""", (str(appt_id or ""),))
    return cur.fetchone()


def _draft(cur, post_id):
    """The draft "post it" is about: the one named, else the newest draft of
    the last week. Only a draft (or a failed post) can still be posted."""
    pid = str(post_id or "").strip()
    if re.match(r"^[0-9a-f-]{36}$", pid):
        cur.execute("SELECT id, caption, platforms, image_urls FROM social_posts "
                    "WHERE id = %s AND status IN ('draft','failed')", (pid,))
    else:
        cur.execute("SELECT id, caption, platforms, image_urls FROM social_posts "
                    "WHERE status = 'draft' AND video_token IS NULL "
                    "AND created_at > now() - interval '7 days' ORDER BY created_at DESC LIMIT 1")
    return cur.fetchone()


def _post_check(role, founder):
    if founder:
        raise ActionError("posting is the client's own decision, so they have to do it")
    if role not in ("owner", "techdeva_admin"):
        raise ActionError("only the business owner can post for the business")


def _need(args, key, what):
    v = str(args.get(key) or "").strip()
    if not v:
        raise ActionError("I need the %s for that" % what)
    return v


def _date(v, what="date"):
    v = str(v or "").strip()
    if not _DATE.match(v):
        raise ActionError("I need the %s (which day)" % what)
    return v


def _time(v):
    v = str(v or "").strip()
    if not _HHMM.match(v):
        raise ActionError("I need the time")
    return v if len(v) == 5 else "0" + v


def _money(v):
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        raise ActionError("I need the amount")
    if n <= 0:
        raise ActionError("the amount has to be more than zero")
    return round(n, 2)


# ------------------------------------------------------------ describe (for "shall I?")
def describe(tid, role, action, args, founder=False):
    """Check the action can run and word the question. Raises ActionError."""
    if action in DASH_OPS:
        from . import dashcustom as dc
        if role not in ("owner", "techdeva_admin"):
            raise ActionError("only the business owner can change the dashboard")
        if not dc.enabled():
            raise ActionError("Customize Dashboard is not switched on")
        try:
            with tenant_session(tid, role) as cur:
                return dc.plan_text(cur, tid, DASH_OPS[action], args)
        except dc.DashError as e:
            raise ActionError(str(e))
    if action == "schedule_post":
        if founder:
            raise ActionError("scheduling a post is the client's own decision, so they have to do it")
        if role not in ("owner", "techdeva_admin"):
            raise ActionError("only the business owner can post for the business")
    if action == "publish_post":
        _post_check(role, founder)
        with tenant_session(tid, role) as cur:
            p = _draft(cur, args.get("post_id"))
        if not p:
            raise ActionError("there is no draft waiting to be posted. Tell me what the post is about and I'll make one")
        args["post_id"] = str(p["id"])      # sealed with the question: a yes posts exactly this one
        where = " and ".join(x.capitalize() for x in (p["platforms"] or ["facebook"]))
        n = len(p["image_urls"] or [])
        pics = " with {} picture{}".format(n, "" if n == 1 else "s") if n else ""
        return "Post this on {} now{}: “{}”?".format(where, pics, str(p["caption"])[:140])
    if action == "add_expense":
        amt = _money(args.get("amount"))
        what = _need(args, "description", "what the expense was for")
        return "Record an expense of PKR {:,.0f} for {}{}?".format(
            amt, what, " on " + args["spent_on"] if args.get("spent_on") else "")
    if action == "cancel_appointment":
        with tenant_session(tid, role) as cur:
            a = _appt(cur, args.get("appointment_id"))
        if not a:
            raise ActionError("I could not find that appointment. Which one do you mean?")
        return "Cancel {}'s appointment on {}?".format(a["customer"] or "the", a["at"])
    if action == "schedule_post":
        _need(args, "caption", "post text")
        _need(args, "at", "date and time to post")
        n = len(args.get("image_urls") or [])
        where = " and ".join(p.capitalize() for p in (args.get("platforms") or ["facebook", "instagram"]))
        pics = " with {} picture{}".format(n, "" if n == 1 else "s") if n else ""
        return "Schedule this post on {}{} for {}: “{}”?{}".format(
            where, pics, args.get("at_words") or args["at"], str(args["caption"])[:140],
            args.get("_note") or "")
    return "Go ahead?"


# ------------------------------------------------------------ run
def run(tid, role, uid, action, args, founder=False):
    """Carry out one action. Returns (result dict for the dashboard, short fact
    for the reply). Raises ActionError with a speakable reason."""
    args = args if isinstance(args, dict) else {}
    if action in DASH_OPS:
        body = dict(args, op=DASH_OPS[action], via="jarvis")
        out = _call(tid, role, uid, "POST", "/dashboard-config/change", body)
        return {"dashboard_config": {"version": out.get("version"), "hidden": out.get("hidden")}}, None
    if action == "navigate":
        sec = str(args.get("section") or "").strip().lower()
        if sec not in SECTIONS:
            raise ActionError("I do not have a page called that")
        return {"navigate": sec}, None

    if action == "book_appointment":
        name = _need(args, "name", "client's name")
        body = {"date": _date(args.get("date")), "time": _time(args.get("time")),
                "name": name, "phone": str(args.get("phone") or "").strip(),
                "status": "booked", "booked_by": "Jarvis"}
        notes = []
        with tenant_session(tid, role) as cur:
            svc = _find(cur, "services", args.get("service"), "id, name, price")
            st = _find(cur, "staff", args.get("staff"))
        if svc:
            body["services"] = [{"service_id": str(svc["id"]), "name": svc["name"], "qty": 1,
                                 "price": float(svc["price"] or 0)}]
        elif args.get("service"):
            notes.append("Service asked for: %s" % str(args["service"])[:120])
        if st:
            body["staff_id"] = str(st["id"])
        if args.get("notes"):
            notes.append(str(args["notes"])[:300])
        if notes:
            body["notes"] = ". ".join(notes)
        out = _call(tid, role, uid, "POST", "/appointments/book", body)
        return {"refresh": ["appointments", "customers"], "appointment": out.get("appointment_id") or out.get("id")}, \
            (None if svc or not args.get("service")
             else "that service isn't on your list, so I put it in the notes")

    if action in ("reschedule_appointment", "cancel_appointment", "complete_appointment"):
        aid = _need(args, "appointment_id", "appointment")
        with tenant_session(tid, role) as cur:
            if not _appt(cur, aid):
                raise ActionError("I could not find that appointment")
        if action == "reschedule_appointment":
            body = {"date": _date(args.get("date")), "time": _time(args.get("time"))}
        else:
            body = {"status": "cancelled" if action == "cancel_appointment" else "completed"}
        _call(tid, role, uid, "PATCH", "/appointments/" + aid, body)
        return {"refresh": ["appointments"]}, None

    if action in ("update_order", "record_order_payment", "cancel_order"):
        oid = _need(args, "order_id", "order")
        if not re.match(r"^[0-9a-f-]{36}$", oid):
            raise ActionError("I could not find that order")
        if action == "update_order":
            st = str(args.get("status") or "").strip().lower().replace(" ", "_")
            if st not in ("confirmed", "out_for_delivery", "delivered"):
                raise ActionError("I can mark an order confirmed, out for delivery or delivered")
            o = _call(tid, role, uid, "POST", "/orders/%s/status" % oid, {"status": st})
        elif action == "cancel_order":
            o = _call(tid, role, uid, "POST", "/orders/%s/status" % oid,
                      {"status": "cancelled", "reason": str(args.get("reason") or "").strip() or "Cancelled by the owner"})
        else:
            o = _call(tid, role, uid, "POST", "/orders/%s/pay" % oid,
                      {"amount": _money(args.get("amount")), "method": str(args.get("method") or "cash").lower()})
        who = o.get("customer_name") or o.get("number")
        if o.get("status") == "cancelled":
            note = "%s's order %s is cancelled and nothing is counted" % (who, o["number"])
        elif o.get("recognised"):
            note = "%s's order %s is delivered and fully paid, so Rs {:,.0f} is counted as a sale".format(o["total"]) % (who, o["number"])
        elif o.get("status") == "delivered" and o.get("payment_status") == "partial":
            note = ("%s's order %s is delivered; Rs {:,.0f} is received and Rs {:,.0f} is still owed, so it is "
                    "not counted as a sale until fully paid").format(o["paid"], o["balance"]) % (who, o["number"])
        elif o.get("status") == "delivered":
            note = ("%s's order %s is delivered, but payment is still pending, so Rs {:,.0f} is not counted "
                    "as collected sales yet").format(o["balance"]) % (who, o["number"])
        elif o.get("payment_status") == "paid":
            note = "%s's order %s is fully paid; it counts as a sale once delivered" % (who, o["number"])
        else:
            note = "%s's order %s is now %s" % (who, o["number"], o.get("status_label", "").lower())
        return {"refresh": ["orders"]}, note

    if action == "add_customer":
        body = {"name": _need(args, "name", "customer's name"), "source": "jarvis"}
        for k in ("phone", "email", "notes"):
            if args.get(k):
                body[k] = str(args[k]).strip()[:500]
        out = _call(tid, role, uid, "POST", "/business/customers", body)
        return {"refresh": ["customers"], "item": out.get("item")}, None

    if action == "add_lead":
        body = {"name": _need(args, "name", "person's name"), "source": "manual"}
        for k in ("phone", "service"):
            if args.get(k):
                body[k] = str(args[k]).strip()[:200]
        _call(tid, role, uid, "POST", "/leads", body)
        return {"refresh": ["leads"]}, None

    if action == "add_followup":
        # through /crm/activities with the person's own login: their role decides
        body = {"title": _need(args, "title", "what the follow-up is"), "via": "jarvis",
                "kind": args.get("kind") if args.get("kind") in ("task", "call", "visit", "meeting") else "task"}
        if args.get("due"):
            body["due_at"] = str(args["due"])
        if args.get("lead_id"):
            body["lead_id"] = str(args["lead_id"])
        else:
            with tenant_session(tid, role) as cur:
                c = _find(cur, "customers", _need(args, "customer_name", "which lead or customer"))
            if not c:
                raise ActionError("I could not find that lead or customer")
            body["customer_id"] = str(c["id"])
        out = _call(tid, role, uid, "POST", "/crm/activities", body)
        return {"refresh": ["followups"], "activity": out.get("activity")}, None

    if action == "complete_followup":
        body = {"done": True}
        if args.get("outcome"):
            body["outcome"] = str(args["outcome"])[:500]
        _call(tid, role, uid, "PATCH", "/crm/activities/" + _need(args, "followup_id", "which follow-up"), body)
        return {"refresh": ["followups"]}, None

    if action == "add_expense":
        body = {"description": _need(args, "description", "what the expense was for")[:300],
                "amount": _money(args.get("amount"))}
        for k in ("category", "method", "notes"):
            if args.get(k):
                body[k] = str(args[k]).strip()[:120]
        if args.get("spent_on"):
            body["spent_on"] = _date(args["spent_on"], "day it was spent")
        out = _call(tid, role, uid, "POST", "/business/expenses", body)
        return {"refresh": ["expenses"], "item": out.get("item")}, None

    if action == "schedule_post":
        if founder:
            raise ActionError("scheduling a post is the client's own decision, so they have to do it")
        if role not in ("owner", "techdeva_admin"):
            raise ActionError("only the business owner can post for the business")
        from .social import create_draft, is_our_media
        imgs = [u for u in (args.get("image_urls") or []) if isinstance(u, str) and is_our_media(u)]
        with tenant_session(tid, role) as cur:
            draft = create_draft(cur, tid, uid, _need(args, "caption", "post text"),
                                 args.get("platforms"), image_urls=imgs)
        try:
            out = _call(tid, role, uid, "POST", "/social/posts/%s/schedule" % draft["id"],
                        {"at": _need(args, "at", "date and time to post")})
        except ActionError as e:
            raise ActionError("I saved it as a draft on the Posts page, but could not schedule it: %s" % e)
        return {"refresh": ["posts"], "post": out.get("post")}, None

    if action == "publish_post":
        _post_check(role, founder)
        with tenant_session(tid, role) as cur:
            p = _draft(cur, args.get("post_id"))
        if not p:
            raise ActionError("that draft is not waiting any more; it may already be posted or discarded")
        out = _call(tid, role, uid, "POST", "/social/posts/%s/publish" % p["id"], {})
        res = out.get("results") or {}
        bad = ["%s (%s)" % (k.capitalize(), str((v or {}).get("error") or "refused")[:120])
               for k, v in res.items() if not (v or {}).get("ok")]
        if out.get("status") == "failed":
            raise ActionError("Facebook did not take it: %s. It is still in your drafts" % "; ".join(bad))
        note = None
        if out.get("status") == "queued":
            note = "the video is being processed and goes out within a minute or two"
        elif bad:
            note = "but it did not go out on %s" % "; ".join(bad)
        # what the platforms confirmed, so the reply never says more than happened
        return {"refresh": ["posts"], "post": out.get("post"), "post_status": out.get("status"),
                "post_results": {k: {"ok": bool((v or {}).get("ok")), "id": (v or {}).get("id"),
                                     "error": (v or {}).get("error")} for k, v in res.items()}}, note
    if action == "write_campaign":
        # Writes a DRAFT through the planner (same owner + Pro checks as the
        # Campaigns page) and puts the attached picture on it. It never
        # launches: spending starts only when the owner presses Launch.
        if founder:
            raise ActionError("an ad campaign is the client's own decision, so they have to ask for it")
        from .social import is_our_media
        prov = str(args.get("provider") or "meta").strip().lower()
        prov = {"facebook": "meta", "instagram": "meta", "fb": "meta"}.get(prov, prov)
        if prov not in ("meta", "google"):
            raise ActionError("I can write campaigns for Facebook and Instagram, or for Google")
        body = {"provider": prov, "service": (str(args.get("service") or "").strip() or None)}
        out = _call(tid, role, uid, "POST", "/adpilot/drafts", body)
        d = out.get("draft") or {}
        imgs = [u for u in (args.get("image_urls") or []) if isinstance(u, str) and is_our_media(u)]
        note = None
        if imgs and d.get("id") and prov == "meta":
            try:
                out = _call(tid, role, uid, "PUT", "/adpilot/drafts/%s" % d["id"], {"creatives": imgs})
                n = len(imgs)
                note = "with your picture" if n == 1 else "with your %d pictures" % n
            except ActionError as e:
                note = "but I could not put your pictures on it (%s); press Add creatives on the campaign" % e
        elif prov == "meta" and not d.get("image_url"):
            note = "with no picture yet: attach one, or upload it on the campaign"
        plan = ""
        try:     # the planner's plan for this draft: how many ads will run, and why
            mine = [x for x in (_call(tid, role, uid, "GET", "/adpilot").get("drafts") or []) if x.get("id") == d.get("id")]
            plan = ((mine[0].get("plan") or {}).get("text") or "").rstrip(".") if mine else ""
        except ActionError:
            pass
        return {"navigate": "marketing", "refresh": ["campaigns"], "draft": d}, (
            "I wrote a %s campaign for %s as a draft%s.%s Nothing runs until you press Launch on Campaigns" % (
                "Facebook and Instagram" if prov == "meta" else "Google", d.get("service") or "your best seller",
                (" " + note) if note else "", (" " + plan + ".") if plan else ""))

    raise ActionError("I cannot do that from here yet")
