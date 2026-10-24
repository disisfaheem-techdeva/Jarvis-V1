"""Drip campaigns: create one from gallery media, change its posts, approve it,
pause, resume and cancel it, and apply what the content agent wrote.

Human authority, enforced here and in the database:
  * only approve() makes a post 'approved', and it records who and when (the
    table refuses an approved post without approved_by / approved_at);
  * editing or moving an approved post sends it back to 'draft', so what is
    published is always exactly what a person approved, at the time they approved;
  * nothing the AI returns can change a post's status (apply_agent_output only
    ever writes drafts).
"""
from __future__ import annotations

import datetime as dt
import json
import os

from fastapi import HTTPException

from . import guardrails, settings
from .drip import DripError, build_schedule, parse_rule, zone

PLATFORMS = ("facebook", "instagram", "gbp")
LIVE_STATES = ("draft", "approved", "publishing")
NAME_MAX = 120


def gbp_enabled() -> bool:
    return os.environ.get("GBP_PUBLISH_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _platforms(raw) -> list[str]:
    out = []
    for p in raw or []:
        p = str(p).strip().lower()
        if p not in PLATFORMS:
            raise HTTPException(400, "Platforms are facebook and instagram.")
        if p == "gbp" and not gbp_enabled():
            raise HTTPException(400, "Google Business Profile posting is coming soon.")
        if p not in out:
            out.append(p)
    if not out:
        raise HTTPException(400, "Choose at least one platform.")
    return out


def clean_rule(rule_config: dict) -> dict:
    try:
        r = parse_rule(rule_config)
    except DripError as e:
        raise HTTPException(400, str(e))
    out = {"days": r["days"], "times": r["times"], "max_per_day": r["max_per_day"],
           "platforms": _platforms((rule_config or {}).get("platforms"))}
    if r["start_date"]:
        out["start_date"] = r["start_date"].isoformat()
    return out


def taken_slots(cur, platforms, exclude_campaign=None) -> list[dt.datetime]:
    """Minutes already used on these platforms: other gallery campaigns' posts and the
    Posts page's own scheduled posts (social_posts), so two systems never collide."""
    cur.execute("""SELECT sp.scheduled_at FROM scheduled_posts sp JOIN marketing_campaigns c ON c.id = sp.campaign_id
                    WHERE sp.platform = ANY(%s) AND sp.status = ANY(%s)
                      AND c.status NOT IN ('cancelled', 'completed') AND c.id IS DISTINCT FROM %s""",
                (list(platforms), list(LIVE_STATES), exclude_campaign))
    out = [r["scheduled_at"] for r in cur.fetchall()]
    cur.execute("SELECT scheduled_at FROM social_posts WHERE status = 'scheduled' AND scheduled_at IS NOT NULL "
                "AND platforms && %s::text[]", (list(p for p in platforms if p != "gbp"),))
    out += [r["scheduled_at"] for r in cur.fetchall()]
    return out


def create(cur, uid, name, asset_ids, rule_config, page_id=None, now=None) -> str:
    """A campaign in 'draft' with one draft post per item per platform, each at its drip time."""
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise HTTPException(400, "Give the campaign a name.")
    ids = []
    for a in asset_ids or []:
        if str(a) not in ids:
            ids.append(str(a))
    if not ids:
        raise HTTPException(400, "Choose at least one photo or video.")
    rule = clean_rule(rule_config)
    cur.execute("SELECT id::text, kind, warnings, service_tag FROM media_assets WHERE id::text = ANY(%s)", (ids,))
    assets = {r["id"]: r for r in cur.fetchall()}
    missing = [a for a in ids if a not in assets]
    if missing:
        raise HTTPException(404, "%d of the chosen files are not in your gallery." % len(missing))
    cfg = settings.load(cur)
    tz = settings.timezone(cfg)
    try:
        zone(tz)
        slots = build_schedule(len(ids), rule_config, tz, now or dt.datetime.now(dt.timezone.utc),
                               taken_slots(cur, rule["platforms"]))
    except DripError as e:
        raise HTTPException(400, str(e))
    cur.execute("""INSERT INTO marketing_campaigns (tenant_id, name, status, rule_config, timezone, page_id, created_by)
                   VALUES (current_tenant(), %s, 'draft', %s::jsonb, %s, %s, %s) RETURNING id::text""",
                (name, json.dumps(rule), tz, (str(page_id) if page_id else None), uid))
    cid = cur.fetchone()["id"]
    for i, aid in enumerate(ids):
        a = assets[aid]
        for p in rule["platforms"]:
            flags = ["media_" + w for w in (a["warnings"] or [])] if p == "instagram" else []
            cur.execute("""INSERT INTO scheduled_posts (tenant_id, campaign_id, media_asset_id, platform, scheduled_at,
                                                        service_tag, flags)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s, %s::jsonb)""",
                        (cid, aid, p, slots[i], a["service_tag"], json.dumps(flags)))
    return cid


# ------------------------------------------------------------------ views
def _iso(t):
    return t.isoformat() if t else None


def post_view(r) -> dict:
    return {"id": str(r["id"]), "campaign_id": str(r["campaign_id"]), "media_asset_id": str(r["media_asset_id"]),
            "platform": r["platform"], "caption": r["caption"], "hashtags": r["hashtags"] or [],
            "caption_source": r["caption_source"], "caption_version": r["caption_version"],
            "flags": [{"code": f, "text": guardrails.flag_text(f)} for f in (r["flags"] or [])],
            "flags_overridden": r["flags_overridden"], "service_tag": r["service_tag"],
            "scheduled_at": _iso(r["scheduled_at"]), "status": r["status"], "attempts": r["attempts"],
            "last_error": r["last_error"], "external_post_id": r["external_post_id"],
            "published_at": _iso(r["published_at"]), "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approved_at": _iso(r["approved_at"]),
            "media": {"url": r.get("public_url"), "kind": r.get("kind"), "name": r.get("original_name")},
            "test_mode": bool(r["external_post_id"] and str(r["external_post_id"]).startswith("stub:"))}


def campaign_view(r, counts=None) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "status": r["status"], "rule_config": r["rule_config"],
            "timezone": r["timezone"], "paused_reason": r["paused_reason"], "captioning": r["captioning"],
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None, "approved_at": _iso(r["approved_at"]),
            "created_at": _iso(r["created_at"]), "counts": counts or {}}


