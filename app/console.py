"""The AI console: the owner types a sentence, the platform changes something.

Two kinds of change, and the difference matters more than the feature does.

* Their dashboard is their own screen. Nobody else sees it and nothing about it
  reaches a customer, so a change applies immediately.
* Teaching the WhatsApp agent changes what an AI says to their customers. That
  is not an agent acting unsupervised -- it is the owner instructing their own
  bot, which is the human decision the approval gate exists to require. So it
  applies, but only for an owner, it is written down as an audited lesson rather
  than smeared into a prompt, and the exact wording is read back so a mistake is
  visible immediately and removable in one click.

An operator can use the console to ask questions. They cannot teach the agent
and they cannot redecorate the business's dashboard.
"""

import base64
import io
import json
import os
import re
import time
import uuid
import wave

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Response

from . import business as biz
from . import jarvis_actions as ja
from .ai import AIUnavailable, CreditsUsedUp, generate_json, fast as ai_fast, last_model as ai_last_model
from .ai import chain_first as ai_chain_first, parse_chain as ai_parse_chain
from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session
from .whatsapp import sync_lessons as wa_sync_lessons

# What the console is allowed to change on a dashboard. A closed list on
# purpose: an open one is a way to write arbitrary values into a config blob
# that other parts of the platform trust.
PREF_KEYS = {
    "accent": str,            # brand colour
    "show_logo_background": bool,
    "logo_opacity": float,    # 0.02 - 0.35
    "hidden_sections": list,  # nav ids this business does not want to see
    "home": str,              # which view opens first
    "density": str,           # comfortable | compact
}

# Business-definition resources the console may change, and the column that
# identifies 'the one they mean' in a sentence (name/title/day). Deliberately
# excludes anything transactional (customers, appointments, invoices,
# payments, expenses, stock, executions) -- those are records of something
# that happened and belong on their own structured screen, not a chat box.
BUSINESS_RESOURCES = {"services", "staff", "business_hours", "business_policies",
                       "locations", "products", "packages", "business_goals"}
BUSINESS_MATCH_COLUMN = {
    "services": "name", "staff": "name", "locations": "name",
    "products": "name", "packages": "name",
    "business_policies": "title", "business_goals": "label",
    "business_hours": "day_of_week",
}

VOICE_RULE = """

VOICE MODE: the owner is talking to you out loud and will HEAR your reply, like
a phone call. Make "reply" one or two short, natural spoken sentences (under 30
words) -- the way a friendly assistant sitting next to them would say it. No
lists, no markdown, no symbols, no reading out ids. Still do the full action."""

CLASSIFY = """You are Jarvis, the owner's personal assistant inside their business
dashboard. You understand what they say and turn it into one step on their
AI business platform. Answer with JSON only.

Fields:
  "kind": one of "action", "dashboard", "teach", "business", "post", "question"
  "action": if kind is action, one of:
            navigate (open a page of the dashboard),
            book_appointment, reschedule_appointment, cancel_appointment,
            complete_appointment (mark done), add_customer, add_lead,
            add_followup (plan a follow-up for a lead or customer: call,
                visit, task), complete_followup (mark a planned follow-up done),
            add_expense, schedule_post (a post to go out at a later time),
            write_campaign (write a PAID ad campaign as a DRAFT the owner
                checks and launches: only when they ask for an ad, ad
                campaign or boost that spends money; a post about an offer
                or promotion is kind post),
            update_order (confirm / out for delivery / delivered),
            record_order_payment, cancel_order,
            publish_post (post a draft that is already made: "post it",
            "post kar do", "publish it", with no new post content)
  "args": if kind is action, an object:
            navigate: section (a section id from the list below)
            book_appointment: name, phone, date (YYYY-MM-DD), time (HH:MM
                24h), service (as named in BUSINESS FACTS services), staff,
                notes
            reschedule_appointment: appointment_id, date, time
            cancel_appointment / complete_appointment: appointment_id
            add_customer: name, phone, email, notes
            add_lead: name, phone, service
            add_followup: lead_id (the "id" of one of recent_leads) or
                customer_name, title (what to do, e.g. "Call about the
                price"), kind (task, call, visit, meeting), due (ISO time with
                +05:00), due_words (e.g. "tomorrow at 11")
            complete_followup: followup_id (the "id" of one of crm_followups),
                outcome (what happened, if said)
            add_expense: description, amount (number, PKR), category, method
                (cash, card, bank, jazzcash, easypaisa), spent_on (YYYY-MM-DD)
            write_campaign: provider ("meta" for Facebook/Instagram, the
                default, or "google"), service (the service or product to
                advertise, as named in BUSINESS FACTS; empty means their
                best seller)
            schedule_post: caption (finished post text, laid out exactly as
                for kind post: headline, description, call to action,
                3 to 5 hashtags),
                platforms, at (ISO time with +05:00, e.g.
                2026-10-02T18:00:00+05:00), at_words (e.g. "Friday 6 pm")
            update_order: order_id, status ("confirmed", "out_for_delivery" or
                "delivered")
            record_order_payment: order_id, amount (number, PKR), method
                (cash, bank, online, card, other)
            cancel_order: order_id, reason
            publish_post: nothing (the latest draft is posted)
          order_id must be the "id" of one of orders.recent_and_open in BUSINESS
          FACTS (match the customer's name, phone or order number). Questions
          about orders, sales or what is owed are kind "question", answered
          from orders in BUSINESS FACTS: "sales" means delivered AND fully paid
          orders only; money not yet paid is "still owed", never a sale.
          appointment_id must be the "id" of one of upcoming_appointments in
          BUSINESS FACTS. Turn "today", "tomorrow", "Friday" into a date with
          next_7_days. Never make up a phone number, id, price or time (an
          unknown optional detail is simply left empty).
  "lesson": if kind is teach, the single fact or rule to remember, written in
            plain words as an instruction to the sales agent. No preamble.
  "prefs": if kind is dashboard, an object using only these keys:
            accent (hex colour), show_logo_background (true/false),
            logo_opacity (0.02 to 0.35), hidden_sections (array of section ids),
            home (section id), density ("comfortable" or "compact")
  "resource": if kind is business, one of services, staff, business_hours,
            business_policies, locations, products, packages, business_goals
  "match": if kind is business, the name/title of the existing item this is
            about (e.g. "HydraFacial", "Monday"), or empty if this is new
  "fields": if kind is business, an object of the columns to set for that
            resource -- always include its name/title/day even when
            updating, so a new item can still be created if none matched:
              services/products/packages: name, price, currency, active
              staff: name, role, department, active
              locations: name, address, city, phone, active
              business_policies: kind, title, body, active
              business_goals: label, metric, target, period
              business_hours: day_of_week (0=Sun,1=Mon,2=Tue,3=Wed,4=Thu,
                              5=Fri,6=Sat), opens ("HH:MM"), closes ("HH:MM"),
                              is_closed (true/false)
  "caption": if kind is post, the finished Facebook/Instagram post, ready to
            publish, written for the business's customers in the business's
            own voice (English unless the owner wrote the post content in
            Roman Urdu or Urdu). Lay it out exactly like this, with a blank
            line between the parts:
              1. Headline: one short hook line (under 10 words) naming the
                 service or offer. This is what people see before "See more".
              2. Description: two or three short lines -- what it is, who it
                 is for, and the offer, price or dates ONLY if the owner said
                 them or they are in BUSINESS FACTS.
              3. Call to action: one line telling people how to book (message
                 on WhatsApp, call, or visit), with the phone or area only if
                 it is in BUSINESS FACTS.
              4. Hashtags: 3 to 5 relevant hashtags on the last line (the
                 service, the kind of business, the city, the business name),
                 no spaces inside a tag. Never more than 5: Instagram allows 5.
            At most two emoji. Never invent prices, discounts, dates, results
            or promises ("guaranteed", "permanent", "100%"), and no medical
            claims.
  "platforms": if kind is post, an array of "facebook" and/or "instagram";
            both if the owner did not say
  "image_url": if kind is post and the owner gave an https picture link, that
            link, otherwise empty
  "headline": if kind is post, the words printed ON the picture: the item or
            offer in 2 to 6 words (a price only if the owner said it)
  "subline": if kind is post, one short line under the headline (up to 8
            words), or empty
  "reply": what you say back. Sound like a warm, switched-on human assistant
           who knows this business, not a machine: natural, friendly, brief
           (one or two sentences), contractions welcome, vary your wording,
           never start with "Sure" or "Certainly" every time. Mention the
           specifics (name, day, time, amount) so they can hear it is right.
           For add_expense, cancel_appointment, schedule_post and publish_post,
           ask for a yes first ("Shall I record 5,000 for the electricity bill?").
           Every OTHER action is carried out at once, so its reply says it is
           done ("Booked Ayesha for tomorrow at 4."), never asks.
           No emoji, no markdown.

If something REQUIRED is missing, do NOT guess: kind is "question" and "reply"
asks for just that piece, briefly. Required is only: for a booking, the
person's name, the day and the time; for moving one, the new day and time;
for an expense, the amount and what it was for; for a customer or lead, the
name. Everything else (phone, service, staff, notes, category, method) is
optional: fill it only if they said it, otherwise leave it out and go ahead
-- never ask for an optional detail before acting. RECENT CONVERSATION shows
what was said before, so a short follow-up ("5 pm", "make it Saturday", "yes
her number is 0300...") continues that request.

Section ids available: overview (home), leads, customers, appointments,
calendar, dailyentry (daily entry), revenue, invoices, expenses, dayclose (day
closing), pnl (profit and loss), services, products, packages, catalogue,
inventory (stock), integrations, reports, settings, console (Jarvis), posts
(social posts), billing (plan and billing), team (staff logins and PINs),
whatsapp, inbox, needshuman (chats needing a human), orders (products sold:
new, confirmed, delivered, payments), calls (phone calls answered by the AI
phone assistant), marketing, attendance,
hr, hermes (AI agents), ai, bizagent (Business & agent: business type, what the
WhatsApp agent knows, and "set up my business": the owner pastes a menu, price
list, hours or rules there and Jarvis fills the dashboard).

"Open/show/go to <page>" is action navigate. Booking, moving, cancelling or
finishing an appointment, adding a customer, a lead or an expense, and posting
at a LATER time are "action". "Make / design a post" is "post". "Post it" about
a post already drafted (no new content) is action publish_post.

If the instruction changes a real fact about the business itself -- a
price, a service, a staff member, opening hours, a policy, a location, a
package, or a goal -- it is "business".
If it is about what the WhatsApp agent should know, say, charge, promise, or
refuse to a CUSTOMER (not a change to the underlying price/hours/policy
itself), it is "teach".
If it is about how their own screen looks or which sections they see, it is
"dashboard".
If they want something posted, shared or announced on Facebook, Instagram or
social media (and no later time is given), it is "post" -- it becomes a draft they approve before anything
goes out, so say "drafted", never "posted". Otherwise it is "question"."""


