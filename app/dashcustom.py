"""Dashboard Customizer (Rafay, 27 Oct 2026): which pages a business sees, and in
what order, by industry preset, by hand (Administration > Customize Dashboard)
or through Jarvis.

Built on what already exists, not beside it: the business's dashboard_prefs
(tenant_config) already holds hidden_sections (the sidebar hides those pages),
home and pinned_by_role. The customizer adds, in the same JSON:
    nav_order      page ids in the order the owner wants them (tabs in a module)
    module_order   module numbers ("01".."10") in sidebar order
    dc_preset      the industry preset last applied
    dc_source      preset | custom | jarvis   (absent = never customized)
    dc_version, dc_updated_by, dc_updated_at
Every change goes through save() below, used by the page and by Jarvis alike,
and is written to the audit log (executions, action dashboard.customize).

Rules:
  * behind DASHBOARD_CUSTOMIZER_ENABLED (env "1"/"true" on, "0"/"false" off;
    unset = platform_settings dashboard_customizer = "1"); off = nothing new
    happens and nothing can be saved here;
  * a business with no dc_source keeps its dashboard exactly as before;
  * hiding only hides: no data is touched and no API is switched off;
  * core pages (CORE) can never be hidden;
  * only the business owner (or TechDeva) changes it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# ------------------------------------------------------------------ registry
# Mirrors the ten modules of web/dashboard.html (MODS). key = the page id.
MODULES = [
    ("01", "Dashboard", [("overview", "Overview")]),
    ("02", "Jarvis & AI", [("console", "Jarvis Assistant"), ("ai", "AI Agents"), ("hermes", "Hermes AI"),
                           ("bizagent", "Business & agent"), ("needshuman", "Approval Queue"),
                           ("history", "Execution History"), ("tools", "Agents")]),
    ("03", "Chats", [("inbox", "WhatsApp Inbox"), ("socialinbox", "Social Inbox"), ("calls", "Calls")]),
    ("04", "CRM", [("leads", "Leads"), ("sales", "Sales"), ("clientwork", "Client work"), ("customers", "Customers"),
                   ("pipeline", "Pipeline"), ("appointments", "Appointments"), ("calendar", "Calendar"),
                   ("followups", "Follow-ups"), ("crmreports", "CRM Reports")]),
    ("05", "Operations", [("orders", "Orders"), ("inventory", "Inventory"), ("dailyentry", "Daily Entry")]),
    ("06", "Team & HR", [("team", "Team"), ("attendance", "Attendance"), ("hr", "HR"), ("roles", "Roles")]),
    ("07", "Marketing", [("marketing", "Campaigns"), ("posts", "Social Media Publisher"), ("captions", "Caption Writer"),
                         ("autopilot", "Content Calendar"), ("advisor", "Ads & Growth"), ("analytics", "Analytics"),
                         ("gallery", "Autopilot Gallery")]),
    ("08", "Business", [("services", "Services"), ("products", "Products"), ("packages", "Packages"), ("deals", "Deals"),
                        ("reports", "Reports"), ("bizprofile", "Business Profile"), ("settings", "Business Settings")]),
    ("09", "Finance", [("revenue", "Revenue"), ("invoices", "Invoices"), ("expenses", "Expenses"),
                       ("dayclose", "Day Close"), ("pnl", "Profit & Loss")]),
    ("10", "Administration", [("integrations", "Integrations"), ("whatsapp", "WhatsApp Connection"),
                              ("billing", "Plan & Billing"), ("usage", "AI usage"), ("permissions", "Permissions"),
                              ("customize", "Customize Dashboard")]),
]
REGISTRY = {key: {"label": label, "module": num, "category": name}
            for num, name, pages in MODULES for key, label in pages}
MODULE_NUMS = [m[0] for m in MODULES]
# Always visible, never hidden: the way back to everything else, money and access.
CORE = {"overview", "console", "settings", "billing", "integrations", "whatsapp", "usage",
        "permissions", "roles", "customize"}

# ------------------------------------------------------------------ industry presets
_BASE = {"overview", "console", "ai", "hermes", "bizagent", "needshuman", "history", "tools",
         "inbox", "socialinbox", "calls", "leads", "sales", "customers", "pipeline", "followups", "crmreports",
         "team", "attendance", "hr", "roles", "marketing", "posts", "captions", "autopilot", "advisor",
         "analytics", "gallery", "reports", "bizprofile", "settings", "revenue", "invoices", "expenses",
         "dayclose", "pnl", "integrations", "whatsapp", "billing", "usage", "permissions", "customize"}
_SERVICE = {"appointments", "calendar", "services", "packages"}
PRESET_ON = {
    "clinic": _BASE | _SERVICE | {"dailyentry", "products", "inventory"},
    "salon": _BASE | _SERVICE | {"dailyentry", "products", "inventory"},
    "gym": _BASE | _SERVICE,
    "academy": _BASE | _SERVICE,
    "restaurant": _BASE | _SERVICE | {"orders", "products", "inventory"},
    "shop": (_BASE - {"attendance", "hr"}) | {"orders", "products", "inventory"},
    "other": set(REGISTRY),
}


def preset_hidden(industry: str | None) -> list:
    on = PRESET_ON.get(industry or "other", PRESET_ON["other"])
    return sorted(k for k in REGISTRY if k not in on and k not in CORE)


# ------------------------------------------------------------------ flag
def enabled(cur=None) -> bool:
    env = os.environ.get("DASHBOARD_CUSTOMIZER_ENABLED", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        # always its own admin read: a business session may not see platform_settings
        with tenant_session(None, "techdeva_admin") as c:
            c.execute("SELECT value FROM platform_settings WHERE key = 'dashboard_customizer'")
            r = c.fetchone()
        return bool(r and r["value"] == "1")
    except Exception:  # noqa: BLE001 -- unknown = off: the dashboard stays as it is
        return False


# ------------------------------------------------------------------ service
def _prefs(cur, tid):
    cur.execute("SELECT dashboard_prefs FROM tenant_config WHERE tenant_id = %s", (str(tid),))
    r = cur.fetchone()
    return dict((r or {}).get("dashboard_prefs") or {})


def _industry(cur, tid):
    cur.execute("SELECT business_type FROM tenants WHERE id = %s", (str(tid),))
    r = cur.fetchone()
    return (r or {}).get("business_type")


def get_config(cur, tid) -> dict:
    p = _prefs(cur, tid)
    industry = _industry(cur, tid)
    return {
        "enabled": enabled(cur),
        "configured": bool(p.get("dc_source")),
        "hidden": [h for h in (p.get("hidden_sections") or []) if h in REGISTRY],
        "nav_order": [k for k in (p.get("nav_order") or []) if k in REGISTRY],
        "module_order": [m for m in (p.get("module_order") or []) if m in MODULE_NUMS],
        "preset": p.get("dc_preset"), "source": p.get("dc_source"), "version": p.get("dc_version") or 0,
        "updated_at": p.get("dc_updated_at"), "updated_by": p.get("dc_updated_by"),
        "industry": industry,
        "modules": [{"num": n, "name": name, "pages": [{"key": k, "label": lbl, "core": k in CORE} for k, lbl in pages]}
                    for n, name, pages in MODULES],
        "core": sorted(CORE),
        "presets": {ind: preset_hidden(ind) for ind in PRESET_ON},
    }


class DashError(Exception):
    """A reason fit to show the owner (and for Jarvis to say)."""


def clean(hidden=None, nav_order=None, module_order=None):
    """Validate a change. Unknown keys are refused; core pages cannot be hidden."""
    out = {}
    if hidden is not None:
        if not isinstance(hidden, list):
            raise DashError("hidden must be a list of page names")
        bad = [str(h) for h in hidden if str(h) not in REGISTRY]
        if bad:
            raise DashError("unknown page: %s" % ", ".join(bad[:5]))
        core = [h for h in hidden if h in CORE]
        if core:
            raise DashError("%s cannot be hidden: %s always stays on the dashboard" % (
                ", ".join(REGISTRY[c]["label"] for c in core), "it" if len(core) == 1 else "they"))
        out["hidden_sections"] = sorted(set(str(h) for h in hidden))
    if nav_order is not None:
        if not isinstance(nav_order, list):
            raise DashError("the page order must be a list")
        seen, order = set(), []
        for k in nav_order:
            k = str(k)
            if k not in REGISTRY:
                raise DashError("unknown page: %s" % k[:40])
            if k not in seen:
                seen.add(k); order.append(k)
        out["nav_order"] = order
    if module_order is not None:
        if not isinstance(module_order, list):
            raise DashError("the module order must be a list")
        mods = []
        for m in module_order:
            m = str(m).zfill(2)
            if m not in MODULE_NUMS:
                raise DashError("unknown module: %s" % m)
            if m not in mods:
                mods.append(m)
        out["module_order"] = mods
    return out


def save(cur, tid, change: dict, source: str, by: str, preset: str | None = None) -> dict:
    """Write a validated change into dashboard_prefs, bump the version, audit it.
    Returns the new config."""
    before = _prefs(cur, tid)
    after = dict(before)
    after.update(change)
    after["dc_source"] = source
    if preset is not None:
        after["dc_preset"] = preset
    after["dc_version"] = int(before.get("dc_version") or 0) + 1
    after["dc_updated_by"] = str(by or "")[:80]
    after["dc_updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    cur.execute("""INSERT INTO tenant_config (tenant_id, dashboard_prefs) VALUES (%s, %s::jsonb)
                   ON CONFLICT (tenant_id) DO UPDATE SET dashboard_prefs = EXCLUDED.dashboard_prefs""",
                (str(tid), json.dumps(after)))
    old_h, new_h = set(before.get("hidden_sections") or []), set(after.get("hidden_sections") or [])
    record(cur, action="dashboard.customize", resource="tenant_config", resource_id=str(tid), actor=str(by or "")[:80],
           actor_type="system" if source == "preset" and by == "signup" else "human", status="ok",
           detail={"source": source, "preset": after.get("dc_preset"), "version": after["dc_version"],
                   "hidden_added": sorted(new_h - old_h), "hidden_removed": sorted(old_h - new_h),
                   "order_changed": before.get("nav_order") != after.get("nav_order")
                   or before.get("module_order") != after.get("module_order")},
           error=None, tenant_id=str(tid))
    return get_config(cur, tid)


def apply_preset(cur, tid, industry, source="preset", by="system") -> dict:
    industry = industry if industry in PRESET_ON else "other"
    return save(cur, tid, {"hidden_sections": preset_hidden(industry), "nav_order": [], "module_order": []},
                source, by, preset=industry)


def change(cur, tid, op: str, args: dict, source: str, by: str) -> dict:
    """The one entry point for enable / disable / reorder / reset / set (page and Jarvis)."""
    cfg = get_config(cur, tid)
    hidden = set(cfg["hidden"])
    if op in ("enable", "disable"):
        keys = [str(k) for k in (args.get("sections") or [])]
        if not keys:
            raise DashError("say which page")
        bad = [k for k in keys if k not in REGISTRY]
        if bad:
            raise DashError("unknown page: %s" % ", ".join(bad[:5]))
        hidden = (hidden | set(keys)) if op == "disable" else (hidden - set(keys))
        return save(cur, tid, clean(hidden=sorted(hidden)), source, by)
    if op == "set":
        return save(cur, tid, clean(hidden=args.get("hidden") or []), source, by)
    if op == "reorder":
        return save(cur, tid, clean(nav_order=args.get("order") or [],
                                    module_order=args.get("module_order")
                                    if args.get("module_order") is not None else None), source, by)
    if op == "reset":
        return apply_preset(cur, tid, args.get("industry") or cfg["industry"], source=source, by=by)
    raise DashError("unknown change")


def plan_text(cur, tid, op: str, args: dict) -> str:
    """What a change would do, in words, before it is made (Jarvis asks first)."""
    cfg = get_config(cur, tid)
    lbl = lambda keys: ", ".join(REGISTRY[k]["label"] for k in keys if k in REGISTRY)  # noqa: E731
    if op in ("enable", "disable"):
        keys = [str(k) for k in (args.get("sections") or []) if str(k) in REGISTRY]
        if not keys:
            raise DashError("I could not tell which page you mean")
        if op == "disable":
            core = [k for k in keys if k in CORE]
            if core:
                raise DashError("%s always stays on the dashboard, so I can't hide it" % lbl(core))
            return "Hide %s from the dashboard? (Nothing is deleted; you can show it again any time.)" % lbl(keys)
        return "Show %s on the dashboard again?" % lbl(keys)
    if op == "set":
        clean(hidden=args.get("hidden") or [])
        now, new = set(cfg["hidden"]), set(args.get("hidden") or [])
        parts = []
        if new - now:
            parts.append("hide " + lbl(sorted(new - now)))
        if now - new:
            parts.append("show " + lbl(sorted(now - new)))
        if not parts:
            raise DashError("that is how the dashboard already is")
        return "%s?" % " and ".join(parts).capitalize()
    if op == "reorder":
        order = [str(k) for k in (args.get("order") or []) if str(k) in REGISTRY]
        if not order:
            raise DashError("tell me which pages should come first")
        return "Put %s first on the dashboard?" % lbl(order)
    if op == "reset":
        ind = args.get("industry") or cfg["industry"] or "other"
        if ind not in PRESET_ON:
            raise DashError("I do not have a preset for that kind of business")
        h = preset_hidden(ind)
        return "Reset the dashboard to the %s layout? %s" % (
            ind, ("It hides " + lbl(h) + ".") if h else "Every page is shown.")
    raise DashError("I can show, hide, reorder or reset the dashboard pages")


# ------------------------------------------------------------------ hooks for signup and type change
def on_signup(tid, industry):
    """New business: its industry's preset. Never raises: signup must still succeed."""
    try:
        with tenant_session(None, "techdeva_admin") as cur:
            if not enabled(cur):
                return None
            return apply_preset(cur, tid, industry, source="preset", by="signup")["preset"]
    except Exception as e:  # noqa: BLE001
        print("[dashcustom] preset at signup failed for %s: %s" % (tid, type(e).__name__), flush=True)
        return None


