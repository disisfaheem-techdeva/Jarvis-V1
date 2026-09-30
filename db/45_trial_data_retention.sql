-- 45_trial_data_retention.sql
-- Faheem's rule (30 Sep 2026): only paying clients' data is kept. A business
-- whose free trial ended unpaid is warned 3 days before, and 15 days after the
-- trial ended all its data is deleted at once (no safety copy).
--   data_retention    per business: the founder's "keep" hold, and when the
--                     owner was warned
--   tenant_deletions  what was deleted and when (name and row counts only, no
--                     business data), and whether its WhatsApp agent was removed
-- Founder-only tables: a business never sees these rows. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS data_retention (
  tenant_id    uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  hold         boolean NOT NULL DEFAULT false,
  hold_reason  text NOT NULL DEFAULT '',
  held_by      text NOT NULL DEFAULT '',
  warned_at    timestamptz,
  emailed      boolean NOT NULL DEFAULT false,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_deletions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL,
  business_name      text NOT NULL DEFAULT '',
  trial_ended_at     timestamptz,
  deleted_at         timestamptz NOT NULL DEFAULT now(),
  rows_deleted       jsonb NOT NULL DEFAULT '{}'::jsonb,
  agent_keys         text[] NOT NULL DEFAULT '{}',
  agents_removed_at  timestamptz,
  agent_error        text
);

DO $rls$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['data_retention','tenant_deletions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS founder_only ON %I', t);
    EXECUTE format('CREATE POLICY founder_only ON %I USING (is_admin()) WITH CHECK (is_admin())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END
$rls$;

DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname IN ('data_retention','tenant_deletions')
             AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: retention tables are founder-only';
END
$verify$;

COMMIT;
