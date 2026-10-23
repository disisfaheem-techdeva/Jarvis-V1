"""Content Autopilot: a month of Facebook/Instagram posts, approved a week at a time.

    library (photos + videos, named and tagged)
        -> POST /autopilot/plan        the AI writes a month of DRAFTS from the
                                       business's own facts, goal and offers,
                                       each with a proposed time (planned_at)
        -> POST /autopilot/approve-week the owner approves one week: its drafts
                                       become ordinary scheduled posts
        -> social.publish_due          the scheduler posts them on time
        -> run_due (scheduler)         reads reach, likes, comments, shares,
                                       saves and followers back from Meta
        -> GET /autopilot/results      what worked; the next plan is told too

Rules kept from social.py: nothing goes public without the client's own
approval (the founder can plan and look, never approve); a draft is only ever
a draft until then. The AI may only use prices and offers the business wrote
down; it never invents reviews, prices or claims.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

import requests
from fastapi import APIRouter, BackgroundTasks, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

TZ = dt.timezone(dt.timedelta(hours=5))   # Asia/Karachi, no daylight saving (as social.LOCAL_OFFSET)
GRAPH = "https://graph.facebook.com/v21.0"
MAX_DAYS = 31
CHUNK_DAYS = 7                 # one AI call per week of the plan: short, reliable replies
LIBRARY_MAX = 120              # library items shown to the planner
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
LANG = {
    "english": "Write in clear, warm English.",
    "mix": "Write the way Pakistanis post: mostly English with a few natural Roman Urdu words "
           "(e.g. 'yaar', 'mazay', 'dawat', 'khana'). Never Urdu script.",
    "urdu": "Write in Roman Urdu (Urdu in English letters), with English for prices and brand names.",
}
CAPTION_TONES = {
    "friendly": "Warm and friendly, like the owner talking to a regular customer.",
    "premium": "Calm, premium and confident. No slang, few emojis.",
    "fun": "Playful and energetic, with a little humour.",
    "urgent": "Short and urgent: a clear reason to act today (only real offers or real limits).",
}
CAPTIONS_PER_HOUR = 30         # per business: the caption writer calls the AI each time
_CAPTION_CALLS: dict = {}
INSIGHTS_EVERY = 6 * 3600      # how often results are read back from Meta
INSIGHTS_WINDOW_DAYS = 30      # posts younger than this get their numbers refreshed
_LAST_INSIGHTS = [0.0]
_LOCK = threading.Lock()


# --------------------------------------------------------------------- helpers
def _who(x_tenant_id, x_role, authorization, session, owner=True):
    from .social import _ident, _owner_only
    tid, role, uid = _ident(x_tenant_id, x_role, authorization, session)
    if owner:
        _owner_only(role)
    return tid, role, uid


def _today():
    return dt.datetime.now(TZ).date()


def _parse_day(s, default=None):
    if not s:
        return default
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise HTTPException(400, "Dates look like 2026-11-01.")


def _local(day: dt.date, hhmm: str) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def library(cur):
    """The business's photos and videos kept for planning, most recent first."""
    from .social import media_url
    from .videos import video_url
    cur.execute("""SELECT id::text AS id, token, label, tags, created_at FROM social_media
                    WHERE in_library ORDER BY created_at DESC LIMIT %s""", (LIBRARY_MAX,))
    photos = [{"kind": "photo", "id": r["id"], "url": media_url(r["token"]), "label": r["label"],
               "tags": list(r["tags"] or []), "created_at": r["created_at"]} for r in cur.fetchall()]
    cur.execute("""SELECT id::text AS id, token, label, tags, duration, created_at FROM social_videos
                    WHERE in_library ORDER BY created_at DESC LIMIT %s""", (LIBRARY_MAX,))
    videos = [{"kind": "video", "id": r["id"], "token": r["token"], "url": video_url(r["token"]),
               "label": r["label"], "tags": list(r["tags"] or []), "duration": float(r["duration"]),
               "created_at": r["created_at"]} for r in cur.fetchall()]
    items = photos + videos
    # how often each one is already used in a post that is still coming or went out
    cur.execute("""SELECT image_urls, video_token FROM social_posts
                    WHERE status IN ('draft','scheduled','publishing','published','partial')""")
    used: dict[str, int] = {}
    for p in cur.fetchall():
        for u in p["image_urls"] or []:
            used[u] = used.get(u, 0) + 1
        if p["video_token"]:
            used[p["video_token"]] = used.get(p["video_token"], 0) + 1
    for it in items:
        it["used"] = used.get(it.get("token") or it["url"], 0)
    return items


def _clean_tags(raw):
    if isinstance(raw, str):
        raw = raw.split(",")
    out = []
    for t in raw or []:
        t = re.sub(r"[^\w\s-]", "", str(t)).strip().lower()[:30]
        if t and t not in out:
            out.append(t)
    return out[:12]


