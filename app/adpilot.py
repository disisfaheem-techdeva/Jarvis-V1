"""Ad autopilot: the growth planner's hands on the ad accounts (Pro).

What it may do, and who decides:
  * Pause a campaign that costs too much. By default the planner only
    suggests it and the OWNER taps Pause. If the owner switches on auto-pause
    and sets their own limit (cost per result), the planner pauses by itself
    and says so on the Growth Advisor page. Any pause can be turned back on.
  * Write a new campaign (Facebook & Instagram click-to-WhatsApp, or Google
    Search): headline, text, hashtags, description, keywords, one of the
    business's own photos, a daily budget. The planner LAUNCHES it by itself
    only when the owner has set a daily budget cap for that platform, and only
    while all campaigns the planner launched stay within that cap. Otherwise it
    waits as a draft for the owner to launch or discard.
  * The AI writes the words. A figure in the copy survives only if it is a
    real price from the business's own services; an invented offer ("50% off")
    is dropped and plain wording is used instead.

Everything is recorded in ad_actions and the audit log. Campaigns are created
paused and switched on only as the very last step, so a half-made campaign
never spends.
"""
import base64
import datetime as _dt
import json
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from . import ads
from .ads import AdsError
from .ai import AIUnavailable, generate_json
from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session
from .plans import plan_for
from .social import _meta_pages

PLANS = ("pro",)
PROVIDERS = ("meta", "google")
CHECK_EVERY = _dt.timedelta(hours=6)
AUTO_LAUNCH_EVERY = _dt.timedelta(days=7)
MIN_DAILY = {"meta": 300.0, "google": 300.0}        # below this the platforms refuse or never deliver
# A/B test of creatives: one ad per creative in one ad set (same text, same
# audience), so only the picture or video differs.
AB_MAX_ADS = 4          # more ads than this split a small budget too thin to learn
AB_PER_AD = 500.0       # PKR a day each ad needs to gather results in a week
AB_DAYS = 7             # first decision
AB_MAX_DAYS = 14        # too close at day 7: keep testing, then keep the cheapest
AB_MIN_RESULTS = 20     # between all the ads, before any 95% verdict
AB_CHECK_EVERY = _dt.timedelta(hours=6)
MAX_CREATIVES = 10
COUNTRIES = {"PK": 2586, "AE": 2784, "SA": 2682, "GB": 2826, "US": 2840}
CITIES = {   # centre points for "within N km of the business"
    "lahore": (31.5204, 74.3587), "karachi": (24.8607, 67.0011), "islamabad": (33.6844, 73.0479),
    "rawalpindi": (33.5651, 73.0169), "faisalabad": (31.4504, 73.1350), "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249), "quetta": (30.1798, 66.9750), "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1877, 74.1945), "hyderabad": (25.3960, 68.3578), "dubai": (25.2048, 55.2708),
}
SETTINGS_COLS = ("auto_pause, max_cost_per_result, min_spend, meta_daily_cap, google_daily_cap, country, city, "
                 "radius_km, website_url, last_check_at, last_auto_launch_at, updated_at")
DRAFT_COLS = ("id, provider, service, copy, image_token, daily_budget, status, external, error, created_by, "
              "created_at, launched_at, page_id, destination, video_token, creatives, test")
DESTINATIONS = ("whatsapp", "website")


def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "Open a business first.")
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can change the ads.")
    if plan_for(tid)["key"] not in PLANS and role != "techdeva_admin":
        raise HTTPException(402, "The ad autopilot is part of the Pro plan.")
    return tid, role, uid


def _page_list(cur, tid):
    """The Facebook Pages this login manages, id and name only."""
    return [{"id": str(p["id"]), "name": p.get("name") or str(p["id"])} for p in _meta_pages(cur, tid)]


def _default_page(cur, pages):
    """The Page a new campaign starts on: the only one, else the Page the owner
    last chose for a campaign, else for a post. Never a guess."""
    ids = {p["id"] for p in pages}
    if len(ids) == 1:
        return next(iter(ids))
    for q in ("SELECT page_id FROM ad_drafts WHERE page_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
              "SELECT page_id FROM social_posts WHERE page_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1"):
        cur.execute(q)
        r = cur.fetchone()
        if r and r["page_id"] in ids:
            return r["page_id"]
    return None


def _num(v, what, lo=0.0, hi=10_000_000.0, allow_none=False):
    if v in (None, "") and allow_none:
        return None
    try:
        x = float(str(v).replace(",", ""))
    except ValueError:
        raise HTTPException(400, "%s must be a number." % what)
    if not lo <= x <= hi:
        raise HTTPException(400, "%s must be between %s and %s." % (what, int(lo), int(hi)))
    return x


# ------------------------------------------------------------------ settings
def get_settings(cur):
    cur.execute("SELECT %s FROM ad_pilot_settings" % SETTINGS_COLS)
    r = cur.fetchone()
    if not r:
        return {"auto_pause": False, "max_cost_per_result": None, "min_spend": 0.0, "meta_daily_cap": 0.0,
                "google_daily_cap": 0.0, "country": "PK", "city": "", "radius_km": 15, "website_url": "",
                "last_check_at": None, "last_auto_launch_at": None, "updated_at": None}
    r = dict(r)
    for k in ("max_cost_per_result", "min_spend", "meta_daily_cap", "google_daily_cap"):
        r[k] = float(r[k]) if r[k] is not None else None
    return r


def _clean_settings(body):
    f = {}
    if "auto_pause" in body:
        f["auto_pause"] = bool(body["auto_pause"])
    if "max_cost_per_result" in body:
        f["max_cost_per_result"] = _num(body["max_cost_per_result"], "The most you pay per result", 1, 1_000_000,
                                        allow_none=True)
    if "min_spend" in body:
        f["min_spend"] = _num(body["min_spend"] or 0, "The spend to wait for", 0, 10_000_000)
    for p in PROVIDERS:
        k = p + "_daily_cap"
        if k in body:
            f[k] = _num(body[k] or 0, "The daily budget cap", 0, 10_000_000)
            if f[k] and f[k] < MIN_DAILY[p]:
                raise HTTPException(400, "A daily cap below %d is too small for the ad platforms to run an ad."
                                    % MIN_DAILY[p])
    if "country" in body:
        c = str(body["country"] or "PK").upper()
        if c not in COUNTRIES:
            raise HTTPException(400, "Country must be one of %s." % ", ".join(COUNTRIES))
        f["country"] = c
    if "city" in body:
        c = str(body["city"] or "").strip().lower()
        if c and c not in CITIES:
            raise HTTPException(400, "City must be one of: %s (or empty for the whole country)."
                                % ", ".join(sorted(CITIES)))
        f["city"] = c
    if "radius_km" in body:
        f["radius_km"] = int(_num(body["radius_km"] or 15, "The distance", 1, 80))
    if "website_url" in body:
        u = str(body["website_url"] or "").strip()[:300]
        if u and not re.match(r"^https://[^\s/]+\.[^\s]+$", u):
            raise HTTPException(400, "The website must start with https:// (for example https://yourclinic.com).")
        f["website_url"] = u
    if f.get("auto_pause") and body.get("max_cost_per_result") in (None, "") and "max_cost_per_result" in body:
        raise HTTPException(400, "Set the most you want to pay per result before turning on auto-pause.")
    return f


