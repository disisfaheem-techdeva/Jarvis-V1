"""The Business Brain for marketing: everything comes from the business's own
tenant_config row (services, price_list, brand, hermes_persona, language,
timezone, and the marketing settings added in db/78). Nothing about a business
is written in code; when a value is missing the caption says less, it never
makes something up.

Read with to_jsonb(tenant_config), so an API that is deployed a moment before
db/78 is applied still answers (the marketing column is simply absent).
"""
from __future__ import annotations

DEFAULT_TZ = "Asia/Karachi"            # the platform default, as everywhere else (console.py, dashboard.py)
UPLOAD_DEFAULTS = {"max_image_mb": 8, "max_video_mb": 100, "max_video_s": 90}


def load(cur) -> dict:
    """The business's config for marketing work (cur must be in that business's session)."""
    cur.execute("SELECT to_jsonb(tc) AS c FROM tenant_config tc WHERE tenant_id = current_tenant()")
    row = cur.fetchone()
    c = (row or {}).get("c") or {}
    cur.execute("SELECT name, industry, business_type FROM tenants WHERE id = current_tenant()")
    t = cur.fetchone() or {}
    c["business_name"] = t.get("name")
    c["industry"] = t.get("industry")
    c["business_type"] = t.get("business_type")
    return c


def marketing(config: dict) -> dict:
    return config.get("marketing") or {}


def upload_limits(config: dict) -> dict:
    out = dict(UPLOAD_DEFAULTS)
    for k, v in (marketing(config).get("upload") or {}).items():
        if k in out:
            try:
                out[k] = max(1, int(v))
            except (TypeError, ValueError):
                pass
    return out


def timezone(config: dict) -> str:
    geo = config.get("geo") or {}
    return config.get("timezone") or geo.get("timezone") or DEFAULT_TZ


def language(config: dict) -> str:
    """The caption language the owner chose: marketing.caption_language, else the business language."""
    return marketing(config).get("caption_language") or config.get("language") or "en"


def banned_phrases(config: dict) -> list[str]:
    return [str(p).strip().lower() for p in (marketing(config).get("banned_phrases") or []) if str(p).strip()]


def services(config: dict) -> list[dict]:
    out = []
    for s in config.get("services") or []:
        if isinstance(s, dict) and s.get("name"):
            out.append({"name": str(s["name"]), "price": s.get("price"), "department": s.get("department")})
    return out


def prices(config: dict) -> set[float]:
    """Every price the business wrote down: service prices and the price list."""
    out = set()

    def add(v):
        try:
            f = float(str(v).replace(",", "").strip())
            if f > 0:
                out.add(round(f, 2))
        except (TypeError, ValueError):
            pass
    for s in services(config):
        add(s["price"])
    pl = config.get("price_list") or {}
    if isinstance(pl, dict):
        for v in pl.values():
            if isinstance(v, dict):
                for x in v.values():
                    add(x)
            else:
                add(v)
    elif isinstance(pl, list):
        for v in pl:
            add(v.get("price") if isinstance(v, dict) else v)
    return out


def currency(config: dict) -> str:
    return config.get("currency") or (config.get("brand") or {}).get("currency") or "PKR"
