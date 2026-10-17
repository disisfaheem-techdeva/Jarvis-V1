"""Social agent: answers Facebook Messenger, Instagram DMs and comments for every business.

    Meta ──signed POST──► /meta/webhook ─► meta_pages says whose Page it is
                                         ─► social_threads / social_messages (stored first)
                                         ─► reply in the background:
          the business's facts (categories.knowledge_block, the same text its WhatsApp
          agent is given) + its persona + the conversation so far -> one AI turn ->
          guards.unbacked_claims (never an invented price or promise) -> sent through
          the Page token (Messenger / Instagram messaging API, comment replies)
                                         ─► a Lead for anyone asking to buy or book
                                         ─► a serious client: email the owner + flag it
                                            on the dashboard (Social Inbox)

The owner can take over any conversation from the dashboard; the AI then stays
quiet there until the owner hands it back. A business can switch the agent off.
A reply the AI is unsure of, a complaint, or a reply that states a price the
business never gave is left for the owner, never sent.

Meta only delivers messages from real customers once the app has these
permissions approved and is Live (docs/META-APP-REVIEW.md): pages_messaging,
pages_manage_metadata, pages_read_user_content, pages_manage_engagement,
instagram_manage_messages, instagram_manage_comments.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time

import requests
from fastapi import APIRouter, BackgroundTasks, Body, Cookie, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

GRAPH = "https://graph.facebook.com/v21.0"
VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "").strip()
DASHBOARD = os.environ.get("OAUTH_REDIRECT_BASE", "https://api.techdeva.net").strip().rstrip("/") + "/dashboard"
HOT_SCORE = 70                     # at or above, with serious=true: alert the owner
HISTORY = 12                       # messages of the conversation the AI sees
MAX_BODY = 512 * 1024
PAGES_EVERY = 600                  # refresh which Page belongs to which business (seconds)
_LAST_PAGES = [0.0]
_PAGES_LOCK = threading.Lock()
BUY_INTENTS = ("price", "order", "booking")
PK_PHONE = re.compile(r"(?:\+?92|0)3\d{2}[\s-]?\d{7}")

SYSTEM = """You are the assistant of ONE business, answering its customers on Facebook and Instagram
(direct messages and comments). Be warm, short and useful, like a helpful person at the shop.

Rules:
- Reply in the customer's language: Roman Urdu if they write Urdu or Roman Urdu, otherwise English.
  Never Urdu script. Address the customer politely ("aap" in Urdu). 2 to 4 short lines.
- Use ONLY the business facts given. Never invent a price, discount, delivery time, stock,
  address, opening hour, review or promise. If you do not know, say the team will confirm.
- Help them take the next step: order, book, or message on WhatsApp if the business gives a
  number. Ask one question at a time (what they want, for how many, when, which area).
- A public comment reply is one short line and never contains prices or personal details:
  thank them and say you have sent the details in a message (or answer a simple question).
- Complaints, angry messages, refunds, medical or legal questions, and anything you are not
  sure about: write a short polite holding reply and set handoff true.
- Spam, scams, ads from others, abuse: intent "spam" and empty replies.
- "serious" means a real buyer: they ask price for a specific product/service, want to order
  or book, give a date, quantity, area or phone number. Window-shopping is not serious.
