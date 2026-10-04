"""Set up my business with Jarvis (4 Oct 2026).

The owner pastes everything about the business (menu or price list, hours,
delivery rules, common questions, what they do NOT offer) or a photo of the
menu. Jarvis reads it into a draft: business type, services with prices,
opening hours, facts. The owner sees and edits the draft; nothing is saved
until they say yes (prices are what customers are told).

Never invents: every service name and every price in the draft must appear in
the owner's own text (or the text read off their photo); anything the AI
"found" that is not there is dropped. Never overwrites: existing services are
kept, duplicates skipped, a changed price is only applied when the owner ticks
it. Works without the AI for plain "name ... price" lines.
"""
from __future__ import annotations

import base64
import re

import requests

from .db import tenant_session

MAX_TEXT = 20000
DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LINE = re.compile(r"^\s*[-*•\d.)\s]*(?P<name>[A-Za-z؀-ۿ][^\n]{1,100}?)\s*(?:[-–—:|.…]+\s*|\s+)"
                  r"(?:rs\.?|pkr|₨)?\s*(?P<price>\d[\d,]*(?:\.\d+)?)\s*(?:/-|rs\.?|pkr)?\s*$", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9؀-ۿ]+", " ", str(s or "").lower()).strip()


def _nums(text):
    return {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


NEG = re.compile(r"\b(?:not|no|don'?t|dont|never|without|nahi|nahin|nai)\b", re.I)


def _source_lines(name, text):
    """The owner's lines that carry this item, never a 'we do NOT do ...' line."""
    n = _norm(name)
    words = [w for w in n.split() if len(w) >= 3]
    out = []
    for line in (text or "").splitlines():
        ln = _norm(line)
        if NEG.search(line) or not ln:
            continue
        if (n and n in ln) or (words and all(w in ln for w in words)):
            out.append(line)
    return out


def _plain_lines(text):
    """'Chicken Biryani .... Rs 450' lines, no AI needed."""
    out = []
    for line in (text or "").splitlines():
        m = LINE.match(line)
        if m:
            name = m.group("name").strip(" -–—:|.")
            try:
                price = float(m.group("price").replace(",", ""))
            except ValueError:
                continue
            if name and price > 0:
                out.append({"name": name[:120], "price": price})
    return out


def _clean(draft, text, slugs):
    """Keep only what the owner's own text supports."""
    src = _norm(text)
    dropped = []
    services, seen = [], set()
    for s in (draft.get("services") or [])[:300]:
        name = re.sub(r"\s+", " ", str((s or {}).get("name") or "")).strip()[:120]
        if not name or _norm(name) in seen:
            continue
        lines = _source_lines(name, text)
        if not lines:
            dropped.append(name)
            continue
        price = s.get("price")
        try:
            price = float(str(price).replace(",", "")) if price not in (None, "") else None
        except ValueError:
            price = None
        note = None
        if price is not None and price not in _nums("\n".join(lines)):     # on the item's own line
            price, note = None, "price not found in your text"
        seen.add(_norm(name))
        services.append({"name": name, "price": price, "category": (str(s.get("category") or "").strip()[:60] or None),
                         "duration_minutes": s.get("duration_minutes") if isinstance(s.get("duration_minutes"), int)
                         and 0 < s["duration_minutes"] < 1440 else None, "note": note})
    hours = []
    for h in (draft.get("hours") or [])[:14]:
        day = str((h or {}).get("day") or "").strip().lower()
        if day not in DAYS:
            continue
        if h.get("closed"):
            hours.append({"day": day, "closed": True, "opens": None, "closes": None})
            continue
        o, c = str(h.get("opens") or ""), str(h.get("closes") or "")
        if _HHMM.match(o) and _HHMM.match(c):
            hours.append({"day": day, "closed": False, "opens": o.zfill(5), "closes": c.zfill(5)})
    by_day = {h["day"]: h for h in hours}
    knowledge = []
    for k in (draft.get("knowledge") or [])[:60]:
        kind = str((k or {}).get("kind") or "")
        title = re.sub(r"\s+", " ", str(k.get("title") or "")).strip()[:300]
        body = str(k.get("body") or "").strip()[:2000]
        if kind not in ("faq", "policy", "not_offered", "info") or not title:
            continue
        # at least half its longer words must come from the owner's text
        words = [w for w in _norm(title + " " + body).split() if len(w) >= 4]
        if words and sum(1 for w in words if w in src) >= max(1, len(words) // 2):
            knowledge.append({"kind": kind, "title": title, "body": body})
    btype = str(draft.get("business_type") or "").strip()
    return {"business_type": btype if btype in slugs else None,
            "business_type_other": (str(draft.get("business_type_other") or "").strip()[:120] or None) if btype == "other" else None,
            "services": services, "hours": [by_day[d] for d in DAYS if d in by_day],
            "knowledge": knowledge, "dropped": dropped[:30]}


def read_text(text, slugs):
    """Owner's text -> cleaned draft. Uses the AI when it can, the plain line reader when it cannot."""
    text = (text or "").strip()[:MAX_TEXT]
    draft, how = None, "ai"
    try:
        from . import ai
        if ai.available():
            system = ("You set up a small business's records from what the owner wrote. Extract ONLY what is "
                      "written; never add, guess or translate items. Return JSON: {\"business_type\": one of "
                      + ", ".join(slugs) + " or null, \"business_type_other\": short description if other, "
                      "\"services\": [{\"name\", \"price\" (number or null), \"category\", \"duration_minutes\"}], "
                      "\"hours\": [{\"day\" (sunday..saturday), \"opens\" \"HH:MM\" 24h, \"closes\" \"HH:MM\", \"closed\" true/false}], "
                      "\"knowledge\": [{\"kind\": faq|policy|not_offered|info, \"title\", \"body\"}]}. "
                      "Services are what the business sells (dishes, treatments, courses, plans) with their price as "
                      "written. Hours: expand 'daily' or 'Mon-Sat' to each day. knowledge: delivery rules, booking "
                      "rules, location, payment methods (info/policy), common questions with their answers (faq), and "
                      "anything the owner says they do NOT offer (not_offered).")
            draft = ai.generate_json(system, text, required_keys=("services",))
    except Exception as e:
        print("[setup] AI read skipped: %s" % str(e)[:200], flush=True)
        draft = None
    if draft is None:
        how = "lines"
        draft = {"services": _plain_lines(text)}
    out = _clean(draft, text, slugs)
    out["read_with"] = how
    return out


PHOTO_ASK = ("Copy every word of this menu or price list exactly as written, one item per line with its "
             "price after it, then any hours, rules or notes. Do not add anything that is not in the picture.")


PHOTO_TIMEOUT = 30          # per model
PHOTO_BUDGET = 50           # whole chain


def _model_for(ai, p, m):
    """The model to use, worked out without touching ai's shared 'active provider'
    state (another request may be generating at the same moment)."""
    import os
    if m:
        return m
    per = os.environ.get("AI_MODEL_" + p.upper(), "").strip()
    if per:
        return per
    if p == ai.configured_provider():
        return os.environ.get("AI_MODEL") or ai.DEFAULT_MODELS.get(p, "")
    return ai.DEFAULT_MODELS.get(p, "")


def _photo_gemini(ai, key, model, b64, mime):
    r = requests.post("%s/v1beta/models/%s:generateContent" % (ai.base_url("gemini"), model),
                      params={"key": key}, timeout=PHOTO_TIMEOUT,
                      json={"contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": b64}}, {"text": PHOTO_ASK}]}],
                            "generationConfig": {"maxOutputTokens": 4000}})
    if r.status_code >= 400:
        return r.status_code, ""
    c = (r.json().get("candidates") or [{}])[0]
    return 200, "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []))


