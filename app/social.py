"""Social posting: Jarvis drafts, a person approves, the platform posts.

Nothing reaches a public Facebook page or Instagram account without an explicit
Approve from the business owner (or the founder acting for that one business).
Drafts can be edited or discarded; nothing is deleted.

Publishing uses the Meta connection the owner made through /connect/meta: the
page access tokens captured at connect time live, encrypted, in
integrations.credentials['pages'].
"""
import re

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .crypto import decrypt_json
from .db import tenant_session

GRAPH = "https://graph.facebook.com/v21.0"
PLATFORMS = ("facebook", "instagram")
COLS = ("id, platforms, caption, image_url, status, results, source, created_by, "
        "approved_by, created_at, updated_at, published_at")


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


def create_draft(cur, tid, uid, caption, platforms=None, image_url=None, source="jarvis"):
    caption = (caption or "").strip()[:2200]
    if len(caption) < 2:
        raise HTTPException(400, "The post needs some words.")
    plats = _clean_platforms(platforms)
    img = _clean_image(image_url)
    cur.execute("INSERT INTO social_posts (tenant_id, platforms, caption, image_url, "
                "source, created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING " + COLS,
                (tid, plats, caption, img, source, str(uid) if uid else None))
    row = dict(cur.fetchone())
    record(cur, action="social.draft", resource="social_posts",
           resource_id=str(row["id"]), actor=str(uid), actor_type="human",
           status="ok", detail={"platforms": plats, "has_image": bool(img)}, error=None)
    return row


def _meta_pages(cur, tid):
    cur.execute("SELECT credentials FROM integrations WHERE provider = 'meta' "
                "AND status = 'connected' LIMIT 1")
    row = cur.fetchone()
    if not row:
        return []
    creds = decrypt_json(row["credentials"], tid, "meta")
    return [p for p in (creds.get("pages") or []) if p.get("id") and p.get("access_token")]


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
        msg = (body.get("error") or {}).get("message") or r.text[:200]
        return None, msg[:300]
    return body, None


def _publish_facebook(page, caption, image_url):
    tok = page["access_token"]
    if image_url:
        body, err = _graph_post("/%s/photos" % page["id"],
                                {"url": image_url, "caption": caption, "access_token": tok})
    else:
        body, err = _graph_post("/%s/feed" % page["id"],
                                {"message": caption, "access_token": tok})
    if err:
        return {"ok": False, "error": err, "page": page.get("name")}
    return {"ok": True, "id": body.get("post_id") or body.get("id"), "page": page.get("name")}


def _publish_instagram(page, caption, image_url):
    ig = page.get("ig_id")
    if not ig:
        return {"ok": False, "error": "No Instagram business account is linked to "
                "the Facebook page %s." % page.get("name")}
    if not image_url:
        return {"ok": False, "error": "Instagram needs a picture."}
    tok = page["access_token"]
    body, err = _graph_post("/%s/media" % ig,
                            {"image_url": image_url, "caption": caption, "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    body2, err = _graph_post("/%s/media_publish" % ig,
                             {"creation_id": body.get("id"), "access_token": tok})
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "id": body2.get("id"), "account": page.get("ig_username")}


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
        return {"connected": bool(pages),
                "pages": [{"name": p.get("name"),
                           "instagram": p.get("ig_username") or (True if p.get("ig_id") else None)}
                          for p in pages]}

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
            row = create_draft(cur, tid, uid, body.get("caption"), body.get("platforms"),
                               body.get("image_url"), source="manual")
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
        if "image_url" in body:
            sets.append("image_url = %s"); vals.append(_clean_image(body.get("image_url")))
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
    def publish(post_id: str, tenant: str | None = None,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The Approve button. Claims the draft first so a double click can
        never post twice, then talks to Meta outside any transaction."""
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session, tenant)
        _owner_only(role)
        _client_decides(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            pages = _meta_pages(cur, tid)
            if not pages:
                raise HTTPException(409, "Connect Facebook first (Integrations, "
                                         "Connect Facebook), then approve again.")
            cur.execute("UPDATE social_posts SET status = 'publishing', approved_by = %s, "
                        "updated_at = now() WHERE id = %s AND (status IN ('draft','failed') "
                        "OR (status = 'publishing' AND updated_at < now() - interval '10 minutes')) "
                        "RETURNING " + COLS, (str(uid), post_id))
            post = cur.fetchone()
            if not post:
                raise HTTPException(409, "That post is not waiting for approval.")
            post = dict(post)
        page = pages[0]
        results = {}
        if "facebook" in post["platforms"]:
            results["facebook"] = _publish_facebook(page, post["caption"], post["image_url"])
        if "instagram" in post["platforms"]:
            results["instagram"] = _publish_instagram(page, post["caption"], post["image_url"])
        oks = [r["ok"] for r in results.values()]
        status = "published" if all(oks) else ("partial" if any(oks) else "failed")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE social_posts SET status = %s, results = %s::jsonb, "
                        "updated_at = now(), published_at = CASE WHEN %s THEN now() END "
                        "WHERE id = %s RETURNING " + COLS,
                        (status, __import__("json").dumps(results), any(oks), post_id))
            row = dict(cur.fetchone())
            record(cur, action="social.publish", resource="social_posts", resource_id=post_id,
                   actor=str(uid), actor_type="human",
                   status="ok" if status == "published" else "error",
                   detail=results, error=None if all(oks) else "not every platform posted")
        return {"post": row, "status": status, "results": results}

    return router