Reply with JSON only."""


# ------------------------------------------------------------------ helpers
def _ident(x_tenant_id, x_role, authorization, session):
    from .social import _ident as social_ident
    return social_ident(x_tenant_id, x_role, authorization, session)


def _owner_only(role):
    from .social import _owner_only as social_owner_only
    social_owner_only(role)


def verify_signature(body: bytes, header: str | None) -> bool:
    """Meta signs every webhook with the app secret (X-Hub-Signature-256: sha256=<hex>)."""
    from .oauth import META_APP_SECRET
    if not META_APP_SECRET or not header or not header.startswith("sha256="):
        return False
    good = hmac.new(META_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, header[7:])


def _graph_post(path, data):
    """POST to Meta. Returns (body, error). The error is Meta's own message; the
    token is never included in what is logged or stored."""
    try:
        r = requests.post(GRAPH + path, json=data, timeout=25)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        return None, "network: %s" % e.__class__.__name__
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        return None, ("%s (code %s)" % (err.get("message") or r.status_code, err.get("code") or r.status_code))[:300]
    return body, None


def _graph_get(path, params):
    try:
        r = requests.get(GRAPH + path, params=params, timeout=15)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        print("[social-agent] meta read %s failed: %s" % (path, e.__class__.__name__), flush=True)
        return None
    if r.status_code >= 400 or "error" in body:
        return None
    return body


def _profile_name(page, platform, person_id):
    """The customer's name, which Meta does not put in a DM webhook (best effort)."""
    fields = "name,username" if platform == "instagram" else "first_name,last_name,name"
    body = _graph_get("/" + str(person_id), {"fields": fields, "access_token": page["access_token"]}) or {}
    name = body.get("name") or " ".join(x for x in (body.get("first_name"), body.get("last_name")) if x)
    return (name or body.get("username") or "").strip()[:120]


def _settings(cur):
    cur.execute("SELECT enabled, reply_comments, alert_email FROM social_agent_settings WHERE tenant_id = current_tenant()")
    row = cur.fetchone()
    return dict(row) if row else {"enabled": False, "reply_comments": True, "alert_email": None}   # opt-in


def _page(cur, tid, page_id):
    """The connected Page (with its token) that a conversation belongs to."""
    from .social import _meta_pages
    for p in _meta_pages(cur, tid):
        if str(p["id"]) == str(page_id):
            return p
    return None


# ------------------------------------------------------------------ which Page is whose
def refresh_pages(force=False) -> int:
    """From the scheduler: record which business each connected Page belongs to, and
    ask Meta to send that Page's messages and comments to us (subscribed_apps)."""
    if not force and time.time() - _LAST_PAGES[0] < PAGES_EVERY:
        return 0
    if not _PAGES_LOCK.acquire(blocking=False):
        return 0
    try:
        _LAST_PAGES[0] = time.time()
        from .social import _meta_pages
        with tenant_session(None, "techdeva_admin") as cur:
            # oldest connection first, so a Page in two businesses' logins goes to the newest.
            # Only businesses where the agent is switched on: a business that is off has
            # its Page left alone, so Meta sends us none of its customers' messages.
            cur.execute("""SELECT i.tenant_id::text AS tid FROM integrations i
                             JOIN social_agent_settings s ON s.tenant_id = i.tenant_id AND s.enabled
                            WHERE i.provider = 'meta' AND i.status = 'connected'
                            ORDER BY i.connected_at NULLS FIRST""")
            tids = [r["tid"] for r in cur.fetchall()]
        n = 0
        for tid in tids:
            try:
                with tenant_session(tid, "owner") as cur:
                    pages = _meta_pages(cur, tid)
            except Exception as e:  # noqa: BLE001 -- one business's login never stops the others
                print("[social-agent] pages skipped for %s: %s" % (tid, e.__class__.__name__), flush=True)
                continue
            for p in pages:
                pid = str(p.get("id") or "")
                if not re.fullmatch(r"[0-9]{3,30}", pid):
                    continue
                ig = str(p.get("ig_id") or "") or None
                with tenant_session(None, "techdeva_admin") as cur:
                    cur.execute("SELECT tenant_id::text AS tid, subscribed FROM meta_pages WHERE page_id = %s", (pid,))
                    old = cur.fetchone()
                    if ig:   # an Instagram account belongs to one Page only
                        cur.execute("UPDATE meta_pages SET ig_id = NULL WHERE ig_id = %s AND page_id <> %s", (ig, pid))
                    cur.execute("""INSERT INTO meta_pages (page_id, ig_id, tenant_id, page_name, updated_at)
                                   VALUES (%s, %s, %s, %s, now())
                                   ON CONFLICT (page_id) DO UPDATE SET ig_id = EXCLUDED.ig_id, tenant_id = EXCLUDED.tenant_id,
                                     page_name = EXCLUDED.page_name, updated_at = now(),
                                     subscribed = CASE WHEN meta_pages.tenant_id = EXCLUDED.tenant_id
                                                       THEN meta_pages.subscribed ELSE false END""",
                                (pid, ig, tid, str(p.get("name") or "")[:120]))
                if old and old["subscribed"] and old["tid"] == tid:
                    continue
                _, err = _graph_post("/%s/subscribed_apps" % pid, {
                    "subscribed_fields": "messages,messaging_postbacks,feed", "access_token": p["access_token"]})
                with tenant_session(None, "techdeva_admin") as cur:
                    cur.execute("UPDATE meta_pages SET subscribed = %s, last_error = %s WHERE page_id = %s",
                                (err is None, err, pid))
                if err:
                    print("[social-agent] page %s not subscribed: %s" % (pid, err), flush=True)
                n += 1
        return n
    finally:
        _PAGES_LOCK.release()


