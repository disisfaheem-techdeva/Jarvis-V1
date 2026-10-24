"""Publishing one gallery post to a Facebook Page or its Instagram account.

    publish(platform, page, media, caption) -> Outcome
    validate(page)                          -> {"ok": bool, "message": str}

Modes, the same shape as hermes_client:
  stub      no live Meta app here (META_PUBLISH_MODE=stub, or META_APP_ID unset):
            nothing is sent. The returned id starts with "stub:" so a stub post
            can never pass for a real one; the dashboard labels it "test mode".
  live      the Graph API with the business's own Page token (from integrations).
  degraded  live, but Meta could not be reached: reported as a transient error,
            retried with back-off by the caller, never as a success.

Every failure carries a kind the caller acts on:
  transient  network, 5xx, rate limits          -> retry (up to 3 attempts)
  auth       expired / revoked / missing token  -> stop: pause the campaign, ask for reconnect
  media      Meta refused the picture, video or text -> this post fails with Meta's own words
  config     no Instagram account on the Page, GBP not switched on -> this post fails

The Graph version lives in one place: META_GRAPH_VERSION (default v21.0, as social.py).
Google Business Profile: interface and stub only, behind GBP_PUBLISH_ENABLED; there is no live path.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

import requests

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v21.0")
GRAPH = "https://graph.facebook.com/" + GRAPH_VERSION
TIMEOUT = float(os.environ.get("META_TIMEOUT", "40"))
IG_POLL_SECONDS = {"image": int(os.environ.get("IG_POLL_IMAGE_S", "60")),
                   "video": int(os.environ.get("IG_POLL_VIDEO_S", "600"))}
AUTH_CODES = {102, 190, 200, 10, 459, 460, 463, 464, 467}          # token/permission problems
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80001, 80004}   # Meta busy or rate limits


@dataclass
class Outcome:
    ok: bool
    external_id: str | None = None
    kind: str | None = None           # transient | auth | media | config (when not ok)
    message: str = ""
    detail: dict = field(default_factory=dict)


def mode() -> str:
    m = os.environ.get("META_PUBLISH_MODE", "").strip().lower()
    if m in ("stub", "live"):
        return m
    return "live" if os.environ.get("META_APP_ID") else "stub"


class GraphError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


def _classify(status: int, body: dict) -> GraphError:
    e = (body or {}).get("error") or {}
    code = e.get("code")
    msg = e.get("error_user_msg") or e.get("message") or "Meta answered %s" % status
    tag = "/".join(str(x) for x in (code, e.get("error_subcode")) if x)
    text = (msg + (" (code %s)" % tag if tag else ""))[:300]
    if status == 401 or code in AUTH_CODES or e.get("type") == "OAuthException" and code in (None, 190):
        return GraphError("auth", text)
    if status >= 500 or status == 429 or code in TRANSIENT_CODES or e.get("is_transient"):
        return GraphError("transient", text)
    return GraphError("media", text)


def _call(method, path, data=None, params=None) -> dict:
    try:
        r = requests.request(method, GRAPH + path, data=data, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GraphError("transient", "Meta could not be reached: %s" % e.__class__.__name__)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        raise _classify(r.status_code, body)
    return body


def _wait_ig(container, tok, kind):
    end = time.time() + IG_POLL_SECONDS[kind]
    while True:
        body = _call("GET", "/" + str(container), params={"fields": "status_code,status", "access_token": tok})
        st = body.get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise GraphError("media", "Instagram could not use this %s: %s" % (kind, body.get("status") or st))
        if time.time() > end:
            raise GraphError("transient", "Instagram is still processing the %s; it will be tried again." % kind)
        time.sleep(2 if kind == "image" else 5)


def _facebook(page, media, caption):
    tok = page["access_token"]
    if media["kind"] == "video":
        body = _call("POST", "/%s/videos" % page["id"], {"file_url": media["url"], "description": caption, "access_token": tok})
        return str(body.get("id"))
    body = _call("POST", "/%s/photos" % page["id"], {"url": media["url"], "caption": caption, "access_token": tok})
    return str(body.get("post_id") or body.get("id"))


def _instagram(page, media, caption):
    ig = page.get("ig_id")
    if not ig:
        raise GraphError("config", "No Instagram business account is linked to the Facebook Page %s." % page.get("name"))
    tok = page["access_token"]
    if media["kind"] == "video":
        c = _call("POST", "/%s/media" % ig, {"media_type": "REELS", "video_url": media["url"], "caption": caption,
                                             "share_to_feed": "true", "access_token": tok})
    else:
        c = _call("POST", "/%s/media" % ig, {"image_url": media["url"], "caption": caption, "access_token": tok})
    _wait_ig(c.get("id"), tok, media["kind"])
    body = _call("POST", "/%s/media_publish" % ig, {"creation_id": c.get("id"), "access_token": tok})
    return str(body.get("id"))


def _stub_id(platform, media, caption):
    return "stub:%s:%s" % (platform, hashlib.sha1((media.get("url", "") + caption).encode()).hexdigest()[:16])


# tests replace this to make the stub fail in a chosen way: {"platform": GraphError(...)}
STUB_FAILURES: dict = {}


def publish(platform: str, page: dict | None, media: dict, caption: str) -> Outcome:
    """media = {"url": public address, "kind": "image"|"video"}. Never raises."""
    try:
        if platform == "gbp":
            if os.environ.get("GBP_PUBLISH_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
                raise GraphError("config", "Google Business Profile posting is not switched on.")
            return Outcome(True, "stub:gbp:" + _stub_id("gbp", media, caption)[-16:], message="GBP is stub-only")
        if mode() == "stub":
            fail = STUB_FAILURES.get(platform)
            if fail:
                raise fail
            return Outcome(True, _stub_id(platform, media, caption), message="stub")
        if not page or not page.get("access_token"):
            raise GraphError("auth", "Facebook is not connected for this business.")
        ext = _facebook(page, media, caption) if platform == "facebook" else _instagram(page, media, caption)
        if not ext or ext == "None":
            raise GraphError("transient", "Meta did not return a post id.")
        return Outcome(True, ext)
    except GraphError as e:
        return Outcome(False, kind=e.kind, message=e.message)
    except Exception as e:                    # a bug here must never look like a success
        return Outcome(False, kind="transient", message="%s while publishing" % e.__class__.__name__)


def validate(page: dict | None) -> dict:
    """Is the Page connection usable right now (before a paused campaign resumes)?"""
    if mode() == "stub":
        fail = STUB_FAILURES.get("validate")
        return {"ok": not fail, "message": getattr(fail, "message", "") or "stub"}
    if not page or not page.get("access_token"):
        return {"ok": False, "message": "Facebook is not connected. Reconnect it, then resume."}
    try:
        _call("GET", "/%s" % page["id"], params={"fields": "id,name", "access_token": page["access_token"]})
        return {"ok": True, "message": ""}
    except GraphError as e:
        if e.kind == "auth":
            return {"ok": False, "message": "The Facebook connection has expired: %s" % e.message}
        return {"ok": False, "message": "Meta could not be reached to check the connection. Try again shortly."}
