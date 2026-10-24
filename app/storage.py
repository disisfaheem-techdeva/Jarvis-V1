"""Where uploaded marketing media lives: local disk in development, any
S3-compatible bucket in production. One small interface, two implementations.

    put(key, src_path, content_type)   store a file under a key
    public_url(key)                     the address Facebook and Instagram fetch it from
    path(key)                           local file to read (local backend only)
    delete(key)

Keys are always tenant-prefixed: tenants/{tenant_id}/marketing/{yyyy}/{mm}/{uuid}.{ext}
(make_key). The uuid is random (122 bits) and nothing lists the files, so a
media address is public-by-link, the same rule /media/<token> already follows
for post pictures: Instagram downloads the file from the address at the moment
it publishes, which can be weeks after upload, so a short-lived signed URL
would expire before the post goes out. A public path with an unguessable name
was chosen over signed URLs for that reason.

STORAGE_BACKEND=local (default): files under MEDIA_DIR/<key>, served by
GET /media/g/<key> on this API (PUBLIC_BASE). STORAGE_BACKEND=s3: S3_BUCKET,
S3_ENDPOINT (optional, for R2/Spaces/MinIO), S3_REGION, S3_PUBLIC_BASE (the
bucket's public address; the bucket must allow public reads of tenants/*).
Credentials come from the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
boto3 is imported only when the s3 backend is used.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import uuid

EXTS = ("jpg", "png", "webp", "mp4", "mov")
KEY_RE = re.compile(r"^tenants/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/marketing/"
                    r"(\d{4})/(\d{2})/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(%s)$" % "|".join(EXTS))
PUBLIC_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "https://api.techdeva.net").strip().rstrip("/")


def make_key(tenant_id: str, ext: str, now: dt.datetime | None = None) -> str:
    tid = str(uuid.UUID(str(tenant_id)))
    if ext not in EXTS:
        raise ValueError("unsupported extension %r" % ext)
    now = now or dt.datetime.now(dt.timezone.utc)
    return "tenants/%s/marketing/%04d/%02d/%s.%s" % (tid, now.year, now.month, uuid.uuid4(), ext)


def tenant_of(key: str) -> str | None:
    m = KEY_RE.match(key or "")
    return m.group(1) if m else None


class LocalStorage:
    name = "local"

    def __init__(self, root: str | None = None, public_base: str | None = None):
        self.root = os.path.abspath(root or os.path.join(os.environ.get("MEDIA_DIR", "/srv/media"), "gallery"))
        self.base = (public_base or PUBLIC_BASE).rstrip("/")

    def path(self, key: str) -> str:
        if not KEY_RE.match(key):
            raise ValueError("not a media key")
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep):
            raise ValueError("not a media key")
        return p

    def put(self, key: str, src_path: str, content_type: str) -> None:
        dst = self.path(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src_path, dst)

    def public_url(self, key: str) -> str:
        self.path(key)
        return "%s/media/g/%s" % (self.base, key)

    def delete(self, key: str) -> None:
        try:
            os.remove(self.path(key))
        except FileNotFoundError:
            pass


class S3Storage:
    name = "s3"

    def __init__(self, client=None):
        self.bucket = os.environ.get("S3_BUCKET", "")
        self.base = os.environ.get("S3_PUBLIC_BASE", "").rstrip("/")
        if not self.bucket or not self.base:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET and S3_PUBLIC_BASE")
        if client is None:
            import boto3                                   # only needed in production
            client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT") or None,
                                  region_name=os.environ.get("S3_REGION") or None)
        self.client = client

    def path(self, key: str) -> str:
        raise RuntimeError("S3 media has no local path; read it from public_url")

    def put(self, key: str, src_path: str, content_type: str) -> None:
        if not KEY_RE.match(key):
            raise ValueError("not a media key")
        with open(src_path, "rb") as f:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=f, ContentType=content_type,
                                   CacheControl="public, max-age=31536000, immutable")

    def public_url(self, key: str) -> str:
        if not KEY_RE.match(key):
            raise ValueError("not a media key")
        return "%s/%s" % (self.base, key)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_STORE = None


def get_storage():
    global _STORE
    if _STORE is None:
        _STORE = S3Storage() if os.environ.get("STORAGE_BACKEND", "local").strip().lower() == "s3" else LocalStorage()
    return _STORE


def set_storage(store) -> None:
    """Tests point the app at a temporary folder."""
    global _STORE
    _STORE = store
