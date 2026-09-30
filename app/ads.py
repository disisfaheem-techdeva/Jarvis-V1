"""Ad accounts and live ad numbers (Part 18): Facebook + Instagram, Google.

Connecting is the OAuth flow in oauth.py (Connect buttons on Integrations).
This module is what comes after it, all under the business's own tenant fence:

  GET  /ads/status             what is connected, which ad account is used,
                               the Facebook pages and Instagram accounts found
  GET  /ads/accounts?provider  the ad accounts the login can see (live)
  PUT  /ads/accounts           choose which one this business uses
  GET  /ads/performance?days   spend, reach, clicks, leads, WhatsApp chats per
                               campaign, read live from Meta / Google Ads
  POST /ads/disconnect/{p}     forget the login (tokens wiped)

Read-only towards the ad platforms: nothing here creates, edits or pauses an
ad. Tokens never leave the server; the answers carry names and numbers only.
Owner only: ad spend and the ad accounts are the owner's business.
"""
import json
import re
import os
import time
from datetime import date, timedelta

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .crypto import decrypt_json, encrypt_json
from .db import tenant_session
from . import oauth

GADS_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v21").strip() or "v21"
GADS = (os.environ.get("GOOGLE_ADS_BASE_URL", "https://googleads.googleapis.com").strip().rstrip("/")
        + "/" + GADS_VERSION)
GOOGLE_TOKEN_URL = os.environ.get("GOOGLE_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token").strip()
GADS_DEV_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip().strip("\"'")
# Set when the business's Google Ads account is reached through a manager
# (MCC) account, e.g. TechDeva's own manager account.
GADS_LOGIN_CID = "".join(c for c in os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "") if c.isdigit())
TIMEOUT = 25
_CACHE = {}          # (tenant, what) -> (expires, value)
PERF_TTL = 900       # the ad platforms update spend a few times an hour at best
ACCT_TTL = 300


class AdsError(Exception):
    """A reason fit to show the owner (never a token)."""


def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "Open a business first.")
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can see or change the ad accounts.")
    return tid, role, uid


def _cached(tid, what, ttl, fn):
    k = (str(tid), what)
    hit = _CACHE.get(k)
    if hit and hit[0] > time.time():
        return hit[1]
    v = fn()
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[k] = (time.time() + ttl, v)
    return v


def forget(tid):
    for k in [k for k in _CACHE if k[0] == str(tid)]:
        _CACHE.pop(k, None)


def _row(cur, tid, provider):
    cur.execute("SELECT status, credentials, external_account, connected_at, last_error "
                "FROM integrations WHERE provider = %s", (provider,))
    r = cur.fetchone()
    if not r or r["status"] != "connected":
        return None, None
    return r, decrypt_json(r["credentials"], tid, provider)