def _grounding(tid):
    """What the planner may treat as true, built the same way the Content agent
    (agents/creative.py) builds it: the tenant's config, the latest analysis
    report and competitor intel; plus the business's own records (services,
    products, prices, knowledge) as its WhatsApp agent sees them.
    Returns (brief text for the prompt, config dict for guards.unbacked_claims)."""
    from types import SimpleNamespace
    from .agents.creative import _facts
    from .persistence import Persistence
    P = Persistence()
    cfg = P.load_config(tid) or {}
    store = P.load_context(tid) or {}
    agent_facts = _facts(SimpleNamespace(config=cfg, store=store))
    with tenant_session(tid, "owner") as cur:
        try:
            from .categories import facts, knowledge_block
            f = facts(cur, tid)
            records = knowledge_block(f)
        except Exception:  # noqa: BLE001 -- a business with no setup yet still gets a plan
            f, records = {}, ""
    brief = (records[:6000] + "\n\nVERIFIED CONFIG AND ANALYSIS (JSON):\n" +
             json.dumps(agent_facts, default=str)[:5000])
    allowed = {"config": cfg, "records": f}
    return brief, allowed


def check_caption(caption, allowed, offers=""):
    """The platform's "never invent facts" check (agents/guards.py) on one caption.
    Offers the owner typed into the plan count as theirs."""
    from .agents.guards import unbacked_claims
    return unbacked_claims(caption, dict(allowed or {}, owner_offers=offers or ""))


def _what_worked(cur, limit=5) -> str:
    """The best posts of the last 60 days, so the next plan does more of that."""
    cur.execute("""SELECT p.caption, p.theme, sum(coalesce(i.reach, 0)) AS reach,
                          sum(i.likes + i.comments * 3 + i.shares * 4 + i.saves * 4) AS score
                     FROM post_insights i JOIN social_posts p ON p.id = i.post_id
                    WHERE p.published_at > now() - interval '60 days'
                    GROUP BY p.id ORDER BY score DESC, reach DESC LIMIT %s""", (limit,))
    rows = cur.fetchall()
    if not rows:
        return "No results yet (first plan)."
    return "\n".join("- [%s] reach %s, engagement score %s: %s" % (
        r["theme"] or "post", int(r["reach"] or 0), int(r["score"] or 0),
        re.sub(r"\s+", " ", r["caption"])[:160]) for r in rows)


SYSTEM = """You are the social media manager for one small business in Pakistan. You plan its
Facebook and Instagram posts. You write captions that people stop for: a strong first line
(the hook, under 90 characters), short lines, one clear call to action (order on WhatsApp,
DM, visit the website or call), and 5 to 10 relevant hashtags (local ones like #Lahore
when it fits). Use emojis sparingly (0 to 3).

Hard rules:
- Use ONLY prices, products, offers, places and facts given to you. Never invent a price,
  a discount, a review, a customer quote, an award or a number.
- If no offer is given for a post, do not mention a discount.
- Vary the posts: product close-ups, how it works, behind the scenes, questions that invite
  comments, occasions (weekends, family dawat, cricket nights, weather), offers (at most one
  in three posts), and reminders of how to order.
- Pick a library item for every post when one fits; prefer items used least, and never use
  the same item twice in one week. A video becomes a Reel.
- Times are in Pakistan time. Lunch (12:30-14:30) and evening (19:30-23:00) do best; spread
  posts, at most two on one day.
Reply with JSON only."""


def _prompt(brief, goal, offers, language, items, worked, days, count, earlier):
    lib = "\n".join("%d: %s %s%s%s" % (
        i, it["kind"], it["label"] or "(no name)", (" tags: " + ", ".join(it["tags"])) if it["tags"] else "",
        " (used %d)" % it["used"] if it["used"] else "") for i, it in enumerate(items)) or "(library is empty: no media)"
    return """BUSINESS
{brief}

GOAL FOR THIS MONTH: {goal}
OFFERS YOU MAY MENTION: {offers}
LANGUAGE: {lang}

LIBRARY (number: kind name tags)
{lib}

WHAT WORKED BEFORE
{worked}

ALREADY PLANNED THIS MONTH (do not repeat these ideas or hooks)
{earlier}

Plan exactly {count} posts across these dates: {days}.
Return {{"posts": [{{"date": "YYYY-MM-DD", "time": "HH:MM", "theme": "one or two words",
"caption": "the full caption without hashtags", "hashtags": ["#tag", ...],
"media": [library numbers, empty if none; several photos make a carousel, a video stands alone],
"platforms": ["facebook", "instagram"]}}]}}""".format(
        brief=brief, goal=goal or "More orders and more followers.",
        offers=offers or "None given: do not mention discounts.", lang=LANG.get(language, LANG["english"]),
        lib=lib, worked=worked, earlier=earlier or "(nothing yet)", count=count,
        days=", ".join(d.isoformat() for d in days))


