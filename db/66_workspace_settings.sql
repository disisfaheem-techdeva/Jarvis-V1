-- 66_workspace_settings.sql
-- Workspace settings on tenant_config: the business's own timezone, currency
-- and dashboard language. Saved through PUT /portal/config (owner only),
-- validated there (app/portal.py ConfigIn). Additive only; existing rows take
-- the defaults, which are what the platform has assumed until now.
BEGIN;
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS timezone text DEFAULT 'Asia/Karachi';
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS currency text DEFAULT 'PKR';
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS language text DEFAULT 'en';
COMMIT;
