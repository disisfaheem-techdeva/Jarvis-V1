"""TechDeva's own work (Part 19): what TechDeva sells, who has it, and the
delivery tasks for TechDeva's team.

The rows belong to TechDeva's own business on the platform (the "home"
tenant, chosen once in the founder console). That keeps the usual walls:

  founder (techdeva_admin)  /techdeva/work/*  everything, including prices
  TechDeva's team           /work/*           the delivery tasks only, through
                                              their own PIN login to TechDeva's
                                              business (RLS: their tenant)
  clients                   nothing           their tenant owns no such rows

The home business is recognised without an admin read: it is the one whose
catalogue has rows (the catalogue is seeded when the home is chosen).
"""
import datetime as _dt
import json
import uuid

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, hash_password, resolve_scope
from .db import tenant_session
from .pins import ACCESS as PIN_ACCESS, _code, _pin_taken, check_pin

HOME_KEY = "home_tenant_id"
STATUSES = ("todo", "doing", "done")


def _who(x_tenant_id, x_role, authorization, session):
    try:
        return resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)


def _admin(*a):
    tid, role, uid = _who(*a)
    if role != "techdeva_admin":
        raise HTTPException(403, "Founder only.")
    return uid


def _text(v, n, what, required=False):
    v = str(v or "").strip()[:n]
    if required and not v:
        raise HTTPException(400, "%s is required." % what)
    return v


def _money(v):
    if v in (None, ""):
        return None
    try:
        n = round(float(str(v).replace(",", "")), 2)
    except ValueError:
        raise HTTPException(400, "Price must be a number.")
    if n < 0:
        raise HTTPException(400, "Price cannot be negative.")
    return n


def _date(v, what):
    if v in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, "%s must be a date like 2026-10-05." % what)


def _home(cur):
    cur.execute("SELECT value FROM platform_settings WHERE key = %s", (HOME_KEY,))
    r = cur.fetchone()
    if not r or not r["value"]:
        return None
    cur.execute("SELECT id, name FROM tenants WHERE id::text = %s", (r["value"],))
    return cur.fetchone()


def _rec(cur, tenant=None, **kw):
    """Audit a founder write. The founder's session has no current tenant, so
    the row is filed under TechDeva's own business; details made JSON-safe."""
    if tenant is None:
        h = _home(cur)
        tenant = h["id"] if h else None
    kw["detail"] = json.loads(json.dumps(kw.get("detail") or {}, default=str))
    record(cur, tenant_id=tenant, **kw)


def _need_home(cur):
    h = _home(cur)
    if not h:
        raise HTTPException(409, "First choose TechDeva's own business (Team page).")
    return h


def _client(cur, client_id):
    cur.execute("SELECT id, name FROM tenants WHERE id::text = %s", (str(client_id or ""),))
    c = cur.fetchone()
    if not c:
        raise HTTPException(404, "That client is not on the platform.")
    return c


def _assignee(cur, home_id, user_id):
    if not user_id:
        return None, ""
    cur.execute("SELECT u.id, coalesce(nullif(u.name, ''), s.name, u.email) AS name FROM users u "
                "LEFT JOIN staff s ON s.id = u.staff_id WHERE u.id::text = %s AND u.tenant_id = %s",
                (str(user_id), home_id))
    u = cur.fetchone()
    if not u:
        raise HTTPException(404, "That person is not on TechDeva's team.")
    return u["id"], u["name"] or ""


TASK_COLS = ("id, client_tenant_id, client_name, client_service_id, title, notes, assignee_user_id, "
             "assignee_name, due_on, status, created_by, done_at, created_at, updated_at")


