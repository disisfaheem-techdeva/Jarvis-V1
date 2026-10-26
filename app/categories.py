"""Business type and what the WhatsApp agent knows (4 Oct 2026).

Found on the live server: the WhatsApp agent was given only the business NAME.
Its real services, prices and hours never reached it, and the template's words
assumed a clinic, so a lead was offered services the business does not have.

  business type   picked by the owner on first login (onboarding gate). It is a
                  row in business_categories: the dashboard's words and tiles,
                  the lead stage names, the WhatsApp menu, and the agent's rules
                  for that kind of business. A new type is a new row.
  knowledge       FAQs, policies and things the business does NOT offer, beside
                  the services, prices and hours the dashboard already keeps.
  agent sync      all of it is written into the business's own agent (SOUL.md,
                  between markers, through its pairctl) with one hard rule:
                  offer and quote only what is listed. Re-sent whenever it
                  changes (the reply checker re-checks every few minutes).

The clinic's hand-built agents (irfan / clinic / salon / workspace keys) are
never written to from here.
"""
from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .whatsapp import _identity, _rows, _pairctl_json

PROTECTED = re.compile(r"irfan|clinic|salon|workspace")
KINDS = ("faq", "policy", "not_offered", "info")
ORDER_TYPES = ("shop", "restaurant")   # businesses that take orders even before products are listed
DAYS_LOWER = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
CAT_COLS = ("slug, name, blurb, icon, sort_order, labels, dashboard_widgets, pipeline_stages, "
            "agent_menu, agent_rules, intake_fields")


def _owner(role):
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can change this.")


def category_for(cur, tid):
    cur.execute("SELECT name, business_type, business_type_other FROM tenants WHERE id = %s", (tid,))
    t = cur.fetchone()
    if not t:
        raise HTTPException(404, "No such business.")
    cat = None
    if t["business_type"]:
        cur.execute("SELECT " + CAT_COLS + " FROM business_categories WHERE slug = %s", (t["business_type"],))
        cat = cur.fetchone()
    return dict(t), (dict(cat) if cat else None)


def facts(cur, tid):
    """Everything the agent may say about this business, from the business's own records."""
    t, cat = category_for(cur, tid)
    cur.execute("""SELECT name, description, category, price, currency, duration_minutes
                     FROM services WHERE tenant_id = %s AND active
                    ORDER BY sort_order, name LIMIT 300""", (tid,))
    services = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT day_of_week, opens, closes, is_closed FROM business_hours
                    WHERE tenant_id = %s AND location_id IS NULL ORDER BY day_of_week""", (tid,))
    hours = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT name, description, category, price, currency FROM products
                    WHERE tenant_id = %s AND active ORDER BY name LIMIT 300""", (tid,))
    products = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT id::text AS id, kind, title, body FROM business_knowledge
                    WHERE tenant_id = %s AND active ORDER BY kind, created_at LIMIT 300""", (tid,))
    knowledge = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT approved FROM business_profiles WHERE tenant_id = %s AND approved IS NOT NULL", (tid,))
    p = cur.fetchone()
    return {"business": t, "category": cat, "services": services, "products": products, "hours": hours,
            "knowledge": knowledge, "website": website_facts(p["approved"] if p else None)}


TEST_PER_DAY = 30         # dashboard test messages a business may send its agent per day
WEBSITE_MAX = 6000         # the website section never crowds out the lists and the hard rule


def _observed(x):
    """The text of one profile claim, only when it was read ON the website
    (app/website_agent.py marks every claim observed / derived / inferred)."""
    if isinstance(x, dict) and x.get("basis") == "observed":
        v = x.get("value")
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:300]
    return None


def website_facts(profile):
    """What the agent may repeat from the owner-approved website profile: only
    claims seen on the site itself, never derived or guessed ones, and never a
    price (prices come from the services and products lists alone)."""
    if not isinstance(profile, dict):
        return []
    out = []
    ident = profile.get("identity") or {}
    for k, label in (("description", "What it does"), ("tagline", "In one line")):
        v = _observed(ident.get(k))
        if v:
            out.append("%s: %s" % (label, v))
    c = profile.get("contact") or {}
    for k, label in (("address", "Address"), ("hours", "Hours (as the website says)")):
        v = _observed(c.get(k))
        if v:
            out.append("%s: %s" % (label, v))
    for k, label in (("phones", "Phone"), ("emails", "Email"), ("service_areas", "Serves")):
        vals = [v for v in (_observed(x) for x in (c.get(k) or [])) if v]
        if vals:
            out.append("%s: %s" % (label, ", ".join(dict.fromkeys(vals))))
    for k, label in (("value_proposition", "Why customers choose it"), ("differentiators", "Strength"), ("policies", "Policy"), ("offers", "Offer on the website")):
        items = profile.get(k)
        for x in (items if isinstance(items, list) else [items])[:8]:
            v = _observed(x)
            if v and not re.search(r"(?i)\b(rs\.?|pkr|usd|\$)\s?\d", v):
                out.append("%s: %s" % (label, v))
    for x in (profile.get("faqs") or [])[:12]:
        if isinstance(x, dict) and x.get("basis") == "observed":
            v = x.get("value")
            if isinstance(v, dict):
                q, a = v.get("question"), v.get("answer")
            else:
                q, a = x.get("question"), x.get("answer")
            if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip() \
                    and not re.search(r"(?i)\b(rs\.?|pkr|usd|\$)\s?\d", a):
                out.append("Q: %s A: %s" % (" ".join(q.split())[:200], " ".join(a.split())[:300]))
    text, kept = 0, []
    for line in out:
        text += len(line) + 3
        if text > WEBSITE_MAX:
            break
        kept.append(line)
    return kept


GUIDE_MAX = 50000          # the whole guide (raised from 20,000 on the owner's request, 26 Oct 2026)
AGENT_MAX = 60000          # the agent keeps only the first 60,000 characters of the knowledge block
                           # (ops/pairctl writeKnowledge); anything past it, the hard rule at the end
                           # included, would be dropped silently, so a guide that overflows is refused
GUIDE_PART = 2000          # business_knowledge.body is at most 2,000 characters per row


def split_guide(text):
    """The owner's guide in parts of at most GUIDE_PART characters, cut at a blank
    line where possible, then at a line end, never inside a word when avoidable.
    Joining the parts with "" gives back exactly the text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = []
    while len(text) > GUIDE_PART:
        cut = text.rfind("\n\n", 0, GUIDE_PART)
        if cut < GUIDE_PART // 2:
            cut = text.rfind("\n", 0, GUIDE_PART)
        if cut < GUIDE_PART // 2:
            cut = text.rfind(" ", 0, GUIDE_PART)
        if cut < GUIDE_PART // 2:
            cut = GUIDE_PART - 1
        parts.append(text[:cut + 1])
        text = text[cut + 1:]
    if text:
        parts.append(text)
    return parts