# ------------------------------------------------------------------ Meta
def _meta_get(path, token, params=None):
    p = dict(params or {})
    p["access_token"] = token
    try:
        r = requests.get(oauth.META_GRAPH + path, params=p, timeout=TIMEOUT)
    except requests.RequestException:
        raise AdsError("Facebook did not answer. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        e = body.get("error") or {}
        if e.get("code") == 190:
            raise AdsError("The Facebook login has expired or was removed. Press Reconnect Facebook.")
        raise AdsError("Facebook said: %s" % str(e.get("message") or r.text)[:240])
    return body


def meta_accounts(token):
    body = _meta_get("/me/adaccounts", token, {"fields": "name,account_id,currency,account_status", "limit": 50})
    return [{"id": str(a.get("account_id")), "name": a.get("name") or ("Ad account " + str(a.get("account_id"))),
             "currency": a.get("currency"), "active": a.get("account_status") == 1}
            for a in body.get("data") or [] if a.get("account_id")]


_META_PRESET = {7: "last_7d", 30: "last_30d", 90: "last_90d"}


def _action(actions, *kinds):
    for k in kinds:                      # first kind present wins (no double count)
        for a in actions or []:
            if a.get("action_type") == k:
                try:
                    return float(a.get("value") or 0)
                except ValueError:
                    return 0.0
    return 0.0


def meta_performance(token, act_id, days):
    body = _meta_get("/act_%s/insights" % act_id, token, {
        "level": "campaign", "date_preset": _META_PRESET[days], "limit": 100,
        "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,account_currency"})
    rows, cur = [], None
    status = meta_campaign_status(token, act_id)
    for c in body.get("data") or []:
        cur = cur or c.get("account_currency")
        cid = str(c.get("campaign_id") or "")
        rows.append({"id": cid, "status": status.get(cid), "name": c.get("campaign_name") or "",
                     "spend": float(c.get("spend") or 0),
                     "impressions": int(float(c.get("impressions") or 0)), "clicks": int(float(c.get("clicks") or 0)),
                     "leads": _action(c.get("actions"), "lead", "onsite_conversion.lead_grouped"),
                     "chats": _action(c.get("actions"), "onsite_conversion.messaging_conversation_started_7d")})
    return rows, cur


def meta_campaign_status(token, act_id):
    """{campaign id: 'active' | 'paused' | other}, empty if Facebook will not say."""
    try:
        body = _meta_get("/act_%s/campaigns" % act_id, token, {"fields": "id,effective_status", "limit": 200})
    except AdsError:
        return {}
    return {str(c.get("id")): str(c.get("effective_status") or "").lower() for c in body.get("data") or []}


def _meta_post(path, token, data):
    d = {k: (json.dumps(v) if isinstance(v, (dict, list, bool)) else v) for k, v in data.items()}
    d["access_token"] = token
    try:
        r = requests.post(oauth.META_GRAPH + path, data=d, timeout=TIMEOUT)
    except requests.RequestException:
        raise AdsError("Facebook did not answer. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        e = body.get("error") or {}
        if e.get("code") == 190:
            raise AdsError("The Facebook login has expired or was removed. Press Reconnect Facebook.")
        raise AdsError("Facebook said: %s" % str(e.get("error_user_msg") or e.get("message") or r.text)[:240])
    return body


def meta_set_status(token, campaign_id, active):
    _meta_post("/%s" % campaign_id, token, {"status": "ACTIVE" if active else "PAUSED"})


# ------------------------------------------------------------------ Google
def _google_token(cur, tid, row, creds):
    """A fresh access token, refreshed with the stored refresh token."""
    if creds.get("access_token") and float(creds.get("expires_at") or 0) > time.time() + 60:
        return creds["access_token"]
    if not creds.get("refresh_token"):
        raise AdsError("Google did not give a lasting login. Press Reconnect Google and allow access again.")
    if not (oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_SECRET):
        raise AdsError("Google is not set up on this server yet.")
    try:
        r = requests.post(GOOGLE_TOKEN_URL, timeout=TIMEOUT, data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": oauth.GOOGLE_CLIENT_ID, "client_secret": oauth.GOOGLE_SECRET})
        body = r.json()
    except (requests.RequestException, ValueError):
        raise AdsError("Google did not answer. Try again in a minute.")
    if r.status_code >= 400 or not body.get("access_token"):
        if body.get("error") == "invalid_grant":
            raise AdsError("The Google login has expired or was removed. Press Reconnect Google.")
        raise AdsError("Google refused the login: %s" % str(body.get("error_description") or body.get("error"))[:200])
    creds = dict(creds, access_token=body["access_token"],
                 expires_at=time.time() + float(body.get("expires_in") or 3600))
    cur.execute("UPDATE integrations SET credentials = %s::jsonb WHERE provider = 'google'",
                (json.dumps(encrypt_json(creds, tid, "google")),))
    return creds["access_token"]


def _gads(method, path, token, json_body=None):
    # Google moved API access levels from the developer token to the Cloud
    # project (Sept 2026). A token is sent only when one is configured; if
    # Google still wants one, its own error message reaches the owner.
    h = {"Authorization": "Bearer " + token}
    if GADS_DEV_TOKEN:
        h["developer-token"] = GADS_DEV_TOKEN
    if GADS_LOGIN_CID:
        h["login-customer-id"] = GADS_LOGIN_CID
    try:
        r = requests.request(method, GADS + path, headers=h, json=json_body, timeout=TIMEOUT)
    except requests.RequestException:
        raise AdsError("Google Ads did not answer. Try again in a minute.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if isinstance(body, list):            # searchStream answers a list
        return body
    if r.status_code >= 400 or "error" in body:
        e = body.get("error") or {}
        detail = ""
        for d in e.get("details") or []:
            for er in d.get("errors") or []:
                detail = er.get("message") or detail
        raise AdsError("Google Ads said: %s" % str(detail or e.get("message") or r.text)[:240])
    return body


def google_accounts(token):
    body = _gads("GET", "/customers:listAccessibleCustomers", token)
    out = []
    for rn in (body.get("resourceNames") or [])[:25]:
        cid = rn.split("/")[-1]
        item = {"id": cid, "name": "Google Ads %s-%s-%s" % (cid[:3], cid[3:6], cid[6:]), "currency": None,
                "manager": False}
        try:
            res = _gads("POST", "/customers/%s/googleAds:search" % cid, token, {"query":
                  "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.manager "
                  "FROM customer LIMIT 1"}).get("results") or []
            c = (res[0] if res else {}).get("customer") or {}
            item.update(name=c.get("descriptiveName") or item["name"], currency=c.get("currencyCode"),
                        manager=bool(c.get("manager")))
        except AdsError:
            pass          # listed but not readable directly (e.g. under another manager)
        out.append(item)
    return out


def google_performance(token, cid, days):
    if days in (7, 30):
        cond = "DURING LAST_%d_DAYS" % days
    else:
        end = date.today() - timedelta(days=1)
        cond = "BETWEEN '%s' AND '%s'" % (end - timedelta(days=days - 1), end)
    q = ("SELECT campaign.id, campaign.name, campaign.status, customer.currency_code, metrics.cost_micros, "
         "metrics.impressions, metrics.clicks, metrics.conversions FROM campaign "
         "WHERE segments.date %s AND metrics.impressions > 0" % cond)
    rows, cur, token_ = {}, None, None
    for _ in range(5):                                    # at most 5 pages
        body = _gads("POST", "/customers/%s/googleAds:search" % cid, token,
                     dict({"query": q}, **({"pageToken": token_} if token_ else {})))
        for r in body.get("results") or []:
            c, m = r.get("campaign") or {}, r.get("metrics") or {}
            cur = cur or (r.get("customer") or {}).get("currencyCode")
            gid = str(c.get("id") or c.get("name") or "")
            x = rows.setdefault(gid, {"id": str(c.get("id") or ""), "name": c.get("name") or "",
                                      "status": {"ENABLED": "active", "PAUSED": "paused"}.get(c.get("status"),
                                                                                             str(c.get("status") or "").lower()),
                                      "spend": 0.0, "impressions": 0, "clicks": 0, "leads": 0.0, "chats": 0.0})
            x["spend"] += float(m.get("costMicros") or 0) / 1e6
            x["impressions"] += int(m.get("impressions") or 0)
            x["clicks"] += int(m.get("clicks") or 0)
            x["leads"] += float(m.get("conversions") or 0)
        token_ = body.get("nextPageToken")
        if not token_:
            break
    return list(rows.values()), cur


def google_set_status(token, cid, campaign_id, active):
    _gads("POST", "/customers/%s/campaigns:mutate" % cid, token, {"operations": [{
        "update": {"resourceName": "customers/%s/campaigns/%s" % (cid, campaign_id),
                   "status": "ENABLED" if active else "PAUSED"},
        "updateMask": "status"}]})


def connection(tid, role, provider):
    """(token, account id) for a connected platform with an ad account chosen,
    or raises AdsError with a reason fit for the owner."""
    with tenant_session(tid, role) as cur:
        row, creds = _row(cur, tid, provider)
        if not row:
            raise AdsError("%s is not connected (Integrations)." % ("Facebook" if provider == "meta" else "Google"))
        if not row["external_account"]:
            raise AdsError("Choose which ad account to use (Integrations).")
        tok = creds.get("access_token") if provider == "meta" else _google_token(cur, tid, row, creds)
        return tok, row["external_account"], creds


def set_campaign_status(tid, role, provider, campaign_id, active):
    if not re.match(r"^\d{1,30}$", str(campaign_id or "")):
        raise AdsError("That campaign id is not valid.")
    tok, acct, _ = connection(tid, role, provider)
    if provider == "meta":
        meta_set_status(tok, campaign_id, active)
    else:
        google_set_status(tok, acct, campaign_id, active)
    forget(tid)


def google_autoselect(tid):
    """Right after Google connects: if the login sees exactly one ordinary
    Google Ads account, use it. Anything else waits for the owner to choose."""
    with tenant_session(tid, "owner") as cur:
        row, creds = _row(cur, tid, "google")
        if not row:
            return None
        tok = _google_token(cur, tid, row, creds)
    accts = [a for a in google_accounts(tok) if not a.get("manager")]
    if len(accts) == 1:
        with tenant_session(tid, "owner") as cur:
            cur.execute("UPDATE integrations SET external_account = %s WHERE provider = 'google'", (accts[0]["id"],))
        return accts[0]["id"]
    return None


# ------------------------------------------------------------------ helpers
def _totals(rows):
    t = {"spend": 0.0, "impressions": 0, "clicks": 0, "leads": 0.0, "chats": 0.0}
    for r in rows:
        for k in t:
            t[k] += r.get(k) or 0
    t["spend"] = round(t["spend"], 2)
    return t


def _provider_status(cur, tid, provider):
    cur.execute("SELECT status, external_account, connected_at, last_error FROM integrations WHERE provider = %s",
                (provider,))
    r = cur.fetchone()
    return {"connected": bool(r and r["status"] == "connected"), "status": (r or {}).get("status") or "never_connected",
            "account": (r or {}).get("external_account") or None, "connected_at": (r or {}).get("connected_at"),
            "last_error": (r or {}).get("last_error")}


def performance_for(tid, role, days):
    """Live ad results per platform for the last `days` days (cached 15 min).
    Used by the Ad results page and the growth advisor."""
    def one(provider):
        out = {"provider": provider, "connected": False, "account": None, "campaigns": [],
               "totals": None, "currency": None, "error": None}
        try:
            with tenant_session(tid, role) as cur:
                row, creds = _row(cur, tid, provider)
                if not row:
                    return out
                out["connected"], out["account"] = True, row["external_account"] or None
                if not out["account"]:
                    out["error"] = "Choose which ad account to show (Integrations)."
                    return out
                tok = creds.get("access_token") if provider == "meta" else _google_token(cur, tid, row, creds)
            rows, cur_ = (meta_performance(tok, out["account"], days) if provider == "meta"
                          else google_performance(tok, out["account"], days))
            rows.sort(key=lambda r: -r["spend"])
            out.update(campaigns=rows, totals=_totals(rows), currency=cur_)
        except AdsError as e:
            out["error"] = str(e)
        return out

    return _cached(tid, "perf:%d" % days, PERF_TTL, lambda: {
        "days": days, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platforms": [one("meta"), one("google")]})


def make_ads_router():
    router = APIRouter(prefix="/ads", tags=["ads"])

    @router.get("/status")
    def status(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            meta, google = _provider_status(cur, tid, "meta"), _provider_status(cur, tid, "google")
            pages = []
            if meta["connected"]:
                _, creds = _row(cur, tid, "meta")
                pages = [{"name": p.get("name"), "instagram": p.get("ig_username")}
                         for p in (creds or {}).get("pages") or []]
                meta["ad_accounts_seen"] = len((creds or {}).get("ad_accounts") or [])
        meta["pages"] = pages
        return {"setup": {"facebook": bool(oauth.META_APP_ID and oauth.META_APP_SECRET),
                          "google": bool(oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_SECRET),
                          "google_ads_reporting": bool(oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_SECRET)},
                "meta": meta, "google": google}

    def _live_accounts(tid, role, provider):
        with tenant_session(tid, role) as cur:
            row, creds = _row(cur, tid, provider)
            if not row:
                raise AdsError("Connect %s first." % ("Facebook" if provider == "meta" else "Google"))
            tok = creds.get("access_token") if provider == "meta" else _google_token(cur, tid, row, creds)
        return meta_accounts(tok) if provider == "meta" else google_accounts(tok)

    @router.get("/accounts")
    def accounts(provider: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if provider not in ("meta", "google"):
            raise HTTPException(400, "provider must be meta or google")
        try:
            return {"provider": provider,
                    "accounts": _cached(tid, "acct:" + provider, ACCT_TTL, lambda: _live_accounts(tid, role, provider))}
        except AdsError as e:
            raise HTTPException(409, str(e))

    @router.put("/accounts")
    def choose(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        provider, acct = str(body.get("provider") or ""), str(body.get("account_id") or "").strip()
        if provider not in ("meta", "google"):
            raise HTTPException(400, "provider must be meta or google")
        try:
            live = _live_accounts(tid, role, provider)
        except AdsError as e:
            raise HTTPException(409, str(e))
        hit = [a for a in live if a["id"] == acct]
        if not hit:
            raise HTTPException(404, "That ad account is not one this login can see.")
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE integrations SET external_account = %s WHERE provider = %s AND status = 'connected'",
                        (acct, provider))
            record(cur, action="ads.account_choose", resource="integrations", resource_id=provider,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"provider": provider, "account": acct, "name": hit[0]["name"]}, error=None)
        forget(tid)
        return {"ok": True, "account": hit[0]}

    @router.get("/performance")
    def performance(days: int = 30, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if days not in (7, 30, 90):
            raise HTTPException(400, "days must be 7, 30 or 90")

        return performance_for(tid, role, days)

    @router.post("/disconnect/{provider}")
    def disconnect(provider: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if provider not in ("meta", "google"):
            raise HTTPException(400, "provider must be meta or google")
        names = ("meta", "instagram") if provider == "meta" else ("google",)
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE integrations SET status = 'disconnected', credentials = '{}'::jsonb, "
                        "disconnected_at = now(), external_account = NULL WHERE provider = ANY(%s) "
                        "RETURNING provider", (list(names),))
            gone = [r["provider"] for r in cur.fetchall()]
            record(cur, action="integration.disconnect", resource="integrations", resource_id=provider,
                   actor=str(uid), actor_type="human", status="ok", detail={"providers": gone}, error=None)
        forget(tid)
        return {"ok": True, "disconnected": gone}

    return router
