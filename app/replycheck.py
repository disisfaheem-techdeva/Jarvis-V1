"""Reply checker: reads what each business's WhatsApp agent actually said.

The platform's price guard (agents/guards.py) only saw replies that went through
the platform. The WhatsApp agents built from the template answer customers
directly, so nothing read their replies: a lead was offered services the
business does not have and nobody knew (4 Oct 2026).

Every 10 minutes, for every business with a template-built agent (never the
clinic's hand-built ones):
  1. keeps the agent's knowledge current (categories.sync_knowledge: re-sent
     only when services, prices, hours or FAQs changed),
  2. reads the agent's replies written since the last pass (pairctl /chats),
  3. flags them:
       bot_error        the agent sent an error or a "something went wrong"
       unbacked_claim   a price, discount or promise not in the business's own
                        records (the existing guard, guards.unbacked_claims)
       unknown_service  it offered something not on the business's list
                        (asked of the AI with the real list; skipped, never
                        guessed, when the AI is not available)
  4. stores each finding once (agent_reply_flags) and opens an incident on
     that business, which its owner and the founder see.
It only reads. It never sends, edits or deletes a message.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import time

from .agents.guards import unbacked_claims
from .orders import order_from_chat, parse_summary
from .db import tenant_session
from .whatsapp import _pairctl_json

EVERY_S = 600
FIRST_LOOK = _dt.timedelta(hours=24)
MAX_MSGS = 40
_last = 0.0

ERROR = re.compile(
    r"sorry,? (?:i|we) (?:encountered|ran into|hit|had) (?:an? )?(?:error|issue|problem)|something went wrong|"
    r"an error (?:occurred|has occurred)|internal (?:server )?error|traceback|exception:|rate.?limit|quota|"
    r"api key|invalid key|\b(?:429|500|502|503)\b|i'?m having (?:some )?trouble|unable to process|"
    r"could not process|technical (?:issue|difficult)", re.I)


def _ts(v):
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ai_unlisted(replies, f):
    """Ask the AI which offered items are not on the business's list.
    Returns {index: [items]}; {} when the AI cannot be asked (never guesses)."""
    try:
        from . import ai
        if not ai.available():
            return {}
    except Exception:
        return {}
    listed = [s["name"] for s in f["services"]]
    extra = [k["title"] + (": " + k["body"] if k["body"] else "") for k in f["knowledge"]
             if k["kind"] in ("info", "policy", "faq")]
    system = ("You check a business's WhatsApp replies. You get the ONLY items the business sells, a few "
              "facts, and numbered replies. For each reply list every service, product, dish, course or "
              "plan that the reply OFFERS, RECOMMENDS or PRICES and that is NOT on the list (spelling "
              "variants and plurals of listed items count as listed). Ignore general words such as "
              "booking, delivery, consultation, appointment or visit unless the facts say the business does "
              "not do them. Answer JSON only: {\"results\": [{\"i\": 1, \"not_listed\": [\"...\"]}]}. "
              "An empty list means the reply is fine.")
    prompt = json.dumps({"business": f["business"]["name"],
                         "type": (f["category"] or {}).get("name") or f["business"].get("business_type_other"),
                         "items_sold": listed, "facts": extra[:40],
                         "not_offered": [k["title"] for k in f["knowledge"] if k["kind"] == "not_offered"],
                         "replies": [{"i": i + 1, "text": r[:1500]} for i, r in enumerate(replies)]},
                        ensure_ascii=False)
    try:
        out = ai.generate_json(system, prompt, required_keys=("results",))
    except Exception as e:
        print("[replycheck] AI check skipped: %s" % str(e)[:200], flush=True)
        return {}
    found = {}
    for r in out.get("results") or []:
        try:
            items = [str(x).strip()[:80] for x in (r.get("not_listed") or []) if str(x).strip()]
            if items:
                found[int(r.get("i")) - 1] = items
        except (TypeError, ValueError, AttributeError):
            continue
    return found


def check_tenant(tid):
    """One pass for one business. Returns the number of new findings."""
    from .categories import PROTECTED, facts, sync_knowledge
    sync_knowledge(tid)
    with tenant_session(tid, "techdeva_admin") as cur:
        cur.execute("SELECT agent_key, pairctl_url FROM whatsapp_connections WHERE tenant_id = %s", (tid,))
        conns = [dict(r) for r in cur.fetchall() if not PROTECTED.search(r["agent_key"] or "")]
        f = facts(cur, tid)
        cur.execute("SELECT last_message_at FROM agent_reply_scan WHERE tenant_id = %s", (tid,))
        r = cur.fetchone()
    since = (r and r["last_message_at"]) or (_dt.datetime.now(_dt.timezone.utc) - FIRST_LOOK)
    newest = since
    replies = []                                    # (agent_key, session, at, text)
    phones = {}                                     # session -> customer phone
    for c in conns:
        try:
            chats = _pairctl_json(c["pairctl_url"], "/chats").get("chats") or []
        except Exception as e:
            print("[replycheck] %s unreachable: %s" % (c["agent_key"], getattr(e, "detail", e)), flush=True)
            continue
        for ch in chats:
            phones[ch.get("session_id")] = ch.get("phone") or ""
            last = _ts(ch.get("last_at"))
            if not last or last <= since:
                continue
            try:
                msgs = _pairctl_json(c["pairctl_url"], "/chats/" + ch["session_id"]).get("messages") or []
            except Exception:
                continue
            for m in msgs:
                at = _ts(m.get("at"))
                if m.get("role") == "assistant" and at and at > since and str(m.get("content") or "").strip():
                    replies.append((c["agent_key"], ch["session_id"], at, str(m["content"])))
                    newest = max(newest, at)
    replies = sorted(replies, key=lambda x: x[2])[-MAX_MSGS:]
    config = {"services": [{"name": s["name"], "price": s["price"]} for s in f["services"]],
              "hours": f["hours"], "knowledge": f["knowledge"]}
    findings = []
    for k, sid, at, text in replies:
        if ERROR.search(text):
            findings.append((k, sid, at, text, "bot_error", "The agent sent an error message to a customer."))
        if parse_summary(text):
            # an ORDER SUMMARY becomes a New order, priced from the business's own
            # list; a total that differs is noted on the order, not guessed at here
            try:
                n = order_from_chat(tid, agent_key=k, session_id=sid, phone=phones.get(sid), at=at, text=text)
                if n:
                    print("[replycheck] %s new WhatsApp order %s" % (tid, n), flush=True)
            except Exception as e:
                print("[replycheck] %s order not created: %s" % (tid, str(e)[:200]), flush=True)
            continue
        bad = unbacked_claims(text, config)
        if bad:
            findings.append((k, sid, at, text, "unbacked_claim", "; ".join(bad)[:500]))
    for i, items in _ai_unlisted([x[3] for x in replies], f).items():
        if 0 <= i < len(replies):
            k, sid, at, text = replies[i]
            findings.append((k, sid, at, text, "unknown_service",
                             "Offered something not on the business's list: " + ", ".join(items)))
    new = 0
    with tenant_session(tid, "techdeva_admin") as cur:
        for k, sid, at, text, kind, detail in findings:
            h = hashlib.sha256(("%s|%s|%s" % (sid, at.isoformat(), text)).encode()).hexdigest()[:24]
            cur.execute("""INSERT INTO agent_reply_flags (tenant_id, agent_key, session_id, msg_hash, message_at,
                                                          kind, excerpt, detail)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, msg_hash, kind) DO NOTHING RETURNING id""",
                        (tid, k, sid, h, at, kind, text[:600], detail))
            if cur.fetchone():
                new += 1
        cur.execute("""INSERT INTO agent_reply_scan (tenant_id, last_message_at, scanned_at) VALUES (%s, %s, now())
                       ON CONFLICT (tenant_id) DO UPDATE SET last_message_at = GREATEST(agent_reply_scan.last_message_at,
                              EXCLUDED.last_message_at), scanned_at = now()""", (tid, newest))
        if new:
            cur.execute("SELECT count(*) AS n FROM agent_reply_flags WHERE tenant_id = %s AND status = 'open'", (tid,))
            n = cur.fetchone()["n"]
            detail = ("%d WhatsApp repl%s to check: an error, or something not on your list. Open "
                      "Business & agent in the menu." % (n, "y" if n == 1 else "ies"))
            cur.execute("""UPDATE incidents SET last_seen_at = now(), seen_count = seen_count + 1, detail = %s
                            WHERE tenant_id = %s AND kind = 'agent.reply_check' AND status = 'open' RETURNING id""",
                        (detail, tid))
            if not cur.fetchone():
                cur.execute("""INSERT INTO incidents (tenant_id, kind, severity, title, detail)
                               VALUES (%s, 'agent.reply_check', 'warn', %s, %s)""",
                            (tid, "Your WhatsApp agent said something to check", detail))
    return new


def run_once():
    """Every business with a template-built WhatsApp agent. Returns new findings."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT DISTINCT w.tenant_id::text AS tid, w.agent_key FROM whatsapp_connections w
                         JOIN tenants t ON t.id = w.tenant_id WHERE t.status IN ('active', 'trial')""")
        from .categories import PROTECTED
        tids = sorted({r["tid"] for r in cur.fetchall() if not PROTECTED.search(r["agent_key"] or "")})
    total = 0
    for tid in tids:
        try:
            total += check_tenant(tid)
        except Exception as e:
            print("[replycheck] %s skipped: %s" % (tid, str(e)[:300]), flush=True)
    return total


def run_due():
    """Called from the scheduler tick; runs at most every EVERY_S seconds."""
    global _last
    if time.monotonic() - _last < EVERY_S:
        return 0
    _last = time.monotonic()
    return run_once()
