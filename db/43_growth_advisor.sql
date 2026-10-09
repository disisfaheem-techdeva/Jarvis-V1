-- 43_growth_advisor.sql
-- The growth advisor (Pro): each week, a few plain tips built from the
-- business's own numbers (leads, bookings, no-shows, revenue, unpaid bills,
-- lapsed customers, ad results). One row per business per week, refreshed
-- during the week; earlier weeks stay as history. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS advisor_reports (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  week_start    date NOT NULL,
  facts         jsonb NOT NULL DEFAULT '{}'::jsonb,
  advice        jsonb NOT NULL DEFAULT '{}'::jsonb,
  worded_by     text NOT NULL DEFAULT 'rules',
  generated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, week_start)
);

ALTER TABLE advisor_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE advisor_reports FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON advisor_reports;
CREATE POLICY tenant_isolation ON advisor_reports USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON advisor_reports TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'advisor_reports') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on advisor_reports';
  END IF;
  RAISE NOTICE 'verify ok: advisor_reports tenant isolated';
END
$verify$;

COMMIT;
