"""Database access with Row-Level Security wired to the current tenant/role.

Every request opens a connection and sets:
    SET app.tenant_id = '<uuid>'
    SET app.role      = 'owner' | 'operator' | 'techdeva_admin'
so the Postgres RLS policies (schema.sql) enforce isolation automatically.
The app connects as the NON-superuser role `techdeva_app` so RLS is not bypassed.
"""
import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://techdeva_app:techdeva@localhost:5432/techdeva",
)


@contextmanager
def tenant_session(tenant_id: str | None, role: str, partner_id: str | None = None):
    """Yield a cursor scoped to a tenant + role. techdeva_admin passes tenant_id=None.

    Founder + one business never means "every business": is_admin() would
    open all rows while the caller believes it is inside one. Acting for a
    single business, the founder is that business's owner, fenced to it.

    A white-label partner (role 'partner') names no business and a business
    session names no partner. Both must never be set at once: the policy on
    `tenants` is "my own business OR my agency's businesses", so a session
    carrying both would see one business's rows and its agency's list mixed
    together. The two lines below make that unreachable rather than a rule
    somebody has to remember."""
    if role == "techdeva_admin" and tenant_id:
        role = "owner"
    if role == "partner":
        tenant_id = None
    else:
        partner_id = None
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # set_config(..., is_local=true) -> scoped to this transaction only
            cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                        (str(tenant_id) if tenant_id else "",))
            cur.execute("SELECT set_config('app.role', %s, true)", (role,))
            cur.execute("SELECT set_config('app.partner_id', %s, true)",
                        (str(partner_id) if partner_id else "",))
            yield cur
        conn.commit()