def _make_drafts(cur, tid, uid, plan_id, posts, items, days, used_this_week):
    """Validate the AI's posts and store them as drafts. Returns how many."""
    from .social import create_draft
    allowed = {d.isoformat() for d in days}
    made = 0
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        day, hhmm = str(p.get("date") or ""), str(p.get("time") or "")
        if day not in allowed or not TIME_RE.match(hhmm):
            continue
        h = int(hhmm[:2])
        if h < 8:                                   # nobody posts at 3 am on purpose
            hhmm = "13:00"
        caption = re.sub(r"\s+\n", "\n", str(p.get("caption") or "")).strip()
        tags = [t if t.startswith("#") else "#" + t for t in
                (re.sub(r"[^\w#]", "", str(x)) for x in (p.get("hashtags") or [])[:12]) if len(t) > 1]
        if tags:
            caption = (caption + "\n\n" + " ".join(tags)).strip()
        if len(caption) < 10:
            continue
        picks = []
        for n in p.get("media") or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 0 <= n < len(items) and n not in picks:
                picks.append(n)
        videos = [n for n in picks if items[n]["kind"] == "video"]
        photos = [n for n in picks if items[n]["kind"] == "photo"][:10]
        video_token, image_urls = None, []
        if videos:
            video_token = items[videos[0]]["token"]
        elif photos:
            image_urls = [items[n]["url"] for n in photos]
        else:
            # no picture chosen: the least used photo not already used this week
            spare = sorted((i for i, it in enumerate(items) if it["kind"] == "photo" and i not in used_this_week),
                           key=lambda i: items[i]["used"])
            if spare:
                image_urls = [items[spare[0]]["url"]]
                photos = [spare[0]]
        for n in (videos[:1] or photos):
            items[n]["used"] += 1
            used_this_week.add(n)
        plats = [x for x in (p.get("platforms") or ["facebook", "instagram"]) if x in ("facebook", "instagram")]
        if not image_urls and not video_token:
            plats = ["facebook"]                    # Instagram needs a picture or a video
        row = create_draft(cur, tid, uid, caption[:2200], plats or ["facebook", "instagram"],
                           source="autopilot", image_urls=image_urls, video_token=video_token)
        cur.execute("UPDATE social_posts SET plan_id = %s, planned_at = %s, theme = %s WHERE id = %s",
                    (plan_id, _local(dt.date.fromisoformat(day), hhmm),
                     str(p.get("theme") or "")[:40] or None, row["id"]))
        made += 1
    return made


def generate_plan(tid, uid, plan_id):
    """Charged to the business's AI credits (app/usage.py)."""
    from .usage import meter
    with meter(tid, "content", "plan"):
        return _generate_plan_unmetered(tid, uid, plan_id)


def _generate_plan_unmetered(tid, uid, plan_id):
    """Write the plan's drafts, a week at a time. Runs after the request returns."""
    from . import ai
    try:
        with tenant_session(tid, "owner") as cur:
            cur.execute("SELECT * FROM content_plans WHERE id = %s", (plan_id,))
            plan = dict(cur.fetchone())
            worked = _what_worked(cur)
            items = library(cur)
        brief, _allowed = _grounding(tid)
        start = plan["starts_on"]
        all_days = [start + dt.timedelta(days=i) for i in range(plan["days"])]
        total, earlier = 0, []
        for k in range(0, len(all_days), CHUNK_DAYS):
            days = all_days[k:k + CHUNK_DAYS]
            count = max(1, round(plan["posts_per_week"] * len(days) / 7))
            reply = ai.generate_json(SYSTEM, _prompt(brief, plan["goal"], plan["offers"], plan["language"],
                                                     items, worked, days, count, "\n".join(earlier[-30:])),
                                     required_keys=("posts",))
            with tenant_session(tid, "owner") as cur:
                made = _make_drafts(cur, tid, uid, plan_id, reply.get("posts"), items, days, set())
                cur.execute("UPDATE content_plans SET drafted = drafted + %s WHERE id = %s", (made, plan_id))
            total += made
            earlier += ["- " + re.sub(r"\s+", " ", str(p.get("caption") or ""))[:90]
                        for p in (reply.get("posts") or []) if isinstance(p, dict)]
        with tenant_session(tid, "owner") as cur:
            cur.execute("UPDATE content_plans SET status = %s, finished_at = now(), error = %s WHERE id = %s",
                        ("ready" if total else "failed", None if total else "The AI did not return usable posts. Try again.",
                         plan_id))
            record(cur, action="autopilot.plan", resource="content_plans", resource_id=str(plan_id),
                   actor=str(uid), actor_type="human", status="ok" if total else "error",
                   detail={"drafts": total, "days": plan["days"]}, error=None)
    except Exception as e:  # noqa: BLE001 -- whatever happened, the owner sees it on the plan
        msg = getattr(e, "detail", None) or str(e) or e.__class__.__name__
        if e.__class__.__name__ == "AIUnavailable":
            msg = "The AI is not available right now. Try again in a few minutes."
        with tenant_session(tid, "owner") as cur:
            cur.execute("UPDATE content_plans SET status = 'failed', finished_at = now(), error = %s "
                        "WHERE id = %s", (str(msg)[:300], plan_id))


