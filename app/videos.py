"""Videos for posts: a Facebook video and an Instagram Reel.

The owner uploads a clip from the Posts page. It is checked with ffprobe
(Instagram takes MP4 with H.264 or HEVC video, 3 seconds to 15 minutes), made
"fast start" so Meta can stream it, and kept on the media volume, never in the
database. Trimming cuts without re-encoding (`-c copy`), so it costs the server
almost no processor time; the cut lands on the nearest key frame, which can
be a second or so from the exact point.

Meta fetches the file from GET /media/v/<token>.mp4, public on purpose like the
pictures: the 160-bit token is the only way to name a video.

Uploaded videos nobody posted are removed after 2 days, posted ones after 30
(Meta keeps its own copy), so the volume does not fill up."""
import json
import os
import re
import secrets
import subprocess
import time

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

MEDIA_DIR = os.environ.get("MEDIA_DIR", "/srv/media")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
VIDEO_MAX = 100 * 1024 * 1024          # per video
TENANT_MAX = 2 * 1024 * 1024 * 1024    # all of one business's videos together
MIN_SECONDS, MAX_SECONDS = 3, 900      # Instagram Reels: 3 s to 15 min
_TOKEN = re.compile(r"^[0-9a-f]{40}$")
_CLEANED = [0.0]


def _dir():
    d = os.path.join(MEDIA_DIR, "v")
    os.makedirs(d, exist_ok=True)
    return d


def video_path(token):
    return os.path.join(_dir(), token + ".mp4")


def video_url(token):
    from .social import PUBLIC_BASE
    return "%s/media/v/%s.mp4" % (PUBLIC_BASE, token)


def token_from_url(url):
    """The token of a video link this platform issued, else None."""
    m = re.search(r"/media/v/([0-9a-f]{40})\.mp4$", str(url or "").strip())
    return m.group(1) if m else None


