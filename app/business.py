"""Tenant-scoped CRUD for the business-definition layer (Part 2).

Every route resolves the tenant through auth.resolve_scope() -- the one
resolver -- then runs inside db.tenant_session(), so Postgres RLS
(is_admin() OR tenant_id = current_tenant()) is what actually enforces
isolation. No route accepts a tenant id from the caller and no query
interpolates one by hand: a row simply is not visible if RLS says so.

Columns are whitelisted per resource, so every table and column name in every
statement is ours, never the caller's. An unknown field is refused with a
readable 400 rather than silently dropped -- a typo that vanishes is how a
price quietly fails to save.
"""

import uuid
from contextlib import contextmanager

import psycopg

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

_TS = "created_at, updated_at"


class Spec:
    """One tenant-owned resource: what may be read, written and ordered by."""

    def __init__(self, table, read, writable, order, required=()):
        self.table = table
        self.read = read
        self.writable = writable
        self.order = order
        self.required = required

RESOURCES: dict = {
    "customers": Spec(
        table="customers",
        read="id, lead_id, name, phone, email, source, status, tags, notes, "
             "first_seen_at, last_interaction_at, " + _TS,
        writable=["lead_id", "name", "phone", "email", "source", "status",
                  "tags", "notes", "last_interaction_at"],
        order="created_at DESC",
        required=("name",)),
    "services": Spec(
        table="services",
        read="id, name, description, category, price, currency, duration_minutes, "
             "active, sort_order, " + _TS,
        writable=["name", "description", "category", "price", "currency",
                  "duration_minutes", "active", "sort_order"],
        order="sort_order ASC, name ASC",
        required=("name",)),
    "products": Spec(
        table="products",
        read="id, name, description, category, sku, price, currency, active, "
         "track_stock, low_stock_threshold, " + _TS,
        writable=["name", "description", "category", "sku", "price", "currency", "active",
                   "track_stock", "low_stock_threshold"],
        order="name ASC",
        required=("name",)),
    "packages": Spec(
        table="packages",
        read="id, name, description, price, currency, validity_days, terms, active, " + _TS,
        writable=["name", "description", "price", "currency", "validity_days",
                  "terms", "active"],
        order="name ASC",
        required=("name",)),
    "package_items": Spec(
        table="package_items",
        read="id, package_id, service_id, product_id, quantity",
        writable=["package_id", "service_id", "product_id", "quantity"],
        order="id ASC",
        required=("package_id",)),
    "staff": Spec(
        table="staff",
        read="id, name, role, department, email, phone, active, " + _TS,
        writable=["name", "role", "department", "email", "phone", "active"],
        order="name ASC",
        required=("name",)),
    "staff_services": Spec(
        table="staff_services",
        read="id, staff_id, service_id",
        writable=["staff_id", "service_id"],
        order="id ASC",
        required=("staff_id", "service_id")),
    "locations": Spec(
        table="locations",
        read="id, name, address, city, country, phone, timezone, is_primary, active, " + _TS,
        writable=["name", "address", "city", "country", "phone", "timezone",
                  "is_primary", "active"],
        order="is_primary DESC, name ASC",
        required=("name",)),
    "business_hours": Spec(
        table="business_hours",
        read="id, location_id, day_of_week, opens, closes, is_closed",
        writable=["location_id", "day_of_week", "opens", "closes", "is_closed"],
        order="day_of_week ASC",
        required=("day_of_week",)),
    "business_policies": Spec(
        table="business_policies",
        read="id, kind, title, body, active, " + _TS,
        writable=["kind", "title", "body", "active"],
        order="kind ASC, title ASC",
        required=("kind", "title", "body")),
    "business_goals": Spec(
        table="business_goals",
        read="id, metric, label, target, period, starts_on, ends_on, active, " + _TS,
        writable=["metric", "label", "target", "period", "starts_on", "ends_on", "active"],
        order="created_at DESC",
        required=("metric", "target")),
}