def guide_text(f):
    """The guide's parts in order, joined back into the text the owner wrote."""
    parts = sorted((x for x in f["knowledge"] if x["kind"] == "guide"), key=lambda x: x["title"])
    return "".join(x["body"] for x in parts).strip()


def _price(s):
    if s.get("price") is None:
        return "price on request"
    p = float(s["price"])
    amt = "{:,.0f}".format(p) if p == int(p) else "{:,.2f}".format(p)
    return ("Rs " + amt) if (s.get("currency") or "PKR") == "PKR" else "%s %s" % (s["currency"], amt)


def knowledge_block(f):
    """The text written into the agent. Deterministic, so a hash tells if it changed."""
    t, cat = f["business"], f["category"]
    words = (cat or {}).get("labels") or {}
    services_word = words.get("services", "Services")
    if f.get("products") and services_word.lower() == "products":
        services_word = "Services"          # real products are listed in their own section
    kind = (cat or {}).get("name") or "service business"
    kind = {"restaurant": "restaurant or café", "clinic": "clinic", "salon": "salon, barber or spa",
            "gym": "gym or fitness studio", "academy": "academy or training centre", "shop": "shop",
            "other": "business"}.get((cat or {}).get("slug"), kind)
    if t.get("business_type") == "other" and t.get("business_type_other"):
        kind = t["business_type_other"]
    kind = (kind or "business").strip()
    if t.get("business_type") != "other":
        kind = kind.lower()                     # "a restaurant, café or food"; the owner's own words keep their case
    article = "an" if re.match(r"(?i)^(?:[aeiou]|it\b|hr\b|fm\b|mri\b|x-?ray)", kind) else "a"
    out = ["## About this business",
           "The business's name is %s. If anything earlier in this file uses another name, use %s." % (t["name"], t["name"]),
           "%s is %s %s." % (t["name"], article, kind),
           "If anything earlier in this file disagrees with this section, this section wins: "
           "it is the truth about this business."]
    out += ["", "## %s (the ONLY ones you may offer)" % services_word]
    if f["services"]:
        for s in f["services"]:
            line = "- %s: %s" % (s["name"], _price(s))
            if s.get("duration_minutes"):
                line += " (%s min)" % s["duration_minutes"]
            if s.get("description"):
                line += ". " + str(s["description"]).strip()[:200]
            out.append(line)
    elif not f.get("products"):
        out.append("- None listed yet. Do not name or price any service: take the customer's question, "
                   "their name and number, and say the team will reply shortly.")
    else:
        out.append("- None. This business sells the products below.")
    if f.get("products"):
        out += ["", "## Products for sale (the ONLY ones you may sell, price each)"]
        for p in f["products"]:
            line = "- %s: %s" % (p["name"], _price(p))
            if p.get("description"):
                line += ". " + str(p["description"]).strip()[:200]
            out.append(line)
    if f["hours"]:
        out += ["", "## Opening hours"]
        for h in f["hours"]:
            d = DAYS[h["day_of_week"]] if 0 <= h["day_of_week"] <= 6 else str(h["day_of_week"])
            out.append("- %s: %s" % (d, "closed" if h["is_closed"] else "%s to %s" % (
                str(h["opens"])[:5], str(h["closes"])[:5])))
    by = {k: [x for x in f["knowledge"] if x["kind"] == k] for k in KINDS}
    if by["info"] or by["policy"] or by["faq"]:
        out += ["", "## Facts and policies"]
        for x in by["info"] + by["policy"]:
            out.append("- %s%s" % (x["title"], (": " + x["body"]) if x["body"] else ""))
        for x in by["faq"]:
            out.append("- Q: %s A: %s" % (x["title"], x["body"] or "(the team will answer)"))
    if f.get("website"):
        out += ["", "## From the business's own website (approved by the owner)",
                "Use these to answer questions about the business. The lists above still decide what is "
                "offered and at what price; if a line here disagrees with them, the lists win."]
        out += ["- " + x for x in f["website"]]
    if by["not_offered"]:
        out += ["", "## This business does NOT offer"]
        for x in by["not_offered"]:
            out.append("- %s%s" % (x["title"], (": " + x["body"]) if x["body"] else ""))
    if cat and cat.get("agent_rules"):
        out += ["", "## How to work for this kind of business", cat["agent_rules"]]
    menu = (cat or {}).get("agent_menu") or []
    if menu:
        out += ["", "## When someone first writes, offer these choices",
                " / ".join("%d. %s" % (i + 1, m) for i, m in enumerate(menu))]
    if f.get("products") or (t.get("business_type") in ORDER_TYPES):
        out += ["", "## Taking an order",
                "When a customer wants to buy, ask for: which item(s) from the lists above and how many, their "
                "name, and delivery (with the full address) or pickup. For an order never ask for an appointment "
                "day or time. Once the customer has said yes to the items and you have the name and the address "
                "(or pickup), send ONE message in exactly this form, with item names written exactly as listed:",
                "ORDER SUMMARY",
                "- 2 x <item name as listed>",
                "Name: <customer name>",
                "Delivery: <full address>   (or: Pickup)",
                "Total: Rs <sum of the listed prices>",
                "Then say the team will confirm the order and the delivery time. Never say the order is confirmed, "
                "dispatched or paid: only the team does that."]
    guide = guide_text(f)
    if guide:
        out += ["", "## The owner's guide: how to talk to customers",
                "The owner wrote this in their own words. Follow it: its tone, its steps and its example "
                "replies. The lists above still decide what is offered and at what price: if this guide names "
                "an item, price, time or offer that is not listed above, the list wins.",
                guide]
    out += ["", "## Language",
            "Reply in the language and script of the customer's LAST message:",
            "- Roman Urdu (Urdu written in English letters, e.g. \"kitne ka hai\", \"kal aa sakte hain?\") -> reply in Roman Urdu.",
            "- English (e.g. \"how much is it?\") -> reply in English.",
            "- Urdu script (e.g. \"\u06a9\u062a\u0646\u06d2 \u06a9\u0627 \u06c1\u06d2\u061f\") -> reply in Urdu script.",
            "- A mix -> the language most of the message is in. If unsure, Roman Urdu.",
            "- A voice message (it reaches you as \"The user sent a voice message~ Here's what they said: ...\") "
            "-> reply in Roman Urdu, or in English if they spoke English. Never reply in Hindi (Devanagari) script, "
            "and never repeat the transcript back.",
            "Switch only when the customer switches. Write service, product and business names exactly as "
            "listed, whatever the language."]
    out += ["", "## Greeting",
            "Say \"Welcome to %s\" (or any greeting) only in your FIRST reply in a chat. In every later "
            "reply, answer straight away: never start a reply with the welcome line again." % t["name"]]
    out += ["", "## Hard rule",
            "Offer, describe and price ONLY the %s listed above, at exactly the prices listed. "
            "If someone asks for anything that is not listed, say plainly that %s does not offer it, "
            "and suggest what it does offer. Never invent a service, price, time, discount or offer. "
            "If you are not sure, say the team will confirm." % ((services_word.lower() + " and products") if f.get("products") and f["services"] else
               ("products" if f.get("products") else services_word.lower()), t["name"])]
    return "\n".join(out)


