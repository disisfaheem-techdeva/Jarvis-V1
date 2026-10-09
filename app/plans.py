"""Plans and their limits (Faheem, 29 Sep 2026):

    Starter  PKR 20,000   1 WhatsApp number    500 conversations / month
    Growth   PKR 34,999   2 WhatsApp numbers  1,500 conversations / month
    Pro      (price set by Faheem)  5 numbers  4,000 conversations / month

Prices and limits live in the `plans` table and are edited in the founder
console; nothing here hard-codes a price. A business with no plan yet (for
example on its free trial) gets Starter limits.

A "conversation" is one customer (one WhatsApp contact) who wrote to one of
the business's numbers this calendar month, Pakistan time. Counted by each
agent's pairing sidecar (GET /usage). An older sidecar without /usage is
counted from its chat list instead, and the result is marked approximate.

Limits:
  * numbers        hard: a business cannot request more numbers than its plan.
  * conversations  soft: counted, shown to the owner with a warning at 80% and
                   100%, and flagged to the founder. The agent is never cut off
                   in the middle of talking to a patient.
"""
import datetime as dt
import json
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

DEFAULT_PLAN = "starter"
PKT = dt.timezone(dt.timedelta(hours=5))
_USAGE: dict = {}
USAGE_TTL = 600


def month_start():
    now = dt.datetime.now(PKT)
    return int(dt.datetime(now.year, now.month, 1, tzinfo=PKT).timestamp())


def all_plans(cur):
    cur.execute("SELECT key, name, price_pkr, price_usd, max_numbers, max_conversations FROM plans ORDER BY sort, key")
    out = []
    for r in cur.fetchall():
        r = dict(r)
        r["price_pkr"] = float(r["price_pkr"]) if r["price_pkr"] is not None else None
        r["price_usd"] = float(r["price_usd"]) if r["price_usd"] is not None else None
        out.append(r)
    return out


def plan_for(tenant_id):
    """The plan a business is on (Starter limits when none is set)."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT p.key, p.name, p.price_pkr, p.max_numbers, p.max_conversations,
                              (a.plan_key IS NOT NULL) AS chosen
                         FROM plans p
                         LEFT JOIN client_accounts a ON a.tenant_id = %s
                        WHERE p.key = COALESCE(a.plan_key, %s)""", (str(tenant_id), DEFAULT_PLAN))
        r = cur.fetchone()
    if not r:
        return {"key": DEFAULT_PLAN, "name": "Starter", "price_pkr": None, "max_numbers": 1,
                "max_conversations": 300, "chosen": False}
    r = dict(r)
    r["price_pkr"] = float(r["price_pkr"]) if r["price_pkr"] is not None else None
    return r


def _pairctl_get(url, path):
    from .whatsapp import _key
    key = _key()
    if not key:
        raise RuntimeError("no internal key")
    req = urllib.request.Request(url.rstrip("/") + path, headers={"x-internal-key": key})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode() or "{}")


def _count_one(pairctl_url, since):
    """(customers, approximate?) for one number, or (None, reason)."""
    try:
        return int(_pairctl_get(pairctl_url, "/usage?since=%d" % since).get("customers") or 0), False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return None, "the agent answered HTTP %s" % e.code
    except Exception as e:                      # agent down: say so, do not guess
        return None, "the agent is not reachable (%s)" % type(e).__name__
    # older sidecar: count distinct customers in its chat list (newest 150 chats)
    try:
        chats = _pairctl_get(pairctl_url, "/chats").get("chats") or []
    except Exception as e:
        return None, "the agent is not reachable (%s)" % type(e).__name__
    iso = dt.datetime.fromtimestamp(since, dt.timezone.utc).isoformat()
    people = {c.get("phone") or c.get("session_id") for c in chats
              if (c.get("last_at") or c.get("started_at") or "") >= iso}
    return len(people), True


def usage(tenant_id, fresh=False):
    """This month's WhatsApp conversations for a business, across its numbers."""
    tenant_id = str(tenant_id)
    hit = _USAGE.get(tenant_id)
    if hit and hit[0] > time.time() and not fresh:
        return hit[1]
    since = month_start()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id, pairctl_url, phone_number, status FROM whatsapp_connections "
                    "WHERE tenant_id = %s ORDER BY created_at", (tenant_id,))
        conns = [dict(r) for r in cur.fetchall()]
    total, approx, problems, per = 0, False, [], []
    for c in conns:
        n, extra = _count_one(c["pairctl_url"], since)
        if n is None:
            problems.append(extra)
        else:
            total += n
            approx = approx or bool(extra)
        per.append({"number": c["phone_number"], "status": c["status"], "conversations": n})
    out = {"conversations": total, "numbers": len(conns), "per_number": per,
           "approximate": approx, "incomplete": problems, "since": dt.datetime.fromtimestamp(since, PKT).date().isoformat()}
    _USAGE[tenant_id] = (time.time() + USAGE_TTL, out)
    return out