def on_type_change(tid, industry, by):
    """The owner changed the business type: a dashboard still on a preset follows
    it; one customized by hand is left alone. Never raises."""
    try:
        with tenant_session(None, "techdeva_admin") as cur:
            if not enabled(cur):
                return None
            if (_prefs(cur, tid).get("dc_source") or "") != "preset":
                return None
            return apply_preset(cur, tid, industry, source="preset", by=by)["preset"]
    except Exception as e:  # noqa: BLE001
        print("[dashcustom] preset on type change failed for %s: %s" % (tid, type(e).__name__), flush=True)
        return None


# ------------------------------------------------------------------ routes
def _who(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _owner_on(cur, role):
    if role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the business owner can customize the dashboard.")
    if not enabled(cur):
        raise HTTPException(409, "Customize Dashboard is not switched on.")


def make_dashcustom_router() -> APIRouter:
    router = APIRouter(tags=["dashboard-customizer"])

    @router.get("/dashboard-config")
    def get(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cfg = get_config(cur, tid)
        cfg["can_edit"] = role in ("owner", "techdeva_admin")
        return cfg

    @router.put("/dashboard-config")
    def put(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Save the page: hidden pages, page order, module order (Save on Customize Dashboard)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            _owner_on(cur, role)
            try:
                ch = clean(hidden=body.get("hidden"), nav_order=body.get("nav_order"),
                           module_order=body.get("module_order"))
            except DashError as e:
                raise HTTPException(400, str(e))
            cfg = get_config(cur, tid)
            if body.get("version") is not None and int(body["version"]) != int(cfg["version"]):
                raise HTTPException(409, "The dashboard was changed somewhere else (maybe by Jarvis). Reload and try again.")
            return save(cur, tid, ch, "custom", str(uid or role))

    @router.post("/dashboard-config/reset")
    def reset(body: dict = Body(default={}), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Back to an industry's layout (this business's own type unless another is named)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            _owner_on(cur, role)
            ind = body.get("industry") or _industry(cur, tid)
            if ind and ind not in PRESET_ON:
                raise HTTPException(400, "No preset for that business type.")
            return apply_preset(cur, tid, ind, source="preset", by=str(uid or role))

    @router.post("/dashboard-config/change")
    def do_change(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One change (enable / disable / reorder / reset / set). Jarvis calls this after the owner said yes."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        source = "jarvis" if body.get("via") == "jarvis" else "custom"
        with tenant_session(tid, role) as cur:
            _owner_on(cur, role)
            try:
                return change(cur, tid, str(body.get("op") or ""), body, source, str(uid or role))
            except DashError as e:
                raise HTTPException(400, str(e))

    @router.put("/techdeva/dashboard-customizer")
    def switch(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Founder: switch the customizer on or off for every business (the env flag, if set, wins)."""
        try:
            _t, role, uid = resolve_scope(x_tenant_id, x_role, authorization, td_session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        if role != "techdeva_admin":
            raise HTTPException(403, "Founder only.")
        on = bool(body.get("on"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO platform_settings (key, value) VALUES ('dashboard_customizer', %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                        ("1" if on else "0",))
            record(cur, action="techdeva.dashboard_customizer", resource="platform_settings",
                   resource_id="dashboard_customizer", actor=str(uid or "founder"), actor_type="human", status="ok",
                   detail={"on": on}, error=None)
        return {"on": on, "env": os.environ.get("DASHBOARD_CUSTOMIZER_ENABLED") or None}

    return router
