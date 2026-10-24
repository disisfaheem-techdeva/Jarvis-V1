"""Marketing > Autopilot Gallery API. Paths start with /marketing (every route in
this app is mounted at the root; the role middleware maps /marketing to the
Marketing section, so view/create/edit/approve follow each person's role).

Staff with Marketing access upload, generate and edit drafts. Approving,
pausing, resuming and cancelling a campaign are the business owner's alone,
and the founder acting for a client may not approve (as on the Posts page).
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, Body, Cookie, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from ..audit import record
from ..auth import SESSION_COOKIE
from ..db import tenant_session
from ..storage import KEY_RE, get_storage
from . import campaigns, media, settings
from .drip import DripError, build_schedule, describe

MAX_FILES = 30
_P = None


def _persist():
    """Events go through the same Persistence the worker reads (events table + Redis queue)."""
    global _P
    if _P is None:
        from ..persistence import Persistence
        _P = Persistence()
    return _P


def _who(x_tenant_id, x_role, authorization, session):
    from ..social import _ident
    return _ident(x_tenant_id, x_role, authorization, session)


def _owner(role, x_tenant_id, x_role, authorization, session, approve=False):
    from ..social import _client_decides, _owner_only
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can do this.")
    _owner_only(role)
    if approve:
        _client_decides(x_tenant_id, x_role, authorization, session)


def _agent_on(tid, agent, what):
    from ..agent_admin import enabled
    if not enabled(tid, agent):
        raise HTTPException(409, "The %s agent is switched off for this business (Jarvis & AI > Agents). "
                                 "Switch it on to %s." % (agent.capitalize(), what))


def make_marketing_router():
    router = APIRouter(prefix="/marketing", tags=["marketing"])

    # ------------------------------------------------------------------ media
    @router.get("/media")
    def list_media(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT * FROM media_assets ORDER BY created_at DESC LIMIT 300")
            rows = [media.asset_view(r) for r in cur.fetchall()]
            cfg = settings.load(cur)
        return {"media": rows, "limits": settings.upload_limits(cfg)}

    @router.post("/media")
    async def upload(files: list[UploadFile] = File(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if len(files) > MAX_FILES:
            raise HTTPException(400, "Upload at most %d files at a time." % MAX_FILES)
        out = []
        for f in files:
            name = os.path.basename(f.filename or "file")
            try:
                with tenant_session(tid, role) as cur:
                    cfg = settings.load(cur)
                    asset = await media.save_upload(cur, tid, uid, f, cfg)
                    record(cur, action="marketing.media_upload", resource="media_assets", resource_id=asset["id"],
                           actor=str(uid), actor_type="human", status="ok",
                           detail={"kind": asset["kind"], "bytes": asset["bytes"], "warnings": len(asset["warnings"])},
                           error=None)
                out.append({"name": name, "ok": True, "asset": asset})
            except HTTPException as e:
                out.append({"name": name, "ok": False, "status": e.status_code, "error": e.detail})
        return {"results": out, "uploaded": sum(1 for r in out if r["ok"])}

    @router.delete("/media/{asset_id}")
    def delete_media(asset_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT storage_key FROM media_assets WHERE id::text = %s", (asset_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "That file is not in your gallery.")
            cur.execute("SELECT 1 FROM scheduled_posts WHERE media_asset_id::text = %s LIMIT 1", (asset_id,))
            if cur.fetchone():
                raise HTTPException(409, "A campaign uses this file. Cancel or finish that campaign first.")
            cur.execute("DELETE FROM media_assets WHERE id::text = %s", (asset_id,))
            record(cur, action="marketing.media_delete", resource="media_assets", resource_id=asset_id,
                   actor=str(uid), actor_type="human", status="ok", detail={}, error=None)
        get_storage().delete(row["storage_key"])
        return {"deleted": asset_id}

    # ------------------------------------------------------------------ campaigns
    @router.post("/autopilot/preview")
    def preview(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The live preview line: dates the rule would give, without saving anything."""
        import datetime as dt
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        n = max(0, min(int(body.get("count") or 0), 500))
        rule = campaigns.clean_rule(body.get("rule_config") or {})
        with tenant_session(tid, role) as cur:
            tz = settings.timezone(settings.load(cur))
            taken = campaigns.taken_slots(cur, rule["platforms"])
        try:
            slots = build_schedule(n, body.get("rule_config") or {}, tz, dt.datetime.now(dt.timezone.utc), taken)
        except DripError as e:
            raise HTTPException(400, str(e))
        return {"timezone": tz, "text": describe(n, body.get("rule_config") or {}, slots, tz),
                "slots": [s.isoformat() for s in slots]}

    @router.post("/autopilot/generate")
    def generate(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _agent_on(tid, "content", "write captions")
        with tenant_session(tid, role) as cur:
            cid = campaigns.create(cur, uid, body.get("name"), body.get("media_asset_ids"), body.get("rule_config"),
                                   page_id=body.get("page_id"))
            record(cur, action="marketing.campaign_create", resource="marketing_campaigns", resource_id=cid,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"items": len(body.get("media_asset_ids") or [])}, error=None)
        _persist().emit(tid, "marketing.campaign_created", {"campaign_id": cid}, "api")
        return {"campaign_id": cid, "captioning": "queued"}

    @router.get("/campaigns")
    def list_campaigns(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            tz = settings.timezone(settings.load(cur))
            return {"campaigns": campaigns.listing(cur), "timezone": tz,
                    "gbp_enabled": campaigns.gbp_enabled()}

    @router.get("/campaigns/{cid}")
    def one_campaign(cid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return campaigns.get(cur, cid)

    @router.patch("/posts/{pid}")
    def edit_post(pid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            out = campaigns.edit_post(cur, uid, pid, body)
            record(cur, action="marketing.post_edit", resource="scheduled_posts", resource_id=pid, actor=str(uid),
                   actor_type="human", status="ok",
                   detail={"changed": sorted(k for k in body if k in ("caption", "hashtags", "scheduled_at", "cancel"))},
                   error=None)
        return out

    @router.post("/posts/{pid}/recaption")
    def recaption(pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _agent_on(tid, "content", "write captions")
        with tenant_session(tid, role) as cur:
            out = campaigns.mark_recaptioning(cur, pid)
        _persist().emit(tid, "marketing.recaption_requested", {"post_id": out["post_id"], "campaign_id": out["campaign_id"]}, "api")
        return {"post_id": out["post_id"], "status": "queued"}

    @router.post("/campaigns/{cid}/approve")
    def approve(cid: str, body: dict | None = Body(None), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _owner(role, x_tenant_id, x_role, authorization, td_session, approve=True)
        _agent_on(tid, "publishing", "publish approved posts")
        overrides = [str(x) for x in ((body or {}).get("override_post_ids") or [])]
        with tenant_session(tid, role) as cur:
            out = campaigns.approve(cur, uid, cid, overrides)
            record(cur, action="marketing.campaign_approve", resource="marketing_campaigns", resource_id=cid,
                   actor=str(uid), actor_type="human", status="ok", detail=out, error=None)
        return out

    def _state(cid, action, x_tenant_id, x_role, authorization, td_session):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        _owner(role, x_tenant_id, x_role, authorization, td_session)
        token = None
        if action == "resume":
            from .. import meta_publish_client as meta
            from .publisher import page_for_campaign
            token = meta.validate(page_for_campaign(tid, cid))
        with tenant_session(tid, role) as cur:
            out = campaigns.set_state(cur, uid, cid, action, token)
            record(cur, action="marketing.campaign_" + action, resource="marketing_campaigns", resource_id=cid,
                   actor=str(uid), actor_type="human", status="ok", detail=out, error=None)
        return out

    @router.post("/campaigns/{cid}/pause")
    def pause(cid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return _state(cid, "pause", x_tenant_id, x_role, authorization, td_session)

    @router.post("/campaigns/{cid}/resume")
    def resume(cid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return _state(cid, "resume", x_tenant_id, x_role, authorization, td_session)

    @router.post("/campaigns/{cid}/cancel")
    def cancel(cid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return _state(cid, "cancel", x_tenant_id, x_role, authorization, td_session)

    return router


def make_gallery_media_router():
    """GET /media/g/<key>: public on purpose, as /media/<token> -- Instagram and
    Facebook download the file from here when the post is published. The key's
    random uuid is the only way to name a file; nothing lists them."""
    router = APIRouter(tags=["marketing"])
    types = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime"}

    @router.get("/media/g/{key:path}")
    def serve(key: str, range: str | None = Header(None)):
        if not KEY_RE.match(key):
            raise HTTPException(404, "Not found.")
        store = get_storage()
        if store.name != "local":
            return RedirectResponse(store.public_url(key), status_code=302)
        try:
            path = store.path(key)
        except ValueError:
            raise HTTPException(404, "Not found.")
        if not os.path.exists(path):
            raise HTTPException(404, "Not found.")
        ctype = types[key.rsplit(".", 1)[1]]
        head = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'", "Accept-Ranges": "bytes"}
        size = os.path.getsize(path)
        m = re.match(r"^bytes=(\d*)-(\d*)$", (range or "").strip())
        if not m or (not m.group(1) and not m.group(2)):
            return FileResponse(path, media_type=ctype, headers=head)
        if m.group(1):
            start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
        else:
            start, end = max(size - int(m.group(2)), 0), size - 1
        end = min(end, size - 1)
        if start > end:
            return Response(status_code=416, headers={"Content-Range": "bytes */%d" % size})

        def chunks():
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    left -= len(b)
                    yield b
        return StreamingResponse(chunks(), status_code=206, media_type=ctype,
                                 headers=dict(head, **{"Content-Range": "bytes %d-%d/%d" % (start, end, size),
                                                       "Content-Length": str(end - start + 1)}))

    return router