# ------------------------------------------------------------------ business facts
def _business(cur, service=None, exclude=()):
    """The business name, what to advertise and everything it sells.
    A clinic sells services; a shop (food, retail) sells products, and its
    best seller comes from delivered orders, not appointments. The picked item
    carries kind "service" or "product" so the wording fits."""
    cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
    biz = (cur.fetchone() or {}).get("name") or "our business"
    cur.execute("SELECT name, price FROM services WHERE coalesce(active, true) ORDER BY sort_order NULLS LAST, name "
                "LIMIT 60")
    services = [{"name": r["name"], "price": float(r["price"]) if r["price"] is not None else None, "kind": "service"}
                for r in cur.fetchall()]
    cur.execute("SELECT name, price FROM products WHERE coalesce(active, true) ORDER BY name LIMIT 60")
    products = [{"name": r["name"], "price": float(r["price"]) if r["price"] is not None else None, "kind": "product"}
                for r in cur.fetchall()]
    items = services + products
    skip = {x.lower() for x in exclude}
    find = lambda name: next((x for x in items if x["name"].lower() == str(name).lower()), None)
    pick = None
    if service:
        pick = find(service) or {"name": service, "price": None, "kind": "service" if services else "product"}
    else:
        cur.execute("""SELECT oi.description AS item, sum(oi.line_total) AS rev
                         FROM order_items oi JOIN orders o ON o.id = oi.order_id
                        WHERE o.status = 'delivered' AND o.created_at >= now() - interval '30 days'
                        GROUP BY 1 ORDER BY 2 DESC LIMIT 10""")
        sold = [r["item"] for r in cur.fetchall()]
        cur.execute("""SELECT coalesce(nullif(service, ''), '') AS service, sum(coalesce(price, 0)) AS rev
                         FROM appointments WHERE status = 'completed' AND starts_at >= now() - interval '30 days'
                          AND coalesce(service, '') <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 10""")
        booked = [r["service"] for r in cur.fetchall()]
        # a shop's best seller first when it sells by orders, else the clinic's
        for name in (sold + booked) if (products and not services) else (booked + sold):
            if name.lower() not in skip and find(name):
                pick = find(name)
                break
        if not pick:
            pick = next((x for x in (services or products) if x["name"].lower() not in skip), None)
    return biz, pick, items


def _latest_photos(cur, n=8):
    cur.execute("SELECT token, created_at FROM social_media ORDER BY created_at DESC LIMIT %s", (n,))
    return [r["token"] for r in cur.fetchall()]


# ------------------------------------------------------------------ the words
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Offers and promises the business never made. Ad text with any of these falls
# back to plain wording (the owner can still write them in by hand).
_CLAIMS = re.compile(r"(%|\b(off|offer|offers|discount\w*|free|limited|hurry|sale|deal|deals|save|saving|only|"
                     r"last chance|guarantee\w*|permanent\w*|cure\w*|100|risk[- ]free|no side effects?|"
                     r"painless|instant|miracle|#1|number one)\b)", re.I)

COPY_PROMPT = """You write ads for a small business in Pakistan (clinic, salon, gym, academy,
restaurant or shop). Write honest, warm, simple ads for ONE item: "kind" says
whether it is a service people book or a product people order. Rules:
- Never invent an offer, discount, percentage, result, guarantee or number. Use a price
  only if it is given, exactly as given.
- No medical promises or before/after claims. No ALL CAPS.
- English, easy words. The call to action is exactly "action" (message on WhatsApp, or
  order on the website). A product is ordered, never booked or "visited".
Reply with JSON:
{"meta": {"headline": "<max 40 chars>", "primary_text": "<2-3 short sentences, max 300 chars>",
          "description": "<max 30 chars>", "hashtags": ["<5 to 8 hashtags, no # sign, no spaces>"]},
 "google": {"headlines": ["<8 to 12 headlines, each max 30 chars>"],
            "descriptions": ["<3 or 4 descriptions, each max 90 chars>"],
            "keywords": ["<8 to 15 search phrases people type, lowercase>"]}}"""


def _tag(s):
    """'Face PRP' -> 'FacePRP', 'hair prp lahore' -> 'HairPrpLahore'."""
    words = re.findall(r"[A-Za-z0-9]+", s or "")
    return "".join(w[:1].upper() + w[1:] for w in words)[:30]


def template_copy(biz, service, city, dest="whatsapp"):
    """Plain wording that needs no AI and makes no claims."""
    svc, where = service["name"], (city.title() if city else "")
    price = (" from PKR {:,.0f}".format(service["price"])) if service.get("price") else ""
    loc = (" in " + where) if where else ""
    shop = service.get("kind") == "product"
    web = dest == "website"
    act_tag = "OrderNow" if shop else "BookNow"
    tags = [t for t in (_tag(svc), _tag(biz), _tag(where) if where else "", _tag(svc + " " + where) if where else "",
                        act_tag) if t]
    if shop:
        each = (", PKR {:,.0f}".format(service["price"])) if service.get("price") else ""
        text = ("Get %s from %s%s%s. %s" % (svc, biz, loc, each,
                "Tap Order now to order on our website." if web else "Message us on WhatsApp to order."))
        desc = "Order now" if web else "Order on WhatsApp"
        g_mid = ("Order Online Today", "Order Now", "%s%s" % (svc, " " + where if where else ""))
        g_desc = ("Order online in a few taps." if web else "Message us on WhatsApp to order.",
                  "Order now from %s." % biz)
        kw_tail = svc + (" " + where if where else " online")
    else:
        text = ("Looking for %s%s? %s offers %s%s. %s" % (svc, loc, biz, svc, price,
                "Tap the button to book on our website." if web else "Message us on WhatsApp to book your visit."))
        desc = "Book now" if web else "Book on WhatsApp"
        g_mid = ("Book Your Appointment", "Message Us on WhatsApp" if not web else "Book Online", "%s Near You" % svc)
        g_desc = ("Easy booking. Tell us the day and time that suits you.",
                  "Book online in a minute." if web else "Message us on WhatsApp and we will reply quickly.")
        kw_tail = "%s %s" % (svc, where) if where else svc + " clinic"
    return {
        "meta": {"headline": ("%s at %s" % (svc, biz))[:40], "primary_text": text[:300],
                 "description": desc, "hashtags": list(dict.fromkeys(tags))[:8]},
        "google": {"headlines": [h[:30] for h in (("%s%s" % (svc, loc)), biz) + g_mid + (svc,)],
                   "descriptions": [d[:90] for d in (("%s offers %s%s%s." % (biz, svc, loc, price)),) + g_desc],
                   "keywords": list(dict.fromkeys(k.lower() for k in (
                       svc, kw_tail, svc + " near me",
                       "best %s %s" % (svc, where) if where else "best " + svc, svc + " price")))},
    }


def _clean_list(v, n_max, each_max, lo=0):
    out = []
    for x in v if isinstance(v, list) else []:
        x = re.sub(r"\s+", " ", str(x or "")).strip()
        if x and len(x) <= each_max and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out[:n_max] if len(out) >= lo else None