def probe(path):
    """Length, size and codecs, or an HTTPException a person can act on."""
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, timeout=60)
        info = json.loads(out.stdout or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise HTTPException(400, "That video could not be read. Save it as MP4 and try again.")
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise HTTPException(400, "That file has no video in it.")
    if v.get("codec_name") not in ("h264", "hevc"):
        raise HTTPException(400, "Instagram needs MP4 video in H.264 (most phones record this). "
                                 "This one is %s: export it again as MP4." % (v.get("codec_name") or "unknown"))
    try:
        dur = float((info.get("format") or {}).get("duration") or v.get("duration") or 0)
    except ValueError:
        dur = 0
    if dur < MIN_SECONDS:
        raise HTTPException(400, "The video must be at least %d seconds long." % MIN_SECONDS)
    if dur > MAX_SECONDS:
        raise HTTPException(400, "The video must be 15 minutes or shorter. Trim it first.")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = str((v.get("tags") or {}).get("rotate") or "")
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = str(sd["rotation"])
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    return {"duration": round(dur, 2), "width": w, "height": h}


def _ffmpeg(args, timeout=300):
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(500, "The video could not be prepared. Try again.")
    if r.returncode != 0:
        raise HTTPException(400, "The video could not be prepared: %s" % r.stderr.decode(errors="replace")[-200:])


def _save(cur, tid, uid, src_path, meta):
    """Move a checked file into place and record it."""
    token = secrets.token_hex(20)
    size = os.path.getsize(src_path)
    os.replace(src_path, video_path(token))
    cur.execute("INSERT INTO social_videos (tenant_id, token, byte_size, duration, width, height, created_by) "
                "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s) RETURNING id",
                (token, size, meta["duration"], meta["width"], meta["height"], str(uid) if uid else None))
    vid = cur.fetchone()["id"]
    return token, vid, size


def _out(token, meta, size):
    return {"token": token, "url": video_url(token), "duration": meta["duration"],
            "width": meta["width"], "height": meta["height"], "bytes": size}


def owns_video(cur, token):
    cur.execute("SELECT 1 FROM social_videos WHERE token = %s", (token,))
    return cur.fetchone() is not None


def cleanup(force=False):
    """Hourly from the scheduler: remove videos nobody needs any more."""
    if not force and time.time() - _CLEANED[0] < 3600:
        return 0
    _CLEANED[0] = time.time()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""DELETE FROM social_videos v
                        WHERE NOT v.in_library
                          AND NOT EXISTS (SELECT 1 FROM social_posts p WHERE p.video_token = v.token
                                             AND p.status IN ('draft','scheduled','publishing','failed'))
                          AND (v.created_at < now() - interval '30 days'
                               OR (v.created_at < now() - interval '2 days'
                                   AND NOT EXISTS (SELECT 1 FROM social_posts p WHERE p.video_token = v.token)))
                        RETURNING token""")
        gone = [r["token"] for r in cur.fetchall()]
    for t in gone:
        try:
            os.remove(video_path(t))
        except OSError:
            pass
    return len(gone)


def make_video_router():
    router = APIRouter(prefix="/social", tags=["social"])

    def who(x_tenant_id, x_role, authorization, td_session):
        from .social import _ident, _owner_only
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        _owner_only(role)
        return tid, role, uid

    @router.post("/video")
    async def upload(request: Request, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The raw file as the request body (Content-Type video/mp4 or video/quicktime)."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in ("video/mp4", "video/quicktime"):
            raise HTTPException(415, "Choose an MP4 or MOV video.")
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > VIDEO_MAX:
            raise HTTPException(413, "That video is larger than 100 MB. Trim or compress it first.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT coalesce(sum(byte_size), 0) AS s FROM social_videos")
            if int(cur.fetchone()["s"]) + max(declared, 0) > TENANT_MAX:
                raise HTTPException(413, "Your saved videos are using all their space (2 GB). "
                                         "Post or discard some drafts first.")
        raw = os.path.join(_dir(), "up-" + secrets.token_hex(8))
        fixed = raw + ".mp4"
        size = 0
        try:
            with open(raw, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > VIDEO_MAX:
                        raise HTTPException(413, "That video is larger than 100 MB. Trim or compress it first.")
                    f.write(chunk)
            if size == 0:
                raise HTTPException(400, "The video arrived empty. Try again.")
            probe(raw)
            # MP4 with the index at the front, so Meta can start reading at once
            _ffmpeg(["-i", raw, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", fixed])
            meta = probe(fixed)
            with tenant_session(tid, role) as cur:
                token, vid, n = _save(cur, tid, uid, fixed, meta)
                record(cur, action="social.video", resource="social_videos", resource_id=str(vid), actor=str(uid),
                       actor_type="human", status="ok", detail={"bytes": n, "seconds": meta["duration"]}, error=None)
            return _out(token, meta, n)
        finally:
            for p in (raw, fixed):
                try:
                    os.remove(p)
                except OSError:
                    pass

    @router.post("/video/{token}/trim")
    def trim(token: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None), authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Keep the part between start and end (seconds). A new video; the original stays."""
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        if not _TOKEN.match(token):
            raise HTTPException(404, "That video is not there.")
        try:
            start, end = float(body.get("start") or 0), float(body.get("end") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Give the start and end in seconds.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT duration FROM social_videos WHERE token = %s", (token,))
            row = cur.fetchone()
        if not row or not os.path.exists(video_path(token)):
            raise HTTPException(404, "That video is not there.")
        dur = float(row["duration"])
        end = min(end or dur, dur)
        if start < 0 or end - start < MIN_SECONDS:
            raise HTTPException(400, "Keep at least %d seconds: the end must be after the start." % MIN_SECONDS)
        out = os.path.join(_dir(), "cut-" + secrets.token_hex(8) + ".mp4")
        try:
            _ffmpeg(["-ss", "%.2f" % start, "-to", "%.2f" % end, "-i", video_path(token), "-map", "0:v:0", "-map", "0:a:0?",
                     "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out])
            meta = probe(out)
            with tenant_session(tid, role) as cur:
                new, vid, n = _save(cur, tid, uid, out, meta)
                record(cur, action="social.video_trim", resource="social_videos", resource_id=str(vid), actor=str(uid),
                       actor_type="human", status="ok", detail={"from": token[:8], "start": start, "end": end}, error=None)
            return _out(new, meta, n)
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    return router


def make_video_media_router():
    router = APIRouter(tags=["social"])

    @router.get("/media/v/{name}")
    def media(name: str, range: str | None = Header(None)):
        """Whole file, or the byte range a video player asks for (seeking)."""
        token = name[:-4] if name.endswith(".mp4") else name
        if not _TOKEN.match(token) or not os.path.exists(video_path(token)):
            raise HTTPException(404, "Not found.")
        path, size = video_path(token), os.path.getsize(video_path(token))
        head = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes"}
        m = re.match(r"^bytes=(\d*)-(\d*)$", (range or "").strip())
        if not m or (not m.group(1) and not m.group(2)):
            return FileResponse(path, media_type="video/mp4", headers=head)
        if m.group(1):
            start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
        else:                                   # "bytes=-500": the last 500 bytes
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
        return StreamingResponse(chunks(), status_code=206, media_type="video/mp4",
                                 headers=dict(head, **{"Content-Range": "bytes %d-%d/%d" % (start, end, size),
                                                       "Content-Length": str(end - start + 1)}))

    return router
