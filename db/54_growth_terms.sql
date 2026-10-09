-- 54_growth_terms.sql
-- Growth proof (founder console): for each client business, the sales level
-- before TechDeva (baseline), when TechDeva started, and the profit share the
-- founder agreed with them. Read and written only by the founder's routes;
-- the client's own sales come from their payments. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS client_growth_terms (
  tenant_id         uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  started_on        date,
  baseline_monthly  numeric CHECK (baseline_monthly IS NULL OR baseline_monthly >= 0),
  baseline_note     text NOT NULL DEFAULT '',
  share_pct         numeric CHECK (share_pct IS NULL OR (share_pct >= 0 AND share_pct <= 50)),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE client_growth_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_growth_terms FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON client_growth_terms;
CREATE POLICY tenant_isolation ON client_growth_terms USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON client_growth_terms TO techdeva_app;

DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'client_growth_terms' AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: client_growth_terms row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: client_growth_terms tenant isolated';
END
$verify$;

COMMIT;