def get(cur, cid) -> dict:
    cur.execute("SELECT * FROM marketing_campaigns WHERE id::text = %s", (str(cid),))
    c = cur.fetchone()
    if not c:
        raise HTTPException(404, "That campaign is not in your business.")
    cur.execute("""SELECT sp.*, ma.public_url, ma.kind, ma.original_name FROM scheduled_posts sp
                     JOIN media_assets ma ON ma.id = sp.media_asset_id
                    WHERE sp.campaign_id = %s ORDER BY sp.scheduled_at, sp.platform""", (c["id"],))
    posts = [post_view(r) for r in cur.fetchall()]
    counts = {}
    for p in posts:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    out = campaign_view(c, counts)
    out["posts"] = posts
    out["blockers"] = approval_blockers(posts)
    return out


def listing(cur) -> list[dict]:
    cur.execute("""SELECT c.*, (SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) AS n FROM scheduled_posts
                                 WHERE campaign_id = c.id GROUP BY status) s) AS counts,
                          (SELECT min(scheduled_at) FROM scheduled_posts WHERE campaign_id = c.id) AS first_at,
                          (SELECT max(scheduled_at) FROM scheduled_posts WHERE campaign_id = c.id) AS last_at
                     FROM marketing_campaigns c ORDER BY c.created_at DESC LIMIT 100""")
    out = []
    for r in cur.fetchall():
        v = campaign_view(r, r["counts"] or {})
        v["first_at"], v["last_at"] = _iso(r["first_at"]), _iso(r["last_at"])
        out.append(v)
    return out


