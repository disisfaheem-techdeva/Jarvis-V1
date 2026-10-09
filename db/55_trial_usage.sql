-- 55_trial_usage.sql
-- Free-trial limits (Faheem, 3 Oct 2026): during the trial a business's
-- WhatsApp agent answers up to 50 customer messages, then pauses until the
-- business pays (its plan then applies). This keeps the last count and whether
-- the agent is paused for the trial limit, so the dashboard can say so even
-- while the paused agent cannot be asked. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS trial_usage (
  tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  messages    int NOT NULL DEFAULT 0 CHECK (messages >= 0),
  paused      boolean NOT NULL DEFAULT false,
  counted_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE trial_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_usage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON trial_usage;
CREATE POLICY tenant_isolation ON trial_usage USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON trial_usage TO techdeva_app;

DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'trial_usage' AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: trial_usage row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: trial_usage tenant isolated';
END
$verify$;

COMMIT;
