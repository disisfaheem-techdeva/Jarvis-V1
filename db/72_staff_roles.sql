-- 72_staff_roles.sql
-- Team roles and permissions (Faheem, 23 Oct 2026).
--   staff_roles      a business's own custom roles: a name and what it may do,
--                    {"crm": ["view","create"], ...}. The preset roles (Manager,
--                    Shopkeeper, Operations, Support, Marketing, Accountant, HR)
--                    live in code (app/roles.py), so every business has them.
--   users.staff_role which role a login has: a preset key ("shopkeeper") or
--                    "custom:<id>". NULL = as today (the owner, or an old
--                    staff/full login), nothing restricted beyond its base role.
-- The base role in users.role (owner/operator) still decides row-level security
-- and the existing owner-only checks; the staff role narrows it per section and
-- action. Additive: no row is changed or deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS staff_roles (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 60),
  permissions jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(permissions) = 'object'),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS staff_roles_tenant_name ON staff_roles (tenant_id, lower(btrim(name)));

ALTER TABLE staff_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_roles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON staff_roles;
CREATE POLICY tenant_isolation ON staff_roles USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON staff_roles TO techdeva_app;

ALTER TABLE users ADD COLUMN IF NOT EXISTS staff_role text
  CHECK (staff_role IS NULL OR staff_role ~ '^([a-z]{2,20}|custom:[0-9a-f-]{36})$');

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'staff_roles') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on staff_roles';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'staff_role') THEN
    RAISE EXCEPTION 'verify failed: users.staff_role missing';
  END IF;
  RAISE NOTICE 'verify ok: staff roles ready; every existing login unchanged (staff_role NULL)';
END
$verify$;

COMMIT;
