"""Countries (Phase 10, 9 Nov 2026; db/97). Foundation for one platform in many countries.

    GET  /countries          the countries TechDeva is set up for (currency, symbol,
                             time zones, languages, date format, payment methods,
                             what the tax is called; tax rates are NOT set until
                             validated per country before launch there)
    GET  /business-profile/country   this business's country and its workspace settings
    PUT  /business-profile/country   choose the country (owner): its currency and time zone
                             are filled into the workspace settings (db/66), the
                             language only if none is set; each can still be
                             changed on its own afterwards (/portal/config)

local_today(cur) is the business's own day (its time zone), for code that dates a
record "today". What still assumes Pakistan is listed in docs/COUNTRIES.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session

DEFAULT_COUNTRY = "PK"


def _row(r):
    return {"code": r["code"], "name": r["name"], "currency": r["currency"], "currency_symbol": r["currency_symbol"],
            "timezones": list(r["timezones"]), "languages": list(r["languages"]), "date_format": r["date_format"],
            "payment_methods": list(r["payment_methods"]), "tax_name": r["tax_name"],
            "tax_rate": float(r["tax_rate"]) if r["tax_rate"] is not None and r["tax_validated"] else None,
            "tax_validated": r["tax_validated"]}


def listing(cur):
    cur.execute("SELECT * FROM countries WHERE active ORDER BY sort_order, name")
    return [_row(r) for r in cur.fetchall()]


def local_today(cur):
    """Today in the business's own time zone (Asia/Karachi when none is set)."""
    from .dashboard import _tz
    cur.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (_tz(cur),))
    return cur.fetchone()["d"]


def current(cur, tid):
    cur.execute("SELECT country, timezone, currency, language FROM tenant_config WHERE tenant_id = %s", (tid,))
    c = cur.fetchone() or {}
    code = c.get("country") or None
    cur.execute("SELECT * FROM countries WHERE code = %s", (code or DEFAULT_COUNTRY,))
    k = cur.fetchone()
    return {"country": code, "assumed": code is None, "timezone": c.get("timezone"), "currency": c.get("currency"),
            "language": c.get("language"), "details": _row(k) if k else None}


def choose(cur, tid, body, actor):
    code = str(body.get("country") or "").strip().upper()
    cur.execute("SELECT * FROM countries WHERE code = %s AND active", (code,))
    k = cur.fetchone()
    if not k:
        raise HTTPException(400, "Choose one of the countries listed.")
    tz = str(body.get("timezone") or "").strip() or k["timezones"][0]
    if tz not in k["timezones"]:
        raise HTTPException(400, "Choose a time zone in %s: %s." % (k["name"], ", ".join(k["timezones"])))
    cur.execute("""INSERT INTO tenant_config (tenant_id, country, timezone, currency, language)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id) DO UPDATE SET country = EXCLUDED.country, timezone = EXCLUDED.timezone,
                          currency = EXCLUDED.currency, language = COALESCE(tenant_config.language, EXCLUDED.language)""",
                (tid, code, tz, k["currency"], k["languages"][0]))
    record(cur, action="business.country", resource="tenant_config", resource_id=str(tid), actor=str(actor or ""),
           detail={"country": code, "timezone": tz, "currency": k["currency"]})
    return current(cur, tid)


def make_countries_router(identity):
    router = APIRouter(tags=["countries"])

    @router.get("/countries")
    def countries_list(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {"countries": listing(cur)}

    @router.get("/business-profile/country")
    def country_get(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return current(cur, tid)

    @router.put("/business-profile/country")
    def country_set(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The business's country: fills its currency and time zone (and language if none)."""
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the owner chooses the country.")
        with tenant_session(tid, role) as cur:
            return choose(cur, tid, body, uid)

    return router
