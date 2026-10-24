"""Checks every AI caption must pass, in code, after it is written. Pure
functions: the business's config is passed in, nothing is read here.

  * a price may only be one the business wrote down (services / price_list);
    any other price is taken out and the caption flagged;
  * the banned phrases come from the business's own config (medical guarantees,
    before/after promises...); a caption using one is flagged for a person;
  * no two captions alike: word 3-gram overlap with each other and with the
    last 30 published captions;
  * the script the business chose (English or Roman Urdu: no Urdu script);
  * the platforms' own limits (Instagram: 2,200 characters, 30 hashtags).
"""
from __future__ import annotations

import re

from . import settings

LIMITS = {"instagram": {"chars": 2200, "hashtags": 30},
          "facebook": {"chars": 63206, "hashtags": 30},
          "gbp": {"chars": 1500, "hashtags": 0}}
SIMILAR = 0.5                           # 3-gram Jaccard at or above this is "too similar"
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_TAG = re.compile(r"[^\w]", re.UNICODE)

FLAG_TEXT = {
    "price_removed": "A price that is not on your price list was taken out. Check the wording.",
    "price_not_listed": "Mentions a price that is not on your price list.",
    "banned_phrase": "Uses a phrase your settings do not allow (a guarantee or promise).",
    "too_similar": "Too close to another caption. Edit it or regenerate.",
    "wrong_script": "Written in a script your caption language does not use.",
    "too_long": "Longer than the platform allows.",
    "caption_failed": "Jarvis could not write this caption. Write it or regenerate.",
    "ai_unavailable": "The AI was not reachable. Write it or regenerate.",
    "vision_unavailable": "Jarvis could not look at this picture; the caption is not about what is in it.",
    "recaptioning": "Jarvis is rewriting this caption.",
    "media_ig_aspect": "Instagram will not take this picture's shape.",
    "media_ig_duration": "Instagram will not take this video's length.",
    "media_ig_codec": "Instagram will not take this video format.",
}


def flag_text(code: str) -> str:
    base = code.split(":", 1)[0]
    text = FLAG_TEXT.get(base, code)
    if ":" in code:
        text += " (%s)" % code.split(":", 1)[1]
    return text


def _price_pattern(currency: str):
    cur = re.escape(currency)
    num = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
    before = r"(?:\b(?:Rs\.?|PKR|%s)\s?|₨\s?)" % cur
    after = r"\s?(?:/-|\b(?:Rs|PKR|rupees|%s)\b)" % cur
    return re.compile(r"(%s%s)|(%s%s)" % (before, num, num, after), re.IGNORECASE)


def _value(s: str):
    m = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", s)
    return round(float(m.group(0).replace(",", "")), 2) if m else None


def strip_unknown_prices(text: str, config: dict) -> tuple[str, bool]:
    """Remove any price not in the business's own list. Returns (text, removed_any)."""
    allowed = settings.prices(config)
    pat = _price_pattern(settings.currency(config))
    removed = False

    def sub(m):
        nonlocal removed
        v = _value(m.group(0))
        if v is not None and v in allowed:
            return m.group(0)
        removed = True
        return ""
    out = pat.sub(sub, text)
    if removed:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\s+([,.!?])", r"\1", out)
        out = re.sub(r"\(\s*\)|:\s*(?=\n|$)", "", out).strip()
    return out, removed


def banned_found(text: str, config: dict) -> list[str]:
    low = " %s " % re.sub(r"\s+", " ", text.lower())
    return [p for p in settings.banned_phrases(config) if re.search(r"(?<!\w)%s(?!\w)" % re.escape(p), low)]


def wrong_script(text: str, config: dict) -> bool:
    lang = settings.language(config).lower()
    return lang not in ("ur", "urdu", "ur-pk") and bool(_ARABIC.search(text))


def shingles(text: str, n=3) -> set:
    words = re.findall(r"\w+", (text or "").lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def similarity(a: str, b: str) -> float:
    x, y = shingles(a), shingles(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def too_similar(text: str, others) -> bool:
    return any(similarity(text, o) >= SIMILAR for o in others if o)


def clean_hashtags(raw, platform: str) -> list[str]:
    out = []
    for t in raw or []:
        t = _TAG.sub("", str(t).lstrip("#"))
        if t and ("#" + t).lower() not in [x.lower() for x in out]:
            out.append("#" + t)
    return out[:LIMITS.get(platform, LIMITS["facebook"])["hashtags"]]


def full_text(caption: str, hashtags) -> str:
    return caption.strip() + (("\n\n" + " ".join(hashtags)) if hashtags else "")


def platform_limit_error(caption: str, hashtags, platform: str) -> str | None:
    lim = LIMITS.get(platform, LIMITS["facebook"])
    if len(hashtags) > lim["hashtags"]:
        return "%s allows at most %d hashtags." % (platform.capitalize(), lim["hashtags"])
    if len(full_text(caption, hashtags)) > lim["chars"]:
        return "%s allows at most %d characters including hashtags." % (platform.capitalize(), lim["chars"])
    return None


def check(caption: str, hashtags, cta: str, platform: str, config: dict, others=()) -> dict:
    """One AI caption for one platform: the cleaned text and its flags."""
    flags = []
    body = (caption or "").strip()
    if cta and cta.strip() and cta.strip().lower() not in body.lower():
        body = body + "\n\n" + cta.strip()
    body, removed = strip_unknown_prices(body, config)
    if removed:
        flags.append("price_removed")
    for p in banned_found(body, config):
        flags.append("banned_phrase:" + p)
    if wrong_script(body, config):
        flags.append("wrong_script")
    if too_similar(body, others):
        flags.append("too_similar")
    tags = clean_hashtags(hashtags, platform)
    lim = LIMITS.get(platform, LIMITS["facebook"])
    while tags and len(full_text(body, tags)) > lim["chars"]:
        tags.pop()                                          # hashtags give way before words do
    if len(full_text(body, tags)) > lim["chars"]:
        flags.append("too_long")
    if not body:
        flags.append("caption_failed")
    return {"caption": body, "hashtags": tags, "flags": flags}


def human_flags(caption: str, config: dict) -> list[str]:
    """What the checks see in a caption a person wrote: shown, never changed."""
    flags = []
    _, removed = strip_unknown_prices(caption, config)
    if removed:
        flags.append("price_not_listed")
    for p in banned_found(caption, config):
        flags.append("banned_phrase:" + p)
    return flags
