"""AI credits: every AI call counted per business and per agent (23 Oct 2026).

Faheem's plans give each business a monthly allowance of AI credits (plans.ai_credits,
or client_accounts.ai_credits_override for a Custom deal; NULL = no limit). What one
call costs is set here (COSTS), so a picture costs more than a line of text.

Every call is written to ai_usage: business, agent, feature, model, time taken and
whether it worked. Never the prompt or the reply: the log shows usage, not content.

Which business and agent a call belongs to comes from the meter:
  * web requests: CreditMeter (middleware) reads the signed-in business from the
    session and names the agent from the address (AGENT_BY_PATH);
  * background work: `with meter(tid, agent, feature):` where the job starts.
A call with no meter is logged as "unattributed", so a gap shows in the founder view.

Two modes (AI_CREDITS_MODE):
  * "count" (default): count, show the owner, warn at 80% and 100%, never stop.
  * "lock": owner tools (Jarvis, captions, pictures, plans, ad copy) stop at 100%
    with a plain message. Customer-facing replies (social inbox, WhatsApp) are
    never cut off mid-conversation; they keep counting and the founder sees it.
Start in "count" and switch after the allowances are checked against real costs.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import os
import time

from .db import tenant_session

COSTS = {"text": 1, "photo_read": 2, "voice": 1, "picture": 10, "video": 50}
MODE = os.environ.get("AI_CREDITS_MODE", "count").strip().lower()
# agents that answer a business's customers: never stopped by the allowance
CUSTOMER_FACING = {"social_inbox", "hermes", "whatsapp"}
PKT = dt.timezone(dt.timedelta(hours=5))
# the address a web request came in on -> the agent doing the work
AGENT_BY_PATH = (("/console", "jarvis"), ("/autopilot/caption", "marketing"), ("/autopilot", "content"),
                 ("/adpilot", "ads"), ("/advisor", "advisor"), ("/social/ai-picture", "design"),
                 ("/social-inbox", "social_inbox"), ("/business-profile", "setup"), ("/social", "marketing"))
_METER: contextvars.ContextVar = contextvars.ContextVar("td_ai_meter", default=None)
_CAP_CACHE: dict = {}
CAP_TTL = 60


@contextlib.contextmanager
def meter(tenant_id, agent, feature=None):
    """Charge the AI calls made inside this block to a business and an agent."""
    tok = _METER.set({"tenant_id": str(tenant_id) if tenant_id else None, "agent": agent, "feature": feature or agent})
    try:
        yield
    finally:
        _METER.reset(tok)


def current():
    return _METER.get()


def month_start():
    now = dt.datetime.now(PKT)
    return dt.datetime(now.year, now.month, 1, tzinfo=PKT)


def allowance(tenant_id):
    """{"cap": int|None, "used": int, "percent": int|None} for this month."""
    hit = _CAP_CACHE.get(tenant_id)
    if hit and hit[0] > time.time():
        return hit[1]
    from .plans import plan_for
    cap = plan_for(tenant_id).get("ai_credits")
    with tenant_session(tenant_id, "owner") as cur:
        cur.execute("SELECT COALESCE(sum(credits), 0) AS n FROM ai_usage WHERE at >= %s", (month_start(),))
        used = int(cur.fetchone()["n"])
    out = {"cap": cap, "used": used, "percent": round(100.0 * used / cap) if cap else None}
    _CAP_CACHE[tenant_id] = (time.time() + CAP_TTL, out)
    return out


def blocked(tenant_id, agent=None):
    """The reason this business may not use the AI now, or None. Only in lock mode."""
    if MODE != "lock" or not tenant_id or agent in CUSTOMER_FACING:
        return None
    try:
        a = allowance(tenant_id)
    except Exception as e:  # noqa: BLE001 -- the count failing must never stop a business
        print("[usage] allowance check skipped: %s" % str(e)[:200], flush=True)
        return None
    if a["cap"] is not None and a["used"] >= a["cap"]:
        return ("This month's %s AI credits are used. They renew on the 1st; to keep going now, "
                "move to a bigger plan on Plan & billing." % format(a["cap"], ","))
    return None


def check():
    """Called before an AI call: the reason the metered business may not use it, or None."""
    m = _METER.get()
    return blocked(m and m["tenant_id"], m and m["agent"])


def charge(kind, ok, *, provider=None, model=None, ms=None, chars_in=None, chars_out=None, error=None,
           tenant_id=None, agent=None, feature=None):
    """Write one AI call to the log. Never raises: counting must not break the work."""
    m = _METER.get() or {}
    tid = tenant_id or m.get("tenant_id")
    try:
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO ai_usage (tenant_id, agent, feature, kind, credits, ok, provider, model, ms,
                                                 chars_in, chars_out, error)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (tid, (agent or m.get("agent") or "unattributed")[:40], (feature or m.get("feature") or "unknown")[:60],
                         kind, COSTS.get(kind, 1) if ok else 0, bool(ok), provider, (model or "")[:80] or None,
                         int(ms) if ms is not None else None, chars_in, chars_out, (error or "")[:300] or None))
        if tid and ok:
            _CAP_CACHE.pop(tid, None)
    except Exception as e:  # noqa: BLE001
        print("[usage] not recorded: %s" % str(e)[:200], flush=True)


def _tenant_from_request(scope):
    from .auth import SESSION_COOKIE, resolve_scope
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
    cookie = headers.get("cookie", "")
    sess = None
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == SESSION_COOKIE:
            sess = v
    try:
        tid, _role, _uid = resolve_scope(headers.get("x-tenant-id"), headers.get("x-role"),
                                         headers.get("authorization"), sess)
        return tid
    except Exception:  # noqa: BLE001 -- not signed in: the endpoint itself refuses
        return None


class CreditMeter:
    """ASGI middleware: AI calls made while serving a request are charged to the
    signed-in business, under the agent its address belongs to."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        agent = next((a for p, a in AGENT_BY_PATH if path.startswith(p)), None)
        if not agent:
            return await self.app(scope, receive, send)
        tid = _tenant_from_request(scope)
        words = [w for w in path.strip("/").split("/") if w and not any(ch.isdigit() for ch in w)]
        tok = _METER.set({"tenant_id": tid, "agent": agent, "feature": "/".join(words[:3])[:60] or agent})
        try:
            await self.app(scope, receive, send)
        finally:
            _METER.reset(tok)