def make_tdwork_router():
    router = APIRouter(tags=["techdeva-work"])

    # ---------------------------------------------------------------- home + team
    @router.get("/techdeva/work/home")
    def get_home(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            cur.execute("SELECT id, name FROM tenants WHERE NOT is_test ORDER BY name")
            cands = [dict(r) for r in cur.fetchall()]
        return {"home": dict(h) if h else None, "candidates": cands}

    @router.put("/techdeva/work/home")
    def set_home(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            t = _client(cur, body.get("tenant_id"))
            cur.execute("INSERT INTO platform_settings (key, value, updated_at) VALUES (%s, %s, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                        (HOME_KEY, str(t["id"])))
            # the catalogue starts with the plans already decided (Plans page)
            cur.execute("SELECT count(*) AS n FROM td_catalog WHERE tenant_id = %s", (t["id"],))
            seeded = 0
            if not cur.fetchone()["n"]:
                cur.execute("SELECT key, name, price_pkr, sort FROM plans ORDER BY sort")
                for p in cur.fetchall():
                    cur.execute("INSERT INTO td_catalog (tenant_id, name, category, price, billing, plan_key, "
                                "sort_order, description) VALUES (%s, %s, 'plan', %s, 'monthly', %s, %s, %s)",
                                (t["id"], "%s plan" % p["name"], p["price_pkr"], p["key"], p["sort"] or 0,
                                 "WhatsApp AI agent + business dashboard"))
                    seeded += 1
            _rec(cur, tenant=t["id"], action="techdeva.home_set", resource="platform_settings", resource_id=HOME_KEY,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"tenant": str(t["id"]), "name": t["name"], "catalog_seeded": seeded}, error=None)
        return {"home": {"id": str(t["id"]), "name": t["name"]}, "catalog_seeded": seeded}

    @router.get("/techdeva/work/team")
    def team(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            if not h:
                return {"home": None, "team": []}
            cur.execute("""SELECT u.id, coalesce(nullif(u.name, ''), s.name, u.email) AS name, u.email,
                                  u.role::text AS role, (u.pin_hash IS NOT NULL) AS has_pin, u.last_login_at,
                                  s.role AS job,
                                  (SELECT count(*) FROM td_tasks t WHERE t.assignee_user_id = u.id
                                    AND t.status <> 'done') AS open_tasks
                             FROM users u LEFT JOIN staff s ON s.id = u.staff_id
                            WHERE u.tenant_id = %s ORDER BY u.role, 2""", (h["id"],))
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""SELECT s.id, s.name, coalesce(s.role, '') AS job, coalesce(s.phone, '') AS phone,
                                  coalesce(s.email, '') AS email, s.joining_date, s.active,
                                  u.id AS user_id, u.role::text AS access, (u.pin_hash IS NOT NULL) AS has_pin,
                                  (SELECT count(*) FROM td_tasks t WHERE t.assignee_user_id = u.id
                                    AND t.status <> 'done') AS open_tasks
                             FROM staff s LEFT JOIN users u ON u.staff_id = s.id
                            WHERE s.tenant_id = %s ORDER BY s.active DESC, s.name""", (h["id"],))
            emps = [dict(r) for r in cur.fetchall()]
            code = _code(cur, h["id"])
        for e in emps:
            e["access"] = "full" if e["access"] == "owner" else ("staff" if e["access"] else None)
        return {"home": dict(h), "team": rows, "employees": emps, "code": code}

    # ---------------------------------------------------------------- employees
    def _emp_fields(body):
        f = {}
        for k, col, n in (("name", "name", 80), ("job", "role", 80), ("phone", "phone", 30), ("email", "email", 120)):
            if k in body:
                f[col] = _text(body.get(k), n, k.capitalize(), required=(k == "name")) or None
        if "joining_date" in body:
            f["joining_date"] = _date(body.get("joining_date"), "Joining date")
        return f

    def _give_pin(cur, home_id, staff_id, name, pin, access, uid):
        """Create or change the employee's PIN login in TechDeva's business."""
        pin = check_pin(pin)
        access = str(access or "staff")
        if access not in PIN_ACCESS:
            raise HTTPException(400, "Access must be staff or full.")
        cur.execute("SELECT id FROM users WHERE staff_id = %s AND tenant_id = %s", (staff_id, home_id))
        u = cur.fetchone()
        if _pin_taken(cur, home_id, pin, except_user=u["id"] if u else None):
            raise HTTPException(409, "Someone in the team already uses that PIN. Choose another.")
        if u:
            cur.execute("UPDATE users SET pin_hash = %s, pin_set_at = now(), role = %s WHERE id = %s",
                        (hash_password(pin * 2), PIN_ACCESS[access], u["id"]))
            return str(u["id"])
        new_id = uuid.uuid4()
        cur.execute("""INSERT INTO users (id, tenant_id, name, email, role, pin_hash, pin_set_at, staff_id)
                       VALUES (%s, %s, %s, %s, %s, %s, now(), %s)""",
                    (str(new_id), home_id, name, "pin+%s@pin.invalid" % new_id, PIN_ACCESS[access],
                     hash_password(pin * 2), staff_id))
        return str(new_id)

    @router.post("/techdeva/work/employees")
    def add_employee(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Add an employee to TechDeva's own business, optionally with a PIN login."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        f = _emp_fields(body)
        if not f.get("name"):
            raise HTTPException(400, "Name is needed.")
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cols = ["tenant_id"] + list(f)
            cur.execute("INSERT INTO staff (%s) VALUES (%s) RETURNING id" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                        [h["id"]] + list(f.values()))
            sid = str(cur.fetchone()["id"])
            login = None
            if str(body.get("pin") or "").strip():
                login = _give_pin(cur, h["id"], sid, f["name"], body.get("pin"), body.get("access"), uid)
            _rec(cur, action="techdeva.employee_add", resource="staff", resource_id=sid, actor=str(uid),
                 actor_type="human", status="ok",
                 detail={"name": f["name"], "job": f.get("role"), "pin_login": bool(login),
                         "access": (body.get("access") or "staff") if login else None}, error=None)
        return {"id": sid, "user_id": login}

    @router.put("/techdeva/work/employees/{staff_id}")
    def edit_employee(staff_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None), authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Edit details, give or change the PIN, remove the PIN, or mark as left
        (which also stops their PIN working; their history stays)."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        f = _emp_fields(body)
        if "active" in body:
            f["active"] = bool(body.get("active"))
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("SELECT id, name FROM staff WHERE id::text = %s AND tenant_id = %s", (staff_id, h["id"]))
            st = cur.fetchone()
            if not st:
                raise HTTPException(404, "That employee is not in TechDeva's business.")
            if f:
                cur.execute("UPDATE staff SET %s, updated_at = now() WHERE id = %%s" % ", ".join("%s = %%s" % k for k in f),
                            list(f.values()) + [st["id"]])
            name = f.get("name") or st["name"]
            if f.get("name"):
                cur.execute("UPDATE users SET name = %s WHERE staff_id = %s AND tenant_id = %s", (name, st["id"], h["id"]))
            if str(body.get("pin") or "").strip():
                _give_pin(cur, h["id"], str(st["id"]), name, body.get("pin"), body.get("access"), uid)
            elif body.get("access") in PIN_ACCESS:
                cur.execute("UPDATE users SET role = %s WHERE staff_id = %s AND tenant_id = %s",
                            (PIN_ACCESS[body["access"]], st["id"], h["id"]))
            if body.get("remove_pin") or f.get("active") is False:
                cur.execute("UPDATE users SET pin_hash = NULL, pin_set_at = now() WHERE staff_id = %s AND tenant_id = %s",
                            (st["id"], h["id"]))
            detail = {k: v for k, v in f.items()}
            detail.update({"name": name, "pin_changed": bool(str(body.get("pin") or "").strip()),
                           "pin_removed": bool(body.get("remove_pin") or f.get("active") is False)})
            if body.get("access") in PIN_ACCESS:
                detail["access"] = body["access"]
            _rec(cur, action="techdeva.employee_edit", resource="staff", resource_id=staff_id, actor=str(uid),
                 actor_type="human", status="ok", detail=detail, error=None)
        return {"ok": True}

    # ---------------------------------------------------------------- catalogue
    @router.get("/techdeva/work/catalog")
    def catalog(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            if not h:
                return {"home": None, "items": []}
            cur.execute("""SELECT c.id, c.name, c.category, c.price, c.billing, c.description, c.plan_key,
                                  c.active, c.sort_order,
                                  (SELECT count(*) FROM td_client_services s WHERE s.catalog_id = c.id
                                    AND s.status = 'active') AS active_clients
                             FROM td_catalog c WHERE c.tenant_id = %s
                            ORDER BY c.active DESC, c.sort_order, c.name""", (h["id"],))
            return {"home": dict(h), "items": [dict(r) for r in cur.fetchall()]}

    def _catalog_fields(body, partial):
        out = {}
        if not partial or "name" in body:
            out["name"] = _text(body.get("name"), 120, "Name", required=True)
        if "category" in body or not partial:
            out["category"] = _text(body.get("category") or "service", 40, "Category")
        if "price" in body:
            out["price"] = _money(body.get("price"))
        if "billing" in body or not partial:
            b = str(body.get("billing") or "monthly")
            if b not in ("monthly", "one_time"):
                raise HTTPException(400, "Billing is monthly or one_time.")
            out["billing"] = b
        if "description" in body:
            out["description"] = _text(body.get("description"), 1000, "Description")
        if "active" in body:
            out["active"] = bool(body.get("active"))
        return out

    @router.post("/techdeva/work/catalog")
    def add_catalog(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        f = _catalog_fields(body, False)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cols = ["tenant_id"] + list(f)
            cur.execute("INSERT INTO td_catalog (%s) VALUES (%s) RETURNING id" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                        [h["id"]] + list(f.values()))
            new_id = cur.fetchone()["id"]
            _rec(cur, action="techdeva.catalog_add", resource="td_catalog", resource_id=str(new_id),
                   actor=str(uid), actor_type="human", status="ok", detail={"name": f["name"], "price": f.get("price")},
                   error=None)
        return {"id": str(new_id)}

    @router.put("/techdeva/work/catalog/{item_id}")
    def edit_catalog(item_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        f = _catalog_fields(body, True)
        if not f:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("UPDATE td_catalog SET %s, updated_at = now() WHERE id::text = %%s AND tenant_id = %%s RETURNING id"
                        % ", ".join("%s = %%s" % k for k in f), list(f.values()) + [item_id, h["id"]])
            if not cur.fetchone():
                raise HTTPException(404, "That service is not in the catalogue.")
            _rec(cur, action="techdeva.catalog_edit", resource="td_catalog", resource_id=item_id,
                   actor=str(uid), actor_type="human", status="ok", detail=f, error=None)
        return {"ok": True}

    # ---------------------------------------------------------------- client services
    @router.get("/techdeva/work/client-services")
    def client_services(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                        authorization: str | None = Header(None),
                        td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            if not h:
                return {"home": None, "items": [], "clients": []}
            cur.execute("""SELECT s.id, s.client_tenant_id, coalesce(t.name, s.client_name) AS client_name,
                                  s.catalog_id, coalesce(c.name, s.service_name) AS service_name, s.price, s.billing,
                                  s.status, s.started_on, s.ended_on, s.notes,
                                  (SELECT count(*) FROM td_tasks k WHERE k.client_service_id = s.id
                                    AND k.status <> 'done') AS open_tasks
                             FROM td_client_services s
                             LEFT JOIN tenants t ON t.id = s.client_tenant_id
                             LEFT JOIN td_catalog c ON c.id = s.catalog_id
                            WHERE s.tenant_id = %s
                            ORDER BY (s.status = 'active') DESC, 3, s.started_on""", (h["id"],))
            items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, name FROM tenants WHERE NOT is_test AND id <> %s ORDER BY name", (h["id"],))
            clients = [dict(r) for r in cur.fetchall()]
        monthly = sum(float(i["price"] or 0) for i in items if i["status"] == "active" and i["billing"] == "monthly")
        return {"home": dict(h), "items": items, "clients": clients, "monthly_total": round(monthly, 2)}

    @router.post("/techdeva/work/client-services")
    def add_client_service(body: dict = Body(...), x_tenant_id: str | None = Header(None),
                           x_role: str | None = Header(None), authorization: str | None = Header(None),
                           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            c = _client(cur, body.get("client_tenant_id"))
            if str(c["id"]) == str(h["id"]):
                raise HTTPException(400, "That is TechDeva's own business, not a client.")
            cur.execute("SELECT id, name, price, billing FROM td_catalog WHERE id::text = %s AND tenant_id = %s",
                        (str(body.get("catalog_id") or ""), h["id"]))
            item = cur.fetchone()
            if not item:
                raise HTTPException(404, "Pick a service from the catalogue.")
            price = _money(body.get("price")) if body.get("price") not in (None, "") else item["price"]
            cur.execute("""INSERT INTO td_client_services (tenant_id, client_tenant_id, client_name, catalog_id,
                              service_name, price, billing, started_on, notes)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s, current_date), %s) RETURNING id""",
                        (h["id"], c["id"], c["name"], item["id"], item["name"], price, item["billing"],
                         _date(body.get("started_on"), "Start date"), _text(body.get("notes"), 1000, "Notes")))
            new_id = cur.fetchone()["id"]
            _rec(cur, action="techdeva.client_service_add", resource="td_client_services", resource_id=str(new_id),
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"client": c["name"], "service": item["name"], "price": price}, error=None)
        return {"id": str(new_id)}

    @router.put("/techdeva/work/client-services/{cs_id}")
    def edit_client_service(cs_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                            x_role: str | None = Header(None), authorization: str | None = Header(None),
                            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        sets, vals = [], []
        if "status" in body:
            s = str(body.get("status"))
            if s not in ("active", "paused", "ended"):
                raise HTTPException(400, "Status is active, paused or ended.")
            sets.append("status = %s"); vals.append(s)
            sets.append("ended_on = CASE WHEN %s = 'ended' THEN coalesce(ended_on, current_date) ELSE NULL END")
            vals.append(s)
        if "price" in body:
            sets.append("price = %s"); vals.append(_money(body.get("price")))
        if "notes" in body:
            sets.append("notes = %s"); vals.append(_text(body.get("notes"), 1000, "Notes"))
        if not sets:
            raise HTTPException(400, "Nothing to change.")
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("UPDATE td_client_services SET %s, updated_at = now() WHERE id::text = %%s AND tenant_id = %%s "
                        "RETURNING id" % ", ".join(sets), vals + [cs_id, h["id"]])
            if not cur.fetchone():
                raise HTTPException(404, "Not found.")
            _rec(cur, action="techdeva.client_service_edit", resource="td_client_services", resource_id=cs_id,
                   actor=str(uid), actor_type="human", status="ok",
                   detail={k: body.get(k) for k in ("status", "price") if k in body}, error=None)
        return {"ok": True}

    # ---------------------------------------------------------------- tasks (founder)
    @router.get("/techdeva/work/tasks")
    def tasks(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _home(cur)
            if not h:
                return {"home": None, "tasks": []}
            cur.execute("SELECT %s FROM td_tasks WHERE tenant_id = %%s ORDER BY (status = 'done'), due_on NULLS LAST, "
                        "created_at DESC LIMIT 500" % TASK_COLS, (h["id"],))
            return {"home": dict(h), "tasks": [dict(r) for r in cur.fetchall()]}

    @router.post("/techdeva/work/tasks")
    def add_task(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        title = _text(body.get("title"), 300, "Task", required=True)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            client_id, client_name, cs_id = None, "", None
            if body.get("client_service_id"):
                cur.execute("SELECT id, client_tenant_id, client_name FROM td_client_services "
                            "WHERE id::text = %s AND tenant_id = %s", (str(body["client_service_id"]), h["id"]))
                cs = cur.fetchone()
                if not cs:
                    raise HTTPException(404, "That client service is not there.")
                cs_id, client_id, client_name = cs["id"], cs["client_tenant_id"], cs["client_name"]
            elif body.get("client_tenant_id"):
                c = _client(cur, body["client_tenant_id"])
                client_id, client_name = c["id"], c["name"]
            a_id, a_name = _assignee(cur, h["id"], body.get("assignee_user_id"))
            cur.execute("""INSERT INTO td_tasks (tenant_id, client_tenant_id, client_name, client_service_id, title, notes,
                              assignee_user_id, assignee_name, due_on, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Founder') RETURNING id""",
                        (h["id"], client_id, client_name, cs_id, title, _text(body.get("notes"), 2000, "Notes"),
                         a_id, a_name, _date(body.get("due_on"), "Due date")))
            new_id = cur.fetchone()["id"]
            _rec(cur, action="techdeva.task_add", resource="td_tasks", resource_id=str(new_id), actor=str(uid),
                   actor_type="human", status="ok", detail={"title": title, "assignee": a_name, "client": client_name},
                   error=None)
        return {"id": str(new_id)}

    @router.put("/techdeva/work/tasks/{task_id}")
    def edit_task(task_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None), authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            sets, vals = [], []
            if "title" in body:
                sets.append("title = %s"); vals.append(_text(body.get("title"), 300, "Task", required=True))
            if "notes" in body:
                sets.append("notes = %s"); vals.append(_text(body.get("notes"), 2000, "Notes"))
            if "due_on" in body:
                sets.append("due_on = %s"); vals.append(_date(body.get("due_on"), "Due date"))
            if "assignee_user_id" in body:
                a_id, a_name = _assignee(cur, h["id"], body.get("assignee_user_id"))
                sets += ["assignee_user_id = %s", "assignee_name = %s"]; vals += [a_id, a_name]
            if "status" in body:
                s = str(body.get("status"))
                if s not in STATUSES:
                    raise HTTPException(400, "Status is todo, doing or done.")
                sets += ["status = %s", "done_at = CASE WHEN %s = 'done' THEN now() ELSE NULL END"]; vals += [s, s]
            if not sets:
                raise HTTPException(400, "Nothing to change.")
            cur.execute("UPDATE td_tasks SET %s, updated_at = now() WHERE id::text = %%s AND tenant_id = %%s RETURNING id"
                        % ", ".join(sets), vals + [task_id, h["id"]])
            if not cur.fetchone():
                raise HTTPException(404, "That task is not there.")
            _rec(cur, action="techdeva.task_edit", resource="td_tasks", resource_id=task_id, actor=str(uid),
                   actor_type="human", status="ok",
                   detail={k: body.get(k) for k in ("status", "assignee_user_id", "due_on") if k in body}, error=None)
        return {"ok": True}

    @router.delete("/techdeva/work/tasks/{task_id}")
    def delete_task(task_id: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            h = _need_home(cur)
            cur.execute("DELETE FROM td_tasks WHERE id::text = %s AND tenant_id = %s RETURNING title", (task_id, h["id"]))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "That task is not there.")
            _rec(cur, action="techdeva.task_delete", resource="td_tasks", resource_id=task_id, actor=str(uid),
                   actor_type="human", status="ok", detail={"title": r["title"]}, error=None)
        return {"ok": True}

    # ---------------------------------------------------------------- TechDeva's team
    @router.get("/work/tasks")
    def my_work(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """TechDeva's team, in TechDeva's own dashboard. Any other business
        gets home=false and nothing (it owns no catalogue or tasks)."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if not tid or role not in ("owner", "operator"):
            return {"home": False, "tasks": []}
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM td_catalog) AS home")
            if not cur.fetchone()["home"]:
                return {"home": False, "tasks": []}
            cur.execute("SELECT %s FROM td_tasks ORDER BY (status = 'done'), due_on NULLS LAST, created_at DESC LIMIT 300"
                        % TASK_COLS)
            rows = [dict(r) for r in cur.fetchall()]
        return {"home": True, "me": str(uid) if uid else None, "role": role, "tasks": rows}

    @router.put("/work/tasks/{task_id}")
    def update_my_work(task_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                       x_role: str | None = Header(None), authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Move a task along. Staff can move only tasks given to them; the
        owner of TechDeva's business can move any."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if not tid or role not in ("owner", "operator"):
            raise HTTPException(403, "Not allowed.")
        s = str(body.get("status") or "")
        if s not in STATUSES:
            raise HTTPException(400, "Status is todo, doing or done.")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id, assignee_user_id, title FROM td_tasks WHERE id::text = %s", (task_id,))
            t = cur.fetchone()
            if not t:
                raise HTTPException(404, "That task is not there.")
            if role != "owner" and str(t["assignee_user_id"] or "") != str(uid or ""):
                raise HTTPException(403, "Only the person the task is given to can change it.")
            cur.execute("UPDATE td_tasks SET status = %s, done_at = CASE WHEN %s = 'done' THEN now() ELSE NULL END, "
                        "updated_at = now() WHERE id = %s", (s, s, t["id"]))
            record(cur, action="work.task_status", resource="td_tasks", resource_id=task_id, actor=str(uid),
                   actor_type="human", status="ok", detail={"status": s, "title": t["title"]}, error=None)
        return {"ok": True}

    return router