def _owner_of(page_or_ig: str):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT tenant_id::text AS tid, page_id, ig_id FROM meta_pages WHERE page_id = %s OR ig_id = %s",
                    (page_or_ig, page_or_ig))
        row = cur.fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------ webhook events -> stored messages
def parse_events(payload: dict) -> list[dict]:
    """Flatten Meta's webhook into simple events. Our own messages (echoes) are dropped."""
    out = []
    obj = payload.get("object")
    for entry in payload.get("entry") or []:
        owner_id = str(entry.get("id") or "")
        platform = "instagram" if obj == "instagram" else "facebook"
        for m in entry.get("messaging") or []:
            msg = m.get("message") or {}
            sender = str((m.get("sender") or {}).get("id") or "")
            if not msg or msg.get("is_echo") or not sender or sender == owner_id:
                continue
            text = msg.get("text") or ("[sent an attachment]" if msg.get("attachments") else "")
            if not text:
                continue
            out.append({"platform": platform, "kind": "dm", "owner_id": owner_id, "person_id": sender,
                        "person_name": "", "post_id": "", "text": text, "external_id": msg.get("mid"),
                        "comment_id": None})
        for ch in entry.get("changes") or []:
            v = ch.get("value") or {}
            if platform == "facebook" and ch.get("field") == "feed" and v.get("item") == "comment" and v.get("verb") == "add":
                frm = v.get("from") or {}
                if str(frm.get("id") or "") in ("", owner_id) or not v.get("message"):
                    continue
                out.append({"platform": "facebook", "kind": "comment", "owner_id": owner_id,
                            "person_id": str(frm.get("id")), "person_name": frm.get("name") or "",
                            "post_id": str(v.get("post_id") or ""), "text": v.get("message"),
                            "external_id": v.get("comment_id"), "comment_id": v.get("comment_id")})
            elif platform == "instagram" and ch.get("field") == "comments":
                frm = v.get("from") or {}
                if str(frm.get("id") or "") in ("", owner_id) or not v.get("text"):
                    continue
                out.append({"platform": "instagram", "kind": "comment", "owner_id": owner_id,
                            "person_id": str(frm.get("id")), "person_name": frm.get("username") or "",
                            "post_id": str((v.get("media") or {}).get("id") or ""), "text": v.get("text"),
                            "external_id": v.get("id"), "comment_id": v.get("id")})
    return out