def write_copy(biz, service, city, services, dest="whatsapp"):
    """AI copy checked field by field; anything off-limits falls back to plain wording."""
    base = template_copy(biz, service, city, dest)
    allowed = {("%.0f" % s["price"]) for s in services if s.get("price")}
    allowed |= {("{:,.0f}".format(s["price"])).replace(",", "") for s in services if s.get("price")}

    def clean(text):
        return (all(n.replace(",", "") in allowed for n in _NUM.findall(text or ""))
                and not _CLAIMS.search(text or ""))

    facts = {"business": biz, "item": service["name"], "kind": service.get("kind") or "service",
             "city": city.title() if city else "", "price_pkr": service.get("price"),
             "action": "order on our website" if dest == "website" else "message us on WhatsApp"}
    try:
        out = generate_json(COPY_PROMPT, json.dumps(facts), required_keys=("meta", "google"), _retry=False)
    except AIUnavailable:
        return base, "rules"
    m, g = out.get("meta") or {}, out.get("google") or {}
    copy, by = json.loads(json.dumps(base)), "ai"
    for k, n in (("headline", 40), ("primary_text", 300), ("description", 30)):
        v = re.sub(r"\s+", " ", str(m.get(k) or "")).strip()
        if v and len(v) <= n and clean(v):
            copy["meta"][k] = v
    tags = [re.sub(r"[^A-Za-z0-9_]", "", str(t).lstrip("#")) for t in (m.get("hashtags") or [])]
    tags = _clean_list([t for t in tags if t and clean(t)], 8, 30, lo=3)
    if tags:
        copy["meta"]["hashtags"] = tags
    for k, n_max, each, lo in (("headlines", 15, 30, 3), ("descriptions", 4, 90, 2), ("keywords", 20, 80, 3)):
        v = _clean_list([x for x in (g.get(k) or []) if clean(str(x))], n_max, each, lo=lo)
        if v:
            copy["google"][k] = v
    return copy, by


def meta_message(copy):
    m = copy["meta"]
    tags = " ".join("#" + t for t in m.get("hashtags") or [])
    return (m["primary_text"] + ("\n\n" + tags if tags else ""))[:2000]


# ------------------------------------------------------------------ launching
def _targeting(s):
    city = CITIES.get((s.get("city") or "").lower())
    return city, int(s.get("radius_km") or 15), s.get("country") or "PK"


def creatives_of(d):
    """The creatives a campaign runs: the owner's attached list, else the one
    photo or video set on the draft (the planner's pick)."""
    out = [c for c in (d.get("creatives") or []) if c.get("kind") in ("image", "video") and c.get("token")]
    if out:
        return out
    if d.get("video_token"):
        return [{"kind": "video", "token": d["video_token"]}]
    return [{"kind": "image", "token": d["image_token"]}] if d.get("image_token") else []