def _identity(x_tenant_id, x_role, authorization, session, target=None):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role == "techdeva_admin":
        # The founder can drive any client's console, but only one client at a
        # time. is_admin() would let every lookup below see ALL tenants, so a
        # "change the HydraFacial price" could land on the wrong business.
        # Acting as that client's owner puts RLS back around exactly one tenant.
        if target:
            try:
                tid = str(uuid.UUID(str(target)))
            except ValueError:
                raise HTTPException(400, "That client id is not valid.")
        if not tid:
            raise HTTPException(400, "Pick a client first.")
        with tenant_session(tid, "owner") as cur:
            cur.execute("SELECT 1 FROM tenants WHERE id = %s", (tid,))
            if not cur.fetchone():
                raise HTTPException(404, "That client is not on the platform.")
        return tid, "owner", uid
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _clean_prefs(raw):
    """Keep only keys we know, in the shapes we allow."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, want in PREF_KEYS.items():
        if k not in raw:
            continue
        v = raw[k]
        if want is bool:
            out[k] = bool(v)
        elif want is float:
            try:
                out[k] = min(max(float(v), 0.02), 0.35)
            except (TypeError, ValueError):
                continue
        elif want is list:
            if isinstance(v, list):
                out[k] = [str(x)[:40] for x in v][:30]
        else:
            s = str(v).strip()[:40]
            if s:
                out[k] = s
    return out


UC_RULE = ("\n\nUniversal caption: when the owner asks to write, make or redo the caption or text that goes "
           "under every post (\"universal caption\", \"har post ke neeche wala caption\"), answer kind \"action\" "
           "with action universal_caption (args: language, optional: english, mix, urdu).")


def _dash_rule():
    """Dashboard Customizer tools for the classifier, only while the flag is on."""
    try:
        from . import dashcustom as dc
        if not dc.enabled():
            return ""
        return ("\n\nDashboard pages: to hide or show pages of the dashboard, put some first, or go back to "
                "the business type's layout, answer kind \"action\" with action dashboard_hide or dashboard_show "
                "(args: sections = array of page ids), dashboard_reorder (args: order = array of page ids, first "
                "= top), or dashboard_reset (args: industry, optional: clinic, salon, gym, academy, restaurant, "
                "shop, other). Page ids: " + ", ".join(sorted(dc.REGISTRY)) + ". Kind \"dashboard\" is then only "
                "for colour, logo, home page and density.")
    except Exception:  # noqa: BLE001
        return ""


# Appended to the classifier so the reply sounds right when spoken.
LANG_RULE = ("\n\nLanguage: your DEFAULT is Roman Urdu (Pakistani style, English business words mixed "
             "in naturally). Use Urdu script only if the owner wrote Urdu script, and English only if the "
             "owner wrote a full English sentence of four or more words. A greeting like hi, hello or salam, "
             "or a short or garbled speech-to-text message, gets Roman Urdu. Keep it natural to say aloud.")

# Who Jarvis is (4 Nov 2026: the owner found it robotic, "How can I help you
# manage your clinic today?"). Personality lives in the wording only: the facts
# rule below still forbids inventing anything, and actions are unchanged.
PERSONA_RULE = (
    "\n\nPersonality: you are a sharp, warm, confident Pakistani assistant, like a smart friend who "
    "runs the business with the owner. Talk like a person, not a help desk: answer greetings and small "
    "talk with a little life (a light, friendly line, a bit of desi humour when it fits), then steer to "
    "the business, e.g. what is waiting today from BUSINESS FACTS. Never use stock lines like \"How can I "
    "help you manage your business today?\" or \"I am an AI\". Address the owner respectfully (aap). "
    "Be brief: one to three sentences unless they ask for detail. If the owner is rude or frustrated, "
    "stay calm, do not lecture, just fix what they need. If asked which AI you run on, say honestly: "
    "TechDeva's Jarvis, running on Google's Gemini models.")

# Jarvis's own model order, tried before the platform chain (AI_MODEL_CHAIN keeps
# the cheap model first for background work). JARVIS_MODEL_CHAIN overrides it.
JARVIS_CHAIN = ai_parse_chain(os.environ.get("JARVIS_MODEL_CHAIN")
                              or "gemini=gemini-3.6-flash,gemini=gemini-3-flash-preview")


def _attached_images(body):
    """Pictures the owner attached in Jarvis: image_urls (several) or the
    older single image_url. Only links this platform issued; at most 10."""
    from .social import MAX_IMAGES, is_our_media
    raw = body.get("image_urls") if isinstance(body.get("image_urls"), list) else []
    raw = raw + [body.get("image_url")]
    out = []
    for u in raw:
        if isinstance(u, str) and is_our_media(u) and u not in out:
            out.append(u)
    return out[:MAX_IMAGES]


# Jarvis used to pass attached pictures on only as "for a social media post",
# so the model never saw them and a photographed menu came back as "I cannot
# read it". A picture sent with words about a menu or price list -- or with
# no words about a post -- is read with the same reader as the "photo of the
# menu" button on Business & agent, and lands there as a suggestion the owner
# checks and saves. Nothing is saved to services from here.
_MENU_WORDS = re.compile(r"menu|price ?list|rate ?list|\brates?\b|\bprices\b|read|parh|"
                         r"\u0645\u06cc\u0646\u0648|\u0631\u06cc\u0679|\u0642\u06cc\u0645\u062a|\u0644\u0633\u0679|\u067e\u0691\u06be", re.I)
# a post, an ad or a campaign: the picture is for that, not a menu to read
_POST_WORDS = re.compile(r"\bpost|caption|publish|schedule|instagram|facebook|\binsta\b|\bfb\b|\bstory|\breel|\bads?\b|"
                         r"design|\bbana|\bbna|\bdaal|\bdal\b|\bdalo|share|upload|promot|offer|\bdeal|discount|"
                         r"advert|campaign|boost|"
                         r"\u067e\u0648\u0633\u0679|\u0688\u06cc\u0632\u0627\u0626\u0646|"
                         r"\u06a9\u0645\u067e\u06cc\u0646|\u0627\u0634\u062a(?:\u06c1|\u06be)\u0627\u0631", re.I)
MENU_PHOTOS_MAX = 3          # pages of one menu read per message


def _wants_menu_read(message, attached):
    """Pictures are for a post unless the words are about a menu or price list
    and not about a post. (It used to be the other way round: pictures sent
    with "is ka design bana do" were read as a menu and never reached a post.)"""
    if not attached:
        return False
    return bool(_MENU_WORDS.search(message)) and not _POST_WORDS.search(message)


def _read_menu_photos(tid, role, urls):
    """Read the words off the attached pictures and file them as a setup
    suggestion for Business & agent. Returns (found_counts, error)."""
    from .setup_reader import photo_to_text, read_text
    texts = []
    with tenant_session(tid, role) as cur:
        for u in urls[:MENU_PHOTOS_MAX]:
            token = u.rsplit("/", 1)[-1].replace(".jpg", "", 1)
            cur.execute("SELECT bytes FROM social_media WHERE tenant_id = %s AND token = %s", (tid, token))
            row = cur.fetchone()
            if row:
                texts.append(bytes(row["bytes"]))
        cur.execute("SELECT slug FROM business_categories WHERE active ORDER BY sort_order")
        slugs = [r["slug"] for r in cur.fetchall()]
    if not texts:
        return None, "I could not open that picture. Attach it again and I will read it."
    words = []
    for data in texts:
        try:
            words.append(photo_to_text(data, "image/jpeg"))
        except RuntimeError as e:
            return None, "I could not read the picture right now (%s). Nothing was changed." % str(e)[:120]
    text = "\n\n".join(w for w in words if w.strip())[:40000]
    if not text.strip():
        return None, "I could not find any writing in that picture. Try a sharper, closer photo of the menu."
    draft = read_text(text, slugs)
    found = {"services": len(draft.get("services") or []), "hours": len(draft.get("hours") or []),
             "knowledge": len(draft.get("knowledge") or [])}
    status = "ready" if any(found.values()) or draft.get("business_type") else "nothing_found"
    src = [{"kind": "photo", "ref": "menu photo (%d page%s)" % (len(texts), "" if len(texts) == 1 else "s"),
            "ok": True, "note": None}]
    with tenant_session(tid, role) as cur:
        cur.execute("""INSERT INTO setup_suggestions (tenant_id, sources, fingerprint, draft, source_text, status)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (tid, json.dumps(src), "photo:" + str(uuid.uuid4()), json.dumps(draft), text, status))
        if status == "ready":   # only the newest suggestion is ever offered
            cur.execute("""UPDATE setup_suggestions SET status = 'dismissed', decided_at = now()
                            WHERE tenant_id = %s AND status = 'ready'
                              AND id <> (SELECT id FROM setup_suggestions WHERE tenant_id = %s
                                          ORDER BY created_at DESC LIMIT 1)""", (tid, tid))
    return (found if status == "ready" else None), (None if status == "ready" else
            "I read the picture but found no services or prices in it. You can paste the price list as text on Business & agent.")