def _photo_openai(ai, key, model, b64, mime):
    """OpenAI-compatible endpoints (how this server reaches Gemini today)."""
    r = requests.post(ai.base_url("openai") + "/v1/chat/completions", timeout=PHOTO_TIMEOUT,
                      headers={"Authorization": "Bearer " + key},
                      json={"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content": [
                          {"type": "text", "text": PHOTO_ASK},
                          {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}}]}]})
    if r.status_code >= 400:
        return r.status_code, ""
    return 200, ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def photo_to_text(data, mime):
    """Read the words off a menu or price-list photo with the AI this server is set
    up with, trying each model in its chain. Raises RuntimeError with a plain reason."""
    from . import ai
    b64 = base64.b64encode(data).decode()
    calls = {"gemini": _photo_gemini, "openai": _photo_openai}
    import os, time
    codes, started = [], time.time()
    for p, m in ai.chain_entries():
        key = os.environ.get(ai._KEY_ENV.get(p, ""), "").strip()
        if p not in calls or not key:
            continue
        if time.time() - started > PHOTO_BUDGET:
            codes.append(0)
            break
        try:
            code, text = calls[p](ai, key, _model_for(ai, p, m), b64, mime)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
            print("[setup] photo via %s:%s failed: %s" % (p, m, type(e).__name__), flush=True)
            codes.append(0)
            continue
        if code == 200 and str(text).strip():
            return str(text)[:MAX_TEXT]
        print("[setup] photo via %s:%s answered %s" % (p, m, code), flush=True)
        codes.append(code)
    if not codes:
        raise RuntimeError("Reading photos needs an AI that can see pictures, and none is set up. Paste the text instead.")
    if 429 in codes:
        raise RuntimeError("Reading photos is not available on the current AI plan right now. Paste the text instead.")
    if all(c == 200 for c in codes):
        raise RuntimeError("No text was found in this photo. Try a clearer photo, or paste the text.")
    raise RuntimeError("The AI could not read this photo. Paste the text instead.")


def compare(tid, draft):
    """Mark each service: new, already listed, listed with a different price, or removed earlier."""
    with tenant_session(tid, "techdeva_admin") as cur:
        cur.execute("SELECT name, price, active FROM services WHERE tenant_id = %s", (tid,))
        have = {_norm(r["name"]): r for r in cur.fetchall()}
    for s in draft["services"]:
        h = have.get(_norm(s["name"]))
        if not h:
            s["status"] = "new"
        elif not h["active"]:
            s["status"] = "removed"         # the owner removed it; only the Services page brings it back
        elif s["price"] is not None and (h["price"] is None or float(h["price"]) != float(s["price"])):
            s["status"], s["current_price"] = "price_changed", (float(h["price"]) if h["price"] is not None else None)
        else:
            s["status"] = "listed"
    return draft