def _post_view(r):
    out = {k: r[k] for k in ("caption", "status", "platforms", "theme", "results", "source")}
    out.update({"id": str(r["id"]), "image_urls": list(r["image_urls"] or []),
                "video_token": r["video_token"], "plan_id": str(r["plan_id"]) if r["plan_id"] else None})
    for k in ("planned_at", "scheduled_at", "published_at"):
        out[k] = r[k].astimezone(TZ).isoformat() if r[k] else None     # Pakistan time, as the owner reads it
    when = r["published_at"] or r["scheduled_at"] or r["planned_at"]
    out["at"] = when.astimezone(TZ).isoformat() if when else None
    return out


# ------------------------------------------------------------------- insights
def _graph_get(path, params):
    """GET from Meta. A refusal is logged (path and Meta's reason, never the token)
    so a missing permission or an expired login shows up in the API logs."""
    try:
        r = requests.get(GRAPH + path, params=params, timeout=20)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        print("[autopilot] meta read %s failed: %s" % (path, e.__class__.__name__), flush=True)
        return None
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        print("[autopilot] meta read %s refused (%s): %s" % (path, err.get("code") or r.status_code,
                                                            str(err.get("message") or "")[:160]), flush=True)
        return None
    return body


def _facebook_numbers(post_id, tok):
    body = _graph_get("/" + post_id, {"fields": "shares,comments.summary(true).limit(0),"
                                                "reactions.summary(true).limit(0)", "access_token": tok})
    if body is None:   # a photo or video id, not a feed post id
        body = _graph_get("/" + post_id, {"fields": "comments.summary(true).limit(0),"
                                                    "likes.summary(true).limit(0)", "access_token": tok})
    if body is None:
        return None
    out = {"likes": int(((body.get("reactions") or body.get("likes") or {}).get("summary") or {}).get("total_count") or 0),
           "comments": int(((body.get("comments") or {}).get("summary") or {}).get("total_count") or 0),
           "shares": int((body.get("shares") or {}).get("count") or 0), "saves": 0, "reach": None, "views": None}
    ins = _graph_get("/%s/insights" % post_id, {"metric": "post_impressions_unique", "access_token": tok})
    for m in (ins or {}).get("data") or []:
        if m.get("name") == "post_impressions_unique" and m.get("values"):
            out["reach"] = int(m["values"][0].get("value") or 0)
    return out


def _instagram_numbers(media_id, tok):
    base = _graph_get("/" + media_id, {"fields": "like_count,comments_count", "access_token": tok})
    if base is None:
        return None
    out = {"likes": int(base.get("like_count") or 0), "comments": int(base.get("comments_count") or 0),
           "shares": 0, "saves": 0, "reach": None, "views": None}
    for metrics in ("reach,shares,saved,views", "reach,shares,saved", "reach"):
        ins = _graph_get("/%s/insights" % media_id, {"metric": metrics, "access_token": tok})
        if ins is None:
            continue
        for m in ins.get("data") or []:
            v = (m.get("values") or [{}])[0].get("value")
            v = int(v or 0) if not isinstance(v, dict) else 0
            key = {"saved": "saves"}.get(m.get("name"), m.get("name"))
            if key in out:
                out[key] = v
        break
    return out