def _tidy_caption(text):
    """Instagram takes at most 5 hashtags; extra ones after the fifth are
    dropped so a Jarvis caption can always go to both platforms."""
    seen = []
    def keep(m):
        tag = m.group(0)
        if tag.lower() in seen or len(seen) >= 5:
            return ""
        seen.append(tag.lower())
        return tag
    text = re.sub(r"#[\w\u0600-\u06ff]+", keep, str(text or ""))
    text = re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text))
    return text.strip()


def _post_platforms(platforms, images):
    """Instagram cannot publish a post without a picture: drop it and say so."""
    from .social import _clean_platforms
    plats = _clean_platforms(platforms)
    if not images and "instagram" in plats:
        plats = [p for p in plats if p != "instagram"] or ["facebook"]
        return plats, " Instagram needs a picture, so this one is for Facebook only. Attach a picture if you want it on Instagram too."
    return plats, ""


_NUM = re.compile(r"\d[\d,.]*")


def _design_text(out, said):
    """The headline and line Jarvis prints on the post's picture. Short, and
    a number (a price, a discount) only if the owner said that number."""
    heard = {n.replace(",", "") for n in _NUM.findall(said or "")}
    def clean(v, most):
        v = re.sub(r"\s+", " ", str(v or "")).strip().strip('"')
        if any(n.replace(",", "") not in heard for n in _NUM.findall(v)):
            return ""
        return v[:most].strip()
    head, sub = clean(out.get("headline"), 48), clean(out.get("subline"), 70)
    return {"head": head, "sub": sub} if head or sub else None


# ------------------------------------------------------------------ facts
# The console used to answer questions with nothing but the question, so a
# "how many appointments this week?" came back as a confident invented number.
# Every call now carries a small live snapshot of the business, read under the
# caller's own tenant fence, and the model is told to answer only from it.
FACTS_RULE = ("\n\nBUSINESS FACTS below are live data from this business's own "
              "records. Answer questions ONLY from them. If the answer is not in "
              "them, say you do not have that yet -- never invent a number, "
              "price, name or date. If the owner is only ASKING something, kind is "
              "\"question\" and nothing is changed. Use action, business, dashboard, teach or post "
              "only when the owner clearly tells you to do, change, add, remove, teach or post.")

_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


_FACTS_CACHE = {}
FACTS_TTL = 30   # seconds; any change Jarvis makes clears it at once


def _forget_facts(tid):
    for k in [k for k in _FACTS_CACHE if k[0] == str(tid)]:
        _FACTS_CACHE.pop(k, None)


def _facts(tid, role):
    """The snapshot, reused for a few seconds: in a conversation every turn
    used to re-run all of these queries before the model could even start."""
    key = (str(tid), role)
    hit = _FACTS_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    f = _facts_fresh(tid, role)
    if len(_FACTS_CACHE) > 500:
        _FACTS_CACHE.clear()
    _FACTS_CACHE[key] = (time.time() + FACTS_TTL, f)
    return f