AGENT_NAMES = {"jarvis": "Jarvis", "marketing": "Marketing", "content": "Content planner", "ads": "Ad campaigns",
               "advisor": "Growth advisor", "design": "AI pictures", "social_inbox": "Social inbox replies",
               "setup": "Business setup", "quality": "Reply checks", "unattributed": "Other"}


def make_usage_router():
    from fastapi import APIRouter, Body, Cookie, Header, HTTPException

    from .audit import record
    from .auth import SESSION_COOKIE
    from .operations import _identity

    router = APIRouter()

    @router.get("/usage/ai")
    def mine(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """This business's AI credits this month: by agent, by day, and the latest calls."""
        tid, role, _uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Usage is on the owner's Plan & billing.")
        _CAP_CACHE.pop(tid, None)
        a = allowance(tid)
        since = month_start()
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT agent, sum(credits) AS credits, count(*) AS calls, count(*) FILTER (WHERE NOT ok) AS failed
                             FROM ai_usage WHERE at >= %s GROUP BY agent ORDER BY 2 DESC""", (since,))
            by_agent = [dict(r, name=AGENT_NAMES.get(r["agent"], r["agent"])) for r in cur.fetchall()]
            cur.execute("""SELECT (at AT TIME ZONE 'Asia/Karachi')::date AS day, sum(credits) AS credits, count(*) AS calls
                             FROM ai_usage WHERE at >= %s GROUP BY 1 ORDER BY 1""", (since,))
            by_day = [{"day": r["day"].isoformat(), "credits": int(r["credits"]), "calls": r["calls"]} for r in cur.fetchall()]
            cur.execute("""SELECT at, agent, feature, kind, credits, ok FROM ai_usage ORDER BY at DESC LIMIT 100""")
            recent = [dict(r, name=AGENT_NAMES.get(r["agent"], r["agent"])) for r in cur.fetchall()]
            cur.execute("""SELECT to_char(date_trunc('month', at AT TIME ZONE 'Asia/Karachi'), 'YYYY-MM') AS month, sum(credits) AS credits
                             FROM ai_usage WHERE at >= %s - interval '6 months' GROUP BY 1 ORDER BY 1""", (since,))
            months = [{"month": r["month"], "credits": int(r["credits"])} for r in cur.fetchall()]
        for r in by_agent:
            r["credits"] = int(r["credits"])
        return {"cap": a["cap"], "used": a["used"], "percent": a["percent"], "mode": MODE, "since": since.date().isoformat(),
                "costs": COSTS, "by_agent": by_agent, "by_day": by_day, "months": months, "recent": recent,
                "warning": None if a["percent"] is None else ("over" if a["percent"] >= 100 else ("near" if a["percent"] >= 80 else None))}

    def _admin(*a):
        tid, role, uid = _identity_any(*a)
        if role != "techdeva_admin":
            raise HTTPException(403, "admin only")
        return uid

    def _identity_any(x_tenant_id, x_role, authorization, session):
        from .auth import AuthError, resolve_scope
        try:
            return resolve_scope(x_tenant_id, x_role, authorization, session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)

    @router.get("/techdeva/ai-usage")
    def founder(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Founder: every business's AI credits this month, and the latest AI calls (no content)."""
        _admin(x_tenant_id, x_role, authorization, td_session)
        from .plans import plan_for
        since = month_start()
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT t.id::text AS tenant_id, t.name, t.is_test,
                                  COALESCE(sum(u.credits), 0) AS credits, count(u.id) AS calls,
                                  count(u.id) FILTER (WHERE NOT u.ok) AS failed, avg(u.ms) FILTER (WHERE u.ok) AS avg_ms
                             FROM tenants t LEFT JOIN ai_usage u ON u.tenant_id = t.id AND u.at >= %s
                            GROUP BY t.id, t.name, t.is_test ORDER BY 4 DESC, t.name""", (since,))
            clients = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT COALESCE(sum(credits), 0) AS credits, count(*) AS calls FROM ai_usage
                            WHERE tenant_id IS NULL AND at >= %s""", (since,))
            platform = dict(cur.fetchone())
            cur.execute("""SELECT u.at, u.tenant_id::text AS tenant_id, t.name, u.agent, u.feature, u.kind, u.credits, u.ok,
                                  u.provider, u.model, u.ms, u.chars_in, u.chars_out, u.error
                             FROM ai_usage u LEFT JOIN tenants t ON t.id = u.tenant_id ORDER BY u.at DESC LIMIT 200""")
            calls = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT model, count(*) AS calls, count(*) FILTER (WHERE NOT ok) AS failed, avg(ms) AS avg_ms
                             FROM ai_usage WHERE at >= %s GROUP BY model ORDER BY 2 DESC""", (since,))
            models = [dict(r) for r in cur.fetchall()]
        for c in clients:
            p = plan_for(c["tenant_id"])
            c.update(credits=int(c["credits"]), plan=p["name"], cap=p.get("ai_credits"), custom_cap=p.get("ai_credits_custom"),
                     percent=round(100.0 * int(c["credits"]) / p["ai_credits"]) if p.get("ai_credits") else None,
                     avg_ms=int(c["avg_ms"]) if c["avg_ms"] is not None else None)
        for m in models:
            m["avg_ms"] = int(m["avg_ms"]) if m["avg_ms"] is not None else None
        return {"mode": MODE, "since": since.date().isoformat(), "costs": COSTS, "clients": clients,
                "platform": {"credits": int(platform["credits"]), "calls": platform["calls"]}, "models": models, "calls": calls}

    @router.put("/techdeva/clients/{tenant_id}/ai-credits")
    def set_credits(tenant_id: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Founder: a business's own monthly AI credits (a Custom deal). Empty = back to its plan's."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        raw = body.get("ai_credits")
        if raw in (None, ""):
            credits = None
        else:
            try:
                credits = int(str(raw).replace(",", ""))
            except ValueError:
                raise HTTPException(400, "AI credits must be a whole number (or empty for the plan's).")
            if not 0 <= credits <= 10000000:
                raise HTTPException(400, "AI credits must be between 0 and 10,000,000.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT 1 FROM tenants WHERE id::text = %s", (tenant_id,))
            if not cur.fetchone():
                raise HTTPException(404, "No such business.")
            cur.execute("""INSERT INTO client_accounts (tenant_id, ai_credits_override) VALUES (%s, %s)
                           ON CONFLICT (tenant_id) DO UPDATE SET ai_credits_override = EXCLUDED.ai_credits_override,
                             updated_at = now()""", (tenant_id, credits))
            record(cur, action="techdeva.client.ai_credits", resource="client_accounts", resource_id=tenant_id,
                   actor=uid, status="ok", detail={"ai_credits": credits}, tenant_id=tenant_id)
        _CAP_CACHE.pop(tenant_id, None)
        return {"ok": True, "ai_credits": credits}

    return router
