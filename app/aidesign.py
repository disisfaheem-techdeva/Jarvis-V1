"""AI pictures for posts ("a woman getting a HydraFacial, calm spa light").

The image model makes the picture only. Words, prices and the logo are laid on
top afterwards by the Posts page designer (Part 23), because image models
misspell text and must never invent a price or a claim. The prompt therefore
asks for no text at all, and for calm space where the designer's band goes.

Uses the Gemini key the platform already has (GEMINI_API_KEY); models from
GEMINI_IMAGE_MODELS, tried in order. Each picture costs a few US cents, so a
business may make AI_PICTURES_PER_DAY a day (default 20). The picture goes back
to the browser; it is stored only when the owner keeps it (as any picture)."""
import os
import time

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_IMAGE_MODELS", "gemini-2.5-flash-image,gemini-3-pro-image-preview").split(",") if m.strip()]
PER_DAY = int(os.environ.get("AI_PICTURES_PER_DAY", "20"))
TIMEOUT = int(os.environ.get("AI_PICTURE_TIMEOUT", "90"))
RATIOS = {"square": "1:1", "portrait": "4:5", "story": "9:16", "landscape": "16:9"}
SPACE = {"top": "the top quarter", "center": "the middle", "bottom": "the bottom quarter"}


def _prompt(biz, industry, idea, text_at, colour):
    kind = (industry or "").replace("_", " ").strip() or "local business"
    return (
        "Create one high-quality, realistic photograph for a social media post by %s, a %s in Pakistan.\n"
        "The picture shows: %s.\n"
        "Style: bright, clean, premium and professional; natural light; natural skin tones; people (if any) look "
        "South Asian and are modestly dressed; tidy modern setting.\n"
        "Keep %s of the picture calm and simple, without important detail, because a text band goes there.\n"
        "Use small touches of the colour %s where it fits naturally.\n"
        "Absolutely no text, letters, numbers, words, signs, logos or watermarks anywhere in the picture.\n"
        "No before-and-after comparison, no medical claims, nothing graphic."
        % (biz, kind, idea, SPACE.get(text_at, "the bottom quarter"), colour))


def _generate(prompt, ratio):
    from .ai import base_url
    from .platform_key import gemini_key
    key = gemini_key()
    if not key:
        raise HTTPException(503, "The AI picture maker is not set up on this server.")
    from . import usage
    why = usage.check()
    if why:
        raise HTTPException(402, why)
    tried, refused = [], False

    def why(r):
        try:
            return str(((r.json() or {}).get("error") or {}).get("message") or "")[:140]
        except ValueError:
            return ""
    started = time.time()
    for m in MODELS:
        if time.time() - started > TIMEOUT:
            break
        for cfg in ({"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": ratio}},
                    {"responseModalities": ["TEXT", "IMAGE"]}):         # models that refuse the first form
            try:
                r = requests.post(f'{base_url("gemini")}/v1beta/models/{m}:generateContent', params={"key": key},
                                  json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg},
                                  timeout=(5, TIMEOUT))
            except requests.RequestException as e:
                tried.append("%s: %s" % (m, type(e).__name__))
                break
            if r.status_code == 400:
                tried.append("%s 400 %s" % (m, why(r)))
                continue
            if r.status_code >= 400:
                tried.append("%s %s %s" % (m, r.status_code, why(r)))
                break
            try:
                cand = (r.json().get("candidates") or [{}])[0]
            except ValueError:
                tried.append("%s: unreadable answer" % m)
                break
            for p in (cand.get("content") or {}).get("parts") or []:
                d = p.get("inlineData") or p.get("inline_data") or {}
                mime = d.get("mimeType") or d.get("mime_type") or ""
                if mime.startswith("image/") and d.get("data"):
                    usage.charge("picture", True, provider="gemini", model=m, ms=(time.time() - started) * 1000)
                    return mime, d["data"], m
            refused = refused or cand.get("finishReason") in ("SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "OTHER")
            tried.append("%s: no picture (%s)" % (m, cand.get("finishReason") or "empty"))
            break
    if refused:
        raise HTTPException(422, "The AI would not make that picture. Describe it differently (for example the "
                                 "treatment room, the product, or a happy client) and try again.")
    print("[aidesign] no picture: %s" % " | ".join(tried), flush=True)
    if tried and all(" 429 " in t for t in tried):
        raise HTTPException(503, "The AI picture maker has used up its allowance on the Gemini key for now. "
                                 "Try again later; TechDeva has been told.")
    raise HTTPException(503, "The AI picture maker is not available right now. Try again in a minute.")


def make_aidesign_router():
    router = APIRouter(prefix="/social", tags=["social"])

    @router.post("/ai-picture")
    def ai_picture(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        from .social import _ident, _owner_only
        tid, role, uid = _ident(x_tenant_id, x_role, authorization, td_session)
        _owner_only(role)
        idea = " ".join(str(body.get("idea") or "").split())[:400]
        if len(idea) < 4:
            raise HTTPException(400, "Describe the picture in a few words.")
        size = str(body.get("size") or "square")
        colour = str(body.get("colour") or "")
        colour = colour if len(colour) == 7 and colour.startswith("#") else "the brand colour"
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT name, industry FROM tenants WHERE id = current_tenant()")
            t = cur.fetchone() or {}
            cur.execute("SELECT count(*) AS n FROM executions WHERE action = 'social.ai_picture' AND status = 'ok' "
                        "AND at >= date_trunc('day', now() AT TIME ZONE 'Asia/Karachi') AT TIME ZONE 'Asia/Karachi'")
            used = int(cur.fetchone()["n"])
        if used >= PER_DAY:
            raise HTTPException(429, "That is today's %d AI pictures. More tomorrow; you can still design with "
                                     "your own photos." % PER_DAY)
        from .trialcap import TRIAL_AI_PICTURES, is_trial
        with tenant_session(None, "techdeva_admin") as cur:
            trial = is_trial(cur, tid)
            if trial:
                cur.execute("SELECT count(*) AS n FROM executions WHERE tenant_id::text = %s "
                            "AND action = 'social.ai_picture' AND status = 'ok'", (str(tid),))
                trial_used = int(cur.fetchone()["n"])
        if trial and trial_used >= TRIAL_AI_PICTURES:
            raise HTTPException(429, "Your free trial includes %d AI pictures and they are used. Choose a plan on "
                                     "Plan & billing for %d a day; you can still design with your own photos."
                                % (TRIAL_AI_PICTURES, PER_DAY))
        mime, data, model = _generate(_prompt(t.get("name") or "our business", t.get("industry"), idea,
                                              str(body.get("text_at") or "bottom"), colour),
                                      RATIOS.get(size, "1:1"))
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(502, "The AI sent a picture that is too large. Try again.")
        with tenant_session(tid, role) as cur:
            record(cur, action="social.ai_picture", resource="social_media", resource_id=None, actor=str(uid),
                   actor_type="human", status="ok", detail={"idea": idea[:200], "size": size, "model": model},
                   error=None)
        left = max(PER_DAY - used - 1, 0)
        if trial:
            left = min(left, max(TRIAL_AI_PICTURES - trial_used - 1, 0))
        return {"image": "data:%s;base64,%s" % (mime, data), "left_today": left, "trial": trial}

    return router
