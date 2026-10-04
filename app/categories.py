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
import json
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from .whatsapp import _identity, _rows, _pairctl_json

PROTECTED = re.compile(r"irfan|clinic|salon|workspace")
KINDS = ("faq", "policy", "not_offered", "info")
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
    cur.execute("""SELECT id::text AS id, kind, title, body FROM business_knowledge
                    WHERE tenant_id = %s AND active ORDER BY kind, created_at LIMIT 300""", (tid,))
    knowledge = [dict(r) for r in cur.fetchall()]
    return {"business": t, "category": cat, "services": services, "hours": hours, "knowledge": knowledge}


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
    kind = (cat or {}).get("name") or "service business"
    if t.get("business_type") == "other" and t.get("business_type_other"):
        kind = t["business_type_other"]
    out = ["## About this business",
           "%s is a %s." % (t["name"], kind.lower() if kind else "business"),
           "Anything earlier in this file about consultations, treatments or a clinic does not apply "
           "unless it is listed below. This section is the truth about this business."]
    out += ["", "## %s (the ONLY ones you may offer)" % services_word]
    if f["services"]:
        for s in f["services"]:
            line = "- %s: %s" % (s["name"], _price(s))
            if s.get("duration_minutes"):
                line += " (%s min)" % s["duration_minutes"]
            if s.get("description"):
                line += ". " + str(s["description"]).strip()[:200]
            out.append(line)
    else:
        out.append("- None listed yet. Do not name or price any service: take the customer's question, "
                   "their name and number, and say the team will reply shortly.")
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
    out += ["", "## Hard rule",
            "Offer, describe and price ONLY the %s listed above, at exactly the prices listed. "
            "If someone asks for anything that is not listed, say plainly that %s does not offer it, "
            "and suggest what it does offer. Never invent a service, price, time, discount or offer. "
            "If you are not sure, say the team will confirm." % (services_word.lower(), t["name"])]
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
        t = f["business"]
        return {"business_type": t["business_type"], "business_type_other": t["business_type_other"],
                "needs_type": not t["business_type"], "category": f["category"],
                "services_count": len(f["services"]), "hours_set": bool(f["hours"]),
                "knowledge": f["knowledge"], "open_flags": flags,
                "sync": dict(s) if s else {"status": "never", "synced_at": None},
                "agent_text": knowledge_block(f) if role in ("owner", "techdeva_admin") else None}

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
        try:
            text = photo_to_text(data, mime)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
        with tenant_session(tid, role) as cur:
            slugs = _slugs(cur)
        return {"draft": compare(tid, read_text(text, slugs)), "source": text}

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
            cur.execute("SELECT lower(btrim(name)) AS n FROM services WHERE tenant_id = %s", (tid,))   # incl. removed: name is unique
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
                        cur.execute("""UPDATE services SET price = %s, updated_at = now()
                                        WHERE tenant_id = %s AND lower(name) = %s AND active""", (price, tid, name.lower()))
                        done["prices_changed"] += cur.rowcount
                    else:
                        done["skipped"] += 1
                    continue
                dur = s.get("duration_minutes") if isinstance(s.get("duration_minutes"), int) and 0 < s["duration_minutes"] < 1440 else None
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
