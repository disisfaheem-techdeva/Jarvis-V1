-- 41_subdomains.sql
-- Each business can have its own address, e.g. drirfan.techdeva.net, where
-- /login is that business's own PIN screen (Faheem, 29 Sep 2026).
-- The founder sets it in the console; nothing is set automatically.
-- Additive: one nullable column.
BEGIN;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subdomain text;
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_subdomain_ck;
ALTER TABLE tenants ADD CONSTRAINT tenants_subdomain_ck CHECK (
  subdomain IS NULL OR (subdomain ~ '^[a-z][a-z0-9-]{1,30}[a-z0-9]$'
    AND subdomain NOT IN ('www','api','portal','crm','mail','admin','app','dashboard','login','pin',
                          'techdeva','founder','status','static','cdn','ftp','smtp','webmail','cpanel')));
CREATE UNIQUE INDEX IF NOT EXISTS tenants_subdomain_key ON tenants (subdomain) WHERE subdomain IS NOT NULL;

COMMIT;