def summary(tenant_id, fresh=False):
    p = plan_for(tenant_id)
    u = usage(tenant_id, fresh)
    pct = round(100.0 * u["conversations"] / p["max_conversations"]) if p["max_conversations"] else 0
    return {"plan": p, "usage": u, "percent": pct,
            "warning": "over" if pct >= 100 else ("near" if pct >= 80 else None)}


def _scope(*a):
    try:
        return resolve_scope(*a)
    except AuthError as e:
        raise HTTPException(e.status, e.message)


def make_plans_router():
    router = APIRouter()

    @router.get("/plan")
    def mine(fresh: bool = False, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _scope(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can see the plan.")
        out = summary(tid, fresh)
        with tenant_session(None, "techdeva_admin") as cur:
            out["plans"] = all_plans(cur)
        from .trialcap import trial_messages, TRIAL_AI_PICTURES
        out["trial"] = trial_messages(tid, fresh)
        if out["trial"] is not None:
            out["trial"]["ai_pictures"] = TRIAL_AI_PICTURES
        return out

    @router.get("/plans")
    def price_list():
        """Public price list (the website shows it)."""
        with tenant_session(None, "techdeva_admin") as cur:
            return {"plans": all_plans(cur)}

    def _admin(*a):
        _t, role, uid = _scope(*a)
        if role != "techdeva_admin":
            raise HTTPException(403, "admin only")
        return uid

    @router.put("/techdeva/plans/{key}")
    def edit_plan(key: str, body: dict = Body(...),
                  x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        sets, vals = [], []
        if "price_pkr" in body:
            raw = body.get("price_pkr")
            if raw in (None, ""):
                price = None
            else:
                try:
                    price = float(str(raw).replace(",", ""))
                except ValueError:
                    raise HTTPException(400, "The price must be a number (or empty for 'talk to us').")
                if price < 0:
                    raise HTTPException(400, "The price cannot be negative.")
            sets.append("price_pkr = %s"); vals.append(price)
        if "price_usd" in body:
            raw = body.get("price_usd")
            if raw in (None, ""):
                usd = None
            else:
                try:
                    usd = float(str(raw).replace(",", "").replace("$", ""))
                except ValueError:
                    raise HTTPException(400, "The dollar price must be a number (or empty for 'talk to us').")
                if usd < 0:
                    raise HTTPException(400, "The price cannot be negative.")
            sets.append("price_usd = %s"); vals.append(usd)
        for k, lo, hi in (("max_numbers", 1, 20), ("max_conversations", 1, 1000000)):
            if k in body:
                try:
                    v = int(body.get(k))
                except (TypeError, ValueError):
                    raise HTTPException(400, "%s must be a whole number." % k)
                if not lo <= v <= hi:
                    raise HTTPException(400, "%s must be between %d and %d." % (k, lo, hi))
                sets.append("%s = %%s" % k); vals.append(v)
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE plans SET " + ", ".join(sets) + ", updated_at = now() WHERE key = %s RETURNING key",
                        vals + [key])
            if not cur.fetchone():
                raise HTTPException(404, "No such plan.")
            record(cur, action="techdeva.plan.edit", resource="plans", resource_id=key, actor=uid,
                   status="ok", detail={k: body.get(k) for k in ("price_pkr", "price_usd", "max_numbers", "max_conversations") if k in body})
            return {"plans": all_plans(cur)}

    @router.put("/techdeva/clients/{tenant_id}/plan")
    def set_plan(tenant_id: str, body: dict = Body(...),
                 x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Put a client on a plan. Their monthly fee becomes the plan price
        (when the plan has one); a different agreed fee can still be typed after."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        key = str(body.get("plan_key") or "").strip() or None
        with tenant_session(None, "techdeva_admin") as cur:
            price = None
            if key:
                cur.execute("SELECT price_pkr FROM plans WHERE key = %s", (key,))
                p = cur.fetchone()
                if not p:
                    raise HTTPException(404, "No such plan.")
                price = p["price_pkr"]
            cur.execute("""INSERT INTO client_accounts (tenant_id, plan_key, monthly_fee)
                           VALUES (%s, %s, COALESCE(%s, 0))
                           ON CONFLICT (tenant_id) DO UPDATE SET plan_key = EXCLUDED.plan_key,
                             monthly_fee = COALESCE(%s, client_accounts.monthly_fee), updated_at = now()
                           RETURNING monthly_fee""", (tenant_id, key, price, price))
            fee = cur.fetchone()["monthly_fee"]
            record(cur, action="techdeva.client.plan", resource="client_accounts", resource_id=tenant_id,
                   actor=uid, status="ok", detail={"plan": key, "fee": float(fee)}, tenant_id=tenant_id)
        return {"ok": True, "plan_key": key, "monthly_fee": float(fee)}

    @router.get("/techdeva/usage")
    def all_usage(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Plan and this month's conversations for every real client."""
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id FROM tenants WHERE NOT is_test")
            ids = [str(r["id"]) for r in cur.fetchall()]
        return {"clients": {t: summary(t) for t in ids}}

    return router
