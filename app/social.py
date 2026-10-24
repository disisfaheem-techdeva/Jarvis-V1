"""Social posting: Jarvis drafts, a person approves, the platform posts.

Nothing reaches a public Facebook page or Instagram account without an explicit
Approve from the business owner (or the founder acting for that one business).
Drafts can be edited or discarded; nothing is deleted.

Publishing uses the Meta connection the owner made through /connect/meta: the
page access tokens captured at connect time live, encrypted, in
integrations.credentials['pages'].
"""
import base64
import binascii
import json
import os
import re
import secrets

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException
from fastapi.responses import Response

from .audit import record
from .auth import SESSION_COOKIE
from .crypto import decrypt_json
from .db import tenant_session

GRAPH = "https://graph.facebook.com/v21.0"
# Pictures for posts are served from this API's public address so Meta can fetch them.
PUBLIC_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "https://api.techdeva.net").strip().rstrip("/")
MEDIA_MAX = 4 * 1024 * 1024
_TOKEN = re.compile(r"^[0-9a-f]{40}$")


def media_url(token):
    return "%s/media/%s.jpg" % (PUBLIC_BASE, token)


def is_our_media(url):
    """A picture link this platform issued (the only kind Jarvis attaches)."""
    url = (url or "").strip()
    pre = PUBLIC_BASE + "/media/"
    return url.startswith(pre) and bool(_TOKEN.match(url[len(pre):].replace(".jpg", "", 1)))
PLATFORMS = ("facebook", "instagram")
LOCAL_OFFSET = "+05:00"   # Asia/Karachi (no daylight saving), as Jarvis's clock in console.py
COLS = ("id, platforms, caption, image_url, image_urls, page_id, status, results, source, created_by, "
        "approved_by, created_at, updated_at, published_at, scheduled_at, video_token")
SCHEDULE_MAX_DAYS = 90
MAX_IMAGES = 10  # Facebook multi-photo and Instagram carousel both stop at 10


def _ident(x_tenant_id, x_role, authorization, session, target=None):
    # console owns the one rule for "founder acts for exactly one client"
    from .console import _identity
    return _identity(x_tenant_id, x_role, authorization, session, target=target)


def _client_decides(x_tenant_id, x_role, authorization, session):
    """Posting publicly is the client's decision alone. The founder can draft
    and see posts, but approve and discard belong to the business."""
    from .auth import AuthError, resolve_scope
    try:
        _t, real_role, _u = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if real_role == "techdeva_admin":
        raise HTTPException(403, "Only the client can approve or discard their posts.")


def _owner_only(role):
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can post for the business.")


def _clean_platforms(raw):
    if isinstance(raw, str):
        raw = [raw]
    out = [str(p).strip().lower() for p in (raw or [])]
    out = [p for p in out if p in PLATFORMS]
    return sorted(set(out)) or ["facebook", "instagram"]


def _clean_image(url):
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"^https://[^\s]+$", url) or len(url) > 1000:
        raise HTTPException(400, "The picture must be a public https:// link.")
    return url


def _clean_images(image_urls=None, image_url=None):
    """The post's pictures in order: image_urls (a list), else the single
    image_url older callers send. Duplicates dropped, at most MAX_IMAGES."""
    raw = image_urls if isinstance(image_urls, list) else ([image_url] if image_url else [])
    out = []
    for u in raw:
        u = _clean_image(u if isinstance(u, str) else None)
        if u and u not in out:
            out.append(u)
    if len(out) > MAX_IMAGES:
        raise HTTPException(400, "A post can have up to %d pictures." % MAX_IMAGES)
    return out


def post_images(post):
    return list(post.get("image_urls") or []) or ([post["image_url"]] if post.get("image_url") else [])


def create_draft(cur, tid, uid, caption, platforms=None, image_url=None, source="jarvis",
                 image_urls=None, video_token=None):
    caption = (caption or "").strip()[:2200]
    if len(caption) < 2:
        raise HTTPException(400, "The post needs some words.")
    plats = _clean_platforms(platforms)
    imgs = _clean_images(image_urls, image_url)
    if video_token:
        from .videos import owns_video
        if not owns_video(cur, video_token):
            raise HTTPException(400, "That video is not there any more. Add it again.")
        imgs = []          # a post is pictures or one video, not both
    cur.execute("INSERT INTO social_posts (tenant_id, platforms, caption, image_url, image_urls, "
                "source, created_by, video_token) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING " + COLS,
                (tid, plats, caption, imgs[0] if imgs else None, imgs, source,
                 str(uid) if uid else None, video_token))
    row = dict(cur.fetchone())
    record(cur, action="social.draft", resource="social_posts",
           resource_id=str(row["id"]), actor=str(uid), actor_type="human",
           status="ok", detail={"platforms": plats, "has_image": bool(imgs),
                                "images": len(imgs), "video": bool(video_token)}, error=None)
    return row