# --------------------------------------------------------------------------
# Part 3 operational resources. Same whitelisted, RLS-enforced router -- these
# are ordinary tenant-owned rows, so they need no second CRUD implementation.
# The things that are NOT ordinary CRUD (an invoice with its lines and running
# balance, a day's closing, stock on hand) live in app/operations.py.
# --------------------------------------------------------------------------
RESOURCES.update({
    "appointments": Spec(
        table="appointments",
        read="id, customer_id, lead_id, service_id, staff_id, location_id, service, "
             "starts_at, ends_at, status, price, currency, notes, operator, created_at",
        writable=["customer_id", "lead_id", "service_id", "staff_id", "location_id",
                  "service", "starts_at", "ends_at", "status", "price", "currency", "notes"],
        order="starts_at DESC",
        required=("starts_at",)),
    "invoices": Spec(
        table="invoices",
        read="id, customer_id, location_id, number, status, issued_on, due_on, currency, "
             "subtotal, discount, tax, total, notes, voided_at, void_reason, " + _TS,
        writable=["customer_id", "location_id", "number", "status", "issued_on",
                  "due_on", "currency", "discount", "tax", "notes"],
        order="issued_on DESC, created_at DESC",
        required=("number",)),
    "payments": Spec(
        table="payments",
        read="id, invoice_id, customer_id, location_id, amount, currency, method, "
             "reference, notes, paid_at, created_at",
        writable=["invoice_id", "customer_id", "location_id", "amount", "currency",
                  "method", "reference", "notes", "paid_at"],
        order="paid_at DESC",
        required=("amount",)),
    "expenses": Spec(
        table="expenses",
        read="id, location_id, category, description, amount, currency, method, "
             "spent_on, notes, created_at",
        writable=["location_id", "category", "description", "amount", "currency",
                  "method", "spent_on", "notes"],
        order="spent_on DESC",
        required=("description", "amount")),
    "stock_movements": Spec(
        table="stock_movements",
        read="id, product_id, location_id, kind, quantity_delta, unit_cost, reason, "
             "reference, occurred_at, created_at",
        writable=["product_id", "location_id", "kind", "quantity_delta", "unit_cost",
                  "reason", "reference", "occurred_at"],
        order="occurred_at DESC",
        required=("product_id", "kind", "quantity_delta")),
    "executions": Spec(
        table="executions",
        # read-only by convention: writable is empty, so POST/PATCH are refused
        # with "nothing to save". An audit trail nobody can edit is the point.
        read="id, actor, actor_type, action, resource, resource_id, status, detail, error, at",
        writable=[],
        order="at DESC"),
})


# Tables whose money or ledger rules live elsewhere. Writing them through the
# generic router would skip those rules (an invoice total not recomputed, a
# payment that does not move the invoice status, an appointment deleted while
# its bill stays behind), so the generic verbs are refused with the way to do it.
DEDICATED = {
    "appointments": {"POST": "Book through POST /appointments/book.",
                     "PATCH": "Change a booking through PATCH /appointments/{id}.",
                     "DELETE": "Delete a booking through DELETE /appointments/{id}."},
    "invoices": {"POST": "Create a bill through POST /operations/invoices.",
                 "PATCH": "Bills are not edited. Take a payment or void the bill.",
                 "DELETE": "Bills are never deleted. Void it with a reason instead."},
    "payments": {"POST": "Take a payment through POST /operations/invoices/{id}/pay.",
                 "PATCH": "Payments are not edited once recorded.",
                 "DELETE": "Payments are never deleted from the books."},
    "stock_movements": {"PATCH": "Stock history is not edited. Record a correcting adjustment.",
                        "DELETE": "Stock history is not deleted. Record a correcting adjustment."},
}

# What still points at a row. Deleting it would silently cascade or orphan
# those records, so the delete is refused and the owner is told what to do.
IN_USE = {
    "customers": [("appointments", "customer_id", "appointment"),
                  ("invoices", "customer_id", "bill"),
                  ("payments", "customer_id", "payment")],
    "services": [("appointments", "service_id", "appointment"),
                 ("invoice_items", "service_id", "bill line"),
                 ("package_items", "service_id", "package")],
    "products": [("stock_movements", "product_id", "stock movement"),
                 ("invoice_items", "product_id", "bill line"),
                 ("package_items", "product_id", "package")],
    "packages": [("invoice_items", "package_id", "bill line")],
    "staff": [("appointments", "staff_id", "appointment"),
              ("staff_attendance", "staff_id", "attendance record")],
    "locations": [("appointments", "location_id", "appointment"),
                  ("invoices", "location_id", "bill")],
}
_DEACTIVATE = {"services", "products", "packages", "staff", "locations"}