def ab_plan(d):
    """How many ads run and why, worked out from the creatives and the budget.
    The planner decides; this is what the owner is told before Launch."""
    cs = creatives_of(d) if d.get("provider") == "meta" else []
    budget = float(d.get("daily_budget") or 0)
    if d.get("provider") != "meta":
        return {"ads": 1, "test": False, "picked": [], "left_out": 0,
                "text": "1 search ad with your headlines and keywords. Google tries the headlines itself."}
    if not cs:
        return {"ads": 0, "test": False, "picked": [], "left_out": 0,
                "text": "Add at least one photo, flyer or video. With two or more, the planner tests them against each other."}
    room = max(1, int(budget // AB_PER_AD))
    n = min(len(cs), AB_MAX_ADS, room)
    # videos first (they usually hold attention longest), then pictures in the order given
    order = [c for c in cs if c["kind"] == "video"] + [c for c in cs if c["kind"] == "image"]
    picked = order[:n]
    cur = "PKR"
    if n == 1:
        text = "1 ad will run with %s." % ("this video" if picked[0]["kind"] == "video" else "this picture")
        if len(cs) > 1:
            text += (" To test your %d creatives against each other, raise the daily budget to at least %s %s "
                     "(about %s %s for each ad)." % (min(len(cs), AB_MAX_ADS), cur,
                                                    "{:,.0f}".format(AB_PER_AD * min(len(cs), AB_MAX_ADS)),
                                                    cur, "{:,.0f}".format(AB_PER_AD)))
    else:
        text = ("%d ads will run, one for each creative, sharing %s %s a day (about %s %s each). After %d days the "
                "planner keeps the one that gets results cheapest and pauses the others; if it is too close to call, "
                "it keeps testing up to %d days." % (n, cur, "{:,.0f}".format(budget), cur,
                                                     "{:,.0f}".format(budget / n), AB_DAYS, AB_MAX_DAYS))
        left = len(cs) - n
        if left > 0:
            text += (" %d creative%s left out: %s" % (left, "" if left == 1 else "s",
                     "at most %d ads are tested at once." % AB_MAX_ADS if n == AB_MAX_ADS else
                     "each ad needs about %s %s a day, so raise the budget to test more." % (cur, "{:,.0f}".format(AB_PER_AD))))
    return {"ads": n, "test": n >= 2, "picked": picked, "left_out": len(cs) - n, "text": text}


VIDEO_WAIT = 45        # seconds Facebook gets to process a video at launch


def _meta_video(act, tok, token):
    """Hand the campaign's video to the ad account; return its id once Facebook
    has processed it. Meta fetches the file from our public media link."""
    import time
    from .videos import video_url
    up = ads._meta_post("/act_%s/advideos" % act, tok, {"file_url": video_url(token)})
    vid = str(up.get("id") or "")
    if not vid:
        raise AdsError("Facebook did not accept the video.")
    started = time.time()
    while True:
        st = ((ads._meta_get("/%s" % vid, tok, {"fields": "status"}).get("status") or {}).get("video_status") or "")
        if st == "ready":
            return vid
        if st == "error":
            raise AdsError("Facebook could not process the video. Try an MP4 under 4 minutes.")
        if time.time() - started > VIDEO_WAIT:
            raise AdsError("Facebook is still processing the video. Press Launch again in a minute.")
        time.sleep(3)


def _meta_image(act, tok, data):
    up = ads._meta_post("/act_%s/adimages" % act, tok, {"bytes": base64.b64encode(data).decode()})
    h = next(iter((up.get("images") or {}).values()), {}).get("hash")
    if not h:
        raise AdsError("Facebook did not accept a picture.")
    return h


def _meta_video_thumb(tok, vid):
    """The cover Facebook picked for the video (a video ad needs one)."""
    rows = ads._meta_get("/%s/thumbnails" % vid, tok, {}).get("data") or []
    pick = next((r for r in rows if r.get("is_preferred")), rows[0] if rows else None)
    if not pick or not pick.get("uri"):
        raise AdsError("Facebook has not made a cover for the video yet. Add a photo as its cover, or try again.")
    return pick["uri"]


def launch_meta(tid, role, d, s):
    tok, act, creds = ads.connection(tid, role, "meta")
    pages = [str(p.get("id")) for p in (creds.get("pages") or []) if p.get("id")]
    if not pages:
        raise AdsError("No Facebook page came through. Press Reconnect Facebook and tick your page.")
    # the Page the owner chose for this campaign; the first Page on the login
    # could be another business's Page
    if d.get("page_id"):
        if str(d["page_id"]) not in pages:
            raise AdsError("That Facebook Page is no longer connected. Press Edit and choose another Page.")
        page = str(d["page_id"])
    elif len(pages) == 1:
        page = pages[0]
    else:
        raise AdsError("Choose which Facebook Page this campaign runs from: press Edit and pick the Page.")
    plan = ab_plan(d)
    if not plan["picked"]:
        raise AdsError("Add a photo, flyer or video first: press 'Add creatives' on the campaign, then Launch.")
    web = (d.get("destination") or "whatsapp") == "website"
    site = (s.get("website_url") or "").strip()
    if web and not site:
        raise AdsError("This campaign's button goes to your website, but no website is set. Add it in the ad "
                       "autopilot rules, or switch the button to WhatsApp.")
    items = []
    with tenant_session(tid, role) as cur:
        for c in plan["picked"]:
            if c["kind"] == "image":
                cur.execute("SELECT bytes FROM social_media WHERE token = %s", (c["token"],))
                row = cur.fetchone()
                if not row:
                    raise AdsError("One of the pictures is no longer there. Remove it and add it again.")
                items.append(dict(c, bytes=bytes(row["bytes"])))
            else:
                items.append(dict(c))
        cover = None
        # the draft's photo is a video's cover only when the owner attached no creatives of their own
        if not d.get("creatives") and d.get("image_token") and any(c["kind"] == "video" for c in items):
            cur.execute("SELECT bytes FROM social_media WHERE token = %s", (d["image_token"],))
            row = cur.fetchone()
            cover = bytes(row["bytes"]) if row else None
    # videos go to Facebook before anything is created, so one it will not
    # take leaves no half-made campaign
    for it in items:
        if it["kind"] == "video":
            it["video_id"] = _meta_video(act, tok, it["token"])
    acct = ads._meta_get("/act_%s" % act, tok, {"fields": "currency"})
    minor = int(round(float(d["daily_budget"]) * 100))       # PKR, USD, AED, GBP, SAR: 2 decimals at Meta
    name = "TechDeva · %s · %s" % (d["service"] or "Campaign", _dt.date.today().isoformat())
    ext = {"currency": acct.get("currency")}
    c = ads._meta_post("/act_%s/campaigns" % act, tok, {
        "name": name, "objective": "OUTCOME_TRAFFIC" if web else "OUTCOME_ENGAGEMENT", "status": "PAUSED",
        "buying_type": "AUCTION",
        "special_ad_categories": [], "is_adset_budget_sharing_enabled": False})
    ext["campaign_id"] = str(c["id"])
    try:
        city, radius, country = _targeting(s)
        geo = ({"custom_locations": [{"latitude": city[0], "longitude": city[1], "radius": radius,
                                      "distance_unit": "kilometer"}]} if city else {"countries": [country]})
        aset = {"name": name, "campaign_id": ext["campaign_id"], "daily_budget": minor, "billing_event": "IMPRESSIONS",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "targeting": {"geo_locations": geo, "age_min": 18, "age_max": 65}, "status": "ACTIVE"}
        if web:     # people who tap through to the website to order
            aset.update(optimization_goal="LINK_CLICKS", destination_type="WEBSITE")
        else:       # a WhatsApp chat with the business
            aset.update(optimization_goal="CONVERSATIONS", destination_type="WHATSAPP",
                        promoted_object={"page_id": page})
        a = ads._meta_post("/act_%s/adsets" % act, tok, aset)
        ext["adset_id"] = str(a["id"])
        # a product is ordered, a service is booked
        cta = ({"type": "ORDER_NOW" if d["copy"].get("kind") == "product" else "BOOK_NOW", "value": {"link": site}} if web else
               {"type": "WHATSAPP_MESSAGE", "value": {"app_destination": "WHATSAPP"}})
        cover_hash = None
        if cover:
            cover_hash = _meta_image(act, tok, cover)
        ext["ads"] = []
        for n, it in enumerate(items, 1):
            if it["kind"] == "video":
                vd = {"video_id": it["video_id"], "message": meta_message(d["copy"]), "title": d["copy"]["meta"]["headline"],
                      "link_description": d["copy"]["meta"]["description"], "call_to_action": cta}
                if cover_hash:
                    vd["image_hash"] = cover_hash          # the owner's photo is the video's cover
                else:
                    vd["image_url"] = _meta_video_thumb(tok, it["video_id"])
                spec = {"page_id": page, "video_data": vd}
            else:
                spec = {"page_id": page, "link_data": {
                    "image_hash": _meta_image(act, tok, it["bytes"]), "message": meta_message(d["copy"]),
                    "name": d["copy"]["meta"]["headline"], "description": d["copy"]["meta"]["description"],
                    "link": site if web else "https://api.whatsapp.com/send", "call_to_action": cta}}
            label = name if len(items) == 1 else "%s · creative %d" % (name, n)
            cr = ads._meta_post("/act_%s/adcreatives" % act, tok, {"name": label, "object_story_spec": spec})
            ad = ads._meta_post("/act_%s/ads" % act, tok, {"name": label, "adset_id": ext["adset_id"],
                                                            "creative": {"creative_id": str(cr["id"])}, "status": "ACTIVE"})
            ext["ads"].append({"ad_id": str(ad["id"]), "creative_id": str(cr["id"]), "kind": it["kind"],
                               "token": it["token"], "n": n})
        ext["creative_id"], ext["ad_id"] = ext["ads"][0]["creative_id"], ext["ads"][0]["ad_id"]
        ads.meta_set_status(tok, ext["campaign_id"], True)        # the switch, last
    except AdsError as e:
        raise AdsError("%s (the half-made campaign stays paused)" % e, ) from None
    finally:
        d["external"] = ext
    return ext


def launch_google(tid, role, d, s):
    if not s.get("website_url"):
        raise AdsError("Google Search ads need your website. Add it in the autopilot settings.")
    tok, cid, _ = ads.connection(tid, role, "google")
    g = d["copy"]["google"]
    name = "TechDeva · %s · %s" % (d["service"] or "Campaign", _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    base = "/customers/%s" % cid
    ext = {}
    b = ads._gads("POST", base + "/campaignBudgets:mutate", tok, {"operations": [{"create": {
        "name": name, "amountMicros": str(int(round(float(d["daily_budget"]) * 1e6))),
        "deliveryMethod": "STANDARD", "explicitlyShared": False}}]})
    ext["budget"] = b["results"][0]["resourceName"]
    c = ads._gads("POST", base + "/campaigns:mutate", tok, {"operations": [{"create": {
        "name": name, "status": "PAUSED", "advertisingChannelType": "SEARCH", "campaignBudget": ext["budget"],
        "targetSpend": {}, "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": True,
                            "targetContentNetwork": False, "targetPartnerSearchNetwork": False}}}]})
    camp = c["results"][0]["resourceName"]
    ext["campaign_id"] = camp.split("/")[-1]
    try:
        city, radius, country = _targeting(s)
        crit = ({"proximity": {"geoPoint": {"latitudeInMicroDegrees": int(city[0] * 1e6),
                                            "longitudeInMicroDegrees": int(city[1] * 1e6)},
                               "radius": radius, "radiusUnits": "KILOMETERS"}} if city else
                {"location": {"geoTargetConstant": "geoTargetConstants/%d" % COUNTRIES[country]}})
        ads._gads("POST", base + "/campaignCriteria:mutate", tok, {"operations": [{"create": dict(crit, campaign=camp)}]})
        ag = ads._gads("POST", base + "/adGroups:mutate", tok, {"operations": [{"create": {
            "name": name, "campaign": camp, "status": "ENABLED", "type": "SEARCH_STANDARD"}}]})
        grp = ag["results"][0]["resourceName"]
        ext["ad_group"] = grp
        ads._gads("POST", base + "/adGroupAds:mutate", tok, {"operations": [{"create": {
            "adGroup": grp, "status": "ENABLED", "ad": {"finalUrls": [s["website_url"]], "responsiveSearchAd": {
                "headlines": [{"text": h} for h in g["headlines"][:15]],
                "descriptions": [{"text": x} for x in g["descriptions"][:4]]}}}}]})
        ads._gads("POST", base + "/adGroupCriteria:mutate", tok, {"operations": [
            {"create": {"adGroup": grp, "status": "ENABLED", "keyword": {"text": k, "matchType": "PHRASE"}}}
            for k in g["keywords"][:20]]})
        ads.google_set_status(tok, cid, ext["campaign_id"], True)   # the switch, last
    except AdsError as e:
        raise AdsError("%s (the half-made campaign stays paused)" % e) from None
    finally:
        d["external"] = ext
    return ext


def planner_spend(cur, provider):
    """Daily budget of the campaigns the planner launched that are still running."""
    cur.execute("SELECT coalesce(sum(daily_budget), 0)::float AS s FROM ad_drafts "
                "WHERE provider = %s AND status = 'launched' AND created_by = 'planner'", (provider,))
    return cur.fetchone()["s"]


def launch(tid, role, uid, draft_id, by):
    """Launch one draft. `by` is 'owner' (their decision, any budget) or
    'planner' (only within the owner's cap)."""
    with tenant_session(tid, role) as cur:
        cur.execute("SELECT %s FROM ad_drafts WHERE id::text = %%s FOR UPDATE" % DRAFT_COLS, (draft_id,))
        d = cur.fetchone()
        if not d:
            raise HTTPException(404, "That campaign draft is not there.")
        if d["status"] not in ("draft", "failed"):
            raise HTTPException(409, "That campaign was already %s." % d["status"])
        s = get_settings(cur)
        if by == "planner":
            cap = float(s[d["provider"] + "_daily_cap"] or 0)
            if not cap or planner_spend(cur, d["provider"]) + float(d["daily_budget"]) > cap:
                return {"launched": False, "reason": "over the daily cap"}
        cur.execute("UPDATE ad_drafts SET status = 'launched', error = NULL WHERE id = %s", (d["id"],))  # claim it
    d = dict(d)
    try:
        ext = (launch_meta if d["provider"] == "meta" else launch_google)(tid, role, d, s)
        ok, err = True, None
    except AdsError as e:
        ext, ok, err = d.get("external") or {}, False, str(e)
    test = {}
    if ok and len(ext.get("ads") or []) >= 2:
        test = {"status": "testing", "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "days": AB_DAYS, "max_days": AB_MAX_DAYS, "ads": ext["ads"], "results": {}}
    with tenant_session(tid, role) as cur:
        cur.execute("UPDATE ad_drafts SET status = %s, external = %s, error = %s, launched_at = CASE WHEN %s "
                    "THEN now() END, test = %s WHERE id = %s",
                    ("launched" if ok else "failed", json.dumps(ext), err, ok, json.dumps(test), d["id"]))
        _log(cur, d["provider"], "launch", ext.get("campaign_id") or "", "TechDeva · " + (d["service"] or ""),
             "Daily budget %s" % ("{:,.0f}".format(float(d["daily_budget"]))), by, ok, err)
        record(cur, action="adpilot.launch", resource="ad_drafts", resource_id=str(d["id"]), actor=str(uid or by),
               actor_type="human" if by == "owner" else "agent", status="ok" if ok else "error",
               detail={"provider": d["provider"], "budget": float(d["daily_budget"]), "external": ext, "by": by},
               error=err)
    ads.forget(tid)
    return {"launched": ok, "error": err, "external": ext}


def _log(cur, provider, kind, cid, name, reason, by, ok, err=None):
    cur.execute("INSERT INTO ad_actions (tenant_id, provider, kind, campaign_id, campaign_name, reason, by_who, "
                "status, error) VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s)",
                (provider, kind, str(cid or ""), (name or "")[:200], (reason or "")[:300], by,
                 "done" if ok else "failed", err))


def set_status(tid, role, uid, provider, campaign_id, name, active, by, reason=""):
    try:
        ads.set_campaign_status(tid, role, provider, campaign_id, active)
        ok, err = True, None
    except AdsError as e:
        ok, err = False, str(e)
    with tenant_session(tid, role) as cur:
        _log(cur, provider, "resume" if active else "pause", campaign_id, name, reason, by, ok, err)
        if ok:
            cur.execute("UPDATE ad_drafts SET status = %s WHERE provider = %s AND external->>'campaign_id' = %s "
                        "AND status IN ('launched','paused')",
                        ("launched" if active else "paused", provider, str(campaign_id)))
        record(cur, action="adpilot.%s" % ("resume" if active else "pause"), resource="campaign",
               resource_id=str(campaign_id), actor=str(uid or by), actor_type="human" if by == "owner" else "agent",
               status="ok" if ok else "error", detail={"provider": provider, "name": name, "reason": reason, "by": by},
               error=err)
    return ok, err


def draft(tid, role, uid, provider, service=None, by="owner", budget=None, page_id=None):
    if provider not in PROVIDERS:
        raise HTTPException(400, "Platform must be meta or google.")
    with tenant_session(tid, role) as cur:
        s = get_settings(cur)
        running = ()
        if by == "planner":       # never a second campaign for a service already advertised there
            cur.execute("SELECT DISTINCT service FROM ad_drafts WHERE provider = %s AND status = 'launched'",
                        (provider,))
            running = [r["service"] for r in cur.fetchall()]
        biz, svc, services = _business(cur, service, running)
        if not svc:
            raise HTTPException(400, "Add your services or products first, so the planner knows what to advertise.")
        photos = _latest_photos(cur)
        page = None
        if provider == "meta":
            pages = _page_list(cur, tid)
            if page_id:
                if str(page_id) not in {p["id"] for p in pages}:
                    raise HTTPException(400, "That Facebook Page is not connected. Choose one of your Pages.")
                page = str(page_id)
            else:
                page = _default_page(cur, pages)
    # a shop with a website sends people to order there; everyone else to WhatsApp
    dest = "website" if (svc.get("kind") == "product" and s.get("website_url")) else "whatsapp"
    copy, worded = write_copy(biz, svc, s.get("city") or "", services, dest)
    cap = float(s[provider + "_daily_cap"] or 0)
    daily = budget or cap or MIN_DAILY[provider] * 2
    if daily < MIN_DAILY[provider]:
        raise HTTPException(400, "The daily budget must be at least %d." % MIN_DAILY[provider])
    with tenant_session(tid, role) as cur:
        cur.execute("INSERT INTO ad_drafts (tenant_id, provider, service, copy, image_token, daily_budget, created_by, "
                    "page_id, destination) VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s) RETURNING "
                    + DRAFT_COLS,
                    (provider, svc["name"], json.dumps(dict(copy, worded_by=worded, kind=svc.get("kind"))),
                     photos[0] if (photos and provider == "meta") else None, daily, by, page, dest))
        row = dict(cur.fetchone())
        record(cur, action="adpilot.draft", resource="ad_drafts", resource_id=str(row["id"]), actor=str(uid or by),
               actor_type="human" if by == "owner" else "agent", status="ok",
               detail={"provider": provider, "service": svc["name"], "budget": daily, "worded_by": worded}, error=None)
    return row


# ------------------------------------------------------------------ the planner's rounds
def expensive(perf_platforms, limit, min_spend):
    """Running campaigns over the owner's limit: [(provider, campaign, cost_per_result)]."""
    out = []
    for p in perf_platforms or []:
        for c in p.get("campaigns") or []:
            if c.get("status") != "active" or not c.get("id"):
                continue
            results = float(c.get("leads") or 0) + float(c.get("chats") or 0)
            spend = float(c.get("spend") or 0)
            if spend < max(min_spend or 0, limit):
                continue
            cpr = spend / results if results else None
            if cpr is None or cpr > limit:
                out.append((p, c, cpr))
    return out


def run_tenant(tid):
    """One planner round for a business: auto-pause, then (weekly) auto-launch."""
    role = "owner"
    now = _dt.datetime.now(_dt.timezone.utc)
    with tenant_session(tid, role) as cur:
        s = get_settings(cur)
        cur.execute("UPDATE ad_pilot_settings SET last_check_at = now() WHERE tenant_id = %s", (tid,))
    done = 0
    if s["auto_pause"] and s["max_cost_per_result"]:
        perf = ads.performance_for(tid, role, 7)
        for p, c, cpr in expensive(perf.get("platforms"), s["max_cost_per_result"], s["min_spend"]):
            cur_ = p.get("currency") or "PKR"
            why = ("{} {:,.0f} spent in 7 days with no results".format(cur_, c["spend"]) if cpr is None else
                   "costs {0} {1:,.0f} per result, over your limit of {0} {2:,.0f}".format(
                       cur_, cpr, s["max_cost_per_result"]))
            ok, _ = set_status(tid, role, None, p["provider"], c["id"], c["name"], False, "planner", why)
            done += ok
    due = not s["last_auto_launch_at"] or now - s["last_auto_launch_at"] >= AUTO_LAUNCH_EVERY
    if due:
        launched_any = False
        for provider in PROVIDERS:
            cap = float(s[provider + "_daily_cap"] or 0)
            if not cap:
                continue
            with tenant_session(tid, role) as cur:
                left = cap - planner_spend(cur, provider)
            if left < MIN_DAILY[provider]:
                continue
            try:
                d = draft(tid, role, None, provider, by="planner", budget=min(cap, left))
            except HTTPException:
                continue
            r = launch(tid, role, None, str(d["id"]), "planner")
            launched_any = launched_any or r.get("launched")
            done += bool(r.get("launched"))
        if any(float(s[p + "_daily_cap"] or 0) for p in PROVIDERS):
            with tenant_session(tid, role) as cur:
                cur.execute("UPDATE ad_pilot_settings SET last_auto_launch_at = now() WHERE tenant_id = %s", (tid,))
    return done


# ------------------------------------------------------------------ A/B test rounds
def _norm_cdf(z):
    import math
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def ab_verdict(rows, min_results=AB_MIN_RESULTS):
    """rows: [{ad_id, impressions, results, spend}]. The ad with the lowest cost
    per result wins if it also gets more results per view than the runner-up
    with 95% confidence (two-proportion z-test). Returns (winner_row | None, why)."""
    import math
    live = [r for r in rows if r["impressions"] > 0]
    total = sum(r["results"] for r in live)
    if len(live) < 2:
        return None, "not enough ads have been shown yet"
    if total < min_results:
        return None, "%d results so far, %d needed to compare" % (total, min_results)
    cpr = lambda r: r["spend"] / r["results"] if r["results"] else float("inf")
    by_rate = sorted(live, key=lambda r: -r["results"] / r["impressions"])
    best, second = by_rate[0], by_rate[1]
    pool = (best["results"] + second["results"]) / (best["impressions"] + second["impressions"])
    se = math.sqrt(pool * (1 - pool) * (1 / best["impressions"] + 1 / second["impressions"])) if 0 < pool < 1 else 0
    z = ((best["results"] / best["impressions"]) - (second["results"] / second["impressions"])) / se if se else 0
    conf = 2 * _norm_cdf(abs(z)) - 1
    if conf < 0.95:
        return None, "too close to call (%d%% sure, 95%% needed)" % min(99, int(conf * 100))
    if cpr(best) > min(cpr(r) for r in live):
        return None, "the ad with the most results is not the cheapest per result"
    return best, "%d%% sure it gets more results per view, and it costs less per result" % min(99, int(conf * 100))


def ab_rows(tok, campaign_id, ads_list, website):
    """Results per ad since launch, from Facebook."""
    body = ads._meta_get("/%s/insights" % campaign_id, tok, {
        "level": "ad", "date_preset": "maximum", "limit": 50,
        "fields": "ad_id,spend,impressions,clicks,inline_link_clicks,actions"})
    got = {str(r.get("ad_id")): r for r in body.get("data") or []}
    rows = []
    for a in ads_list:
        r = got.get(str(a["ad_id"])) or {}
        acts = r.get("actions")
        res = (float(r.get("inline_link_clicks") or 0) if website else
               ads._action(acts, "onsite_conversion.messaging_conversation_started_7d")
               + ads._action(acts, "lead", "onsite_conversion.lead_grouped"))
        rows.append({"ad_id": str(a["ad_id"]), "n": a.get("n"), "kind": a.get("kind"), "token": a.get("token"),
                     "spend": float(r.get("spend") or 0), "impressions": int(float(r.get("impressions") or 0)),
                     "clicks": int(float(r.get("clicks") or 0)), "results": res})
    return rows


def ab_round(tid, force=False):
    """Check this business's running tests; keep each winner, pause the rest."""
    role = "owner"
    now = _dt.datetime.now(_dt.timezone.utc)
    with tenant_session(tid, role) as cur:
        cur.execute("SELECT %s FROM ad_drafts WHERE test->>'status' = 'testing' AND status = 'launched'" % DRAFT_COLS)
        tests = [dict(r) for r in cur.fetchall()]
    done = 0
    for d in tests:
        t = dict(d["test"])
        checked = t.get("checked_at")
        if not force and checked and now - _dt.datetime.fromisoformat(checked) < AB_CHECK_EVERY:
            continue
        try:
            tok, act, _ = ads.connection(tid, role, "meta")
            rows = ab_rows(tok, d["external"]["campaign_id"], t["ads"], d.get("destination") == "website")
        except AdsError as e:
            t.update(checked_at=now.isoformat(), note="Could not read the results: %s" % e)
            rows, tok = None, None
        if rows is not None:
            age = (now - _dt.datetime.fromisoformat(t["started_at"])).total_seconds() / 86400
            t.update(results={r["ad_id"]: r for r in rows}, checked_at=now.isoformat(), day=int(age) + 1)
            win, why = ab_verdict(rows) if age >= t.get("days", AB_DAYS) else (None, "")
            if not win and age >= t.get("max_days", AB_MAX_DAYS):
                # out of time: keep the cheapest per result (or, with no results at all, the most clicked)
                withres = [r for r in rows if r["results"] > 0]
                win = (min(withres, key=lambda r: r["spend"] / r["results"]) if withres
                       else max(rows, key=lambda r: r["clicks"]))
                why = ("no clear winner after %d days, so the planner kept the one with the lowest cost per result"
                       % int(age) if withres else "no results after %d days, so the planner kept the most clicked ad" % int(age))
            if win:
                paused, failed = [], []
                for r in rows:
                    if r["ad_id"] == win["ad_id"]:
                        continue
                    try:
                        ads.meta_set_status(tok, r["ad_id"], False)
                        paused.append(r["ad_id"])
                    except AdsError:
                        failed.append(r["ad_id"])
                t.update(status="done", winner=win["ad_id"], winner_n=win.get("n"), reason=why,
                         paused=paused, pause_failed=failed, decided_at=now.isoformat())
                done += 1
            else:
                t["note"] = ("Testing: day %d of %d." % (int(age) + 1, t.get("days", AB_DAYS)) if age < t.get("days", AB_DAYS)
                             else "Still testing: %s. The planner decides by day %d." % (why, t.get("max_days", AB_MAX_DAYS)))
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE ad_drafts SET test = %s WHERE id = %s", (json.dumps(t, default=str), d["id"]))
            if t.get("status") == "done" and t.get("decided_at") == now.isoformat():
                _log(cur, "meta", "ab_winner", d["external"].get("campaign_id") or "", "TechDeva · " + (d["service"] or ""),
                     "Kept creative %s: %s" % (t.get("winner_n"), t.get("reason")), "planner", not t.get("pause_failed"))
                record(cur, action="adpilot.ab_winner", resource="ad_drafts", resource_id=str(d["id"]), actor="planner",
                       actor_type="agent", status="ok", detail={"winner": t.get("winner"), "paused": t.get("paused"),
                                                                 "reason": t.get("reason")}, error=None)
    return done


def ab_due():
    """Scheduler hook: every business with a test still running (on Pro)."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT DISTINCT d.tenant_id FROM ad_drafts d
                         JOIN client_accounts a ON a.tenant_id = d.tenant_id AND a.plan_key = ANY(%s)
                        WHERE d.test->>'status' = 'testing' AND d.status = 'launched'""", (list(PLANS),))
        due = [str(r["tenant_id"]) for r in cur.fetchall()]
    n = 0
    for tid in due:
        try:
            n += ab_round(tid)
        except Exception as e:                      # one business's trouble never stops the rest
            print("[adpilot] A/B %s skipped: %s" % (tid, type(e).__name__), flush=True)
    return n


def run_due():
    """Scheduler hook: businesses on Pro with the autopilot switched on, whose
    last round is older than CHECK_EVERY."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT s.tenant_id FROM ad_pilot_settings s
                         JOIN client_accounts a ON a.tenant_id = s.tenant_id AND a.plan_key = ANY(%s)
                         JOIN tenants t ON t.id = s.tenant_id AND t.status::text IN ('active', 'trial')
                        WHERE (s.auto_pause OR s.meta_daily_cap > 0 OR s.google_daily_cap > 0)
                          AND (s.last_check_at IS NULL OR s.last_check_at < now() - %s::interval)""",
                    (list(PLANS), "%d hours" % int(CHECK_EVERY.total_seconds() // 3600)))
        due = [str(r["tenant_id"]) for r in cur.fetchall()]
    n = ab_due()
    for tid in due:
        try:
            n += run_tenant(tid)
        except Exception as e:                      # one business's trouble never stops the rest
            print("[adpilot] %s skipped: %s" % (tid, type(e).__name__), flush=True)
    return n


# ------------------------------------------------------------------ routes
def _draft_out(r):
    r = dict(r)
    r["daily_budget"] = float(r["daily_budget"])
    r["image_url"] = ("/media/%s.jpg" % r["image_token"]) if r.get("image_token") else None
    r.pop("image_token", None)
    plan = ab_plan(r)
    r["plan"] = {"ads": plan["ads"], "test": plan["test"], "left_out": plan["left_out"], "text": plan["text"],
                 "picked": [c["token"] for c in plan["picked"]]}
    r["video_url"] = ("/media/v/%s.mp4" % r["video_token"]) if r.get("video_token") else None
    r.pop("video_token", None)
    r["creatives"] = [{"kind": c["kind"], "token": c["token"],
                       "url": ("/media/v/%s.mp4" if c["kind"] == "video" else "/media/%s.jpg") % c["token"]}
                      for c in (r.get("creatives") or [])]
    return r


def make_adpilot_router():
    router = APIRouter()

    @router.get("/adpilot")
    def overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            s = get_settings(cur)
            cur.execute("SELECT %s FROM ad_drafts WHERE status <> 'discarded' ORDER BY created_at DESC LIMIT 20"
                        % DRAFT_COLS)
            drafts = [_draft_out(r) for r in cur.fetchall()]
            cur.execute("SELECT provider, kind, campaign_id, campaign_name, reason, by_who, status, error, created_at "
                        "FROM ad_actions ORDER BY created_at DESC LIMIT 30")
            log = [dict(r) for r in cur.fetchall()]
            photos = _latest_photos(cur)
            cur.execute("SELECT name FROM services WHERE coalesce(active, true) ORDER BY sort_order NULLS LAST, name "
                        "LIMIT 60")
            services = [r["name"] for r in cur.fetchall()]
            connected = {}
            for p in PROVIDERS:
                cur.execute("SELECT status = 'connected' AND coalesce(external_account, '') <> '' AS ok "
                            "FROM integrations WHERE provider = %s", (p,))
                r = cur.fetchone()
                connected[p] = bool(r and r["ok"])
            cur.execute("SELECT external_account FROM integrations WHERE provider = 'meta' AND status = 'connected'")
            r = cur.fetchone()
            meta_account = (r and r["external_account"]) or None
            pages = _page_list(cur, tid)
            default_page = _default_page(cur, pages)
        return {"meta_pages": pages, "meta_account": meta_account, "default_page": default_page,
                "settings": s, "drafts": drafts, "actions": log, "cities": sorted(CITIES), "services": services,
                "connected": connected,
                "countries": list(COUNTRIES), "photos": ["/media/%s.jpg" % t for t in photos],
                "min_daily": MIN_DAILY}

    @router.put("/adpilot/settings")
    def save_settings(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        f = _clean_settings(body)
        with tenant_session(tid, role) as cur:
            cur.execute("INSERT INTO ad_pilot_settings (tenant_id) VALUES (current_tenant()) ON CONFLICT DO NOTHING")
            if f:
                cur.execute("UPDATE ad_pilot_settings SET %s, updated_by = %%s, updated_at = now() "
                            "WHERE tenant_id = current_tenant()" % ", ".join("%s = %%s" % k for k in f),
                            list(f.values()) + [str(uid or role)])
            s = get_settings(cur)
            if s["auto_pause"] and not s["max_cost_per_result"]:
                raise HTTPException(400, "Set the most you want to pay per result before turning on auto-pause.")
            record(cur, action="adpilot.settings", resource="ad_pilot_settings", resource_id=str(tid),
                   actor=str(uid or role), actor_type="human", status="ok", detail=f, error=None)
        return {"ok": True, "settings": s}

    @router.post("/adpilot/pause")
    def pause(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        provider = str(body.get("provider") or "")
        if provider not in PROVIDERS:
            raise HTTPException(400, "Platform must be meta or google.")
        active = bool(body.get("resume"))
        if not re.match(r"^\d{1,30}$", str(body.get("campaign_id") or "")):
            raise HTTPException(400, "That campaign id is not valid.")
        ok, err = set_status(tid, role, uid, provider, str(body.get("campaign_id") or ""),
                             str(body.get("campaign_name") or "")[:200], active, "owner",
                             str(body.get("reason") or "")[:300])
        if not ok:
            raise HTTPException(502, err)
        return {"ok": True}

    @router.post("/adpilot/drafts")
    def new_draft(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner asks for a campaign: it is written and waits as a draft for
        the owner to check and press Launch (the owner is right here). Only the
        planner's own weekly round launches by itself, within the cap."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        d = draft(tid, role, uid, str(body.get("provider") or ""), (body.get("service") or None), "owner",
                  page_id=(str(body.get("page_id") or "").strip() or None))
        return {"draft": _draft_out(d)}

    @router.put("/adpilot/drafts/{draft_id}")
    def edit_draft(draft_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None), authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT %s FROM ad_drafts WHERE id::text = %%s" % DRAFT_COLS, (draft_id,))
            d = cur.fetchone()
            if not d or d["status"] not in ("draft", "failed"):
                raise HTTPException(409, "Only a campaign that has not launched can be changed.")
            copy = d["copy"]
            m, g = copy.setdefault("meta", {}), copy.setdefault("google", {})
            for k, n in (("headline", 40), ("primary_text", 300), ("description", 30)):
                if k in body:
                    v = re.sub(r"\s+", " ", str(body[k] or "")).strip()
                    if not v or len(v) > n:
                        raise HTTPException(400, "The %s must be 1 to %d characters." % (k.replace("_", " "), n))
                    m[k] = v
            if "hashtags" in body:
                m["hashtags"] = [t for t in (re.sub(r"[^A-Za-z0-9_]", "", str(x).lstrip("#"))
                                             for x in (body["hashtags"] or [])) if t][:10]
            for k, n_max, each, lo in (("headlines", 15, 30, 3), ("descriptions", 4, 90, 2), ("keywords", 20, 80, 1)):
                if k in body:
                    v = _clean_list(body[k], n_max, each, lo=lo)
                    if v is None:
                        raise HTTPException(400, "Give at least %d %s, each up to %d characters." % (lo, k, each))
                    g[k] = v
            budget = float(d["daily_budget"])
            if "daily_budget" in body:
                budget = _num(body["daily_budget"], "The daily budget", MIN_DAILY[d["provider"]], 10_000_000)
            img = d["image_token"]
            if "image_url" in body:
                tok = str(body["image_url"] or "").rsplit("/", 1)[-1].replace(".jpg", "")
                cur.execute("SELECT token FROM social_media WHERE token = %s", (tok,))
                if not cur.fetchone():
                    raise HTTPException(400, "Choose one of your own photos.")
                img = tok
            creatives = d.get("creatives") or []
            if "creatives" in body:
                from .videos import token_from_url, owns_video
                if not isinstance(body["creatives"], list) or len(body["creatives"]) > MAX_CREATIVES:
                    raise HTTPException(400, "Up to %d creatives per campaign." % MAX_CREATIVES)
                creatives = []
                for u in body["creatives"]:
                    u = str(u or "")
                    vt = token_from_url(u)
                    if vt:
                        if not owns_video(cur, vt):
                            raise HTTPException(400, "Choose your own videos.")
                        c = {"kind": "video", "token": vt}
                    else:
                        it = u.rsplit("/", 1)[-1].replace(".jpg", "")
                        cur.execute("SELECT token FROM social_media WHERE token = %s", (it,))
                        if not cur.fetchone():
                            raise HTTPException(400, "Choose your own photos and flyers.")
                        c = {"kind": "image", "token": it}
                    if c not in creatives:
                        creatives.append(c)
            video = d.get("video_token")
            if "video_url" in body:
                from .videos import token_from_url, owns_video
                if not body["video_url"]:
                    video = None
                else:
                    tok = token_from_url(body["video_url"]) or ""
                    if not tok or not owns_video(cur, tok):
                        raise HTTPException(400, "Choose one of your own videos.")
                    video = tok
            dest = d.get("destination") or "whatsapp"
            if "destination" in body and body["destination"] != dest:
                dest = str(body["destination"] or "")
                if dest not in DESTINATIONS:
                    raise HTTPException(400, "The button goes to WhatsApp or to your website.")
                if dest == "website" and not get_settings(cur).get("website_url"):
                    raise HTTPException(400, "Add your website in the ad autopilot rules first, then choose it here.")
                # the words must match the new button ("Order now" vs "Message us on WhatsApp");
                # only the default wording is rewritten, not text the owner typed in the same save
                if not any(k in body for k in ("headline", "primary_text", "description")):
                    biz, item, items = _business(cur, d["service"])
                    fresh = template_copy(biz, item, get_settings(cur).get("city") or "", dest)
                    m.update({k: fresh["meta"][k] for k in ("primary_text", "description")})
                    m["hashtags"] = fresh["meta"]["hashtags"]
            page = d["page_id"]
            if "page_id" in body and d["provider"] == "meta":
                page = str(body["page_id"] or "").strip() or None
                if page and page not in {p["id"] for p in _page_list(cur, tid)}:
                    raise HTTPException(400, "That Facebook Page is not connected. Choose one of your Pages.")
            cur.execute("UPDATE ad_drafts SET copy = %s, daily_budget = %s, image_token = %s, page_id = %s, "
                        "video_token = %s, destination = %s, creatives = %s WHERE id = %s",
                        (json.dumps(copy), budget, img, page, video, dest, json.dumps(creatives), d["id"]))
            record(cur, action="adpilot.draft_edit", resource="ad_drafts", resource_id=draft_id,
                   actor=str(uid or role), actor_type="human", status="ok",
                   detail={"fields": sorted(body)}, error=None)
        return {"ok": True}

    @router.post("/adpilot/drafts/{draft_id}/launch")
    def launch_draft(draft_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        r = launch(tid, role, uid, draft_id, "owner")
        if not r["launched"]:
            raise HTTPException(502, r.get("error") or "The campaign did not launch.")
        return r

    @router.delete("/adpilot/drafts/{draft_id}")
    def discard(draft_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE ad_drafts SET status = 'discarded' WHERE id::text = %s AND status IN ('draft','failed') "
                        "RETURNING id", (draft_id,))
            if not cur.fetchone():
                raise HTTPException(409, "Only a campaign that has not launched can be discarded.")
            record(cur, action="adpilot.discard", resource="ad_drafts", resource_id=draft_id, actor=str(uid or role),
                   actor_type="human", status="ok", detail={}, error=None)
        return {"ok": True}

    return router