def _facts_fresh(tid, role):
    f = {}
    tz = "Asia/Karachi"
    try:
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT to_char(now() AT TIME ZONE %s, 'Dy DD Mon YYYY HH24:MI') AS now", (tz,))
            f["now_pakistan_time"] = cur.fetchone()["now"]
            cur.execute("SELECT to_char(d, 'Dy') AS day, to_char(d, 'YYYY-MM-DD') AS date "
                        "FROM generate_series((now() AT TIME ZONE %s)::date, "
                        "(now() AT TIME ZONE %s)::date + 7, interval '1 day') AS d", (tz, tz))
            f["next_7_days"] = [r["day"] + " " + r["date"] for r in cur.fetchall()]
            cur.execute("SELECT name, role FROM staff WHERE coalesce(active, true) "
                        "ORDER BY name LIMIT 30")
            f["staff"] = [dict(r) for r in cur.fetchall()]
            # With ids, so "cancel Ayesha's appointment tomorrow" can name one.
            cur.execute("""
                SELECT a.id, to_char(a.starts_at AT TIME ZONE %(tz)s, 'Dy YYYY-MM-DD HH24:MI') AS at,
                       c.name AS customer, a.service, a.status
                  FROM appointments a LEFT JOIN customers c ON c.id = a.customer_id
                 WHERE a.starts_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s
                   AND a.starts_at < now() + interval '15 days'
                 ORDER BY a.starts_at LIMIT 30""", {"tz": tz})
            f["upcoming_appointments"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT name, price, currency, active FROM services "
                        "ORDER BY sort_order NULLS LAST, name LIMIT 40")
            f["services"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT day_of_week, opens::text, closes::text, is_closed "
                        "FROM business_hours ORDER BY day_of_week LIMIT 14")
            f["business_hours"] = [
                {"day": _DAYS[r["day_of_week"] % 7], "opens": r["opens"],
                 "closes": r["closes"], "closed": r["is_closed"]} for r in cur.fetchall()]
            cur.execute("""
                SELECT
                  count(*) FILTER (WHERE (starts_at AT TIME ZONE %(tz)s)::date
                                         = (now() AT TIME ZONE %(tz)s)::date) AS today,
                  count(*) FILTER (WHERE date_trunc('week', starts_at AT TIME ZONE %(tz)s)
                                         = date_trunc('week', now() AT TIME ZONE %(tz)s)) AS this_week_mon_to_sun,
                  count(*) FILTER (WHERE starts_at >= now()) AS upcoming,
                  count(*) AS all_time
                FROM appointments""", {"tz": tz})
            f["appointments"] = dict(cur.fetchone())
            cur.execute("""
                SELECT count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS last_7_days,
                       count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS last_30_days,
                       count(*) AS all_time
                FROM leads""")
            f["leads"] = dict(cur.fetchone())
            cur.execute("SELECT count(*) AS total FROM customers")
            f["customers"] = cur.fetchone()["total"]
            # Orders (products sold). A sale counts only when delivered AND fully
            # paid; with ids so "mark Ali's order delivered" can name one.
            from .orders import summary as _osum, list_orders as _olist
            os_ = _osum(cur, role)
            f["orders"] = {"counts": os_["counts"], "sources_this_month": os_["sources"],
                           "rule": "a sale counts only when an order is delivered AND fully paid"}
            if os_.get("money"):
                f["orders"]["money_pkr"] = os_["money"]
            cur.execute("""SELECT count(*) FILTER (WHERE started_at >= date_trunc('day', now())) AS today,
                                  count(*) FILTER (WHERE started_at >= date_trunc('month', now())) AS this_month,
                                  count(*) FILTER (WHERE NOT reviewed AND outcome IN ('interested','callback')) AS to_follow_up
                             FROM calls""")
            f["phone_calls"] = dict(cur.fetchone())
            cur.execute("""SELECT caller_name, from_number, outcome, summary, follow_up, started_at::text AS at
                             FROM calls WHERE status = 'completed' ORDER BY started_at DESC LIMIT 10""")
            f["phone_calls"]["recent"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id::text FROM orders WHERE status IN ('new','confirmed','out_for_delivery') "
                        "OR created_at >= now() - interval '30 days' OR (status = 'delivered' AND recognised_at IS NULL) "
                        "ORDER BY created_at DESC LIMIT 40")
            keep = {r["id"] for r in cur.fetchall()}
            f["orders"]["recent_and_open"] = [
                {"id": o["id"], "number": o["number"], "customer": o["customer_name"], "phone": o["customer_phone"],
                 "items": o["items"], "total": o["total"], "status": o["status_label"],
                 "payment": o["payment_status"], "paid": o["paid"], "still_owed": o["balance"],
                 "source": o["source"], "placed": str(o["created_at"])[:16]}
                for o in _olist(cur, None, None, 200) if o["id"] in keep][:40]
            if role in ("owner", "techdeva_admin"):
                cur.execute("""
                    SELECT coalesce(sum(amount), 0)::float AS collected_this_month
                    FROM payments
                    WHERE date_trunc('month', paid_at AT TIME ZONE %(tz)s)
                          = date_trunc('month', now() AT TIME ZONE %(tz)s)""", {"tz": tz})
                f["money_pkr"] = dict(cur.fetchone())
                cur.execute("""
                    SELECT coalesce(sum(amount), 0)::float AS spent
                    FROM expenses
                    WHERE date_trunc('month', spent_on) = date_trunc('month', (now() AT TIME ZONE %(tz)s)::date)""",
                            {"tz": tz})
                f["money_pkr"]["expenses_this_month"] = cur.fetchone()["spent"]
    except Exception as e:  # a facts failure must never block the console
        f["facts_error"] = type(e).__name__
    f.update(_more_facts(tid, role))
    # the owner-approved profile from the business's website (app/website_agent.py, db/76)
    try:
        from .website_agent import context_text
        bp = context_text(tid)
        if bp:
            f["business_profile_from_website"] = bp
    except Exception as e:  # noqa: BLE001
        f["business_profile_from_website_unavailable"] = type(e).__name__
    if not f.get("services"):
        try:
            from .persistence import Persistence
            cfg = Persistence().load_config(tid) or {}
            fb = biz._onboarding_fallback("services", cfg) or []
            f["services"] = [{k: v for k, v in dict(i).items() if k in ("name", "price", "currency")}
                             for i in fb][:40]
            if not f.get("business_hours") and cfg.get("working_hours"):
                f["business_hours"] = cfg.get("working_hours")
        except Exception:
            pass
    return f