def refresh_insights(tid) -> int:
    """Read the numbers for one business's recent posts and its follower counts."""
    from .social import _meta_pages
    with tenant_session(tid, "owner") as cur:
        pages = {str(p["id"]): p for p in _meta_pages(cur, tid)}
        if not pages:
            return 0
        cur.execute("""SELECT id, page_id, results FROM social_posts
                        WHERE status IN ('published','partial')
                          AND published_at > now() - make_interval(days => %s)""", (INSIGHTS_WINDOW_DAYS,))
        posts = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT page_id FROM social_posts WHERE page_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1")
        last = cur.fetchone()
    done, rows = 0, []
    for p in posts:
        page = pages.get(str(p["page_id"] or "")) or (next(iter(pages.values())) if len(pages) == 1 else None)
        if not page:
            continue
        res = p["results"] if isinstance(p["results"], dict) else json.loads(p["results"] or "{}")
        for platform, fetch in (("facebook", _facebook_numbers), ("instagram", _instagram_numbers)):
            r = res.get(platform) or {}
            if not r.get("ok") or not r.get("id"):
                continue
            nums = fetch(str(r["id"]), page["access_token"])
            if nums:
                rows.append((p["id"], platform, str(r["id"]), nums))
    # follower counts for the Page in use (and its Instagram)
    page = pages.get(str(last["page_id"])) if last and last["page_id"] else None
    page = page or (next(iter(pages.values())) if len(pages) == 1 else None)
    followers = []
    if page:
        fb = _graph_get("/" + str(page["id"]), {"fields": "followers_count,fan_count", "access_token": page["access_token"]})
        if fb:
            followers.append(("facebook", int(fb.get("followers_count") or fb.get("fan_count") or 0)))
        if page.get("ig_id"):
            ig = _graph_get("/" + str(page["ig_id"]), {"fields": "followers_count", "access_token": page["access_token"]})
            if ig:
                followers.append(("instagram", int(ig.get("followers_count") or 0)))
    with tenant_session(tid, "owner") as cur:
        for pid, platform, ext, n in rows:
            cur.execute("""INSERT INTO post_insights (post_id, tenant_id, platform, external_id, reach, likes,
                                                      comments, shares, saves, views, fetched_at)
                           VALUES (%s, current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, now())
                           ON CONFLICT (post_id, platform) DO UPDATE SET reach = EXCLUDED.reach,
                             likes = EXCLUDED.likes, comments = EXCLUDED.comments, shares = EXCLUDED.shares,
                             saves = EXCLUDED.saves, views = EXCLUDED.views, fetched_at = now()""",
                        (pid, platform, ext, n["reach"], n["likes"], n["comments"], n["shares"], n["saves"], n["views"]))
            done += 1
        for platform, count in followers:
            cur.execute("""INSERT INTO account_insights (tenant_id, platform, day, followers)
                           VALUES (current_tenant(), %s, %s, %s)
                           ON CONFLICT (tenant_id, platform, day) DO UPDATE SET followers = EXCLUDED.followers""",
                        (platform, _today(), count))
    return done


