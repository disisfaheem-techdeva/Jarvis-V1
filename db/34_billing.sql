-- 34_billing.sql
-- The client's side of paying TechDeva.
--   payment_claims    : a client says "I paid invoice X by <method>, ref <ref>".
--                       TechDeva checks its bank/JazzCash and confirms or rejects.
--                       A claim is never a payment until the founder confirms it
--                       (then a client_payments row is written).
--   platform_settings : founder-edited text, e.g. how to pay TechDeva. Admin-only;
--                       the API hands a client only the keys meant for clients.
-- Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS payment_claims (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id  uuid REFERENCES client_invoices(id) ON DELETE SET NULL,
  amount      numeric NOT NULL CHECK (amount > 0),
  method      text NOT NULL,
  reference   text NOT NULL DEFAULT '',
  note        text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','rejected')),
  claimed_by  text,
  decided_by  text,
  decided_at  timestamptz,
  reason      text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payment_claims_tenant_idx ON payment_claims (tenant_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS payment_claims_one_open_per_invoice
  ON payment_claims (invoice_id) WHERE status = 'pending';

ALTER TABLE payment_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_claims FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON payment_claims;
CREATE POLICY tenant_isolation ON payment_claims
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE ON payment_claims TO techdeva_app;

CREATE TABLE IF NOT EXISTS platform_settings (
  key        text PRIMARY KEY,
  value      text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE platform_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON platform_settings;
CREATE POLICY admin_only ON platform_settings USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE ON platform_settings TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payment_claims')
     OR NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'platform_settings') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: payment_claims tenant isolated, platform_settings admin-only';
END
$verify$;

COMMIT;
