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
import re
import uuid
import wave

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Response

from . import business as biz
from .ai import AIUnavailable, generate_json
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

CLASSIFY = """You turn a business owner's instruction into one action for their
AI business platform. Answer with JSON only.

Fields:
  "kind": one of "dashboard", "teach", "business", "post", "question"
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
  "caption": if kind is post, the finished social media caption, ready to
            publish, written for the business's customers in the business's
            own voice, using only facts the owner gave. Include a short call
            to action. Hashtags only if the owner asked for them.
  "platforms": if kind is post, an array of "facebook" and/or "instagram";
            both if the owner did not say
  "image_url": if kind is post and the owner gave an https picture link, that
            link, otherwise empty
  "reply": one short sentence, addressed to the owner, saying what you did or
           answering their question. No emoji, no exclamation marks.

Section ids available: overview, leads, customers, appointments, revenue,
workforce, needshuman, inbox, invoices, expenses, dayclose, pnl, services,
packages, inventory, integrations, reports, settings, console.

If the instruction changes a real fact about the business itself -- a
price, a service, a staff member, opening hours, a policy, a location, a
package, or a goal -- it is "business".
If it is about what the WhatsApp agent should know, say, charge, promise, or
refuse to a CUSTOMER (not a change to the underlying price/hours/policy
itself), it is "teach".
If it is about how their own screen looks or which sections they see, it is
"dashboard".
If they want something posted, shared or announced on Facebook, Instagram or
social media, it is "post" -- it becomes a draft they approve before anything
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


# Appended to the classifier so the reply sounds right when spoken.
LANG_RULE = ("\n\nLanguage: write \"reply\" in the same language and script the owner used "
             "(English, Urdu script, or Roman Urdu). Keep it short and natural to say aloud.")


# ------------------------------------------------------------------ facts
# The console used to answer questions with nothing but the question, so a
# "how many appointments this week?" came back as a confident invented number.
# Every call now carries a small live snapshot of the business, read under the
# caller's own tenant fence, and the model is told to answer only from it.
FACTS_RULE = ("\n\nBUSINESS FACTS below are live data from this business's own "
              "records. Answer questions ONLY from them. If the answer is not in "
              "them, say you do not have that yet -- never invent a number, "
              "price, name or date. If the owner is only ASKING something, kind is "
              "\"question\" and nothing is changed. Use business, dashboard, teach or post "
              "only when the owner clearly tells you to change, add, remove, teach or post.")

_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _facts(tid, role):
    f = {}
    tz = "Asia/Karachi"
    try:
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT to_char(now() AT TIME ZONE %s, 'Dy DD Mon YYYY HH24:MI') AS now", (tz,))
            f["now_pakistan_time"] = cur.fetchone()["now"]
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
            if role in ("owner", "techdeva_admin"):
                cur.execute("""
                    SELECT coalesce(sum(amount), 0)::float AS collected_this_month
                    FROM payments
                    WHERE date_trunc('month', paid_at AT TIME ZONE %(tz)s)
                          = date_trunc('month', now() AT TIME ZONE %(tz)s)""", {"tz": tz})
                f["money_pkr"] = dict(cur.fetchone())
    except Exception as e:  # a facts failure must never block the console
        f["facts_error"] = type(e).__name__
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


def _with_facts(tid, role, message):
    return ("BUSINESS FACTS:\n" + json.dumps(_facts(tid, role), default=str)[:6000] +
            "\n\nOWNER SAYS:\n" + message)


# ------------------------------------------------------------------ voice
# Browsers have no Urdu voice worth hearing, so Jarvis speaks through Gemini's
# speech models. Tried in order; a busy model falls through to the next.
_TTS_MODELS = ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts",
               "gemini-2.5-flash-preview-tts")


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
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Voice is not set up on this server.")
    style = ("Read this aloud in natural, warm Pakistani Urdu, like a friendly assistant: "
             if urdu else "Read this aloud in a warm, friendly, clear voice: ")
    last = "no model tried"
    for m in _TTS_MODELS:
        try:
            r = requests.post(
                f'{base_url("gemini")}/v1beta/models/{m}:generateContent',
                params={"key": key},
                json={"contents": [{"parts": [{"text": style + text}]}],
                      "generationConfig": {
                          "responseModalities": ["AUDIO"],
                          "speechConfig": {"voiceConfig": {
                              "prebuiltVoiceConfig": {"voiceName": "Kore"}}}}},
                timeout=25)
            if r.status_code >= 400:
                last = "%s_%s" % (m, r.status_code)
                continue
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            data = base64.b64decode(part["data"])
            mime = part.get("mimeType", "")
            if "wav" in mime:
                return data
            mm = re.search(r"rate=(\d+)", mime)
            return _pcm_to_wav(data, int(mm.group(1)) if mm else 24000)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            last = "%s: %s" % (m, type(e).__name__)
    raise HTTPException(503, "Voice is busy right now (%s)." % last)


def make_console_router():
    router = APIRouter(prefix="/console", tags=["console"])

    @router.post("")
    def ask(body: dict = Body(...),
            x_tenant_id: str | None = Header(None),
            x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session,
                                   target=body.get("tenant_id"))
        message = (body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "Type what you would like changed.")
        if len(message) > 2000:
            raise HTTPException(400, "That is too long. Say it in a sentence or two.")

        try:
            # No retry here on purpose. A person is sitting in front of this
            # box: a second full pass through the model chain turns a slow
            # answer into a minute of silence, and silence reads as broken.
            out = generate_json(CLASSIFY + LANG_RULE + FACTS_RULE, _with_facts(tid, role, message),
                                required_keys=("kind",), _retry=False)
        except AIUnavailable as e:
            # Better to say the console is deaf than to guess at an instruction
            # that might change what the business says to its customers.
            raise HTTPException(503, "The console could not reach the AI right now, "
                                     "so nothing was changed. (%s)" % str(e)[:120])

        kind = (out.get("kind") or "question").strip().lower()
        reply = (out.get("reply") or "").strip() or "Done."

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
            from .social import create_draft
            with tenant_session(tid, role) as cur:
                draft = create_draft(cur, tid, uid, out.get("caption") or message,
                                     out.get("platforms"), out.get("image_url") or None)
            return {"kind": "post", "post": draft,
                    "reply": "I drafted this post. Check it, then press Approve & post."}
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
        return Response(content=_tts(text, urdu), media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})

    return router