# Every module Jarvis may answer from. Each part is read in its own session, so
# one missing table cannot blank the rest; money stays owner-only as above.
_MORE = (
    ("products", """SELECT p.name, p.price, p.active, p.track_stock, p.low_stock_threshold,
                           CASE WHEN p.track_stock THEN (SELECT COALESCE(sum(quantity_delta), 0) FROM stock_movements m
                                                          WHERE m.product_id = p.id) END AS in_stock
                      FROM products p ORDER BY p.active DESC, p.name LIMIT 60""", "rows"),
    ("packages", "SELECT name, price, active FROM packages ORDER BY active DESC, name LIMIT 20", "rows"),
    ("offers", """SELECT title, discount_type, discount_value, starts_on, ends_on, active FROM offers
                   WHERE active ORDER BY created_at DESC LIMIT 15""", "rows"),
    ("recent_leads", """SELECT id, name, phone, service, status, source, created_at::date AS added FROM leads
                         ORDER BY created_at DESC LIMIT 15""", "rows"),
    # the team's planned follow-ups (CRM, db/77); ids for complete_followup
    ("crm_followups", """SELECT a.id, a.title, a.kind, to_char(a.due_at AT TIME ZONE 'Asia/Karachi', 'YYYY-MM-DD HH24:MI') AS due,
                                a.due_at < now() AS overdue, a.lead_id, l.name AS lead, a.customer_id, c.name AS customer,
                                u.name AS assigned_to
                           FROM crm_activities a LEFT JOIN leads l ON l.id = a.lead_id
                           LEFT JOIN customers c ON c.id = a.customer_id LEFT JOIN users u ON u.id = a.assigned_to
                          WHERE a.done_at IS NULL AND a.kind <> 'note'
                          ORDER BY a.due_at ASC NULLS LAST LIMIT 30""", "rows"),
    ("recent_customers", """SELECT name, phone, status, created_at::date AS added FROM customers
                             ORDER BY created_at DESC LIMIT 15""", "rows"),
    ("invoices", """SELECT count(*) FILTER (WHERE status IN ('issued','partial')) AS unpaid,
                           count(*) FILTER (WHERE status IN ('issued','partial') AND due_on < CURRENT_DATE) AS overdue,
                           count(*) FILTER (WHERE issued_on >= date_trunc('month', CURRENT_DATE)) AS issued_this_month
                      FROM invoices""", "one"),
    ("expenses", """SELECT category, description, amount, spent_on FROM expenses
                     ORDER BY spent_on DESC, created_at DESC LIMIT 15""", "rows"),
    ("social_posts", """SELECT left(caption, 120) AS caption, platforms, status, results, scheduled_at, published_at
                         FROM social_posts WHERE status <> 'discarded' ORDER BY updated_at DESC LIMIT 10""", "rows"),
    ("ad_campaigns", """SELECT provider, service, daily_budget, status, destination, created_at::date AS made,
                               launched_at::date AS launched, test->>'status' AS ab_test, error
                          FROM ad_drafts WHERE status <> 'discarded' ORDER BY created_at DESC LIMIT 10""", "rows"),
    ("social_inbox", """SELECT count(*) AS conversations, count(*) FILTER (WHERE hot) AS serious_clients,
                               count(*) FILTER (WHERE needs_owner IS NOT NULL AND needs_owner <> '') AS waiting_for_owner
                          FROM social_threads""", "one"),
    ("team_today", """SELECT s.name, a.status, a.check_in::text, a.check_out::text FROM staff_attendance a
                        JOIN staff s ON s.id = a.staff_id
                       WHERE a.day = (now() AT TIME ZONE 'Asia/Karachi')::date ORDER BY s.name LIMIT 40""", "rows"),
    ("ai_tasks", """SELECT agent, type, status::text, updated_at FROM tasks ORDER BY updated_at DESC LIMIT 10""", "rows"),
)
# which dashboard section (app/roles.py) each fact belongs to; a login with a
# role is only given the facts of the sections it may see
FACT_SECTION = {
    "business_profile_from_website": "business",
    "upcoming_appointments": "crm", "appointments": "crm", "leads": "crm", "customers": "crm",
    "recent_leads": "crm", "recent_customers": "crm", "crm_followups": "crm",
    "services": "business", "business_hours": "business", "products": "business", "packages": "business",
    "orders": "operations",
    "phone_calls": "chats", "social_inbox": "chats",
    "money_pkr": "finance", "invoices": "finance", "expenses": "finance",
    "social_posts": "marketing", "ad_campaigns": "marketing", "offers": "marketing",
    "team_today": "team", "ai_tasks": "jarvis",
}


def _more_facts(tid, role):
    out = {}
    for key, sql, shape in _MORE:
        try:
            with tenant_session(tid, role) as cur:
                cur.execute(sql)
                out[key] = [dict(r) for r in cur.fetchall()] if shape == "rows" else dict(cur.fetchone() or {})
        except Exception as e:  # noqa: BLE001 -- one module failing must not blank the others
            out[key + "_unavailable"] = type(e).__name__
    return out


def _facts_for(tid, role, uid):
    """The snapshot this person may see: everything for the owner; for a login
    with a team role, only the sections that role can view (app/roles.py)."""
    f = _facts(tid, role)
    try:
        from .roles import SECTIONS, permissions_for
        p = permissions_for(uid) if uid else None
    except Exception:  # noqa: BLE001
        p = None
    if not p:
        return f
    may = {sec for sec, acts in (p.get("permissions") or {}).items() if "view" in acts}
    out = {k: v for k, v in f.items() if FACT_SECTION.get(k.replace("_unavailable", ""), "") in may or k.replace("_unavailable", "") not in FACT_SECTION}
    hidden = sorted({SECTIONS[s] for k, s in FACT_SECTION.items() if s not in may and s in SECTIONS})
    if hidden:
        out["not_in_your_role"] = ("This person's role (%s) does not include: %s. If asked about these, say their role "
                                   "does not include it and the owner can change that." % (p.get("name"), ", ".join(hidden)))
    return out


def _history(raw):
    """The last few turns the dashboard sends back, so "5 pm" or "yes, her
    number is 0300..." continues the request before it."""
    lines = []
    if isinstance(raw, list):
        for h in raw[-8:]:
            if not isinstance(h, dict):
                continue
            who = "Owner" if h.get("role") == "user" else "Jarvis"
            text = re.sub(r"\s+", " ", str(h.get("text") or "")).strip()[:300]
            if text:
                lines.append("%s: %s" % (who, text))
    return "\n".join(lines)


def _role_may(uid, section, action):
    """A login with no team role keeps its base access; one with a role needs that section and action."""
    try:
        from .roles import permissions_for
        p = permissions_for(uid) if uid else None
    except Exception:  # noqa: BLE001
        return True
    return p is None or action in (p.get("permissions", {}).get(section) or [])


def _with_facts(tid, role, message, history="", uid=None):
    return ("BUSINESS FACTS:\n" + json.dumps(_facts_for(tid, role, uid), default=str)[:16000] +
            ("\n\nRECENT CONVERSATION:\n" + history if history else "") +
            "\n\nOWNER SAYS:\n" + message)


# ------------------------------------------------------------------ voice
# Browsers have no Urdu voice worth hearing, so Jarvis speaks through Gemini's
# speech models. Tried in order; a busy model falls through to the next.
_TTS_MODELS = ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts",
               "gemini-2.5-flash-preview-tts")
# A voice that takes longer than this is worse than the browser's own voice,
# which the dashboard falls back to. Was 25 s per model (up to 75 s of silence).
TTS_TIMEOUT = 8
TTS_BUDGET = 12
_TTS_CACHE = {}          # text -> wav, last 64 phrases ("Done.", repeated answers)


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _tts(text: str, urdu: bool) -> bytes:
    import os
    from .ai import base_url
    from .platform_key import gemini_key
    key = gemini_key()
    if not key:
        raise HTTPException(503, "Voice is not set up on this server.")
    from . import usage
    why = usage.check()
    if why:
        raise HTTPException(402, why)
    style = ("Read this aloud in natural, warm Pakistani Urdu, like a friendly assistant: "
             if urdu else "Read this aloud in a warm, friendly, clear voice: ")
    last = "no model tried"
    started = time.time()
    for m in _TTS_MODELS:
        if time.time() - started > TTS_BUDGET:
            last += "; out of time after %ds" % TTS_BUDGET
            break
        try:
            r = requests.post(
                f'{base_url("gemini")}/v1beta/models/{m}:generateContent',
                params={"key": key},
                json={"contents": [{"parts": [{"text": style + text}]}],
                      "generationConfig": {
                          "responseModalities": ["AUDIO"],
                          "speechConfig": {"voiceConfig": {
                              "prebuiltVoiceConfig": {"voiceName": "Kore"}}}}},
                timeout=(4, TTS_TIMEOUT))
            if r.status_code >= 400:
                last = "%s_%s" % (m, r.status_code)
                continue
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            data = base64.b64decode(part["data"])
            mime = part.get("mimeType", "")
            usage.charge("voice", True, provider="gemini", model=m, ms=(time.time() - started) * 1000, chars_in=len(text))
            if "wav" in mime:
                return data
            mm = re.search(r"rate=(\d+)", mime)
            return _pcm_to_wav(data, int(mm.group(1)) if mm else 24000)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            last = "%s: %s" % (m, type(e).__name__)
    raise HTTPException(503, "Voice is busy right now (%s)." % last)


def _sentence(e):
    t = str(e).strip()
    t = t[:1].upper() + t[1:]
    return t if t.endswith((".", "?", "!")) else t + "."


