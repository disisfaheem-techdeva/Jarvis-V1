-- 86: API Manager auto-switcher (Rafay, 26 Oct 2026, Hermes training phase 3).
--
-- Keys the founder puts "in rotation" form a pool. When an agent's key keeps
-- hitting its limit (429 / 413) and gives no answers for 15 minutes, the
-- platform moves that agent to the next pool key that has passed the test
-- bench, records the switch here and opens a founder incident. Off unless
-- the founder turns it on (platform_settings ai_key_autoswitch = '1').
--
-- ROLLBACK (by hand, techdeva database only):
--   DROP TABLE IF EXISTS ai_key_switches;
--   ALTER TABLE ai_keys DROP COLUMN IF EXISTS in_pool, DROP COLUMN IF EXISTS pool_order;
--   DELETE FROM platform_settings WHERE key = 'ai_key_autoswitch';
--   DELETE FROM _td_migrations WHERE name = '86_ai_key_pool.sql';
BEGIN;

ALTER TABLE ai_keys ADD COLUMN IF NOT EXISTS in_pool boolean NOT NULL DEFAULT false;
ALTER TABLE ai_keys ADD COLUMN IF NOT EXISTS pool_order int NOT NULL DEFAULT 100;

CREATE TABLE IF NOT EXISTS ai_key_switches (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_key  text NOT NULL,
  from_last4 text,
  to_key_id  uuid REFERENCES ai_keys(id) ON DELETE SET NULL,
  to_last4   text,
  reason     text NOT NULL,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_key_switches_agent_idx ON ai_key_switches (agent_key, at DESC);

ALTER TABLE ai_key_switches ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_key_switches FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_key_switches;
CREATE POLICY admin_only ON ai_key_switches USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_key_switches TO techdeva_app;

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'ai_keys' AND column_name = 'in_pool')
     OR NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ai_key_switches' AND relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify: 86 incomplete';
  END IF;
  RAISE NOTICE 'verify: 86 ok';
END
$verify$;

COMMIT;