def store_event(ev: dict):
    """Save one incoming message. Returns (tenant_id, thread_id, message_id) or None
    (not one of our businesses, or already stored: Meta retries)."""
    who = _owner_of(ev["owner_id"])
    if not who:
        return None
    tid = who["tid"]
    with tenant_session(tid, "owner") as cur:
        cur.execute("""INSERT INTO social_threads (tenant_id, platform, kind, page_id, person_id, person_name, post_id,
                                                   last_in_at, last_message_at)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, now(), now())
                       ON CONFLICT (tenant_id, platform, kind, person_id, post_id) DO UPDATE SET
                         last_in_at = now(), last_message_at = now(),
                         person_name = CASE WHEN EXCLUDED.person_name <> '' THEN EXCLUDED.person_name
                                            ELSE social_threads.person_name END
                       RETURNING id::text""",
                    (ev["platform"], ev["kind"], who["page_id"], ev["person_id"], ev["person_name"][:120], ev["post_id"]))
        thread_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO social_messages (tenant_id, thread_id, author, visibility, text, external_id, status)
                       VALUES (current_tenant(), %s, 'customer', %s, %s, %s, 'received')
                       ON CONFLICT DO NOTHING RETURNING id::text""",
                    (thread_id, "public" if ev["kind"] == "comment" else "private", str(ev["text"])[:4000],
                     ev.get("external_id")))
        row = cur.fetchone()
    return (tid, thread_id, row["id"]) if row else None


# ------------------------------------------------------------------ the reply
def _brief(cur, tid) -> str:
    from .categories import facts, knowledge_block
    try:
        return knowledge_block(facts(cur, tid))[:8000]
    except Exception:  # noqa: BLE001
        cur.execute("SELECT name FROM tenants WHERE id = %s", (tid,))
        r = cur.fetchone()
        return "The business is called %s." % (r["name"] if r else "this business")


def _allowed(cur, tid) -> dict:
    """What a reply may state as fact, for guards.unbacked_claims."""
    from .categories import facts
    from .persistence import Persistence
    try:
        records = facts(cur, tid)
    except Exception:  # noqa: BLE001
        records = {}
    return {"config": Persistence().load_config(tid) or {}, "records": records}


def _send_dm(page, platform, person_id, text):
    """Messenger and Instagram DMs both go through the Page's /messages."""
    body, err = _graph_post("/%s/messages" % page["id"], {
        "recipient": {"id": person_id}, "messaging_type": "RESPONSE", "message": {"text": text},
        "access_token": page["access_token"]})
    return (body or {}).get("message_id"), err


def _send_private_reply(page, comment_id, text):
    """A private message to the person who left a comment (one per comment, Meta's rule)."""
    body, err = _graph_post("/%s/messages" % page["id"], {
        "recipient": {"comment_id": comment_id}, "message": {"text": text}, "access_token": page["access_token"]})
    return (body or {}).get("message_id"), err


def _reply_comment(page, platform, comment_id, text):
    path = "/%s/replies" % comment_id if platform == "instagram" else "/%s/comments" % comment_id
    body, err = _graph_post(path, {"message": text, "access_token": page["access_token"]})
    return (body or {}).get("id"), err


def _log_out(cur, thread_id, author, visibility, text, ext, err, status=None):
    cur.execute("""INSERT INTO social_messages (tenant_id, thread_id, author, visibility, text, external_id, status, error)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s)""",
                (thread_id, author, visibility, text[:4000], ext, status or ("failed" if err else "sent"), err))


def _upsert_lead(cur, th, verdict, text):
    name = (verdict.get("name") or th["person_name"] or "").strip()[:120] or None
    phone = (verdict.get("phone") or "").strip()[:40]
    m = PK_PHONE.search(text or "")
    if not phone and m:
        phone = m.group(0)
    wants = (verdict.get("wants") or verdict.get("intent") or "").strip()[:120] or None
    note = "From %s %s: %s" % (th["platform"].capitalize(), "comment" if th["kind"] == "comment" else "message",
                               re.sub(r"\s+", " ", text or "")[:300])
    priority = 1 if verdict.get("serious") else 3
    if th["lead_id"]:
        cur.execute("""UPDATE leads SET phone = coalesce(nullif(%s, ''), phone), service = coalesce(%s, service),
                                        priority = least(priority, %s), updated_at = now(),
                                        notes = left(coalesce(notes, '') || E'\\n' || %s, 2000)
                        WHERE id = %s""", (phone, wants, priority, note, th["lead_id"]))
        return th["lead_id"]
    cur.execute("""INSERT INTO leads (tenant_id, name, phone, service, source, priority, tags, notes, created_by)
                   VALUES (current_tenant(), %s, nullif(%s, ''), %s, %s::lead_source, %s, %s, %s, 'social_agent')
                   RETURNING id""",
                (name, phone, wants, th["platform"], priority, [th["platform"], th["kind"]], note))
    lid = cur.fetchone()["id"]
    cur.execute("UPDATE social_threads SET lead_id = %s WHERE id = %s", (lid, th["id"]))
    return lid