def _when(args):
    """'2026-09-30' + '16:00' -> 'Wed 30 Sep at 4:00 pm' (for spoken replies)."""
    import datetime
    try:
        d = datetime.date.fromisoformat(str(args.get("date")))
        out = d.strftime("%a %d %b").replace(" 0", " ")
    except (TypeError, ValueError):
        return ""
    try:
        h, m = (int(x) for x in str(args.get("time")).split(":"))
        out += " at %d:%02d %s" % (h % 12 or 12, m, "am" if h < 12 else "pm")
    except (TypeError, ValueError):
        pass
    return out


def _done_text(action, args):
    args = args if isinstance(args, dict) else {}
    name = str(args.get("name") or "").strip()
    when = _when(args)
    if action == "book_appointment":
        return "Done, %s is booked%s." % (name or "the appointment", " for " + when if when else "")
    if action == "reschedule_appointment":
        return "Done, it's moved to %s." % when if when else "Done, it's moved."
    if action == "cancel_appointment":
        return "Done, that appointment is cancelled."
    if action == "complete_appointment":
        return "Done, it's marked as completed."
    if action == "add_customer":
        return "Done, %s is added to your customers." % (name or "they")
    if action == "add_lead":
        return "Done, %s is added as a new lead." % (name or "they")
    if action == "add_followup":
        return "Done, the follow-up is planned%s." % (" for " + str(args.get("due_words")) if args.get("due_words") else "")
    if action == "complete_followup":
        return "Done, it's marked as done."
    if action == "add_expense":
        try:
            amt = "PKR {:,.0f}".format(float(str(args.get("amount")).replace(",", "")))
        except (TypeError, ValueError):
            amt = "the expense"
        what = str(args.get("description") or "").strip()
        return "Done, %s%s is recorded." % (amt, " for " + what if what else "")
    if action == "schedule_post":
        return "Done, the post will go out %s." % (args.get("at_words") or "at the time you picked")
    if action == "publish_post":
        return "Done, it's posted."
    if action == "update_order":
        return "Done, the order is updated."
    if action == "record_order_payment":
        return "Done, the payment is recorded."
    if action == "cancel_order":
        return "Done, that order is cancelled."
    if action == "navigate":
        return "Here you go."
    return "Done."


def _published_text(status, results):
    """The reply after a publish, from what each platform answered."""
    name = {"facebook": "Facebook", "instagram": "Instagram"}
    went = [name.get(k, k) for k, v in results.items() if v.get("ok") and v.get("id")]
    bad = ["%s (%s)" % (name.get(k, k), str(v.get("error") or "refused")[:120]) for k, v in results.items() if not v.get("ok")]
    if status == "queued":
        return ("It's queued: Facebook is processing the video and it goes out within a minute or two. "
                "Its status shows on the Posts page.")
    if status == "published" and went:
        return "Posted on %s; confirmed by the platform." % " and ".join(went)
    if status == "partial" and went:
        return "Posted on %s, but it did not go out on %s." % (" and ".join(went), "; ".join(bad))
    return "It did not go out%s. It is still in your drafts." % ((": " + "; ".join(bad)) if bad else "")


def _do_action(tid, role, uid, action, args, founder, reply):
    try:
        result, note = ja.run(tid, role, uid, action, args, founder=founder)
    except ja.ActionError as e:
        # Said, not thrown: in a spoken conversation an error box is silence.
        return {"kind": "action", "action": action, "ok": False,
                "reply": "I couldn't do that: %s" % _sentence(e)}
    if action in getattr(ja, "DASH_OPS", {}):
        # the screen updates the way a "dashboard" answer already does (PREFS)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT dashboard_prefs FROM tenant_config WHERE tenant_id = %s", (tid,))
            r = cur.fetchone()
        return {"kind": "dashboard", "action": action, "ok": True, "reply": "Done, the dashboard is updated.",
                "changed": result.get("dashboard_config"), "prefs": (r or {}).get("dashboard_prefs") or {}}
    _forget_facts(tid)
    # The model writes its reply before anything runs, and sometimes as a
    # question ("Shall I book...?") although the booking is already made. A
    # client must never hear a question about something already done, so a
    # questioning (or missing) reply is replaced by what actually happened.
    if not reply or reply.rstrip().endswith(("?", "\u061f")):
        reply = _done_text(action, args)
    if action == "publish_post":
        # only what Facebook / Instagram confirmed: a post id back means it is out
        reply = _published_text(result.get("post_status"), result.get("post_results") or {})
        return dict(result, kind="action", action=action, ok=True, reply=reply)
    if note and action == "write_campaign":
        # the model's own words could say "launched"; only what happened is said
        reply = note + "."
    elif note:
        reply = reply.rstrip(".") + " -- " + note + "."
    return dict(result, kind="action", action=action, ok=True, reply=reply)