# Who may change what. Reading is open to everyone in the business; these are
# the writes an operator (front desk) is refused. Defining what the business
# sells and at what price, its people, places and rules is the owner's call,
# as is removing or rewriting a record of money that already went out.
OWNER_ROLES = ("owner", "techdeva_admin")
OWNER_ONLY = {
    "services": {"POST", "PATCH", "DELETE"},
    "products": {"POST", "PATCH", "DELETE"},
    "packages": {"POST", "PATCH", "DELETE"},
    "package_items": {"POST", "PATCH", "DELETE"},
    "staff": {"POST", "PATCH", "DELETE"},
    "staff_services": {"POST", "PATCH", "DELETE"},
    "locations": {"POST", "PATCH", "DELETE"},
    "business_hours": {"POST", "PATCH", "DELETE"},
    "business_policies": {"POST", "PATCH", "DELETE"},
    "business_goals": {"POST", "PATCH", "DELETE"},
    "expenses": {"PATCH", "DELETE"},
    "customers": {"DELETE"},
}


def _allowed(resource, verb, role):
    if verb in OWNER_ONLY.get(resource, ()) and role not in OWNER_ROLES:
        raise HTTPException(403, "Only the business owner can do this.")


@contextmanager
def _readable_db_errors(resource):
    """Turn a database refusal into a sentence the owner can act on, not a 500."""
    try:
        yield
    except psycopg.errors.UniqueViolation:
        what = "phone number" if resource == "customers" else "name"
        raise HTTPException(409, "Another {0} entry already has this {1}.".format(
            resource.rstrip("s"), what))
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(400, "One of the linked records does not exist in this business.")
    except psycopg.errors.CheckViolation:
        raise HTTPException(400, "A value is out of range (for example a negative price or an empty name).")
    except (psycopg.errors.InvalidTextRepresentation, psycopg.errors.InvalidDatetimeFormat,
            psycopg.errors.NumericValueOutOfRange, psycopg.errors.DatetimeFieldOverflow):
        raise HTTPException(400, "A value is not in the right format (a number, date or id).")


def _uuid(item_id):
    try:
        return str(uuid.UUID(str(item_id)))
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")


def _refuse_generic(resource, verb):
    why = DEDICATED.get(resource, {}).get(verb)
    if why:
        raise HTTPException(405, why)


def _in_use(cur, resource, item_id):
    used = []
    for table, col, label in IN_USE.get(resource, []):
        cur.execute("SELECT count(*) AS n FROM {0} WHERE {1} = %s".format(table, col), (item_id,))
        n = cur.fetchone()["n"]
        if n:
            used.append("{0} {1}{2}".format(n, label, "" if n == 1 else "s"))
    return used


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _spec(resource):
    spec = RESOURCES.get(resource)
    if not spec:
        raise HTTPException(404, "unknown business resource: {0}".format(resource))
    return spec


def _clean(spec, body, creating):
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    unknown = [k for k in body if k not in spec.writable]
    if unknown:
        raise HTTPException(400, "unknown field(s): {0}. allowed: {1}".format(
            ", ".join(sorted(unknown)), ", ".join(spec.writable)))
    data = {k: v for k, v in body.items() if k in spec.writable}
    if creating:
        for field in spec.required:
            v = data.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise HTTPException(400, "{0} is required".format(field))
    if not data:
        raise HTTPException(400, "nothing to save")
    return data