# ------------------------------------------------------------------ approval
def approval_blockers(posts, overrides=()) -> list[dict]:
    """What stops approval: a draft with no caption, or with flags nobody overrode."""
    out = []
    for p in posts:
        if p["status"] != "draft":
            continue
        if not (p["caption"] or "").strip():
            out.append({"post_id": p["id"], "reason": "empty", "text": "No caption yet."})
        elif p["flags"] and not p["flags_overridden"] and p["id"] not in overrides:
            out.append({"post_id": p["id"], "reason": "flagged",
                        "text": "; ".join(f["text"] for f in p["flags"])})
    return out


def approve(cur, uid, cid, overrides=(), now=None) -> dict:
    c = get(cur, cid)
    if c["status"] in ("cancelled", "completed"):
        raise HTTPException(409, "This campaign is %s." % c["status"])
    if c["captioning"] in ("pending", "running") or any("recaptioning" in [f["code"] for f in p["flags"]] for p in c["posts"]):
        raise HTTPException(409, "Jarvis is still writing captions. Approve once they are ready.")
    overrides = {str(o) for o in overrides or ()}
    blockers = approval_blockers(c["posts"], overrides)
    if blockers:
        raise HTTPException(409, {"message": "%d post%s need%s attention before approval." % (
            len(blockers), "" if len(blockers) == 1 else "s", "s" if len(blockers) == 1 else ""), "blockers": blockers})
    now = now or dt.datetime.now(dt.timezone.utc)
    late = [p for p in c["posts"] if p["status"] == "draft" and dt.datetime.fromisoformat(p["scheduled_at"]) <= now]
    if late:
        raise HTTPException(409, {"message": "%d post%s past %s time. Move %s to a new time first." % (
            len(late), " is" if len(late) == 1 else "s are", "its" if len(late) == 1 else "their",
            "it" if len(late) == 1 else "them"), "blockers": [{"post_id": p["id"], "reason": "late"} for p in late]})
    drafts = [p["id"] for p in c["posts"] if p["status"] == "draft"]
    if not drafts:
        raise HTTPException(409, "There is nothing waiting for approval.")
    cur.execute("""UPDATE scheduled_posts SET status = 'approved', approved_by = %s, approved_at = now(),
                          flags_overridden = flags_overridden OR id::text = ANY(%s), updated_at = now()
                    WHERE campaign_id = %s AND status = 'draft' AND id::text = ANY(%s)""",
                (uid, list(overrides), c["id"], drafts))
    n = cur.rowcount
    cur.execute("""UPDATE marketing_campaigns SET status = CASE WHEN status = 'paused' THEN 'paused' ELSE 'active' END,
                          approved_by = %s, approved_at = now(), updated_at = now() WHERE id = %s""", (uid, c["id"]))
    return {"approved": n, "overridden": sorted(overrides & set(drafts))}


# ------------------------------------------------------------------ editing one post
def _post(cur, pid, lock=True):
    cur.execute("SELECT * FROM scheduled_posts WHERE id::text = %s" + (" FOR UPDATE" if lock else ""), (str(pid),))
    p = cur.fetchone()
    if not p:
        raise HTTPException(404, "That post is not in your business.")
    return p


