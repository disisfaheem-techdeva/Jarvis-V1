"""Jarvis reads the business's own Facebook page and website (5 Oct 2026).

The Growth analyzer already read a client's website and Facebook page, but only
for marketing (followers, posts, ads); nothing it saw reached the dashboard.
This reads the same sources for what the business IS and SELLS: the page's
category, about text, hours, phone and address, and the website's menu,
price, service, about and contact pages. That text goes through the same
reader as "Set up my business with Jarvis" (setup_reader.read_text: nothing
that is not in the text survives), and the result is kept as a suggestion.

The owner sees "Jarvis read your page and found ..." with the usual preview and
presses Save; nothing reaches the business's records before that. A business
is read again only when its sources change (a page connected, a website
added), never on a loop. Read only: nothing is posted to Facebook or the site.
Businesses whose WhatsApp agent is one of the protected hand-built ones (the
clinic) are skipped, as in the reply checker.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .db import tenant_session
from .safe_http import get as safe_get, BlockedURL, normalise

EVERY_S = 600
PER_TICK = 3                  # businesses read per pass (each is a few web requests)
FAILED_RETRY_S = 6 * 3600
GRAPH = "https://graph.facebook.com/v19.0"
UA = "Mozilla/5.0 (compatible; TechDevaSetupReader/1.0; +https://techdeva.net/bot)"
PAGE_TEXT = 8000              # characters kept per web page
MAX_PAGES = 4                 # home + 3 likely pages
LIKELY = re.compile(r"menu|price|pricing|rates|service|services|treatment|course|package|about|contact|hours|timing", re.I)
DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
        "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_last = 0.0


# ------------------------------------------------------------------ sources
def sources(cur, tid):
    """What we may read for this business: its connected Facebook pages and the
    website it gave us (ad autopilot settings or its signup). Admin cursor."""
    from .crypto import decrypt_json
    pages, sites = [], []
    cur.execute("""SELECT credentials FROM integrations
                    WHERE tenant_id = %s AND provider = 'meta' AND status = 'connected' LIMIT 1""", (tid,))
    r = cur.fetchone()
    if r:
        try:
            creds = decrypt_json(r["credentials"], tid, "meta")
            pages = [{"id": str(p["id"]), "token": p["access_token"]} for p in (creds.get("pages") or [])
                     if p.get("id") and p.get("access_token")][:3]
        except Exception as e:                       # never a guessed token
            print("[pagereader] %s meta credentials unreadable: %s" % (tid, type(e).__name__), flush=True)
    cur.execute("SELECT website_url FROM ad_pilot_settings WHERE tenant_id = %s", (tid,))
    sites += [x["website_url"] for x in cur.fetchall()]
    cur.execute("SELECT website_url FROM signups WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1", (tid,))
    sites += [x["website_url"] for x in cur.fetchall()]
    # only real addresses (a host with a dot): "http@cozzytaste" was stored as one
    sites = list(dict.fromkeys(u for u in (normalise(s) for s in sites)
                               if u and re.match(r"^https?://[a-z0-9.-]+\.[a-z]{2,}(?:[:/?#]|$)", u, re.I)))[:2]
    return pages, sites


def fingerprint(pages, sites):
    refs = sorted(["fb:" + p["id"] for p in pages] + ["web:" + s.lower().rstrip("/") for s in sites])
    return hashlib.sha256("|".join(refs).encode()).hexdigest()[:24] if refs else ""


# ------------------------------------------------------------------ readers
def _fb_hours(h):
    """{'mon_1_open': '09:00', 'mon_1_close': '22:00', ...} -> 'Monday 09:00 to 22:00' lines.
    Days the page does not list are left out (unknown, not 'closed')."""
    out = []
    for k, day in DAYS.items():
        spans = []
        for n in (1, 2):
            o, c = h.get("%s_%d_open" % (k, n)), h.get("%s_%d_close" % (k, n))
            if o and c:
                spans.append("%s to %s" % (o, c))
        if spans:
            out.append("%s %s" % (day, ", ".join(spans)))
    return out


def read_facebook(page):
    """The page's own description of the business, with its own page token."""
    fields = "name,category,category_list{name},about,description,hours,phone,single_line_address,website,price_range"
    try:
        r = requests.get("%s/%s" % (GRAPH, page["id"]), params={"fields": fields, "access_token": page["token"]},
                         timeout=15)
    except requests.RequestException as e:
        return {"ok": False, "note": "Facebook could not be reached (%s)" % type(e).__name__}
    if r.status_code >= 400:
        return {"ok": False, "note": "Facebook answered %s" % r.status_code}
    d = r.json()
    cats = [c.get("name") for c in (d.get("category_list") or {}).get("data", []) if c.get("name")] if isinstance(d.get("category_list"), dict) \
        else [c.get("name") for c in (d.get("category_list") or []) if isinstance(c, dict) and c.get("name")]
    lines = ["Facebook page: %s" % (d.get("name") or "")]
    if d.get("category") or cats:
        lines.append("Business category: %s" % ", ".join(dict.fromkeys([x for x in [d.get("category")] + cats if x])))
    for key, label in (("about", "About"), ("description", "Description"), ("phone", "Phone"),
                       ("single_line_address", "Address"), ("website", "Website"), ("price_range", "Price range")):
        if d.get(key):
            lines.append("%s: %s" % (label, str(d[key]).strip()))
    hrs = _fb_hours(d.get("hours") or {})
    if hrs:
        lines.append("Opening hours:")
        lines += hrs
    return {"ok": True, "text": "\n".join(lines), "website": d.get("website") or "", "name": d.get("name") or ""}