def _onboarding_fallback(resource, cfg):
    """When a client finished onboarding but never manually added rows via
    Settings/Catalogue, the operational tables are genuinely empty even though
    the onboarding config blob has real data. Surface that data here, read-only,
    so the client does not see "nothing yet" for something they already told us.
    Each synthesized item carries "source": "onboarding" so it is never confused
    with a real, editable row. Never fabricates: only returns items built from
    values the client actually entered."""
    if not cfg:
        return None
    if resource == "services":
        services = cfg.get("services") or []
        if not isinstance(services, list) or not services:
            return None
        price_list = cfg.get("price_list") or []
        price_by_name = {}
        # Stored two ways: [{"service": .., "price": ..}] or {"Service": price}.
        if isinstance(price_list, dict):
            price_list = [{"service": k, "price": v} for k, v in price_list.items()]
        for p in price_list:
            if isinstance(p, dict) and p.get("service"):
                price_by_name[str(p["service"]).strip().lower()] = p
        items = []
        for i, s in enumerate(services):
            # Onboarding stores services either as {"name": ...} objects or as
            # plain names ("Rhinoplasty"). Plain names were silently dropped,
            # so a client who listed ten services saw none.
            if isinstance(s, str) and s.strip():
                s = {"name": s.strip()}
            if not isinstance(s, dict) or not s.get("name"):
                continue
            price_row = price_by_name.get(str(s["name"]).strip().lower(), {})
            items.append({
                "id": None,
                "name": s.get("name"),
                "description": None,
                "category": s.get("category"),
                "price": price_row.get("price", s.get("price")),
                "currency": price_row.get("currency", s.get("currency")),
                "duration_minutes": s.get("duration_min"),
                "active": True,
                "sort_order": i,
                "source": "onboarding",
            })
        return items or None
    if resource == "locations":
        geo = cfg.get("geo") or {}
        if not isinstance(geo, dict) or not any(geo.get(k) for k in ("address", "city", "country")):
            return None
        brand = cfg.get("brand") or {}
        return [{
            "id": None,
            "name": (brand.get("name") if isinstance(brand, dict) else None) or "Main location",
            "address": geo.get("address"),
            "city": geo.get("city"),
            "country": geo.get("country"),
            "phone": None,
            "timezone": None,
            "is_primary": True,
            "active": True,
            "source": "onboarding",
        }]
    return None


def _unsaved_onboarding(P, tid, saved_rows):
    """Onboarding services whose name is not in the services table yet."""
    try:
        cfg = P.load_config(tid) or {}
    except Exception:
        return []
    items = _onboarding_fallback("services", cfg) or []
    have = {str(r.get("name") or "").strip().lower() for r in saved_rows}
    return [i for i in items if str(i["name"]).strip().lower() not in have]