def edit_post(cur, uid, pid, body: dict, now=None) -> dict:
    p = _post(cur, pid)
    if p["status"] in ("publishing", "published", "cancelled"):
        raise HTTPException(409, "A %s post cannot be changed." % p["status"])
    sets, vals, back_to_draft = [], [], False
    if body.get("cancel"):
        cur.execute("UPDATE scheduled_posts SET status = 'cancelled', updated_at = now() WHERE id = %s", (p["id"],))
        _maybe_complete(cur, p["campaign_id"])
        return {"status": "cancelled"}
    if "caption" in body or "hashtags" in body:
        cfg = settings.load(cur)
        caption = str(body.get("caption", p["caption"]) or "").strip()
        tags = guardrails.clean_hashtags(body.get("hashtags", p["hashtags"]) or [], p["platform"])
        if not caption:
            raise HTTPException(400, "Write a caption, or regenerate one.")
        err = guardrails.platform_limit_error(caption, tags, p["platform"])
        if err:
            raise HTTPException(400, err)
        # a person wrote it: the AI's flags go, but the same checks still say what they see
        flags = guardrails.human_flags(caption, cfg)
        sets += ["caption = %s", "hashtags = %s::jsonb", "caption_source = 'human_edited'",
                 "caption_version = caption_version + 1", "flags = %s::jsonb", "flags_overridden = false"]
        vals += [caption, json.dumps(tags), json.dumps(flags)]
        back_to_draft = True
    if body.get("scheduled_at"):
        try:
            t = dt.datetime.fromisoformat(str(body["scheduled_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "The new time looks like 2026-11-07T12:00:00+05:00.")
        if t.tzinfo is None:
            raise HTTPException(400, "Send the new time with its time zone.")
        t = t.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
        if t <= (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(minutes=1):
            raise HTTPException(400, "Choose a time in the future.")
        if t in set(x.replace(second=0, microsecond=0) for x in taken_slots(cur, [p["platform"]])
                    if x != p["scheduled_at"]):
            raise HTTPException(409, "Another post already goes out on %s at that minute." % p["platform"])
        sets.append("scheduled_at = %s")
        vals.append(t)
        back_to_draft = True
    if not sets:
        raise HTTPException(400, "Nothing to change.")
    if back_to_draft:
        sets += ["status = 'draft'", "approved_by = NULL", "approved_at = NULL", "retry_after = NULL", "last_error = NULL"]
    cur.execute("UPDATE scheduled_posts SET %s, updated_at = now() WHERE id = %%s RETURNING *" % ", ".join(sets),
                vals + [p["id"]])
    row = cur.fetchone()
    return {"post_id": str(row["id"]), "status": row["status"], "needs_approval": row["status"] == "draft"}


def mark_recaptioning(cur, pid) -> dict:
    p = _post(cur, pid)
    if p["status"] in ("publishing", "published", "cancelled"):
        raise HTTPException(409, "A %s post cannot be rewritten." % p["status"])
    flags = [f for f in (p["flags"] or []) if f != "recaptioning"] + ["recaptioning"]
    cur.execute("""UPDATE scheduled_posts SET flags = %s::jsonb, status = 'draft', approved_by = NULL, approved_at = NULL,
                          updated_at = now() WHERE id = %s""", (json.dumps(flags), p["id"]))
    return {"post_id": str(p["id"]), "campaign_id": str(p["campaign_id"]), "media_asset_id": str(p["media_asset_id"])}


# ------------------------------------------------------------------ pause / resume / cancel
def set_state(cur, uid, cid, action, token_ok=None) -> dict:
    c = get(cur, cid)
    if action == "pause":
        if c["status"] not in ("active", "awaiting_approval"):
            raise HTTPException(409, "Only a running campaign can be paused.")
        cur.execute("UPDATE marketing_campaigns SET status = 'paused', paused_reason = 'owner', updated_at = now() "
                    "WHERE id::text = %s", (c["id"],))
    elif action == "resume":
        if c["status"] != "paused":
            raise HTTPException(409, "This campaign is not paused.")
        if token_ok is not None and not token_ok["ok"]:
            raise HTTPException(409, {"message": token_ok["message"], "reconnect": True})
        state = "active" if c["approved_at"] else "awaiting_approval"
        cur.execute("UPDATE marketing_campaigns SET status = %s, paused_reason = NULL, updated_at = now() "
                    "WHERE id::text = %s", (state, c["id"]))
    elif action == "cancel":
        if c["status"] in ("cancelled", "completed"):
            raise HTTPException(409, "This campaign is already %s." % c["status"])
        cur.execute("UPDATE scheduled_posts SET status = 'cancelled', updated_at = now() "
                    "WHERE campaign_id::text = %s AND status IN ('draft', 'approved', 'failed')", (c["id"],))
        cur.execute("UPDATE marketing_campaigns SET status = 'cancelled', updated_at = now() WHERE id::text = %s", (c["id"],))
    else:
        raise HTTPException(400, "Unknown action.")
    cur.execute("SELECT status FROM marketing_campaigns WHERE id::text = %s", (c["id"],))
    return {"status": cur.fetchone()["status"]}


def _maybe_complete(cur, cid):
    """Every post is published, failed or cancelled -> the campaign is completed."""
    cur.execute("""UPDATE marketing_campaigns c SET status = 'completed', updated_at = now()
                    WHERE c.id = %s AND c.status = 'active'
                      AND NOT EXISTS (SELECT 1 FROM scheduled_posts WHERE campaign_id = c.id
                                       AND status IN ('draft', 'approved', 'publishing'))""", (cid,))


# ------------------------------------------------------------------ what the content agent wrote
def apply_agent_output(cur, kind, payload) -> None:
    """Writes the content agent's work as DRAFTS. Never touches a post's approval."""
    cid = payload.get("campaign_id")
    if kind == "analysis":
        for a in payload.get("assets") or []:
            cur.execute("UPDATE media_assets SET vision_summary = %s::jsonb, service_tag = %s WHERE id::text = %s",
                        (json.dumps(a.get("vision_summary")), a.get("service_tag"), str(a.get("id"))))
            cur.execute("""UPDATE scheduled_posts SET service_tag = %s, updated_at = now()
                            WHERE media_asset_id::text = %s AND campaign_id::text = %s AND status = 'draft'""",
                        (a.get("service_tag"), str(a.get("id")), str(cid)))
            if a.get("vision_flag"):
                cur.execute("""UPDATE scheduled_posts SET flags = flags || %s::jsonb
                                WHERE media_asset_id::text = %s AND campaign_id::text = %s AND status = 'draft'
                                  AND NOT flags ? %s""",
                            (json.dumps([a["vision_flag"]]), str(a.get("id")), str(cid), a["vision_flag"]))
        cur.execute("UPDATE marketing_campaigns SET captioning = 'running', updated_at = now() "
                    "WHERE id::text = %s AND captioning = 'pending'", (str(cid),))
        return
    if kind in ("captions", "recaption"):
        for c in payload.get("captions") or []:
            where, args = ("id::text = %s", [str(c["post_id"])]) if c.get("post_id") else \
                ("media_asset_id::text = %s AND campaign_id::text = %s", [str(c["asset_id"]), str(cid)])
            for platform, text in (c.get("per_platform") or {}).items():
                cur.execute("""UPDATE scheduled_posts SET caption = %s, hashtags = %s::jsonb, caption_source = 'ai',
                                      caption_version = caption_version + 1,
                                      flags = (SELECT COALESCE(jsonb_agg(f), '[]'::jsonb) FROM jsonb_array_elements_text(flags) f
                                                WHERE f LIKE 'media_%%' OR f = 'vision_unavailable') || %s::jsonb,
                                      flags_overridden = false, status = 'draft', approved_by = NULL, approved_at = NULL,
                                      updated_at = now()
                                WHERE """ + where + """ AND platform = %s AND status IN ('draft', 'approved', 'failed')
                                  AND (caption_source = 'ai' OR %s)""",
                            [text["caption"], json.dumps(text["hashtags"]), json.dumps(text["flags"])] + args
                            + [platform, kind == "recaption"])
        if kind == "captions":
            cur.execute("""UPDATE marketing_campaigns SET captioning = %s,
                                  status = CASE WHEN status = 'draft' THEN 'awaiting_approval' ELSE status END,
                                  updated_at = now() WHERE id::text = %s""",
                        ("failed" if payload.get("failed") else "done", str(cid)))
        return
    raise ValueError("unknown output kind %r" % kind)