def _meta_pages(cur, tid):
    cur.execute("SELECT credentials FROM integrations WHERE provider = 'meta' "
                "AND status = 'connected' LIMIT 1")
    row = cur.fetchone()
    if not row:
        return []
    creds = decrypt_json(row["credentials"], tid, "meta")
    return [p for p in (creds.get("pages") or []) if p.get("id") and p.get("access_token")]


def _pick_page(pages, page_id):
    """The Page a post goes to. With several Pages the owner must choose:
    guessing would post a business's content on someone else's Page."""
    if page_id:
        for p in pages:
            if str(p["id"]) == str(page_id):
                return p
        raise HTTPException(409, "That Facebook Page is no longer connected. Choose another Page.")
    if len(pages) == 1:
        return pages[0]
    raise HTTPException(409, "Choose which Facebook Page to post to (top of the Posts page).")


def _chosen_page(cur, pages, page_id, post_id):
    """The Page named now, else the one already on this post, else the Page
    the owner last chose for any post (so Jarvis and the Approve buttons
    elsewhere follow the owner's choice). Never a guess."""
    if not page_id:
        cur.execute("SELECT page_id FROM social_posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        page_id = row and row["page_id"]
    if not page_id and len(pages) > 1:
        cur.execute("SELECT page_id FROM social_posts WHERE page_id IS NOT NULL "
                    "ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        ids = {str(p["id"]) for p in pages}
        page_id = row["page_id"] if row and row["page_id"] in ids else None
    return _pick_page(pages, page_id)


def _graph_err(r, body):
    e = body.get("error") or {}
    msg = e.get("error_user_msg") or e.get("message") or r.text[:200]
    code = "/".join(str(x) for x in (e.get("code"), e.get("error_subcode")) if x)
    return (msg + (" (code %s)" % code if code else ""))[:300]


def _ig_ready(container, tok, wait=60):
    """Instagram processes each uploaded picture before it can be published;
    publishing too early fails with 'An unknown error has occurred'."""
    import time
    end = time.time() + wait
    while True:
        try:
            r = requests.get(GRAPH + "/" + str(container),
                             params={"fields": "status_code,status", "access_token": tok}, timeout=20)
            body = r.json()
        except (requests.RequestException, ValueError):
            body = {}
        st = body.get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return None
        if st in ("ERROR", "EXPIRED"):
            return "Instagram could not use a picture: %s" % (body.get("status") or st)
        if time.time() > end:
            return "Instagram took too long to process the pictures. Try again in a minute."
        time.sleep(2)


def _graph_post(path, data):
    try:
        r = requests.post(GRAPH + path, data=data, timeout=40)
    except requests.RequestException as e:
        return None, "network: " + str(e)[:160]
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        return None, _graph_err(r, body)
    return body, None


def _publish_facebook(page, caption, images):
    tok = page["access_token"]
    if len(images) > 1:
        # Several pictures: upload each unpublished, then one feed post that
        # attaches them all (Facebook's multi-photo post).
        attached = {}
        for i, url in enumerate(images):
            ph, err = _graph_post("/%s/photos" % page["id"],
                                  {"url": url, "published": "false", "access_token": tok})
            if err:
                return {"ok": False, "error": "picture %d: %s" % (i + 1, err), "page": page.get("name")}
            attached["attached_media[%d]" % i] = json.dumps({"media_fbid": ph.get("id")})
        body, err = _graph_post("/%s/feed" % page["id"],
                                dict(attached, message=caption, access_token=tok))
    elif images:
        body, err = _graph_post("/%s/photos" % page["id"],
                                {"url": images[0], "caption": caption, "access_token": tok})
    else:
        body, err = _graph_post("/%s/feed" % page["id"],
                                {"message": caption, "access_token": tok})
    if err:
        return {"ok": False, "error": err, "page": page.get("name")}
    return {"ok": True, "id": body.get("post_id") or body.get("id"), "page": page.get("name")}


def _publish_instagram(page, caption, images):
    ig = page.get("ig_id")
    if not ig:
        return {"ok": False, "error": "No Instagram business account is linked to "
                "the Facebook page %s." % page.get("name")}
    if not images:
        return {"ok": False, "error": "Instagram needs a picture."}
    tok = page["access_token"]
    if len(images) > 1:
        # Carousel: one item container per picture, then the carousel itself.
        kids = []
        for i, url in enumerate(images):
            item, err = _graph_post("/%s/media" % ig, {"image_url": url, "is_carousel_item": "true",
                                                       "access_token": tok})
            if err:
                return {"ok": False, "error": "picture %d: %s" % (i + 1, err)}
            err = _ig_ready(item.get("id"), tok)
            if err:
                return {"ok": False, "error": "picture %d: %s" % (i + 1, err)}
            kids.append(str(item.get("id")))
        body, err = _graph_post("/%s/media" % ig, {"media_type": "CAROUSEL", "children": ",".join(kids),
                                                   "caption": caption, "access_token": tok})
    else:
        body, err = _graph_post("/%s/media" % ig,
                                {"image_url": images[0], "caption": caption, "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    err = _ig_ready(body.get("id"), tok)
    if err:
        return {"ok": False, "error": err}
    body2, err = _graph_post("/%s/media_publish" % ig,
                             {"creation_id": body.get("id"), "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "id": body2.get("id"), "account": page.get("ig_username")}


def _publish_facebook_video(page, caption, url):
    body, err = _graph_post("/%s/videos" % page["id"], {"file_url": url, "description": caption,
                                                       "access_token": page["access_token"]})
    if err:
        return {"ok": False, "error": err, "page": page.get("name")}
    return {"ok": True, "id": body.get("id"), "page": page.get("name"), "video": True}


def _publish_reel(page, caption, url):
    ig = page.get("ig_id")
    if not ig:
        return {"ok": False, "error": "No Instagram business account is linked to "
                "the Facebook page %s." % page.get("name")}
    tok = page["access_token"]
    body, err = _graph_post("/%s/media" % ig, {"media_type": "REELS", "video_url": url, "caption": caption,
                                               "share_to_feed": "true", "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    # Instagram downloads and processes the whole video before it can go out
    err = _ig_ready(body.get("id"), tok, wait=600)
    if err:
        return {"ok": False, "error": err.replace("pictures", "video").replace("a picture", "the video")}
    body2, err = _graph_post("/%s/media_publish" % ig, {"creation_id": body.get("id"), "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "id": body2.get("id"), "account": page.get("ig_username"), "reel": True}


def publish_post(tid, role, post_id, actor, claim_from, page_id=None):
    """Claim the post first so a double click (or the worker and a person at
    once) can never post twice, then talk to Meta outside any transaction."""
    with tenant_session(tid, role) as cur:
        pages = _meta_pages(cur, tid)
        if not pages:
            raise HTTPException(409, "Connect Facebook first (Integrations, "
                                     "Connect Facebook), then approve again.")
        page = _chosen_page(cur, pages, page_id, post_id)
        cur.execute("UPDATE social_posts SET status = 'publishing', page_id = %s, "
                    "approved_by = COALESCE(approved_by, %s), updated_at = now() "
                    "WHERE id = %s AND (status = ANY(%s) "
                    "OR (status = 'publishing' AND updated_at < now() - interval '10 minutes')) "
                    "RETURNING " + COLS, (str(page["id"]), actor, post_id, list(claim_from)))
        post = cur.fetchone()
        if not post:
            raise HTTPException(409, "That post is not waiting for approval.")
        post = dict(post)
    images = post_images(post)
    results = {}
    if post.get("video_token"):
        from .videos import video_url
        url = video_url(post["video_token"])
        if "facebook" in post["platforms"]:
            results["facebook"] = _publish_facebook_video(page, post["caption"], url)
        if "instagram" in post["platforms"]:
            results["instagram"] = _publish_reel(page, post["caption"], url)
    else:
        if "facebook" in post["platforms"]:
            results["facebook"] = _publish_facebook(page, post["caption"], images)
        if "instagram" in post["platforms"]:
            results["instagram"] = _publish_instagram(page, post["caption"], images)
    oks = [r["ok"] for r in results.values()]
    status = "published" if all(oks) else ("partial" if any(oks) else "failed")
    with tenant_session(tid, role) as cur:
        cur.execute("UPDATE social_posts SET status = %s, results = %s::jsonb, "
                    "updated_at = now(), published_at = CASE WHEN %s THEN now() END "
                    "WHERE id = %s RETURNING " + COLS,
                    (status, json.dumps(results), any(oks), post_id))
        row = dict(cur.fetchone())
        record(cur, action="social.publish", resource="social_posts", resource_id=post_id,
               actor=actor, actor_type="human" if actor != "scheduler" else "system",
               status="ok" if status == "published" else "error",
               detail=results, error=None if all(oks) else "not every platform posted")
    return {"post": row, "status": status, "results": results}


STUCK_MSG = ("TechDeva did not get Facebook's answer for this post, so it may or may not have been posted. "
             "Check your Page; if it is not there, approve it again.")


def publish_due(limit=10):
    """Called by the worker's scheduler: post what the owner scheduled.

    Each post is published in its OWN tenant's session (role owner), never an
    admin session -- _meta_pages reads the tenant's connection through RLS, and
    an admin session would see every tenant's."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id, tenant_id FROM social_posts WHERE status = 'scheduled' "
                    "AND scheduled_at <= now() ORDER BY scheduled_at LIMIT %s", (limit,))
        due = [(str(r["tenant_id"]), str(r["id"])) for r in cur.fetchall()]
        # A post claimed for publishing whose answer from Meta never got saved (the worker
        # restarted, or an error after the claim) would sit as "publishing" for ever. Posting
        # it again could post twice, so it becomes "failed" with what is known; the owner
        # checks the Page and, if it is not there, approves it again.
        cur.execute("""UPDATE social_posts SET status = 'failed', updated_at = now(),
                              results = COALESCE(results, '{}'::jsonb) || jsonb_build_object('error', %s::text)
                        WHERE status = 'publishing' AND updated_at < now() - interval '15 minutes'
                    RETURNING id, tenant_id""", (STUCK_MSG,))
        for r in cur.fetchall():
            record(cur, action="social.publish", resource="social_posts", resource_id=str(r["id"]),
                   actor="scheduler", actor_type="system", status="error", detail={"stuck": True},
                   error="no answer from Meta was saved", tenant_id=str(r["tenant_id"]))
    done = 0
    for tid, pid in due:
        try:
            publish_post(tid, "owner", pid, "scheduler", ("scheduled",))
        except HTTPException as e:
            # e.g. Facebook was disconnected after scheduling: say so on the post
            with tenant_session(tid, "owner") as cur:
                cur.execute("UPDATE social_posts SET status = 'failed', results = %s::jsonb, "
                            "updated_at = now() WHERE id = %s AND status = 'scheduled'",
                            (__import__("json").dumps({"error": e.detail}), pid))
                record(cur, action="social.publish", resource="social_posts", resource_id=pid,
                       actor="scheduler", actor_type="system", status="error",
                       detail={}, error=str(e.detail)[:300])
        done += 1
    try:
        from .videos import cleanup
        cleanup()
    except Exception as e:  # noqa: BLE001 -- housekeeping never stops posting
        print("[social] video cleanup skipped: %s" % e, flush=True)
    return done


def make_social_router():
    router = APIRouter(prefix="/social", tags=["social"])

    @router.get("/accounts")
    def accounts(tenant: str | None = None,
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        with tenant_session(tid, role) as cur:
            pages = _meta_pages(cur, tid)
            cur.execute("SELECT 1 FROM integrations WHERE provider = 'meta' AND status = 'connected' LIMIT 1")
            logged_in = bool(cur.fetchone())
            cur.execute("SELECT page_id FROM social_posts WHERE page_id IS NOT NULL "
                        "ORDER BY updated_at DESC LIMIT 1")
            last = cur.fetchone()
        # facebook_login: Facebook is connected; connected: it also gave us a
        # Page we may post to (posting needs both).
        ids = [str(p.get("id")) for p in pages]
        chosen = ids[0] if len(ids) == 1 else (last["page_id"] if last and last["page_id"] in ids else None)
        return {"connected": bool(pages), "facebook_login": logged_in, "page_id": chosen,
                "pages": [{"id": str(p.get("id")), "name": p.get("name"),
                           "instagram": p.get("ig_username") or (True if p.get("ig_id") else None)}
                          for p in pages]}

    @router.post("/media")
    def upload_media(body: dict = Body(...),
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Store a picture for a post and return the public link Meta will fetch.
        JPEG only (Instagram accepts nothing else); the browser converts first."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        _owner_only(role)
        data = str(body.get("image") or "")
        if not data.startswith("data:image/jpeg;base64,"):
            raise HTTPException(400, "Send the picture as a JPEG from the dashboard.")
        try:
            raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, "That picture could not be read.")
        if not raw.startswith(b"\xff\xd8"):
            raise HTTPException(400, "That is not a JPEG picture.")
        if len(raw) > MEDIA_MAX:
            raise HTTPException(413, "That picture is larger than 4 MB. Try a smaller one.")
        token = secrets.token_hex(20)
        with tenant_session(tid, role) as cur:
            cur.execute("INSERT INTO social_media (tenant_id, token, mime, bytes, byte_size, created_by) "
                        "VALUES (current_tenant(), %s, 'image/jpeg', %s, %s, %s) RETURNING id",
                        (token, raw, len(raw), str(uid) if uid else None))
            mid = cur.fetchone()["id"]
            record(cur, action="social.media", resource="social_media", resource_id=str(mid),
                   actor=str(uid), actor_type="human", status="ok", detail={"bytes": len(raw)}, error=None)
        return {"id": str(mid), "url": media_url(token), "bytes": len(raw)}

    @router.get("/posts")
    def list_posts(tenant: str | None = None,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT " + COLS + " FROM social_posts WHERE status <> 'discarded' "
                        "ORDER BY created_at DESC LIMIT 50")
            rows = [dict(r) for r in cur.fetchall()]
        return {"posts": rows, "count": len(rows)}

    @router.post("/posts")
    def new_post(body: dict = Body(...),
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session,
                                body.get("tenant_id"))
        _owner_only(role)
        with tenant_session(tid, role) as cur:
            vt = None
            if body.get("video_url"):
                from .videos import token_from_url
                vt = token_from_url(body.get("video_url"))
                if not vt:
                    raise HTTPException(400, "That video link is not one of yours. Add the video again.")
            row = create_draft(cur, tid, uid, body.get("caption"), body.get("platforms"),
                               body.get("image_url"), source="manual",
                               image_urls=body.get("image_urls"), video_token=vt)
        return {"post": row}

    @router.patch("/posts/{post_id}")
    def edit_post(post_id: str, body: dict = Body(...), tenant: str | None = None,
                  x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        sets, vals = [], []
        if "caption" in body:
            cap = (body.get("caption") or "").strip()[:2200]
            if len(cap) < 2:
                raise HTTPException(400, "The post needs some words.")
            sets.append("caption = %s"); vals.append(cap)
        if "image_urls" in body or "image_url" in body:
            imgs = _clean_images(body.get("image_urls"), body.get("image_url"))
            sets.append("image_url = %s"); vals.append(imgs[0] if imgs else None)
            sets.append("image_urls = %s"); vals.append(imgs)
        if "platforms" in body:
            sets.append("platforms = %s"); vals.append(_clean_platforms(body.get("platforms")))
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_posts SET " + ", ".join(sets) + ", updated_at = now() "
                        "WHERE id = %s AND status IN ('draft','failed') RETURNING " + COLS,
                        vals + [post_id])
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "Only a draft can be edited.")
            record(cur, action="social.edit", resource="social_posts", resource_id=post_id,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"fields": list(body.keys())}, error=None)
        return {"post": dict(row)}

    @router.delete("/posts/{post_id}")
    def discard(post_id: str, tenant: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        _client_decides(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_posts SET status = 'discarded', updated_at = now() "
                        "WHERE id = %s AND status IN ('draft','failed') RETURNING id", (post_id,))
            if not cur.fetchone():
                raise HTTPException(409, "Only a draft can be discarded.")
            record(cur, action="social.discard", resource="social_posts", resource_id=post_id,
                   actor=str(uid), actor_type="human", status="ok", detail={}, error=None)
        return {"ok": True}

    @router.post("/posts/{post_id}/publish")
    def publish(post_id: str, body: dict | None = Body(None), tenant: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The Approve button. Claims the draft first so a double click can
        never post twice, then talks to Meta outside any transaction."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        _client_decides(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT video_token FROM social_posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
            if row and row["video_token"]:
                # Meta takes minutes to process a video: the worker posts it in
                # the background (within a minute) instead of holding this page.
                pages = _meta_pages(cur, tid)
                if not pages:
                    raise HTTPException(409, "Connect Facebook first (Integrations, Connect Facebook), then approve again.")
                page = _chosen_page(cur, pages, (body or {}).get("page_id"), post_id)
                cur.execute("UPDATE social_posts SET status = 'scheduled', scheduled_at = now(), page_id = %s, "
                            "approved_by = %s, updated_at = now() WHERE id = %s AND status IN ('draft','failed') "
                            "RETURNING " + COLS, (str(page["id"]), str(uid), post_id))
                post = cur.fetchone()
                if not post:
                    raise HTTPException(409, "That post is not waiting for approval.")
                record(cur, action="social.schedule", resource="social_posts", resource_id=post_id,
                       actor=str(uid), actor_type="human", status="ok",
                       detail={"at": "now", "video": True, "page": page.get("name")}, error=None)
                return {"post": dict(post), "status": "queued", "results": {}}
        return publish_post(tid, role, post_id, str(uid), ("draft", "failed"),
                            page_id=(body or {}).get("page_id"))

    @router.post("/posts/{post_id}/schedule")
    def schedule(post_id: str, body: dict = Body(...), tenant: str | None = None,
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Approve a draft for a later time. Approving is still the client's
        decision; the worker only carries it out when the time comes."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        _client_decides(x_tenant_id, x_role, authorization, td_session)
        at = str(body.get("at") or "").strip()
        # The dashboard always sends UTC ("...Z"). A time with no zone (Jarvis)
        # is the business's local time, the same zone Jarvis reads the clock in,
        # never the database's UTC.
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$", at):
            at += LOCAL_OFFSET
        with tenant_session(tid, role) as cur:
            pages = _meta_pages(cur, tid)
            if not pages:
                raise HTTPException(409, "Connect Facebook first (Integrations, "
                                         "Connect Facebook), then schedule again.")
            page = _chosen_page(cur, pages, body.get("page_id"), post_id)
            try:
                cur.execute("SELECT %s::timestamptz AS at, %s::timestamptz > now() + interval '1 minute' AS future, "
                            "%s::timestamptz < now() + make_interval(days => %s) AS near",
                            (at, at, at, SCHEDULE_MAX_DAYS))
            except Exception:
                raise HTTPException(400, "Pick a date and time for the post.")
            chk = cur.fetchone()
            if not chk["future"]:
                raise HTTPException(400, "Pick a time in the future.")
            if not chk["near"]:
                raise HTTPException(400, "A post can be scheduled up to %d days ahead." % SCHEDULE_MAX_DAYS)
            cur.execute("UPDATE social_posts SET status = 'scheduled', scheduled_at = %s, approved_by = %s, "
                        "page_id = %s, updated_at = now() WHERE id = %s AND status IN ('draft','failed') "
                        "RETURNING " + COLS, (chk["at"], str(uid), str(page["id"]), post_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "Only a draft can be scheduled.")
            record(cur, action="social.schedule", resource="social_posts", resource_id=post_id,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"at": chk["at"].isoformat(), "page": page.get("name")}, error=None)
        return {"post": dict(row)}

    @router.post("/posts/{post_id}/unschedule")
    def unschedule(post_id: str, tenant: str | None = None,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_posts SET status = 'draft', scheduled_at = NULL, updated_at = now() "
                        "WHERE id = %s AND status = 'scheduled' RETURNING " + COLS, (post_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "That post is not scheduled (it may already be posting).")
            record(cur, action="social.unschedule", resource="social_posts", resource_id=post_id,
                   actor=str(uid), actor_type="human", status="ok", detail={}, error=None)
        return {"post": dict(row)}

    return router


def make_media_router():
    """GET /media/<token>.jpg -- public on purpose: Facebook and Instagram fetch
    the picture from here when a post is published. The 160-bit token is the
    only way to name a picture; nothing lists them."""
    router = APIRouter(tags=["social"])

    @router.get("/media/{name}")
    def media(name: str):
        token = name[:-4] if name.endswith(".jpg") else name
        if not _TOKEN.match(token):
            raise HTTPException(404, "Not found.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT bytes FROM social_media WHERE token = %s", (token,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Not found.")
        return Response(content=bytes(row["bytes"]), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'"})

    return router