def _alert_owner(tid, th, verdict, text):
    """A serious client: email the owner (once a day per conversation) and log it."""
    from .account import mail_configured, send_mail
    with tenant_session(tid, "owner") as cur:
        cur.execute("""UPDATE social_threads SET hot_alerted_at = now()
                        WHERE id = %s AND (hot_alerted_at IS NULL OR hot_alerted_at < now() - interval '24 hours')
                        RETURNING id""", (th["id"],))
        if not cur.fetchone():
            return False
        st = _settings(cur)
        cur.execute("SELECT name FROM tenants WHERE id = %s", (tid,))
        biz = (cur.fetchone() or {}).get("name") or "your business"
        cur.execute("SELECT email FROM users WHERE tenant_id = current_tenant() AND role = 'owner' ORDER BY created_at")
        to = [st["alert_email"]] if st.get("alert_email") else [r["email"] for r in cur.fetchall()]
        record(cur, action="social.hot_lead", resource="social_threads", resource_id=th["id"], actor="social_agent",
               actor_type="agent", status="ok",
               detail={"platform": th["platform"], "kind": th["kind"], "score": verdict.get("score"),
                       "wants": verdict.get("wants")}, error=None)
    who = th["person_name"] or "Someone"
    subject = "🔥 Serious client on %s: %s" % (th["platform"].capitalize(), who)
    lines = ["%s is asking %s on %s." % (who, "in a comment" if th["kind"] == "comment" else "in a direct message",
                                        th["platform"].capitalize()),
             "", "They said: \"%s\"" % re.sub(r"\s+", " ", text or "")[:400],
             "Wants: %s" % (verdict.get("wants") or verdict.get("intent") or "not clear yet")]
    if verdict.get("phone"):
        lines.append("Phone: %s" % verdict["phone"])
    lines += ["Serious score: %s/100" % verdict.get("score"), "",
              "The assistant has replied. Open the conversation to take over:",
              "%s#/socialinbox?id=%s" % (DASHBOARD, th["id"]), "", "— %s, TechDeva" % biz]
    if mail_configured():
        for addr in to:
            try:
                send_mail(addr, subject, "\n".join(lines))
            except Exception as e:  # noqa: BLE001 -- the dashboard still shows it
                print("[social-agent] alert email to owner failed: %s" % e.__class__.__name__, flush=True)
    try:
        from .persistence import Persistence
        Persistence().notify(tid, "owner", subject)
    except Exception:  # noqa: BLE001
        pass
    return True