def run_due(force=False) -> int:
    """From the scheduler: refresh results for every business with Facebook connected."""
    if not force and time.time() - _LAST_INSIGHTS[0] < INSIGHTS_EVERY:
        return 0
    if not _LOCK.acquire(blocking=False):
        return 0
    try:
        _LAST_INSIGHTS[0] = time.time()
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT DISTINCT i.tenant_id::text AS tid FROM integrations i
                            WHERE i.provider = 'meta' AND i.status = 'connected'""")
            tids = [r["tid"] for r in cur.fetchall()]
        n = 0
        for tid in tids:
            try:
                n += refresh_insights(tid)
            except Exception as e:  # noqa: BLE001 -- one business's problem never stops the others
                print("[autopilot] insights skipped for %s: %s" % (tid, e), flush=True)
        return n
    finally:
        _LOCK.release()


# ---------------------------------------------------------------------- router
def make_autopilot_router():
    router = APIRouter(prefix="/autopilot", tags=["autopilot"])

    @router.get("/media")
    def media(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session, owner=False)
        with tenant_session(tid, role) as cur:
            items = library(cur)
        for it in items:
            it["created_at"] = it["created_at"].isoformat()
            it.pop("token", None)
        return {"items": items}

    @router.patch("/media/{kind}/{item_id}")
    def edit_media(kind: str, item_id: str, body: dict = Body(...),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Name and tag a library item, or add/remove it (photo by id, video by id)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        table = {"photo": "social_media", "video": "social_videos"}.get(kind)
        if not table:
            raise HTTPException(404, "Not found.")
        sets, vals = [], []
        if "label" in body:
            sets.append("label = %s"); vals.append(re.sub(r"\s+", " ", str(body.get("label") or "")).strip()[:120])
        if "tags" in body:
            sets.append("tags = %s"); vals.append(_clean_tags(body.get("tags")))
        if "in_library" in body:
            sets.append("in_library = %s"); vals.append(bool(body.get("in_library")))
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            try:
                cur.execute("UPDATE %s SET %s WHERE id = %%s::uuid RETURNING id" % (table, ", ".join(sets)),
                            vals + [item_id])
            except Exception:
                raise HTTPException(404, "Not found.")
            if not cur.fetchone():
                raise HTTPException(404, "That photo or video is not in this business.")
        return {"ok": True}

    @router.post("/media/video/{token}/keep")
    def keep_video(token: str, body: dict | None = Body(None),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """After POST /social/video: keep the upload in the library (so cleanup leaves it)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        label = re.sub(r"\s+", " ", str((body or {}).get("label") or "")).strip()[:120]
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_videos SET in_library = true, label = CASE WHEN %s <> '' THEN %s ELSE label END "
                        "WHERE token = %s RETURNING id::text", (label, label, token))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "That video is not in this business.")
        return {"id": row["id"]}

    @router.post("/caption")
    def caption(body: dict = Body(...),
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Write captions for one post, for any kind of business, from its own records.
        Nothing is saved or posted: the owner copies one into a post or edits it."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        topic = str(body.get("topic") or "").strip()[:400]
        if len(topic) < 3:
            raise HTTPException(400, "Say what the post is about, e.g. 'weekend BBQ platter' or 'Hair PRP results'.")
        offers = str(body.get("offers") or "").strip()[:600]
        language = body.get("language") if body.get("language") in LANG else "english"
        tone = body.get("tone") if body.get("tone") in CAPTION_TONES else "friendly"
        now = time.time()
        recent = [t for t in _CAPTION_CALLS.get(tid, []) if now - t < 3600]
        if len(recent) >= CAPTIONS_PER_HOUR:
            raise HTTPException(429, "That is %d caption requests this hour. Try again a little later." % CAPTIONS_PER_HOUR)
        from . import ai
        if not ai.available():
            raise HTTPException(503, "The AI is not set up on this platform yet, so it cannot write captions.")
        _CAPTION_CALLS[tid] = recent + [now]
        brief, allowed = _grounding(tid)
        prompt = """BUSINESS
{brief}

THE POST IS ABOUT: {topic}
OFFERS YOU MAY MENTION: {offers}
TONE: {tone}
LANGUAGE: {lang}

Write 3 different captions for this one Facebook and Instagram post: different hooks and angles.
Return {{"captions": [{{"caption": "the full caption without hashtags", "hashtags": ["#tag", ...]}}]}}""".format(
            brief=brief, topic=topic, offers=offers or "None given: do not mention discounts.",
            tone=CAPTION_TONES[tone], lang=LANG[language])
        try:
            out = ai.generate_json(SYSTEM, prompt, required_keys=("captions",))
        except ai.CreditsUsedUp as e:
            raise HTTPException(402, str(e))
        except Exception as e:  # noqa: BLE001
            print("[caption] %s: %s" % (tid, str(e)[:200]), flush=True)
            raise HTTPException(502, "The AI did not answer. Try again in a minute, or write the caption yourself.")
        caps = []
        for c in (out.get("captions") or [])[:3] if isinstance(out.get("captions"), list) else []:
            if not isinstance(c, dict):
                continue
            text = re.sub(r"\s+\n", "\n", str(c.get("caption") or "")).strip()[:2000]
            tags = [t if t.startswith("#") else "#" + t for t in
                    (re.sub(r"[^\w#]", "", str(x)) for x in (c.get("hashtags") or [])[:12]) if len(t) > 1]
            if len(text) < 10:
                continue
            caps.append({"caption": text, "hashtags": tags, "unbacked": check_caption(text, allowed, offers)})
        if not caps:
            raise HTTPException(502, "The AI's answer had no usable caption. Try again, or write the caption yourself.")
        with tenant_session(tid, role) as cur:
            record(cur, action="caption.write", resource="caption", actor=str(uid) if uid else None,
                   detail={"topic": topic[:120], "language": language, "tone": tone, "count": len(caps)})
        return {"captions": caps}

    @router.post("/plan")
    def plan(background: BackgroundTasks, body: dict = Body(...),
             x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Start writing a plan. Returns at once; the drafts appear as each week is written."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        start = _parse_day(body.get("starts_on"), _today() + dt.timedelta(days=1))
        if start < _today():
            raise HTTPException(400, "Start the plan today or later.")
        try:
            days = int(body.get("days") or 30)
            per_week = int(body.get("posts_per_week") or 7)
        except (TypeError, ValueError):
            raise HTTPException(400, "Days and posts per week are numbers.")
        if not 1 <= days <= MAX_DAYS:
            raise HTTPException(400, "A plan covers 1 to %d days." % MAX_DAYS)
        if not 1 <= per_week <= 14:
            raise HTTPException(400, "Choose 1 to 14 posts a week.")
        language = body.get("language") if body.get("language") in LANG else "english"
        from . import ai
        if not ai.available():
            raise HTTPException(503, "The AI is not set up on this platform yet, so it cannot write a plan.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM content_plans WHERE status = 'generating' "
                        "AND created_at > now() - interval '15 minutes' LIMIT 1")
            if cur.fetchone():
                raise HTTPException(409, "A plan is already being written. It takes a few minutes.")
            cur.execute("""INSERT INTO content_plans (tenant_id, starts_on, days, posts_per_week, goal, offers,
                                                      language, created_by)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (start, days, per_week, str(body.get("goal") or "").strip()[:600],
                         str(body.get("offers") or "").strip()[:1200], language, str(uid) if uid else None))
            plan_id = str(cur.fetchone()["id"])
        background.add_task(generate_plan, tid, uid, plan_id)
        return {"plan_id": plan_id, "status": "generating"}

    @router.get("/plans")
    def plans(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session, owner=False)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT id::text AS id, starts_on, days, posts_per_week, goal, offers, language, status,
                                  drafted, error, created_at, finished_at FROM content_plans
                            ORDER BY created_at DESC LIMIT 5""")
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k in ("starts_on", "created_at", "finished_at"):
                r[k] = r[k].isoformat() if r[k] else None
        return {"plans": rows}

    @router.get("/calendar")
    def calendar(start: str | None = None, days: int = 35,
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session, owner=False)
        first = _parse_day(start, _today() - dt.timedelta(days=_today().weekday()))
        days = max(1, min(int(days or 35), 62))
        lo = dt.datetime(first.year, first.month, first.day, tzinfo=TZ)
        hi = lo + dt.timedelta(days=days)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT id, caption, status, platforms, theme, results, source, image_urls, video_token,
                                  plan_id, planned_at, scheduled_at, published_at FROM social_posts
                            WHERE status <> 'discarded'
                              AND coalesce(published_at, scheduled_at, planned_at) >= %s
                              AND coalesce(published_at, scheduled_at, planned_at) < %s
                            ORDER BY coalesce(published_at, scheduled_at, planned_at)""", (lo, hi))
            posts = [_post_view(r) for r in cur.fetchall()]
            cur.execute("""SELECT post_id::text AS id, sum(coalesce(reach, 0)) AS reach, sum(likes) AS likes,
                                  sum(comments) AS comments, sum(shares) AS shares, sum(saves) AS saves
                             FROM post_insights GROUP BY post_id""")
            nums = {r["id"]: {k: int(r[k] or 0) for k in ("reach", "likes", "comments", "shares", "saves")}
                    for r in cur.fetchall()}
        drafts = [p for p in posts if p["status"] in ("draft", "failed")]
        if drafts:
            allowed = _grounding(tid)[1]
            with tenant_session(tid, role) as cur:
                cur.execute("SELECT id::text AS id, offers FROM content_plans")
                offers = {r["id"]: r["offers"] for r in cur.fetchall()}
            for p in drafts:
                p["check"] = check_caption(p["caption"], allowed, offers.get(p["plan_id"] or "", ""))
        for p in posts:
            p["numbers"] = nums.get(p["id"])
        return {"start": first.isoformat(), "days": days, "posts": posts}

    @router.patch("/posts/{post_id}/time")
    def move(post_id: str, body: dict = Body(...),
             x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Move a planned draft to another day/time (local Pakistan time)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        day = _parse_day(body.get("date"))
        hhmm = str(body.get("time") or "")
        if not day or not TIME_RE.match(hhmm):
            raise HTTPException(400, "Choose a date and a time.")
        at = _local(day, hhmm)
        if at <= dt.datetime.now(TZ):
            raise HTTPException(400, "Pick a time in the future.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_posts SET planned_at = %s, updated_at = now() "
                        "WHERE id = %s AND status IN ('draft','failed') RETURNING id", (at, post_id))
            if not cur.fetchone():
                raise HTTPException(409, "Only a draft can be moved. Unschedule it first.")
        return {"ok": True, "planned_at": at.isoformat()}

    @router.post("/approve-week")
    def approve_week(body: dict = Body(...),
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner's one click for a week: every planned draft in it is scheduled
        for its proposed time. Drafts whose time has passed are left as drafts."""
        from .social import _chosen_page, _client_decides, _meta_pages
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _client_decides(x_tenant_id, x_role, authorization, td_session)
        first = _parse_day(body.get("week_start"))
        if not first:
            raise HTTPException(400, "Which week?")
        lo = dt.datetime(first.year, first.month, first.day, tzinfo=TZ)
        hi = lo + dt.timedelta(days=7)
        with tenant_session(tid, role) as cur:
            pages = _meta_pages(cur, tid)
            if not pages:
                raise HTTPException(409, "Connect Facebook first (Posts, Connect Facebook & Instagram), then approve.")
            cur.execute("""SELECT s.id, s.planned_at, s.caption, coalesce(c.offers, '') AS offers
                             FROM social_posts s LEFT JOIN content_plans c ON c.id = s.plan_id
                            WHERE s.status IN ('draft','failed')
                              AND s.planned_at >= %s AND s.planned_at < %s ORDER BY s.planned_at""", (lo, hi))
            rows = [dict(r) for r in cur.fetchall()]
        allowed = _grounding(tid)[1] if rows else {}
        with tenant_session(tid, role) as cur:
            scheduled, past, held = 0, 0, []
            for r in rows:
                if r["planned_at"] <= dt.datetime.now(TZ) + dt.timedelta(minutes=2):
                    past += 1
                    continue
                claims = check_caption(r["caption"], allowed, r["offers"])
                if claims:
                    # a price or promise the business never wrote down: never posted unseen
                    held.append({"id": str(r["id"]), "claims": claims})
                    continue
                page = _chosen_page(cur, pages, body.get("page_id"), str(r["id"]))
                cur.execute("""UPDATE social_posts SET status = 'scheduled', scheduled_at = planned_at,
                                      approved_by = %s, page_id = %s, updated_at = now()
                                WHERE id = %s AND status IN ('draft','failed')""",
                            (str(uid), str(page["id"]), r["id"]))
                scheduled += cur.rowcount
            record(cur, action="autopilot.approve_week", resource="social_posts", resource_id=None,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"week": first.isoformat(), "scheduled": scheduled, "past": past,
                           "held": len(held)}, error=None)
        return {"scheduled": scheduled, "past": past, "held": held}

    @router.get("/results")
    def results(days: int = 30,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session, owner=False)
        days = max(1, min(int(days or 30), 90))
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT i.platform, count(DISTINCT i.post_id) AS posts, sum(coalesce(i.reach, 0)) AS reach,
                                  sum(i.likes) AS likes, sum(i.comments) AS comments, sum(i.shares) AS shares,
                                  sum(i.saves) AS saves, max(i.fetched_at) AS fetched
                             FROM post_insights i JOIN social_posts p ON p.id = i.post_id
                            WHERE p.published_at > now() - make_interval(days => %s)
                            GROUP BY i.platform""", (days,))
            by_platform = {r["platform"]: {k: int(r[k] or 0) for k in ("posts", "reach", "likes", "comments",
                                                                      "shares", "saves")} for r in cur.fetchall()}
            cur.execute("""SELECT platform, day, followers FROM account_insights
                            WHERE day > (now() AT TIME ZONE 'Asia/Karachi')::date - %s ORDER BY day""", (days,))
            followers: dict = {}
            for r in cur.fetchall():
                f = followers.setdefault(r["platform"], {"first": r["followers"], "now": r["followers"]})
                f["now"] = r["followers"]
            for f in followers.values():
                f["gained"] = f["now"] - f["first"]
            cur.execute("""SELECT p.id::text AS id, p.caption, p.theme, p.published_at, p.image_urls,
                                  sum(coalesce(i.reach, 0)) AS reach, sum(i.likes) AS likes, sum(i.comments) AS comments,
                                  sum(i.shares) AS shares, sum(i.saves) AS saves,
                                  sum(i.likes + i.comments * 3 + i.shares * 4 + i.saves * 4) AS score
                             FROM post_insights i JOIN social_posts p ON p.id = i.post_id
                            WHERE p.published_at > now() - make_interval(days => %s)
                            GROUP BY p.id ORDER BY score DESC, reach DESC LIMIT 5""", (days,))
            top = [{"id": r["id"], "caption": r["caption"][:220], "theme": r["theme"],
                    "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                    "image": (r["image_urls"] or [None])[0],
                    **{k: int(r[k] or 0) for k in ("reach", "likes", "comments", "shares", "saves")}}
                   for r in cur.fetchall()]
            cur.execute("""SELECT coalesce(p.theme, 'other') AS theme, count(DISTINCT p.id) AS posts,
                                  avg(i.likes + i.comments * 3 + i.shares * 4 + i.saves * 4) AS avg_score
                             FROM post_insights i JOIN social_posts p ON p.id = i.post_id
                            WHERE p.published_at > now() - make_interval(days => %s)
                            GROUP BY 1 ORDER BY avg_score DESC NULLS LAST LIMIT 8""", (days,))
            themes = [{"theme": r["theme"], "posts": r["posts"], "avg_score": round(float(r["avg_score"] or 0), 1)}
                      for r in cur.fetchall()]
            cur.execute("""SELECT count(*) FILTER (WHERE status = 'scheduled') AS scheduled,
                                  count(*) FILTER (WHERE status = 'draft' AND planned_at > now()) AS waiting,
                                  count(*) FILTER (WHERE status IN ('published','partial')
                                                   AND published_at > now() - make_interval(days => %s)) AS published,
                                  count(*) FILTER (WHERE status = 'failed') AS failed
                             FROM social_posts""", (days,))
            counts = dict(cur.fetchone())
        return {"days": days, "by_platform": by_platform, "followers": followers, "top": top,
                "themes": themes, "counts": counts}

    return router
