"""Team roles and permissions (Faheem, 23 Oct 2026).

A business's team logins can be given a role. A role says, for each section of
the dashboard, which actions the person may take:

    sections  dashboard, jarvis, chats, crm, operations, team, marketing,
              business, finance, admin   (the dashboard's ten modules)
    actions   view, create, edit, delete, approve, manage

Preset roles (PRESETS) exist in every business; the owner can also make custom
roles (table staff_roles, db/72). A login's role is users.staff_role.

How it is enforced: RoleGuard, one middleware, maps every request's address to
a section (SECTION_BY_PATH) and its method to an action (GET view, POST create,
PUT/PATCH edit, DELETE delete; approving, publishing and launching are approve;
connecting accounts and settings are manage) and refuses what the role does not
allow, with 403. An address the map does not know is refused for a login with a
role, so a new endpoint is closed until it is mapped, never open by accident.

Each role also has a base (users.role): "operator" or "owner". The base still
decides row-level security and the existing owner-only checks; the role only
narrows it. So an Accountant (base owner) reaches the owner-only money pages,
and nothing else. Logins with no role (the owner, and every login made before
roles) are not touched: they work exactly as before.

Only the owner without a role (not a Manager) manages roles: a role can never
give itself more.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope, revoke_sessions, token_from, verify_token
from .db import tenant_session

SECTIONS = {"dashboard": "Dashboard", "jarvis": "Jarvis & AI", "chats": "Chats", "crm": "CRM",
            "operations": "Operations", "team": "Team & HR", "marketing": "Marketing",
            "business": "Business", "finance": "Finance", "admin": "Administration"}
ACTIONS = ("view", "create", "edit", "delete", "approve", "manage")
ALL = list(ACTIONS)
WORK = ["view", "create", "edit"]

PRESETS = {
    "manager":    {"name": "Admin / Manager", "base": "owner",
                   "about": "Runs the business day to day: CRM, orders, reports and the team. Not billing, plans or roles.",
                   "permissions": {"dashboard": ALL, "jarvis": ["view", "create", "edit", "approve"], "chats": ALL, "crm": ALL,
                                   "operations": ALL, "team": ALL, "marketing": ["view", "create", "edit", "delete", "approve"],
                                   "business": ["view", "create", "edit", "delete"], "finance": ["view", "create", "edit", "approve"],
                                   "admin": ["view"]}},
    "shopkeeper": {"name": "Shopkeeper / Sales", "base": "operator",
                   "about": "Takes orders and sales, looks after customers. Sees prices, not the books.",
                   "permissions": {"dashboard": ["view"], "chats": ["view", "create"], "crm": WORK, "operations": WORK,
                                   "business": ["view"], "finance": ["view", "create"]}},
    "operations": {"name": "Operations staff", "base": "operator",
                   "about": "Processes orders and stock, and does the jobs assigned to them.",
                   "permissions": {"dashboard": ["view"], "crm": ["view"], "operations": WORK, "business": ["view"],
                                   "team": ["view"]}},
    "support":    {"name": "Customer support", "base": "operator",
                   "about": "Answers customer messages and follows up.",
                   "permissions": {"dashboard": ["view"], "chats": WORK, "crm": WORK, "business": ["view"]}},
    "marketing":  {"name": "Marketing staff", "base": "owner",
                   "about": "Captions, posts, campaigns and content. Drafts only: the owner publishes and launches.",
                   "permissions": {"dashboard": ["view"], "jarvis": ["view", "create"], "marketing": ["view", "create", "edit", "delete"],
                                   "business": ["view"]}},
    "accountant": {"name": "Accountant / Finance", "base": "owner",
                   "about": "Invoices, payments, expenses, day close and the money reports.",
                   "permissions": {"dashboard": ["view"], "finance": ["view", "create", "edit", "approve"], "operations": ["view"],
                                   "crm": ["view"], "business": ["view"]}},
    "hr":         {"name": "HR / Team manager", "base": "owner",
                   "about": "Team records, attendance and HR.",
                   "permissions": {"dashboard": ["view"], "team": ["view", "create", "edit", "delete"]}},
}

# The address a request goes to -> the section it belongs to. Longest match wins.
SECTION_BY_PATH = [
    # dashboard: overview (Reports sit under Business, 24 Oct 2026)
    ("/portal/summary", "dashboard"), ("/portal/report", "business"), ("/report", "business"), ("/incidents", "dashboard"),
    ("/attribution", "marketing"), ("/summary", "dashboard"), ("/dashboard", "dashboard"), ("/support", "dashboard"),
    # Jarvis and the AI team
    ("/console", "jarvis"), ("/client/request", "jarvis"), ("/speak", "jarvis"), ("/tasks", "jarvis"), ("/portal/deliverables", "jarvis"),
    ("/policies", "admin"), ("/brain", "jarvis"), ("/orchestrator", "jarvis"), ("/ai", "jarvis"), ("/work", "jarvis"),
    ("/events", "jarvis"),
    # chats
    ("/whatsapp/chats", "chats"), ("/whatsapp", "admin"), ("/social-inbox/settings", "admin"), ("/social-inbox", "chats"),
    ("/chats", "chats"), ("/threads", "chats"), ("/calls/numbers", "admin"), ("/calls", "chats"),
    # CRM
    ("/leads", "crm"), ("/crm", "crm"), ("/sales", "crm"), ("/customers", "crm"), ("/business/customers", "crm"), ("/appointments", "crm"),
    ("/operations/appointments", "crm"), ("/calendar", "crm"), ("/business/appointments", "crm"),
    # operations
    ("/orders", "operations"), ("/pos", "operations"), ("/operations/inventory", "operations"), ("/business/stock_movements", "operations"),
    ("/storefront/connect", "admin"), ("/storefront", "operations"),
    # team
    ("/team/logins", "admin"), ("/team/device", "admin"), ("/team/roles", "admin"), ("/team", "team"),
    ("/business/staff", "team"), ("/business/staff_services", "team"),
    # marketing
    ("/social", "marketing"), ("/autopilot", "marketing"), ("/adpilot", "marketing"), ("/ads", "marketing"),
    ("/advisor", "marketing"), ("/marketing", "marketing"), ("/business/offers", "marketing"),
    # business: catalogue and settings
    ("/business/services", "business"), ("/business/products", "business"), ("/business/packages", "business"),
    ("/business/package_items", "business"), ("/business/locations", "business"), ("/business/business_hours", "business"),
    ("/business/business_policies", "business"), ("/business/business_goals", "business"), ("/business/resources", "business"),
    ("/business-profile", "business"), ("/categories", "business"), ("/brand", "business"), ("/portal/config", "business"),
    ("/portal/growth", "dashboard"),
    # finance
    ("/operations/invoices", "finance"), ("/business/invoices", "finance"), ("/business/payments", "finance"),
    ("/business/expenses", "finance"), ("/operations/closing", "finance"), ("/operations/pnl", "finance"),
    ("/revenue", "finance"), ("/business/executions", "jarvis"),
    ("/ledger", "finance"),                     # udhaar ledger (app/ledger.py, db/94)
    # administration
    ("/integrations", "admin"), ("/oauth", "admin"), ("/connect", "admin"), ("/disconnect", "admin"),
    ("/billing", "admin"), ("/plan", "admin"), ("/usage", "admin"),
]
SECTION_BY_PATH.sort(key=lambda x: -len(x[0]))
# open to every signed-in login whatever its role: who am I, signing in and out,
# the business's name and look, and what the dashboard needs to draw itself
ALWAYS_EXACT = {"/me", "/console/prefs", "/business-profile", "/categories", "/brand", "/brand/logo", "/portal/config",
                "/health", "/team/roles/mine", "/me/theme",
                # talking to Jarvis: open to every login; what Jarvis shows and does is
                # limited to the person's role inside it (console._facts_for, _role_may,
                # and its actions go through this same check)
                "/console", "/console/speak",
                # the same Jarvis through its v1 contract (app/jarvis_api.py), same rule
                "/jarvis/v1"}
ALWAYS_PREFIX = ("/auth/", "/public/", "/media/", "/assets/", "/console/conversations", "/jarvis/v1/")
PAGES = ("/", "/dashboard", "/login", "/pin", "/reset", "/portal", "/onboarding", "/techdeva", "/signup",
         "/orders-view", "/storefront-view", "/plans", "/pos")
APPROVE_WORDS = ("approve", "reject", "publish", "launch", "resume", "pause", "approve-week", "void", "dismiss", "apply")
MANAGE_WORDS = ("settings", "connect", "disconnect", "prefs", "sync", "type")
_CACHE: dict = {}
TTL = 30


def section_of(path):
    for prefix, sec in SECTION_BY_PATH:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return sec
    return None


def action_of(method, path):
    words = set(re.split(r"[/?]", path.lower()))
    if method in ("GET", "HEAD"):
        return "view"
    if words & set(APPROVE_WORDS):
        return "approve"
    if words & set(MANAGE_WORDS):
        return "manage"
    return {"POST": "create", "PUT": "edit", "PATCH": "edit", "DELETE": "delete"}.get(method, "manage")


def clean(perms):
    """Only known sections and actions; 'view' comes with any other action."""
    out = {}
    for sec, acts in (perms or {}).items():
        if sec not in SECTIONS or not isinstance(acts, (list, tuple)):
            continue
        a = [x for x in ACTIONS if x in acts]
        if a and "view" not in a:
            a.insert(0, "view")
        if a:
            out[sec] = a
    return out


def base_for(perms):
    """operator when the role stays on staff ground; owner when it needs owner-only pages."""
    owner_ground = {"marketing", "finance", "team", "admin"}
    if any(s in owner_ground for s in perms) or any("manage" in a or "approve" in a for a in perms.values()):
        return "owner"
    return "operator"


def role_of(cur, key):
    """(name, base, permissions) for a staff_role value, or None."""
    if not key:
        return None
    if key in PRESETS:
        p = PRESETS[key]
        return p["name"], p["base"], clean(p["permissions"])
    if key.startswith("custom:"):
        cur.execute("SELECT name, permissions FROM staff_roles WHERE id::text = %s", (key[7:],))
        r = cur.fetchone()
        if r:
            perms = clean(r["permissions"])
            return r["name"], base_for(perms), perms
    return None


def permissions_for(user_id):
    """None = not restricted (no role). Else {"name", "permissions"}. Cached 30 s."""
    now = time.time()
    hit = _CACHE.get(user_id)
    if hit and hit[0] > now:
        return hit[1]
    value = None
    try:
        uuid.UUID(str(user_id))
    except ValueError:
        return None
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT staff_role FROM users WHERE id = %s", (str(user_id),))
        r = cur.fetchone()
        if r and r["staff_role"]:
            got = role_of(cur, r["staff_role"])
            # a role that no longer exists: nothing allowed until the owner picks another
            value = {"key": r["staff_role"], "name": got[0] if got else "Removed role",
                     "permissions": got[2] if got else {}}
    _CACHE[user_id] = (now + TTL, value)
    if len(_CACHE) > 5000:
        _CACHE.clear()
    return value


def forget(user_id=None):
    if user_id:
        _CACHE.pop(str(user_id), None)
    else:
        _CACHE.clear()


def allowed(perms, method, path):
    """(ok, why) for a login with a role."""
    if path in ALWAYS_EXACT or path.startswith(ALWAYS_PREFIX) or path in PAGES:
        return True, None
    sec = section_of(path)
    if not sec:
        return False, "Your role does not include this."
    act = action_of(method, path)
    if act in (perms.get(sec) or []):
        return True, None
    verb = {"view": "see", "create": "add to", "edit": "change", "delete": "delete in", "approve": "approve or publish in",
            "manage": "change the settings of"}[act]
    return False, "Your role cannot %s %s. Ask the owner." % (verb, SECTIONS[sec])


def install(app):
    """The check as one middleware, so no endpoint can forget it."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import Response

    @app.middleware("http")
    async def role_guard(request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in PAGES or path.startswith(ALWAYS_PREFIX) or path == "/health":
            return await call_next(request)
        tok = token_from(request.headers.get("authorization"), request.cookies.get(SESSION_COOKIE))
        if tok:
            try:
                claims = verify_token(tok)
            except AuthError:
                claims = None
            if claims and claims.get("r") in ("owner", "operator") and claims.get("u"):
                perms = await run_in_threadpool(permissions_for, claims["u"])
                if perms is not None:
                    ok, why = allowed(perms["permissions"], request.method, path)
                    if not ok:
                        return Response(json.dumps({"detail": why, "role": perms["name"]}), status_code=403,
                                        media_type="application/json")
        return await call_next(request)


def _owner_only(*a):
    """The owner without a role (the real owner, or a 'full' login made before roles)."""
    try:
        tid, role, uid = resolve_scope(*a)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid or role not in ("owner", "techdeva_admin"):
        raise HTTPException(403, "Only the owner manages roles.")
    if uid and permissions_for(uid) is not None:
        raise HTTPException(403, "Only the owner manages roles.")
    return tid, role, uid


def make_roles_router():
    router = APIRouter()

    @router.get("/team/roles/mine")
    def mine(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What this login may do: null permissions = everything its base role allows."""
        try:
            tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, td_session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        p = permissions_for(uid) if uid else None
        return {"role": p["name"] if p else None, "key": p["key"] if p else None,
                "permissions": p["permissions"] if p else None, "sections": SECTIONS, "actions": ACTIONS}

    @router.get("/team/roles")
    def list_roles(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner_only(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id::text, name, permissions, updated_at FROM staff_roles ORDER BY lower(name)")
            custom = [{"key": "custom:" + r["id"], "name": r["name"], "custom": True, "base": base_for(clean(r["permissions"])),
                       "permissions": clean(r["permissions"])} for r in cur.fetchall()]
            cur.execute("""SELECT u.id::text, u.name, u.role::text AS base, u.staff_role, (u.pin_hash IS NOT NULL) AS has_pin,
                                  (u.email NOT LIKE '%%@pin.invalid') AS has_email
                             FROM users u WHERE u.tenant_id = current_tenant() ORDER BY (u.role = 'owner' AND u.staff_role IS NULL) DESC, u.name""")
            people = [dict(r, me=r["id"] == str(uid)) for r in cur.fetchall()]
        presets = [{"key": k, "name": p["name"], "about": p["about"], "custom": False, "base": p["base"],
                    "permissions": clean(p["permissions"])} for k, p in PRESETS.items()]
        return {"sections": SECTIONS, "actions": ACTIONS, "presets": presets, "custom": custom, "people": people}

    def _perms_from(body):
        perms = clean(body.get("permissions") if isinstance(body.get("permissions"), dict) else {})
        if not perms:
            raise HTTPException(400, "Tick at least one thing this role may do.")
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:60]
        if len(name) < 2:
            raise HTTPException(400, "Give the role a name, e.g. 'Cashier'.")
        if name.lower() in {p["name"].lower() for p in PRESETS.values()} | {"owner"}:
            raise HTTPException(409, "There is already a role called %s." % name)
        return name, perms

    @router.post("/team/roles")
    def add_role(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner_only(x_tenant_id, x_role, authorization, td_session)
        name, perms = _perms_from(body)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM staff_roles WHERE lower(btrim(name)) = lower(%s)", (name,))
            if cur.fetchone():
                raise HTTPException(409, "There is already a role called %s." % name)
            cur.execute("INSERT INTO staff_roles (tenant_id, name, permissions) VALUES (current_tenant(), %s, %s) RETURNING id::text",
                        (name, json.dumps(perms)))
            rid = cur.fetchone()["id"]
            record(cur, action="team.role_add", resource="staff_roles", resource_id=rid, actor=str(uid),
                   status="ok", detail={"name": name, "permissions": perms}, tenant_id=tid)
        return {"ok": True, "key": "custom:" + rid, "name": name, "permissions": perms, "base": base_for(perms)}

    @router.put("/team/roles/{rid}")
    def edit_role(rid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner_only(x_tenant_id, x_role, authorization, td_session)
        name, perms = _perms_from(body)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM staff_roles WHERE lower(btrim(name)) = lower(%s) AND id::text <> %s", (name, rid))
            if cur.fetchone():
                raise HTTPException(409, "There is already a role called %s." % name)
            cur.execute("UPDATE staff_roles SET name = %s, permissions = %s, updated_at = now() WHERE id::text = %s RETURNING id",
                        (name, json.dumps(perms), rid))
            if not cur.fetchone():
                raise HTTPException(404, "No such role in this business.")
            # people on this role move to its new base (owner-only pages need an owner base)
            cur.execute("UPDATE users SET role = %s WHERE staff_role = %s AND role <> %s RETURNING id::text",
                        (base_for(perms), "custom:" + rid, base_for(perms)))
            moved = [r["id"] for r in cur.fetchall()]
            for m in moved:              # their sessions carry the old base: sign in again
                revoke_sessions(cur, m)
            record(cur, action="team.role_edit", resource="staff_roles", resource_id=rid, actor=str(uid),
                   status="ok", detail={"name": name, "permissions": perms, "logins": len(moved)}, tenant_id=tid)
        forget()
        return {"ok": True, "base": base_for(perms), "logins": len(moved)}

    @router.delete("/team/roles/{rid}")
    def delete_role(rid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _owner_only(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT count(*) AS n FROM users WHERE staff_role = %s", ("custom:" + rid,))
            n = cur.fetchone()["n"]
            if n:
                raise HTTPException(409, "%d login%s still use this role. Give them another role first." % (n, "" if n == 1 else "s"))
            cur.execute("DELETE FROM staff_roles WHERE id::text = %s RETURNING name", (rid,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "No such role in this business.")
            record(cur, action="team.role_delete", resource="staff_roles", resource_id=rid, actor=str(uid),
                   status="ok", detail={"name": r["name"]}, tenant_id=tid)
        return {"ok": True}

    @router.put("/team/logins/{user_id}/role")
    def set_role(user_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Give a login a role, or none ("staff": as before roles, staff access; "full": everything)."""
        tid, role, uid = _owner_only(x_tenant_id, x_role, authorization, td_session)
        key = str(body.get("role") or "").strip()
        if str(user_id) == str(uid):
            raise HTTPException(400, "You cannot change your own role.")
        with tenant_session(tid, role) as cur:
            if key in ("staff", "full"):
                new_base, new_key, label = ("operator" if key == "staff" else "owner"), None, ("Staff (no role)" if key == "staff" else "Full access")
            else:
                got = role_of(cur, key)
                if not got:
                    raise HTTPException(400, "Pick one of the roles.")
                label, new_base, _p = got
                new_key = key
            cur.execute("UPDATE users SET staff_role = %s, role = %s WHERE id::text = %s AND tenant_id = current_tenant() RETURNING name",
                        (new_key, new_base, user_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That login is not in this business.")
            revoke_sessions(cur, user_id)      # the new role applies from their next sign-in, everywhere
            record(cur, action="team.login_role", resource="users", resource_id=user_id, actor=str(uid),
                   status="ok", detail={"name": r["name"], "role": label}, tenant_id=tid)
        forget(user_id)
        return {"ok": True, "name": r["name"], "role": label}

    return router