def make_business_router(P=None):
    router = APIRouter()

    @router.get("/business/resources")
    def resources(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What this tenant can define. Requires a session like everything else."""
        _identity(x_tenant_id, x_role, authorization, td_session)
        return {"resources": {k: {"writable": v.writable, "required": list(v.required)}
                              for k, v in RESOURCES.items()}}

    @router.get("/business/{resource}")
    def list_items(resource: str,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT {0} FROM {1} ORDER BY {2} LIMIT 500".format(
                spec.read, spec.table, spec.order))
            rows = cur.fetchall()
        if not rows and P is not None:
            try:
                cfg = P.load_config(tid) or {}
            except Exception:
                cfg = {}
            fallback = _onboarding_fallback(resource, cfg)
            if fallback:
                return {"resource": resource, "count": len(fallback), "items": fallback,
                        "note": "from onboarding, not yet saved as editable records"}
        out = {"resource": resource, "count": len(rows), "items": rows}
        if resource == "services" and rows and P is not None:
            pend = _unsaved_onboarding(P, tid, rows)
            if pend:
                out["pending_onboarding"] = pend
        return out

    @router.post("/business/services/import-onboarding")
    def import_onboarding(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                          authorization: str | None = Header(None),
                          td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Save the services the client listed during onboarding as real, editable
        rows in the services table -- the one list appointments, bills and the AI
        read. Idempotent: a name already saved is skipped, never duplicated."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _allowed("services", "POST", role)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT name FROM services")
            have = cur.fetchall()
        todo = _unsaved_onboarding(P, tid, have) if P is not None else []
        saved = []
        with _readable_db_errors("services"), tenant_session(tid, role) as cur:
            for s in todo:
                price = s.get("price")
                try:
                    price = float(str(price).replace(",", "")) if price not in (None, "") else None
                except ValueError:
                    price = None
                cur.execute(
                    "INSERT INTO services (tenant_id, name, category, price, currency, duration_minutes, sort_order) "
                    "VALUES (current_tenant(), %s, %s, %s, COALESCE(%s, 'PKR'), %s, %s) RETURNING id, name, price",
                    (s["name"], s.get("category"), price, s.get("currency"),
                     s.get("duration_minutes") if isinstance(s.get("duration_minutes"), int) else None,
                     s.get("sort_order") or 0))
                saved.append(cur.fetchone())
            if saved:
                record(cur, action="services.import_onboarding", resource="services", resource_id=None,
                       actor=uid, detail={"saved": [x["name"] for x in saved]})
        return {"saved": saved, "count": len(saved)}

    @router.post("/business/{resource}")
    def create_item(resource: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _refuse_generic(resource, "POST")
        _allowed(resource, "POST", role)
        data = _clean(spec, body, True)
        cols = ["tenant_id"] + list(data.keys())
        vals = [tid] + list(data.values())
        sql = "INSERT INTO {0} ({1}) VALUES ({2}) RETURNING {3}".format(
            spec.table, ", ".join(cols), ", ".join(["%s"] * len(cols)), spec.read)
        with _readable_db_errors(resource), tenant_session(tid, role) as cur:
            cur.execute(sql, vals)
            row = cur.fetchone()
            record(cur, action="create", resource=resource, resource_id=row.get("id"),
                   actor=uid, detail={"fields": sorted(data.keys())})
        return {"resource": resource, "item": row}

    @router.patch("/business/{resource}/{item_id}")
    def update_item(resource: str, item_id: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _refuse_generic(resource, "PATCH")
        _allowed(resource, "PATCH", role)
        item_id = _uuid(item_id)
        data = _clean(spec, body, False)
        sets = ", ".join("{0} = %s".format(k) for k in data)
        if "updated_at" not in spec.read:
            sql = "UPDATE {0} SET {1} WHERE id = %s RETURNING {2}"
        else:
            sql = "UPDATE {0} SET {1}, updated_at = now() WHERE id = %s RETURNING {2}"
        sql = sql.format(spec.table, sets, spec.read)
        with _readable_db_errors(resource), tenant_session(tid, role) as cur:
            cur.execute(sql, list(data.values()) + [item_id])
            row = cur.fetchone()
            if row:
                record(cur, action="update", resource=resource, resource_id=item_id,
                       actor=uid, detail={"fields": sorted(data.keys())})
        # RLS makes another tenant's row invisible, so "not found" is also the
        # correct answer to "someone guessed an id that isn't theirs".
        if not row:
            raise HTTPException(404, "not found")
        return {"resource": resource, "item": row}

    @router.delete("/business/{resource}/{item_id}")
    def delete_item(resource: str, item_id: str,
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        _refuse_generic(resource, "DELETE")
        _allowed(resource, "DELETE", role)
        item_id = _uuid(item_id)
        with tenant_session(tid, role) as cur:
            used = _in_use(cur, resource, item_id)
            if used:
                raise HTTPException(409, "This is still used by {0}, so deleting it would break "
                                         "those records.{1}".format(", ".join(used),
                    " Mark it inactive instead." if resource in _DEACTIVATE else ""))
            cur.execute("DELETE FROM {0} WHERE id = %s RETURNING id".format(spec.table), (item_id,))
            row = cur.fetchone()
            if row:
                record(cur, action="delete", resource=resource, resource_id=item_id, actor=uid)
        if not row:
            raise HTTPException(404, "not found")
        return {"resource": resource, "deleted": row["id"]}

    # ---------------------------------------------------------------- revenue
    # Part 1 found POST /revenue with no way to read it back. revenue_events
    # already holds amount / type / attributed_source / occurred_at, so this
    # reads the existing table -- no new revenue store.
    @router.get("/revenue")
    def read_revenue(days: int = 90,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if days < 1 or days > 1095:
            raise HTTPException(400, "days must be between 1 and 1095")
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT id, lead_id, amount, type, attributed_source, occurred_at, "
                "campaign_id, attribution_model FROM revenue_events "
                "WHERE occurred_at >= now() - make_interval(days => %s) "
                "ORDER BY occurred_at DESC LIMIT 500", (days,))
            events = cur.fetchall()
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n "
                "FROM revenue_events WHERE occurred_at >= now() - make_interval(days => %s)",
                (days,))
            totals = cur.fetchone() or {}
            cur.execute(
                "SELECT attributed_source::text AS source, COALESCE(SUM(amount), 0) AS total "
                "FROM revenue_events WHERE occurred_at >= now() - make_interval(days => %s) "
                "GROUP BY 1 ORDER BY 2 DESC", (days,))
            by_source = cur.fetchall()
        return {"window_days": days, "total": totals.get("total"),
                "count": totals.get("n"), "by_source": by_source, "events": events}

    return router