def respond(tid, thread_id):
    """One AI turn for a conversation, and everything that follows from it."""
    from . import ai
    from .agents.guards import unbacked_claims
    with tenant_session(tid, "owner") as cur:
        st = _settings(cur)
        cur.execute("SELECT * FROM social_threads WHERE id = %s", (thread_id,))
        th = cur.fetchone()
        if not th:
            return None
        th = dict(th)
        th["id"] = str(th["id"])
        if not st["enabled"] or th["ai_paused"]:
            return "paused"
        if th["kind"] == "comment" and not st["reply_comments"]:
            return "comments_off"
        cur.execute("""SELECT author, visibility, text, external_id, status FROM social_messages
                        WHERE thread_id = %s ORDER BY created_at DESC LIMIT %s""", (thread_id, HISTORY))
        hist = list(reversed([dict(r) for r in cur.fetchall()]))
        last_in = next((m for m in reversed(hist) if m["author"] == "customer"), None)
        if not last_in or hist[-1]["author"] != "customer":
            return "nothing_new"
        brief = _brief(cur, tid)
        allowed = _allowed(cur, tid)
        cur.execute("SELECT hermes_persona FROM tenant_config WHERE tenant_id = current_tenant()")
        persona = ((cur.fetchone() or {}).get("hermes_persona") or "").strip()[:1500]
        page = _page(cur, tid, th["page_id"])
    if not page:
        with tenant_session(tid, "owner") as cur:
            cur.execute("UPDATE social_threads SET needs_owner = %s WHERE id = %s",
                        ("This Facebook Page is no longer connected. Reconnect Facebook on the Posts page.", thread_id))
        return "no_page"
    if not th["person_name"] and th["kind"] == "dm":
        name = _profile_name(page, th["platform"], th["person_id"])
        if name:
            th["person_name"] = name
            with tenant_session(tid, "owner") as cur:
                cur.execute("UPDATE social_threads SET person_name = %s WHERE id = %s", (name, thread_id))
    convo = "\n".join("%s%s: %s" % ({"customer": "Customer", "ai": "You", "owner": "Owner"}[m["author"]],
                                    " (public comment)" if m["visibility"] == "public" else "", m["text"]) for m in hist)
    prompt = """BUSINESS FACTS (the only facts that exist)
{brief}

{persona}
CHANNEL: {platform} {kind}{name}
CONVERSATION SO FAR
{convo}

Return {{"reply": "private message to send (empty if none)",
 "public_reply": "one short public line under the comment (comments only, else empty)",
 "intent": "price|order|booking|question|complaint|thanks|spam|other",
 "serious": true/false, "score": 0-100, "handoff": true/false, "reason": "why handoff, else empty",
 "name": "customer's name if they gave it", "phone": "phone if they gave it", "wants": "what they want, short"}}""".format(
        brief=brief, persona=("PERSONA AND TONE\n" + persona + "\n") if persona else "",
        platform=th["platform"].capitalize(), kind="comment" if th["kind"] == "comment" else "direct message",
        name=(" from " + th["person_name"]) if th["person_name"] else "", convo=convo)
    try:
        v = ai.generate_json(SYSTEM, prompt, required_keys=("reply", "intent"))
    except Exception as e:  # noqa: BLE001 -- AI down: the owner sees it waiting, nothing fake is sent
        with tenant_session(tid, "owner") as cur:
            cur.execute("UPDATE social_threads SET needs_owner = %s WHERE id = %s",
                        ("The assistant could not answer (AI unavailable). Reply yourself.", thread_id))
        print("[social-agent] AI failed for %s: %s" % (thread_id, e.__class__.__name__), flush=True)
        return "ai_failed"
    intent = str(v.get("intent") or "other").lower()[:20]
    try:
        score = max(0, min(100, int(v.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    v["score"] = score
    serious = bool(v.get("serious")) and intent != "spam"
    reply = str(v.get("reply") or "").strip()[:1000]
    public = str(v.get("public_reply") or "").strip()[:300]
    claims = unbacked_claims(reply + "\n" + public, allowed)
    needs = None
    outcome = "replied"
    with tenant_session(tid, "owner") as cur:
        cur.execute("UPDATE social_threads SET intent = %s, score = greatest(coalesce(score, 0), %s) WHERE id = %s",
                    (intent, score, thread_id))
    if intent == "spam":
        outcome = "spam"
    elif claims:
        needs = "The reply was held back: " + "; ".join(claims)[:250] + ". Reply yourself."
        with tenant_session(tid, "owner") as cur:
            _log_out(cur, thread_id, "ai", "private", reply or public, None, None, status="held")
        outcome = "held"
    else:
        if v.get("handoff"):
            needs = (str(v.get("reason") or "The assistant left this for you.")[:250])
        sent = []
        if th["kind"] == "dm" and reply:
            ext, err = _send_dm(page, th["platform"], th["person_id"], reply)
            sent.append(("private", reply, ext, err))
        elif th["kind"] == "comment":
            comment_id = last_in["external_id"]
            if public and comment_id:
                ext, err = _reply_comment(page, th["platform"], comment_id, public)
                sent.append(("public", public, ext, err))
            if reply and comment_id and intent in BUY_INTENTS + ("question",):
                ext, err = _send_private_reply(page, comment_id, reply)
                sent.append(("private", reply, ext, err))
        with tenant_session(tid, "owner") as cur:
            for vis, text, ext, err in sent:
                _log_out(cur, thread_id, "ai", vis, text, ext, err)
                if err:
                    needs = "Meta refused the reply: %s" % err[:200]
                    outcome = "send_failed"
    with tenant_session(tid, "owner") as cur:
        cur.execute("UPDATE social_threads SET needs_owner = %s, last_message_at = now() WHERE id = %s", (needs, thread_id))
        if intent in BUY_INTENTS or serious:
            _upsert_lead(cur, th, dict(v, intent=intent, serious=serious), last_in["text"])
        hot = serious and score >= HOT_SCORE
        if hot:
            cur.execute("UPDATE social_threads SET hot = true WHERE id = %s", (thread_id,))
    if hot:
        _alert_owner(tid, th, v, last_in["text"])
    return outcome


def _respond_safe(tid, thread_id):
    try:
        respond(tid, thread_id)
    except Exception as e:  # noqa: BLE001 -- never let one conversation crash the worker
        print("[social-agent] respond failed for %s: %s" % (thread_id, e.__class__.__name__), flush=True)


# ------------------------------------------------------------------ routers
def make_meta_webhook_router():
    router = APIRouter(tags=["social-agent"])

    @router.get("/meta/webhook", include_in_schema=False)
    def verify(hub_mode: str | None = Query(None, alias="hub.mode"),
               hub_token: str | None = Query(None, alias="hub.verify_token"),
               hub_challenge: str | None = Query(None, alias="hub.challenge")):
        """Meta's one-time check when the webhook is set up in the app dashboard."""
        if VERIFY_TOKEN and hub_mode == "subscribe" and hub_token and hmac.compare_digest(hub_token, VERIFY_TOKEN):
            return PlainTextResponse(str(hub_challenge or ""))
        raise HTTPException(403, "Forbidden.")

    @router.post("/meta/webhook", include_in_schema=False)
    async def receive(request: Request, background: BackgroundTasks):
        body = await request.body()
        if len(body) > MAX_BODY or not verify_signature(body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(401, "Bad signature.")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(400, "Bad JSON.")
        stored = 0
        for ev in parse_events(payload):
            try:
                got = store_event(ev)
            except Exception as e:  # noqa: BLE001 -- answer Meta 200 anyway, or it retries and disables us
                print("[social-agent] could not store an event: %s" % e.__class__.__name__, flush=True)
                continue
            if got:
                stored += 1
                background.add_task(_respond_safe, got[0], got[1])
        return {"ok": True, "stored": stored}

    return router


def make_social_inbox_router():
    router = APIRouter(prefix="/social-inbox", tags=["social-agent"])

    @router.get("/threads")
    def threads(filter: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        where = {"hot": "AND t.hot", "owner": "AND t.needs_owner IS NOT NULL"}.get(filter or "", "")
        with tenant_session(tid, role) as cur:
            st = _settings(cur)
            cur.execute("""SELECT t.id::text AS id, t.platform, t.kind, t.person_name, t.intent, t.score, t.hot,
                                  t.needs_owner, t.ai_paused, t.lead_id::text AS lead_id, t.last_message_at,
                                  (SELECT m.text FROM social_messages m WHERE m.thread_id = t.id
                                    ORDER BY m.created_at DESC LIMIT 1) AS last
                             FROM social_threads t WHERE true """ + where + """
                            ORDER BY t.hot DESC, t.last_message_at DESC LIMIT 200""")
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT count(*) FILTER (WHERE hot) AS hot, count(*) FILTER (WHERE needs_owner IS NOT NULL) AS owner,
                                  count(*) AS total FROM social_threads""")
            counts = dict(cur.fetchone())
            cur.execute("SELECT page_name, subscribed, last_error, ig_id IS NOT NULL AS instagram FROM meta_pages")
            pages = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["last_message_at"] = r["last_message_at"].isoformat()
        return {"threads": rows, "counts": counts, "settings": st, "pages": pages}

    @router.get("/threads/{thread_id}")
    def thread(thread_id: str,
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            try:
                cur.execute("""SELECT id::text AS id, platform, kind, person_name, intent, score, hot, needs_owner,
                                      ai_paused, lead_id::text AS lead_id, last_in_at FROM social_threads WHERE id = %s""",
                            (thread_id,))
            except Exception:
                raise HTTPException(404, "Not found.")
            th = cur.fetchone()
            if not th:
                raise HTTPException(404, "That conversation is not in this business.")
            cur.execute("""SELECT author, visibility, text, status, error, created_at FROM social_messages
                            WHERE thread_id = %s ORDER BY created_at""", (thread_id,))
            msgs = [dict(m, created_at=m["created_at"].isoformat()) for m in cur.fetchall()]
        th = dict(th)
        th["last_in_at"] = th["last_in_at"].isoformat() if th["last_in_at"] else None
        return {"thread": th, "messages": msgs}

    @router.post("/threads/{thread_id}/reply")
    def owner_reply(thread_id: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner (or staff) writes. The AI goes quiet in this conversation."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        if role == "techdeva_admin":
            raise HTTPException(403, "Only the business replies to its customers.")
        text = str(body.get("text") or "").strip()
        if not text or len(text) > 1000:
            raise HTTPException(400, "Write a reply (up to 1000 characters).")
        public = bool(body.get("public"))
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT * FROM social_threads WHERE id = %s", (thread_id,))
            th = cur.fetchone()
            if not th:
                raise HTTPException(404, "That conversation is not in this business.")
            th = dict(th)
            page = _page(cur, tid, th["page_id"])
            cur.execute("""SELECT external_id FROM social_messages WHERE thread_id = %s AND author = 'customer'
                            AND external_id IS NOT NULL ORDER BY created_at DESC LIMIT 1""", (thread_id,))
            last = cur.fetchone()
        if not page:
            raise HTTPException(409, "This Facebook Page is not connected any more. Reconnect Facebook on the Posts page.")
        if th["kind"] == "dm":
            ext, err = _send_dm(page, th["platform"], th["person_id"], text)
        elif public:
            ext, err = _reply_comment(page, th["platform"], (last or {}).get("external_id"), text)
        else:
            ext, err = _send_private_reply(page, (last or {}).get("external_id"), text)
        with tenant_session(tid, role) as cur:
            _log_out(cur, thread_id, "owner", "public" if public and th["kind"] == "comment" else "private", text, ext, err)
            cur.execute("UPDATE social_threads SET ai_paused = true, needs_owner = NULL, last_message_at = now() "
                        "WHERE id = %s", (thread_id,))
            record(cur, action="social.owner_reply", resource="social_threads", resource_id=thread_id,
                   actor=str(uid), actor_type="human", status="error" if err else "ok",
                   detail={"platform": th["platform"], "kind": th["kind"]}, error=err)
        if err:
            raise HTTPException(502, "Not sent: %s" % err)
        return {"sent": True}

    @router.post("/threads/{thread_id}/ai")
    def hand_back(thread_id: str, body: dict = Body(...),
                  x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Pause or resume the AI in one conversation."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        if role == "techdeva_admin":
            raise HTTPException(403, "Only the business decides this.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_threads SET ai_paused = %s WHERE id = %s RETURNING id",
                        (not bool(body.get("on")), thread_id))
            if not cur.fetchone():
                raise HTTPException(404, "That conversation is not in this business.")
        return {"ok": True, "ai": bool(body.get("on"))}

    @router.put("/settings")
    def settings(body: dict = Body(...),
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        _owner_only(role)
        email = str(body.get("alert_email") or "").strip()[:160] or None
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(400, "That email address does not look right.")
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO social_agent_settings (tenant_id, enabled, reply_comments, alert_email, updated_at)
                           VALUES (current_tenant(), %s, %s, %s, now())
                           ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled,
                             reply_comments = EXCLUDED.reply_comments, alert_email = EXCLUDED.alert_email, updated_at = now()""",
                        (bool(body.get("enabled", False)), bool(body.get("reply_comments", True)), email))
            record(cur, action="social.agent_settings", resource="social_agent_settings", resource_id=None,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"enabled": bool(body.get("enabled", False)), "reply_comments": bool(body.get("reply_comments", True))},
                   error=None)
        return {"ok": True}

    return router
