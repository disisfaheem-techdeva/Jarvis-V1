"""The trial lock (Faheem's decision, 29 Sep 2026): the free trial is 7 days;
when it ends unpaid, the business's dashboard is locked and asks them to pay.

What is locked: requests made with a business user's login (owner or staff)
for a business whose status is still 'trial' and whose trial_ends_at has
passed. They get HTTP 402 with a plain message, except the few things needed
to see the lock screen and pay (sign in/out, /me, the business name and logo,
Plan & Billing).

Also locked: 'paused' businesses (an ended trial is moved to 'paused' by the
scheduler within seconds; the founder can pause one too).

What is NOT locked: the founder (techdeva_admin); the business's WhatsApp agent
(it runs in its own containers and does not use a dashboard login, so patients
keep getting answers); the worker; paid ('active') businesses.

It unlocks the moment the founder confirms a payment (billing.confirm sets the
business 'active'), or extends the trial.
"""
import json
import time

from .auth import AuthError, SESSION_COOKIE, token_from, verify_token
from .db import tenant_session

TRIAL_DAYS = 7
MESSAGE = ("Your free trial has ended. Please pay to keep using your dashboard. "
           "Your WhatsApp agent is still answering your customers.")
OPEN_EXACT = {"/", "/dashboard", "/reset", "/pin", "/onboarding", "/portal", "/techdeva", "/health",
              "/me", "/portal/summary", "/portal/config", "/console/prefs", "/brand", "/brand/logo"}
OPEN_PREFIX = ("/auth/", "/billing", "/public/", "/media/", "/signup")
_CACHE: dict = {}
TTL = 20


def locked(tenant_id) -> bool:
    if not tenant_id:
        return False
    now = time.time()
    hit = _CACHE.get(tenant_id)
    if hit and hit[0] > now:
        return hit[1]
    with tenant_session(None, "techdeva_admin") as cur:
        # 'paused' counts too: the scheduler moves an ended trial to 'paused'
        # within seconds (persistence.mark_trial_flagged), so checking 'trial'
        # alone never locked anything on the live server.
        cur.execute("SELECT status::text = 'paused' OR (status::text = 'trial' AND trial_ends_at IS NOT NULL "
                    "AND trial_ends_at < now()) AS locked FROM tenants WHERE id = %s", (str(tenant_id),))
        row = cur.fetchone()
    value = bool(row and row["locked"])
    _CACHE[tenant_id] = (now + TTL, value)
    if len(_CACHE) > 5000:
        _CACHE.clear()
    return value


def forget(tenant_id):
    """Call after a status change so the lock follows at once, not in 20 s."""
    _CACHE.pop(str(tenant_id), None)


def install(app):
    """The lock as one middleware, so no endpoint can forget to check it."""
    from starlette.responses import Response

    @app.middleware("http")
    async def trial_lock(request, call_next):
        path = request.url.path
        if path in OPEN_EXACT or path.startswith(OPEN_PREFIX) or request.method == "OPTIONS":
            return await call_next(request)
        tok = token_from(request.headers.get("authorization"), request.cookies.get(SESSION_COOKIE))
        if tok:
            try:
                claims = verify_token(tok)
            except AuthError:
                claims = None
            if claims and claims.get("r") in ("owner", "operator") and claims.get("t"):
                from starlette.concurrency import run_in_threadpool
                if await run_in_threadpool(locked, claims["t"]):
                    return Response(json.dumps({"detail": MESSAGE, "locked": True, "reason": "trial_ended"}),
                                    status_code=402, media_type="application/json")
        return await call_next(request)
