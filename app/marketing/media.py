"""Uploading photos and videos to the gallery: what a file really is (from its
bytes, never the name or the browser's label), its size, a fingerprint so the
same file is not stored twice, and whether Instagram will take it.

Instagram's publishing API only accepts JPEG pictures, so PNG and WEBP are
converted to JPEG here, once, instead of failing on the day the post is due.
Problems Instagram would reject later (shape, length, video codec) are written
on the asset as warnings at upload, when the person can still pick another file.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile

from fastapi import HTTPException

from ..storage import get_storage, make_key
from .settings import upload_limits

FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# Instagram's own rules (platform facts, not business facts)
IG_IMAGE_RATIO = (0.8, 1.91)             # 4:5 portrait to 1.91:1 landscape
IG_VIDEO_SECONDS = (3, 15 * 60)          # Reels published through the API
IG_VIDEO_RATIO = (0.01, 10.0)
IG_VIDEO_CODECS = ("h264", "hevc")


def sniff(head: bytes) -> tuple[str, str, str] | None:
    """(kind, mime, ext) from the first bytes of a file, or None."""
    if head[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg", "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png", "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", "webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video", "video/quicktime", "mov"
        return "video", "video/mp4", "mp4"
    return None


def _image_info(path: str, mime: str) -> tuple[str, int, int]:
    """Check it opens, turn it the right way up, store it as JPEG. Returns (path, w, h)."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            if mime == "image/jpeg":
                return path, w, h
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            out = path + ".jpg"
            im.save(out, "JPEG", quality=90)
            return out, w, h
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "That picture could not be opened. Save it as JPG and try again.")


def _video_info(path: str) -> tuple[int, int, float, str]:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, timeout=60)
        info = json.loads(out.stdout or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise HTTPException(400, "That video could not be read. Save it as MP4 and try again.")
    v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    if not v:
        raise HTTPException(400, "That file has no video in it.")
    try:
        dur = float((info.get("format") or {}).get("duration") or v.get("duration") or 0)
    except ValueError:
        dur = 0.0
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = str((v.get("tags") or {}).get("rotate") or "")
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = str(sd["rotation"])
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    return w, h, round(dur, 2), str(v.get("codec_name") or "")


def instagram_warnings(kind: str, w: int, h: int, seconds: float | None = None, codec: str = "") -> list[str]:
    out = []
    ratio = (w / h) if w and h else 0
    if kind == "image":
        if not (IG_IMAGE_RATIO[0] - 0.005 <= ratio <= IG_IMAGE_RATIO[1] + 0.005):
            out.append("ig_aspect")
    else:
        if seconds is not None and not (IG_VIDEO_SECONDS[0] <= seconds <= IG_VIDEO_SECONDS[1]):
            out.append("ig_duration")
        if not (IG_VIDEO_RATIO[0] <= ratio <= IG_VIDEO_RATIO[1]):
            out.append("ig_aspect")
        if codec and codec not in IG_VIDEO_CODECS:
            out.append("ig_codec")
    return out


WARNING_TEXT = {
    "ig_aspect": "Instagram will not take this shape (pictures between 4:5 tall and 1.91:1 wide).",
    "ig_duration": "Instagram Reels must be 3 seconds to 15 minutes long.",
    "ig_codec": "Instagram needs H.264 video; export it again as MP4.",
}


async def save_upload(cur, tid: str, uid: str | None, upload, config: dict) -> dict:
    """Store one uploaded file. Returns the asset row, or raises HTTPException with a reason."""
    limits = upload_limits(config)
    name = os.path.basename(upload.filename or "file")[:200]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".up")
    paths = [tmp.name]
    try:
        h = hashlib.sha256()
        size, head = 0, b""
        cap = max(limits["max_image_mb"], limits["max_video_mb"]) * 1024 * 1024
        while True:
            chunk = await upload.read(1 << 20)
            if not chunk:
                break
            if not head:
                head = chunk[:32]
            size += len(chunk)
            if size > cap:
                raise HTTPException(413, "%s is larger than %d MB." % (name, limits["max_video_mb"]))
            h.update(chunk)
            tmp.write(chunk)
        tmp.close()
        if size == 0:
            raise HTTPException(400, "%s arrived empty." % name)
        kind_mime = sniff(head)
        if not kind_mime:
            raise HTTPException(415, "%s is not a JPG, PNG, WEBP, MP4 or MOV file." % name)
        kind, mime, ext = kind_mime
        if kind == "image" and size > limits["max_image_mb"] * 1024 * 1024:
            raise HTTPException(413, "%s is larger than %d MB." % (name, limits["max_image_mb"]))
        digest = h.hexdigest()
        cur.execute("SELECT id::text, original_name FROM media_assets WHERE sha256 = %s", (digest,))
        dup = cur.fetchone()
        if dup:
            raise HTTPException(409, "%s is already in your gallery%s." % (
                name, " as %s" % dup["original_name"] if dup["original_name"] and dup["original_name"] != name else ""))
        seconds, codec = None, ""
        if kind == "image":
            stored, w, hgt = _image_info(tmp.name, mime)
            if stored != tmp.name:
                paths.append(stored)
                mime, ext = "image/jpeg", "jpg"
        else:
            stored = tmp.name
            w, hgt, seconds, codec = _video_info(tmp.name)
            if seconds > limits["max_video_s"]:
                raise HTTPException(400, "%s is %d seconds; videos can be up to %d seconds."
                                    % (name, seconds, limits["max_video_s"]))
        warnings = instagram_warnings(kind, w, hgt, seconds, codec)
        key = make_key(tid, ext)
        store = get_storage()
        store.put(key, stored, mime)
        cur.execute("""INSERT INTO media_assets (tenant_id, storage_key, public_url, original_name, mime_type, kind,
                                                 width, height, duration_s, bytes, sha256, warnings, created_by)
                       VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       RETURNING *""",
                    (key, store.public_url(key), name, mime, kind, w, hgt, seconds, os.path.getsize(stored),
                     digest, json.dumps(warnings), uid))
        return asset_view(cur.fetchone())
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass


def asset_view(r) -> dict:
    d = {k: r[k] for k in ("storage_key", "public_url", "original_name", "mime_type", "kind", "width", "height",
                           "bytes", "service_tag")}
    d["id"] = str(r["id"])
    d["duration_s"] = float(r["duration_s"]) if r["duration_s"] is not None else None
    d["warnings"] = [{"code": w, "text": WARNING_TEXT.get(w, w)} for w in (r["warnings"] or [])]
    d["vision_summary"] = r["vision_summary"]
    d["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
    return d
