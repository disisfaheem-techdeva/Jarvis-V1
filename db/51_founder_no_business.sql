-- 51_founder_no_business.sql
-- A founder (techdeva_admin) account belongs to no business. One was
-- attached to TechDeva's own business, which made its logins "admin + that
-- business" and let one business's screens show every business's rows
-- (Faheem, 1 Oct 2026). The code already ignores it (db.py, auth.py); this
-- fixes the account itself and stops it happening again.
-- Changes only founder accounts' business link; no business data is touched.
BEGIN;
UPDATE users SET tenant_id = NULL WHERE role = 'techdeva_admin' AND tenant_id IS NOT NULL;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_founder_no_tenant_ck;
ALTER TABLE users ADD CONSTRAINT users_founder_no_tenant_ck
  CHECK (role <> 'techdeva_admin' OR tenant_id IS NULL);
COMMIT;