def sync_knowledge(tid, force=False):
    """Write the business's knowledge into each of its own agents. Never raises.
    Returns 'no_agent', 'unchanged', 'synced:<n>' or 'failed:<why>'."""
    try:
        with tenant_session(tid, "techdeva_admin") as cur:
            conns = [r for r in _rows(cur, tid) if not PROTECTED.search(r["agent_key"] or "")]
            f = facts(cur, tid)
            text = knowledge_block(f)
            h = hashlib.sha256(text.encode()).hexdigest()[:16]
            cur.execute("SELECT hash, status FROM agent_knowledge_sync WHERE tenant_id = %s", (tid,))
            prev = cur.fetchone()
        if not conns:
            return "no_agent"
        if not force and prev and prev["hash"] == h and str(prev["status"]).startswith("synced"):
            return "unchanged"
        n, errors = 0, []
        for r in conns:
            try:
                _pairctl_json(r["pairctl_url"], "/soul/knowledge", "POST", {"text": text})
                n += 1
            except Exception as e:                      # one agent down must not block the others
                errors.append("%s: %s" % (r["agent_key"], getattr(e, "detail", e)))
        status = ("synced:%d" % n) if not errors else ("failed:" + "; ".join(errors))[:500]
        with tenant_session(tid, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO agent_knowledge_sync (tenant_id, hash, status, detail, synced_at)
                           VALUES (%s, %s, %s, %s, now())
                           ON CONFLICT (tenant_id) DO UPDATE SET hash = EXCLUDED.hash, status = EXCLUDED.status,
                                  detail = EXCLUDED.detail, synced_at = now()""",
                        (tid, h if not errors else None, status, None))
            record(cur, action="whatsapp.knowledge_sync", resource="agent_knowledge_sync",
                   resource_id=tid, actor="system", actor_type="system",
                   status="ok" if not errors else "error", detail={"agents": n, "hash": h},
                   error="; ".join(errors)[:500] or None, tenant_id=tid)
        return status
    except Exception as e:
        print("[categories] knowledge sync failed for %s: %s" % (tid, e), flush=True)
        return "failed:%s" % str(e)[:200]


def make_categories_router():
    router = APIRouter(tags=["business-type"])

    @router.get("/categories")
    def categories(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT " + CAT_COLS + " FROM business_categories WHERE active ORDER BY sort_order, name")
            return {"categories": [dict(r) for r in cur.fetchall()]}

    @router.get("/business-profile")
    def profile(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Business type, what the agent knows, and whether it reached the agent."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            f = facts(cur, tid)
            cur.execute("SELECT status, synced_at FROM agent_knowledge_sync WHERE tenant_id = %s", (tid,))
            s = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM agent_reply_flags WHERE tenant_id = %s AND status = 'open'", (tid,))
            flags = cur.fetchone()["n"]
            # the Calls page shows only where a phone line is set up (or calls were taken)
            cur.execute("SELECT (SELECT count(*) FROM voice_numbers) + (SELECT count(*) FROM (SELECT 1 FROM calls LIMIT 1) c) AS n")
            phone_lines = cur.fetchone()["n"]
            sug = None
            if role in ("owner", "techdeva_admin"):
                cur.execute("""SELECT id::text, draft, sources FROM setup_suggestions
                                WHERE tenant_id = %s AND status = 'ready' ORDER BY created_at DESC LIMIT 1""", (tid,))
                r = cur.fetchone()
                if r:
                    d = r["draft"] or {}
                    sug = {"id": r["id"], "business_type": d.get("business_type"),
                           "services": len(d.get("services") or []), "hours": len(d.get("hours") or []),
                           "knowledge": len(d.get("knowledge") or []),
                           "sources": [x for x in (r["sources"] or []) if x.get("ok")]}
        t = f["business"]
        return {"business_type": t["business_type"], "business_type_other": t["business_type_other"],
                "needs_type": not t["business_type"], "category": f["category"],
                "services_count": len(f["services"]) + len(f.get("products") or []), "hours_set": bool(f["hours"]),
                "products_count": len(f.get("products") or []),
                "phone_lines": phone_lines,
                "knowledge": f["knowledge"], "open_flags": flags,
                "sync": dict(s) if s else {"status": "never", "synced_at": None},
                "suggestion": sug,
                "agent_text": knowledge_block(f) if role in ("owner", "techdeva_admin") else None}

    @router.get("/business-profile/training")
    def training(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Where the WhatsApp agent's knowledge comes from (Hermes AI > How your agent learns)."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            f = facts(cur, tid)
            cur.execute("SELECT status, version, approved_at FROM business_profiles WHERE tenant_id = %s", (tid,))
            p = cur.fetchone()
            cur.execute("""SELECT count(*) AS n, count(*) FILTER (WHERE said_as LIKE 'WhatsApp /sikho%%') AS wa
                             FROM agent_lessons WHERE tenant_id = %s AND active AND agent = 'whatsapp'""", (tid,))
            les = cur.fetchone()
            cur.execute("SELECT status, synced_at FROM agent_knowledge_sync WHERE tenant_id = %s", (tid,))
            s = cur.fetchone()
            conns = [r for r in _rows(cur, tid) if not PROTECTED.search(r["agent_key"] or "")]
            cur.execute("""SELECT count(*) AS n FROM executions WHERE tenant_id = %s AND action = 'agent.test_chat'
                              AND at > now() - interval '1 day'""", (tid,))
            tests = cur.fetchone()["n"]
        g = guide_text(f)
        return {"website": {"status": p["status"] if p else "none", "version": p["version"] if p else None,
                            "approved_at": p["approved_at"] if p else None, "facts": len(f["website"])},
                "services": len(f["services"]), "products": len(f.get("products") or []), "hours_set": bool(f["hours"]),
                "knowledge": len([x for x in f["knowledge"] if x["kind"] != "guide"]), "guide_chars": len(g),
                "lessons": les["n"], "lessons_whatsapp": les["wa"],
                "sync": dict(s) if s else {"status": "never", "synced_at": None},
                "agent": bool(conns), "tests_today": tests, "tests_per_day": TEST_PER_DAY}

    @router.post("/business-profile/test-chat")
    def test_chat(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner writes to their own agent as a customer would, from the dashboard.
        Same agent, same knowledge, its own session (not WhatsApp: nothing is sent to
        anyone, and the chat does not show in the inbox). Each message is one AI
        request, so there is a daily cap."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        msg = " ".join(str(body.get("message") or "").split())
        if not msg or len(msg) > 1000:
            raise HTTPException(400, "Write a message of up to 1,000 characters.")
        with tenant_session(tid, role) as cur:
            conns = [r for r in _rows(cur, tid) if not PROTECTED.search(r["agent_key"] or "")]
            if not conns:
                raise HTTPException(409, "Your WhatsApp agent is not set up yet.")
            cur.execute("""SELECT count(*) AS n FROM executions WHERE tenant_id = %s AND action = 'agent.test_chat'
                              AND at > now() - interval '1 day'""", (tid,))
            if cur.fetchone()["n"] >= TEST_PER_DAY:
                raise HTTPException(429, "That is today's %d test messages. Try again tomorrow." % TEST_PER_DAY)
            record(cur, action="agent.test_chat", resource="agents", resource_id=conns[0]["agent_key"],
                   actor=str(uid or role), actor_type="human", status="ok", detail={"chars": len(msg)}, error=None,
                   tenant_id=tid)
        agent = conns[0]["agent_key"]
        if not re.fullmatch(r"[a-z0-9-]{3,60}", agent or ""):
            raise HTTPException(409, "This agent cannot be tested from here.")
        conv = re.sub(r"[^A-Za-z0-9]", "", str(body.get("chat") or ""))[:16] or "1"
        sid = ("tdtest-%s-%s" % (str(tid).replace("-", "")[:12], conv))[:64]
        from . import hermes_client as hc
        import os
        headers = {"Content-Type": "application/json"}
        if os.environ.get("HERMES_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["HERMES_API_KEY"]
        base = "http://hermes-agent-%s:8642" % agent
        try:
            hc._ensure_session(base, sid, "", headers, 10, 0)
            _st, out = hc._post_with_retries(base + "/api/sessions/%s/chat" % sid, {"message": msg}, headers, 120, 0)
        except Exception as e:                      # the agent is down or the model failed
            raise HTTPException(502, "Your agent did not answer (%s). On WhatsApp the customer would get no reply "
                                     "and you would get an alert." % str(e)[:160])
        m = (out or {}).get("message")
        reply = m.get("content") if isinstance(m, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise HTTPException(502, "Your agent gave no answer.")
        if reply.startswith("API call failed"):
            # the model call failed; on WhatsApp this never reaches the customer (T10)
            why = ("the AI key has used up its free requests for today" if re.search(r"\b429\b|RESOURCE_EXHAUSTED|quota", reply)
                   else "the AI service refused the request")
            raise HTTPException(502, "Your agent could not answer: %s. On WhatsApp the customer gets no reply and you get "
                                     "an alert. Ask TechDeva to give your agent another key (API Manager)." % why)
        return {"reply": reply[:4000], "chat": conv}

    @router.put("/business-profile/type")
    def set_type(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        slug = str(body.get("slug") or "").strip()
        other = re.sub(r"\s+", " ", str(body.get("other") or "")).strip()[:120] or None
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT slug FROM business_categories WHERE slug = %s AND active", (slug,))
            if not cur.fetchone():
                raise HTTPException(400, "Pick one of the business types.")
        if slug == "other" and not other:
            raise HTTPException(400, "Tell us in a few words what your business does.")
        # The tenants row is not writable from a business session (tenant_self is
        # read-only by design), so like the owner's rename (portal.py) the write
        # runs as admin, limited to the caller's own, already verified business.
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""UPDATE tenants SET business_type = %s, business_type_other = %s
                            WHERE id = %s RETURNING business_type""", (slug, other if slug == "other" else None, tid))
            if not cur.fetchone():
                raise HTTPException(404, "No such business.")
            record(cur, action="business.type_set", resource="tenants", resource_id=tid, actor=str(uid),
                   actor_type="human", status="ok", detail={"type": slug, "other": other}, tenant_id=tid)
        from .dashcustom import on_type_change          # Dashboard Customizer; no-op while off
        on_type_change(tid, slug, str(uid or role))
        return {"business_type": slug, "sync": sync_knowledge(tid)}

    def _kn_body(body):
        kind = str(body.get("kind") or "").strip()
        title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()
        text = str(body.get("body") or "").strip()
        if kind not in KINDS:
            raise HTTPException(400, "kind must be faq, policy, not_offered or info")
        if not 1 <= len(title) <= 300:
            raise HTTPException(400, "Write a short title or question (up to 300 characters).")
        if len(text) > 2000:
            raise HTTPException(400, "Keep the answer under 2,000 characters.")
        return kind, title, text

    @router.post("/business-profile/knowledge")
    def add_knowledge(body: dict = Body(...), x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None), authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        kind, title, text = _kn_body(body)
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO business_knowledge (tenant_id, kind, title, body, created_by)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id::text AS id""",
                        (tid, kind, title, text, uid if uid and len(str(uid)) == 36 else None))
            kid = cur.fetchone()["id"]
            record(cur, action="business.knowledge_add", resource="business_knowledge", resource_id=kid,
                   actor=str(uid), actor_type="human", status="ok", detail={"kind": kind})
        return {"id": kid, "sync": sync_knowledge(tid)}

    @router.put("/business-profile/knowledge/{kid}")
    def edit_knowledge(kid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                       x_role: str | None = Header(None), authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        kind, title, text = _kn_body(body)
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE business_knowledge SET kind = %s, title = %s, body = %s, updated_at = now()
                            WHERE id::text = %s AND tenant_id = %s AND active RETURNING id""", (kind, title, text, kid, tid))
            if not cur.fetchone():
                raise HTTPException(404, "No such entry.")
            record(cur, action="business.knowledge_edit", resource="business_knowledge", resource_id=kid,
                   actor=str(uid), actor_type="human", status="ok", detail={"kind": kind})
        return {"id": kid, "sync": sync_knowledge(tid)}

    @router.delete("/business-profile/knowledge/{kid}")
    def remove_knowledge(kid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                         authorization: str | None = Header(None),
                         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE business_knowledge SET active = false, updated_at = now()
                            WHERE id::text = %s AND tenant_id = %s AND active RETURNING id""", (kid, tid))
            if not cur.fetchone():
                raise HTTPException(404, "No such entry.")
            record(cur, action="business.knowledge_remove", resource="business_knowledge", resource_id=kid,
                   actor=str(uid), actor_type="human", status="ok", detail={})
        return {"ok": True, "sync": sync_knowledge(tid)}

    # ---- the owner's full guide for the WhatsApp agent (db/82): kept exactly as written
    @router.get("/business-profile/guide")
    def get_guide(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT title, body, kind, updated_at FROM business_knowledge
                            WHERE tenant_id = %s AND active AND kind = 'guide'""", (tid,))
            rows = [dict(r) for r in cur.fetchall()]
        text = guide_text({"knowledge": rows})
        return {"text": text, "chars": len(text), "parts": len(rows), "max": GUIDE_MAX,
                "updated_at": max((r["updated_at"] for r in rows), default=None)}

    @router.put("/business-profile/guide")
    def put_guide(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Replace the guide with this text (empty removes it), then send it to the agent."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        text = str(body.get("text") or "").replace("<!--", "").replace("-->", "").strip()
        if len(text) > GUIDE_MAX:
            raise HTTPException(400, "The guide is %s characters; it can be up to %s." % (
                "{:,}".format(len(text)), "{:,}".format(GUIDE_MAX)))
        if text and len(text) < 20:
            raise HTTPException(400, "Write the full guide, or use the short box for a single rule.")
        parts = split_guide(text) if text else []
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE business_knowledge SET active = false, updated_at = now()
                            WHERE tenant_id = %s AND kind = 'guide' AND active""", (tid,))
            for i, part in enumerate(parts):
                cur.execute("""INSERT INTO business_knowledge (tenant_id, kind, title, body, created_by)
                               VALUES (%s, 'guide', %s, %s, %s)""",
                            (tid, "Owner's guide, part %02d of %02d" % (i + 1, len(parts)), part,
                             uid if uid and len(str(uid)) == 36 else None))
            # the whole block the agent receives must fit, or its end (the hard rule) would be cut off
            total = len(knowledge_block(facts(cur, tid)))
            if total > AGENT_MAX:
                raise HTTPException(400, "Together with your products, prices and rules this comes to %s characters; "
                                         "your agent can take %s. Shorten the guide by about %s characters." % (
                                             "{:,}".format(total), "{:,}".format(AGENT_MAX), "{:,}".format(total - AGENT_MAX)))
            record(cur, action="business.guide_" + ("set" if parts else "remove"), resource="business_knowledge",
                   resource_id=tid, actor=str(uid), actor_type="human", status="ok",
                   detail={"chars": len(text), "parts": len(parts)})
        return {"chars": len(text), "parts": len(parts), "sync": sync_knowledge(tid, force=True)}

    @router.post("/business-profile/sync")
    def sync_now(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        s = sync_knowledge(tid, force=True)
        if s == "no_agent":
            raise HTTPException(409, "No WhatsApp agent has been set up for this business yet.")
        if s.startswith("failed"):
            raise HTTPException(503, "Could not reach your WhatsApp agent. Try again in a minute.")
        return {"ok": True, "status": s}

    # ---- Set up my business with Jarvis: read, preview, then apply on "yes"
    def _slugs(cur):
        cur.execute("SELECT slug FROM business_categories WHERE active ORDER BY sort_order")
        return [r["slug"] for r in cur.fetchall()]

    @router.post("/business-profile/setup/read")
    def setup_read(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Read what the owner pasted into a draft. Saves nothing."""
        from .setup_reader import read_text, compare
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        text = str(body.get("text") or "")
        if len(text.strip()) < 10:
            raise HTTPException(400, "Paste your menu or price list, hours and anything customers ask about.")
        with tenant_session(tid, role) as cur:
            slugs = _slugs(cur)
        d = compare(tid, read_text(text, slugs))
        return {"draft": d, "source": text.strip()[:20000]}

    @router.post("/business-profile/setup/read-photo")
    async def setup_photo(request: Request, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                          authorization: str | None = Header(None),
                          td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A photo of the menu or price list: its words are read, then treated like pasted text."""
        from .setup_reader import read_text, compare, photo_to_text
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(400, "Send a JPG, PNG or WEBP photo.")
        data = await request.body()
        if not data or len(data) > 6 * 1024 * 1024:
            raise HTTPException(400, "The photo must be under 6 MB.")
        def work():                 # AI calls block: run them off the event loop
            text = photo_to_text(data, mime)
            with tenant_session(tid, role) as cur:
                slugs = _slugs(cur)
            return {"draft": compare(tid, read_text(text, slugs)), "source": text}
        try:
            return await run_in_threadpool(work)
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    # ---- what Jarvis read on the business's own Facebook page and website
    def _suggestion(cur, tid, sid=None):
        cur.execute("""SELECT id::text, draft, sources, source_text, status, created_at FROM setup_suggestions
                        WHERE tenant_id = %s AND (%s::uuid IS NULL OR id = %s::uuid)
                        ORDER BY created_at DESC LIMIT 1""", (tid, sid, sid))
        return cur.fetchone()

    @router.get("/business-profile/setup/suggestion")
    def get_suggestion(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The latest read of the business's own page and website (owner only)."""
        from .setup_reader import compare
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        with tenant_session(tid, role) as cur:
            r = _suggestion(cur, tid)
        if not r:
            return {"suggestion": None}
        d = r["draft"] or {}
        if r["status"] == "ready":
            d = compare(tid, {**{"services": [], "hours": [], "knowledge": [], "dropped": []}, **d})
        return {"suggestion": {"id": r["id"], "status": r["status"], "draft": d, "sources": r["sources"],
                               "source": r["source_text"], "read_at": r["created_at"].isoformat()}}

    @router.post("/business-profile/setup/from-page")
    async def read_page_now(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                            authorization: str | None = Header(None),
                            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Read the business's Facebook page and website again now."""
        from .page_reader import read_business
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        st = await run_in_threadpool(read_business, tid, True)
        if st == "no_sources":
            raise HTTPException(409, "Connect your Facebook page (Integrations) or add your website first.")
        return {"status": st}

    @router.post("/business-profile/setup/suggestion/{sid}/dismiss")
    def dismiss_suggestion(sid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                           authorization: str | None = Header(None),
                           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE setup_suggestions SET status = 'dismissed', decided_at = now()
                            WHERE tenant_id = %s AND id = %s::uuid AND status = 'ready' RETURNING id""", (tid, sid))
            if not cur.fetchone():
                raise HTTPException(404, "Nothing to dismiss.")
        return {"ok": True}

    @router.post("/business-profile/setup/apply")
    def setup_apply(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Save what the owner ticked in the preview. Adds; never deletes; a price
        is only changed where the owner ticked that change."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        d = body.get("draft") or {}
        change = {re.sub(r"\s+", " ", str(x)).strip().lower() for x in (body.get("change_prices") or [])}
        done = {"type": None, "services_added": 0, "prices_changed": 0, "hours_set": 0, "knowledge_added": 0, "skipped": 0}
        with tenant_session(tid, role) as cur:
            slugs = _slugs(cur)
        slug = str(d.get("business_type") or "")
        if slug in slugs:
            other = (str(d.get("business_type_other") or "").strip()[:120] or None) if slug == "other" else None
            if slug != "other" or other:
                with tenant_session(None, "techdeva_admin") as cur:      # see set_type
                    cur.execute("UPDATE tenants SET business_type = %s, business_type_other = %s WHERE id = %s",
                                (slug, other, tid))
                done["type"] = slug
        with tenant_session(tid, role) as cur:
            # a shop's items are products (with stock); everyone else's are services
            cur.execute("SELECT business_type FROM tenants WHERE id = %s", (tid,))
            table = "products" if (cur.fetchone() or {}).get("business_type") == "shop" else "services"
            cur.execute("SELECT lower(btrim(name)) AS n FROM services WHERE tenant_id = %s "
                        "UNION SELECT lower(btrim(name)) FROM products WHERE tenant_id = %s", (tid, tid))   # incl. removed: names are unique
            have = {r["n"] for r in cur.fetchall()}
            for i, s in enumerate((d.get("services") or [])[:300]):
                name = re.sub(r"\s+", " ", str((s or {}).get("name") or "")).strip()[:120]
                if not name:
                    continue
                try:
                    price = float(s["price"]) if s.get("price") not in (None, "") else None
                except (TypeError, ValueError):
                    price = None
                if price is not None and price < 0:
                    price = None
                if name.lower() in have:
                    if name.lower() in change and price is not None:
                        for tb in ("services", "products"):
                            cur.execute("UPDATE " + tb + """ SET price = %s, updated_at = now()
                                            WHERE tenant_id = %s AND lower(btrim(name)) = %s AND active""", (price, tid, name.lower()))
                            done["prices_changed"] += cur.rowcount
                    else:
                        done["skipped"] += 1
                    continue
                dur = s.get("duration_minutes") if isinstance(s.get("duration_minutes"), int) and 0 < s["duration_minutes"] < 1440 else None
                if table == "products":
                    cur.execute("""INSERT INTO products (tenant_id, name, price, category) VALUES (%s, %s, %s, %s)""",
                                (tid, name, price, (str(s.get("category") or "").strip()[:60] or None)))
                else:
                    cur.execute("""INSERT INTO services (tenant_id, name, price, category, duration_minutes, sort_order)
                                   VALUES (%s, %s, %s, %s, %s, %s)""",
                                (tid, name, price, (str(s.get("category") or "").strip()[:60] or None), dur, 1000 + i))
                have.add(name.lower()); done["services_added"] += 1
            for h in (d.get("hours") or [])[:7]:
                day = str((h or {}).get("day") or "").lower()
                if day not in DAYS_LOWER:
                    continue
                dow = DAYS_LOWER.index(day)
                closed = bool(h.get("closed"))
                o, c = str(h.get("opens") or ""), str(h.get("closes") or "")
                if not closed and not (re.match(r"^\d{1,2}:\d{2}$", o) and re.match(r"^\d{1,2}:\d{2}$", c)):
                    continue
                cur.execute("DELETE FROM business_hours WHERE tenant_id = %s AND location_id IS NULL AND day_of_week = %s", (tid, dow))
                cur.execute("""INSERT INTO business_hours (tenant_id, day_of_week, opens, closes, is_closed)
                               VALUES (%s, %s, %s, %s, %s)""", (tid, dow, None if closed else o, None if closed else c, closed))
                done["hours_set"] += 1
            cur.execute("SELECT lower(title) AS t FROM business_knowledge WHERE tenant_id = %s AND active", (tid,))
            titles = {r["t"] for r in cur.fetchall()}
            for k in (d.get("knowledge") or [])[:60]:
                kind = str((k or {}).get("kind") or "")
                title = re.sub(r"\s+", " ", str(k.get("title") or "")).strip()[:300]
                if kind not in KINDS or not title or title.lower() in titles:
                    continue
                cur.execute("""INSERT INTO business_knowledge (tenant_id, kind, title, body, created_by)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (tid, kind, title, str(k.get("body") or "").strip()[:2000], uid if uid and len(str(uid)) == 36 else None))
                titles.add(title.lower()); done["knowledge_added"] += 1
            sid = str(body.get("suggestion_id") or "")
            if re.match(r"^[0-9a-f-]{36}$", sid):
                cur.execute("""UPDATE setup_suggestions SET status = 'saved', decided_at = now()
                                WHERE tenant_id = %s AND id = %s::uuid AND status = 'ready'""", (tid, sid))
                done["from_page"] = bool(cur.rowcount)
            record(cur, action="business.setup_with_jarvis", resource="services", resource_id=tid, actor=str(uid),
                   actor_type="human", status="ok", detail=done)
        done["sync"] = sync_knowledge(tid)
        return done

    @router.get("/business-profile/checks")
    def checks(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT id::text AS id, kind, excerpt, detail, message_at, created_at, status
                             FROM agent_reply_flags WHERE tenant_id = %s AND status = 'open'
                            ORDER BY created_at DESC LIMIT 100""", (tid,))
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT scanned_at FROM agent_reply_scan WHERE tenant_id = %s", (tid,))
            sc = cur.fetchone()
        return {"flags": rows, "scanned_at": sc["scanned_at"] if sc else None}

    @router.post("/business-profile/checks/{fid}/dismiss")
    def dismiss(fid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _owner(role)
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE agent_reply_flags SET status = 'dismissed'
                            WHERE id::text = %s AND tenant_id = %s AND status = 'open' RETURNING id""", (fid, tid))
            if not cur.fetchone():
                raise HTTPException(404, "No such finding.")
            record(cur, action="whatsapp.reply_flag_dismiss", resource="agent_reply_flags", resource_id=fid,
                   actor=str(uid), actor_type="human", status="ok", detail={})
        return {"ok": True}

    return router