def _page_text(url):
    try:
        resp = safe_get(url, headers={"User-Agent": UA}, timeout=12)
    except BlockedURL as e:
        return None, [], "refused: %s" % e
    except requests.RequestException as e:
        return None, [], "unreachable (%s)" % type(e).__name__
    if resp.status_code >= 400 or "html" not in (resp.headers.get("content-type") or "html"):
        return None, [], "answered %s" % resp.status_code
    soup = BeautifulSoup(resp.text, "html.parser")
    ld = [s.get_text(" ", strip=True)[:1500] for s in soup.find_all("script", attrs={"type": "application/ld+json"})][:3]
    for t in soup(["script", "style", "noscript", "svg", "iframe"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", x).strip() for x in soup.get_text("\n").splitlines()]
    text = "\n".join(x for x in lines if x)[:PAGE_TEXT]
    host = urlparse(resp.url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(resp.url, a["href"]).split("#")[0]
        if urlparse(href).netloc == host and href not in links and LIKELY.search(href + " " + a.get_text(" ")):
            links.append(href)
    return text + ("\n" + "\n".join(ld) if ld else ""), links, None


def read_website(url):
    text, links, err = _page_text(url)
    if text is None:
        return {"ok": False, "note": "Website %s" % err}
    parts = ["Website %s:\n%s" % (url, text)]
    for link in links[:MAX_PAGES - 1]:
        t, _, e = _page_text(link)
        if t:
            parts.append("Website page %s:\n%s" % (link, t))
    return {"ok": True, "text": "\n\n".join(parts)}


# ------------------------------------------------------------------ one business
def read_business(tid, force=False):
    """Charged to the business's AI credits (app/usage.py)."""
    from .usage import meter
    with meter(tid, "setup", "page_read"):
        return _read_business_unmetered(tid, force)


def _read_business_unmetered(tid, force=False):
    """Read this business's page and site into a suggestion. Returns the status."""
    from .setup_reader import read_text
    with tenant_session(None, "techdeva_admin") as cur:
        pages, sites = sources(cur, tid)
        fp = fingerprint(pages, sites)
        if not fp:
            return "no_sources"
        # a menu photo read by Jarvis is not a page read: skip it, or every
        # photo would make the page and website look changed and be re-read
        cur.execute("""SELECT fingerprint, status, extract(epoch FROM now() - created_at) AS age
                         FROM setup_suggestions WHERE tenant_id = %s AND fingerprint NOT LIKE 'photo:%%'
                        ORDER BY created_at DESC LIMIT 1""", (tid,))
        last = cur.fetchone()
        cur.execute("SELECT slug FROM business_categories WHERE active ORDER BY sort_order")
        slugs = [r["slug"] for r in cur.fetchall()]
    if last and last["fingerprint"] == fp and not force and not (last["status"] == "failed" and last["age"] > FAILED_RETRY_S):
        return "unchanged"
    texts, src = [], []
    for p in pages:
        r = read_facebook(p)
        src.append({"kind": "facebook", "ref": r.get("name") or p["id"], "ok": r["ok"], "note": r.get("note")})
        if r["ok"]:
            texts.append(r["text"])
            w = normalise(r.get("website"))
            if w and w.lower().rstrip("/") not in [s.lower().rstrip("/") for s in sites] and len(sites) < 2:
                sites.append(w)                      # the page names its own website
    for s in sites:
        r = read_website(s)
        src.append({"kind": "website", "ref": s, "ok": r["ok"], "note": r.get("note")})
        if r["ok"]:
            texts.append(r["text"])
    text = "\n\n".join(texts)[:40000]
    if not text.strip():
        status, draft = "failed", {}
    else:
        draft = read_text(text, slugs)
        found = len(draft["services"]) + len(draft["hours"]) + len(draft["knowledge"]) + (1 if draft.get("business_type") else 0)
        status = "ready" if found else "nothing_found"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO setup_suggestions (tenant_id, sources, fingerprint, draft, source_text, status)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (tid, json.dumps(src), fp, json.dumps(draft), text, status))
        if status == "ready":   # only the newest suggestion is ever offered
            cur.execute("""UPDATE setup_suggestions SET status = 'dismissed', decided_at = now()
                            WHERE tenant_id = %s AND status = 'ready'
                              AND id <> (SELECT id FROM setup_suggestions WHERE tenant_id = %s
                                          ORDER BY created_at DESC LIMIT 1)""", (tid, tid))
    return status


def run_once():
    from .categories import PROTECTED
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT t.id::text AS tid FROM tenants t
                        WHERE t.status IN ('active', 'trial')
                          AND (EXISTS (SELECT 1 FROM integrations i WHERE i.tenant_id = t.id
                                        AND i.provider = 'meta' AND i.status = 'connected')
                               OR EXISTS (SELECT 1 FROM ad_pilot_settings a WHERE a.tenant_id = t.id AND a.website_url <> '')
                               OR EXISTS (SELECT 1 FROM signups s WHERE s.tenant_id = t.id AND coalesce(s.website_url, '') <> ''))
                        ORDER BY t.created_at DESC""")
        tids = [r["tid"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT tenant_id::text AS tid, agent_key FROM whatsapp_connections")
        skip = {r["tid"] for r in cur.fetchall() if PROTECTED.search(r["agent_key"] or "")}
    done = 0
    for tid in tids:
        if tid in skip:
            continue
        try:
            st = read_business(tid)
        except Exception as e:
            print("[pagereader] %s skipped: %s" % (tid, str(e)[:300]), flush=True)
            continue
        if st not in ("unchanged", "no_sources"):
            done += 1
            print("[pagereader] %s read: %s" % (tid, st), flush=True)
            if done >= PER_TICK:
                break
    return done


def run_due():
    """Called from the scheduler tick; runs at most every EVERY_S seconds."""
    global _last
    if time.monotonic() - _last < EVERY_S:
        return 0
    _last = time.monotonic()
    return run_once()