def make_console_router():
    router = APIRouter(prefix="/console", tags=["console"])

    @router.post("")
    def ask(response: Response, body: dict = Body(...),
            x_tenant_id: str | None = Header(None),
            x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One turn with Jarvis, kept in the person's conversation (app/jarvis_memory.py).
        What they said is saved first; the history the model reads comes from the
        database; Jarvis's answer (or the error) is saved after."""
        from . import jarvis_memory as jm
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=body.get("tenant_id"))
        if not jm.is_person(uid):       # a service token or dev header: answer, keep nothing
            return _ask(response, body, x_tenant_id, x_role, authorization, td_session)
        said = (str(body.get("said") or "").strip() or "Yes") if body.get("confirm") else (body.get("message") or "").strip()
        conv = jm.open_conversation(tid, role, uid, body.get("conversation_id"), said)
        response.headers["X-Jarvis-Conversation"] = conv
        body = dict(body, history=jm.history(tid, role, conv))
        if said:
            jm.add_message(tid, role, conv, "user", said[:2000], "confirm" if body.get("confirm") else "message")
        try:
            out = _ask(response, body, x_tenant_id, x_role, authorization, td_session)
        except HTTPException as e:
            jm.add_message(tid, role, conv, "jarvis", str(e.detail)[:1000], "error", {"status": e.status_code})
            raise HTTPException(e.status_code, e.detail, headers={"X-Jarvis-Conversation": conv})
        meta = {k: out[k] for k in ("action", "ok", "navigate", "post_status", "post_results") if k in out}
        if out.get("post") and isinstance(out["post"], dict):
            meta["post"] = {"id": str(out["post"].get("id")), "status": out["post"].get("status")}
        jm.add_message(tid, role, conv, "jarvis", out.get("reply") or "", out.get("kind"), meta)
        return dict(out, conversation_id=conv)

    def _ask(response, body, x_tenant_id, x_role, authorization, td_session):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=body.get("tenant_id"))
        try:
            founder = resolve_scope(x_tenant_id, x_role, authorization, td_session)[1] == "techdeva_admin"
        except AuthError:
            founder = False

        # "Yes" to a question Jarvis asked: run exactly what was sealed, no model.
        if body.get("confirm"):
            action, args = ja.unseal(body["confirm"], tid, uid, role)
            return _do_action(tid, role, uid, action, args, founder, None)

        message = (body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "Type what you would like changed.")
        if len(message) > 2000:
            raise HTTPException(400, "That is too long. Say it in a sentence or two.")

        # Voice conversation: the answer is spoken, so keep it to what a person
        # can listen to. Timing goes back in headers so a slow turn can be
        # pinned on the model or the voice, not guessed at.
        voice = bool(body.get("voice"))
        attached = _attached_images(body)
        if _wants_menu_read(message, attached):
            if role not in ("owner", "techdeva_admin"):
                return {"kind": "question", "reply": "Only the business owner can add a menu or price list."}
            found, err = _read_menu_photos(tid, role, attached)
            if err:
                return {"kind": "question", "reply": err}
            what = ", ".join(x for x in (
                "%d item%s" % (found["services"], "" if found["services"] == 1 else "s") if found["services"] else "",
                "opening hours" if found["hours"] else "",
                "%d note%s" % (found["knowledge"], "" if found["knowledge"] == 1 else "s") if found["knowledge"] else "") if x)
            return {"kind": "action", "action": "navigate", "ok": True, "navigate": "bizagent",
                    "reply": "I read your menu and found %s. It is on Business & agent now: check the names and prices, "
                             "then press Save. Nothing changes until you do." % (what or "your business details")}
        if attached:
            message += ("\n[The owner attached %d picture%s to this message, for the post or ad campaign they ask for.]"
                        % (len(attached), "" if len(attached) == 1 else "s"))
        system = CLASSIFY + _dash_rule() + UC_RULE + LANG_RULE + PERSONA_RULE + FACTS_RULE + (VOICE_RULE if voice else "")
        t0 = time.time()
        try:
            # No retry here on purpose. A person is sitting in front of this
            # box: a second full pass through the model chain turns a slow
            # answer into a minute of silence, and silence reads as broken.
            with ai_fast(), ai_chain_first(JARVIS_CHAIN):
                out = generate_json(system, _with_facts(tid, role, message, _history(body.get("history")), uid),
                                    required_keys=("kind",), _retry=False)
            response.headers["Server-Timing"] = "think;dur=%d" % int((time.time() - t0) * 1000)
            response.headers["X-Jarvis-Model"] = ai_last_model() or ""
        except CreditsUsedUp as e:          # this month's AI credits are used (lock mode)
            raise HTTPException(402, str(e))
        except AIUnavailable as e:
            # Better to say the console is deaf than to guess at an instruction
            # that might change what the business says to its customers.
            raise HTTPException(503, "The console could not reach the AI right now, "
                                     "so nothing was changed. (%s)" % str(e)[:120])

        kind = (out.get("kind") or "question").strip().lower()
        reply = (out.get("reply") or "").strip() or "Done."

        if kind == "action":
            action = (out.get("action") or "").strip().lower()
            args = out.get("args") if isinstance(out.get("args"), dict) else {}
            if action not in ja.ACTIONS:
                return {"kind": "question", "reply": reply}
            # The model sometimes fills an optional detail nobody said (live, it
            # added "saloon services" to a plain booking). A service or staff
            # name with no word in what was said -- this turn or the recent
            # ones -- is dropped instead of being booked or noted.
            heard = (message + " " + _history(body.get("history"))).lower()
            for k in ("service", "staff"):
                v = str(args.get(k) or "").strip().lower()
                if v and not any(w in heard for w in re.findall(r"[a-z0-9\u0600-\u06ff]{3,}", v)):
                    args.pop(k, None)
            if action == "write_campaign":
                args["image_urls"] = attached
            if action == "schedule_post":
                args.pop("image_url", None)
                args["image_urls"] = attached
                args["platforms"], note = _post_platforms(args.get("platforms"), attached)
                if note:
                    args["_note"] = note
                if args.get("caption"):
                    args["caption"] = _tidy_caption(args["caption"])
            if action in ja.CONFIRM:
                try:
                    question = ja.describe(tid, role, action, args, founder)
                except ja.ActionError as e:
                    return {"kind": "question", "reply": _sentence(e)}
                return {"kind": "confirm", "action": action, "summary": question,
                        "reply": reply if reply.rstrip().endswith("?") else question,
                        "confirm": ja.seal(tid, uid, role, action, args)}
            return _do_action(tid, role, uid, action, args, founder, reply)
        if kind in ("teach", "dashboard", "post", "business"):
            # these change things here, not through another API, so the team
            # role (app/roles.py) is checked here as the middleware would
            need = {"post": ("marketing", "create"), "business": ("business", "edit"),
                    "teach": ("business", "edit"), "dashboard": ("admin", "manage")}[kind]
            if not _role_may(uid, *need):
                from .roles import SECTIONS
                return {"kind": "question", "reply": "Your role cannot change %s, so I have left it as it is. "
                                                     "The owner can do it or give your role that permission." % SECTIONS[need[0]]}
            _forget_facts(tid)

        if kind == "teach":
            if role not in ("owner", "techdeva_admin"):
                raise HTTPException(403, "Only the business owner can teach the sales agent.")
            lesson = (out.get("lesson") or message).strip()
            if len(lesson) < 3:
                raise HTTPException(400, "I could not tell what to teach from that.")
            with tenant_session(tid, role) as cur:
                cur.execute("""INSERT INTO agent_lessons (tenant_id, agent, lesson, said_as, created_by)
                               VALUES (%s, 'whatsapp', %s, %s, %s)
                               RETURNING id, lesson, created_at""",
                            (tid, lesson[:2000], message[:2000], uid))
                row = cur.fetchone()
                record(cur, action="console.teach", resource="agent_lessons",
                       resource_id=str(row["id"]), actor=str(uid), actor_type="human",
                       status="ok", detail={"lesson": lesson[:200]}, error=None)
            # The platform's own agents read agent_lessons directly; the
            # client's WhatsApp agent reads SOUL.md, so push it there too.
            agent_sync = wa_sync_lessons(tid, role)
            return {"kind": "teach", "reply": reply, "agent_sync": agent_sync,
                    "lesson": row["lesson"], "lesson_id": str(row["id"])}

        if kind == "dashboard":
            if role not in ("owner", "techdeva_admin"):
                raise HTTPException(403, "Only the business owner can change the dashboard.")
            prefs = _clean_prefs(out.get("prefs"))
            # Dashboard Customizer on: hiding/showing pages is asked first, like its own tools
            from . import dashcustom as _dc
            if "hidden_sections" in prefs and _dc.enabled():
                want = {"hidden": prefs.pop("hidden_sections")}
                try:
                    question = ja.describe(tid, role, "dashboard_set", want, founder)
                except ja.ActionError as e:
                    return {"kind": "question", "reply": _sentence(e)}
                if not prefs:
                    return {"kind": "confirm", "action": "dashboard_set", "summary": question, "reply": question,
                            "confirm": ja.seal(tid, uid, role, "dashboard_set", want)}
            if not prefs:
                return {"kind": "question", "reply":
                        "I could not tell which dashboard setting you meant. "
                        "Try naming the colour, a section to hide, or which page to open first."}
            with tenant_session(tid, role) as cur:
                cur.execute("""INSERT INTO tenant_config (tenant_id, dashboard_prefs)
                               VALUES (%s, %s::jsonb)
                               ON CONFLICT (tenant_id) DO UPDATE
                                 SET dashboard_prefs = tenant_config.dashboard_prefs || EXCLUDED.dashboard_prefs
                               RETURNING dashboard_prefs""",
                            (tid, json.dumps(prefs)))
                merged = cur.fetchone()["dashboard_prefs"]
                record(cur, action="console.dashboard", resource="tenant_config",
                       resource_id=str(tid), actor=str(uid), actor_type="human",
                       status="ok", detail=prefs, error=None)
            return {"kind": "dashboard", "reply": reply, "changed": prefs, "prefs": merged}
        if kind == "post":
            if role not in ("owner", "techdeva_admin"):
                raise HTTPException(403, "Only the business owner can post for the business.")
            from .social import _clean_platforms, create_draft
            # Pictures the owner attached in Jarvis win over any link the
            # model extracted; only links this platform issued are attached.
            images = attached or ([out["image_url"]] if out.get("image_url") else [])
            plats, note = _post_platforms(out.get("platforms"), images)
            design = _design_text(out, message)
            if design:
                # the dashboard lays these words on the first picture (or on the
                # brand colour when there is none) and swaps it into the draft;
                # with a picture, Instagram can take it after all
                design["platforms"] = _clean_platforms(out.get("platforms"))
                note = ""
            with tenant_session(tid, role) as cur:
                draft = create_draft(cur, tid, uid, _tidy_caption(out.get("caption") or message),
                                     plats, image_urls=images)
            n = len(images)
            made = "I designed and drafted this post" if design else "I drafted this post"
            made += (" with your %d pictures" % n if n > 1 else " with your picture" if n else "")
            res = {"kind": "post", "post": draft, "design": design}
            if founder:
                return dict(res, reply=made + ". It's in the client's drafts for them to approve." + note)
            # posting is public: ask, and a "yes" (or "post it") redeems
            # exactly this draft through the same sealed confirmation
            return dict(res, reply=made + ". It's saved in your drafts. Shall I post it now?" + note,
                        confirm=ja.seal(tid, uid, role, "publish_post", {"post_id": str(draft["id"])}),
                        no_reply="Okay, it stays in your drafts on the Posts page. Say \"post it\" when you want it out.")
        if kind == "business":
            if role not in ("owner", "techdeva_admin"):
                raise HTTPException(403, "Only the business owner can change business details.")
            resource = (out.get("resource") or "").strip()
            if resource not in BUSINESS_RESOURCES:
                return {"kind": "question", "reply":
                        "I can change services, staff, hours, policies, locations, "
                        "products, packages or goals from here. For anything else, "
                        "use the matching page in the sidebar."}
            spec = biz.RESOURCES[resource]
            fields = out.get("fields") if isinstance(out.get("fields"), dict) else {}
            match = (out.get("match") or "").strip()
            match_col = BUSINESS_MATCH_COLUMN.get(resource)
            with tenant_session(tid, role) as cur:
                row = None
                # business_hours has no name/title -- the day itself is the
                # identity, and it is already unique per tenant+location, so
                # match on the day the model extracted into fields rather
                # than a free-text 'match' string.
                if resource == "business_hours":
                    try:
                        day_val = int(fields.get("day_of_week")) \
                            if fields.get("day_of_week") is not None else None
                    except (TypeError, ValueError):
                        day_val = None
                    if day_val is not None:
                        cur.execute(
                            "SELECT {0} FROM {1} WHERE day_of_week = %s LIMIT 1".format(
                                spec.read, spec.table), (day_val,))
                        row = cur.fetchone()
                elif match and match_col:
                    cur.execute(
                        "SELECT {0} FROM {1} WHERE {2}::text ILIKE %s ORDER BY {3} LIMIT 1".format(
                            spec.read, spec.table, match_col, spec.order),
                        (match,))
                    row = cur.fetchone()
                if row:
                    data = biz._clean(spec, fields, False)
                    if not data:
                        return {"kind": "question", "reply": reply or "Nothing there to change."}
                    sets = ", ".join("{0} = %s".format(k) for k in data)
                    has_updated = "updated_at" in spec.read
                    sql = "UPDATE {0} SET {1}{2} WHERE id = %s RETURNING {3}".format(
                        spec.table, sets, ", updated_at = now()" if has_updated else "", spec.read)
                    cur.execute(sql, list(data.values()) + [row["id"]])
                    saved = cur.fetchone()
                    action = "update"
                else:
                    data = biz._clean(spec, fields, True)
                    cols = ["tenant_id"] + list(data.keys())
                    vals = [tid] + list(data.values())
                    sql = "INSERT INTO {0} ({1}) VALUES ({2}) RETURNING {3}".format(
                        spec.table, ", ".join(cols), ", ".join(["%s"] * len(cols)), spec.read)
                    cur.execute(sql, vals)
                    saved = cur.fetchone()
                    action = "create"
                record(cur, action="console.business." + action, resource=resource,
                       resource_id=str(saved.get("id")), actor=str(uid), actor_type="human",
                       status="ok", detail=data, error=None)
            return {"kind": "business", "resource": resource, "action": action,
                    "reply": reply, "item": saved}


        return {"kind": "question", "reply": reply}

    @router.get("/lessons")
    def lessons(tenant: str | None = None,
                x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Everything the sales agent has been told, newest first."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=tenant)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT id, lesson, said_as, created_at FROM agent_lessons
                            WHERE active AND agent = 'whatsapp'
                            ORDER BY created_at DESC LIMIT 200""")
            rows = [dict(r) for r in cur.fetchall()]
        return {"lessons": rows, "count": len(rows)}

    @router.delete("/lessons/{lesson_id}")
    def retire(lesson_id: str,
               tenant: str | None = None,
               x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Retire a lesson. Nothing is deleted -- what the agent was told and
        when it stopped being told it is part of the record."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=tenant)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can change what the agent knows.")
        with tenant_session(tid, role) as cur:
            cur.execute("""UPDATE agent_lessons SET active = false, retired_at = now()
                            WHERE id = %s AND active RETURNING id""", (lesson_id,))
            if not cur.fetchone():
                raise HTTPException(404, "That lesson is not there.")
            record(cur, action="console.retire", resource="agent_lessons",
                   resource_id=lesson_id, actor=str(uid), actor_type="human",
                   status="ok", detail={}, error=None)
        return {"ok": True, "agent_sync": wa_sync_lessons(tid, role)}

    @router.get("/prefs")
    def prefs(tenant: str | None = None,
              x_tenant_id: str | None = Header(None),
              x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=tenant)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT dashboard_prefs FROM tenant_config WHERE tenant_id = %s", (tid,))
            row = cur.fetchone()
        return {"prefs": (row or {}).get("dashboard_prefs") or {}}

    @router.put("/prefs/pins")
    def save_pins(body: dict = Body(...),
                  x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Which sections each role sees pinned on its Dashboard.

        Stored in the same dashboard_prefs the console writes (no new table),
        keyed by the platform's two tenant roles. Owner only: deciding what the
        front desk sees first is the owner's call."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can choose what is pinned.")
        raw = body.get("pinned_by_role")
        if not isinstance(raw, dict):
            raise HTTPException(400, "Send pinned_by_role as {owner: [...], operator: [...]}.")
        clean = {}
        for r in ("owner", "operator"):
            ids = raw.get(r) or []
            if not isinstance(ids, list) or len(ids) > 12:
                raise HTTPException(400, "Each role can pin at most 12 sections.")
            ok = []
            for x in ids:
                x = str(x)
                if not re.fullmatch(r"[a-z]{2,20}", x):
                    raise HTTPException(400, "Unknown section: %s" % x[:30])
                if x not in ok:
                    ok.append(x)
            clean[r] = ok
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO tenant_config (tenant_id, dashboard_prefs)
                           VALUES (%s, %s::jsonb)
                           ON CONFLICT (tenant_id) DO UPDATE
                             SET dashboard_prefs = tenant_config.dashboard_prefs || EXCLUDED.dashboard_prefs
                           RETURNING dashboard_prefs""",
                        (tid, json.dumps({"pinned_by_role": clean})))
            merged = cur.fetchone()["dashboard_prefs"]
            record(cur, action="dashboard.pins", resource="tenant_config", resource_id=str(tid),
                   actor=str(uid), actor_type="human", status="ok", detail=clean, error=None)
        return {"prefs": merged}

    @router.post("/speak")
    def speak(body: dict = Body(...),
              x_tenant_id: str | None = Header(None),
              x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        # Any signed-in user may hear Jarvis; nothing here reads or writes data.
        try:
            resolve_scope(x_tenant_id, x_role, authorization, td_session)
        except AuthError as e:
            raise HTTPException(401, str(e))
        text = (body.get("text") or "").strip()[:600]
        if not text:
            raise HTTPException(400, "Nothing to say.")
        urdu = body.get("lang") == "ur" or any("\u0600" <= c <= "\u06ff" for c in text)
        t0 = time.time()
        key = ("ur:" if urdu else "en:") + text
        wav = _TTS_CACHE.get(key)
        if wav is None:
            wav = _tts(text, urdu)
            if len(_TTS_CACHE) >= 64:
                _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
            _TTS_CACHE[key] = wav
        return Response(content=wav, media_type="audio/wav",
                        headers={"Cache-Control": "no-store",
                                 "Server-Timing": "voice;dur=%d" % int((time.time() - t0) * 1000)})

    return router
