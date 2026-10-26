-- 84: WhatsApp agent On/Off and auto-hold (Rafay, 26 Oct 2026).
--
-- The founder can switch any template agent off and on from the console
-- (API Manager). And when an agent's AI key keeps hitting its limit with no
-- answers, the platform puts the agent on hold by itself (auto = true), opens
-- an incident, and switches it back on at resume_at (the key's quota reset).
-- Applied by ops/agent-template/ai-keys-sync.sh (root cron): off = the
-- agent's compose project is stopped (WhatsApp stays paired; customers get no
-- reply while it is off), on = started again.
--
-- ROLLBACK (by hand, techdeva database only):
--   DROP TABLE IF EXISTS agent_power;
--   DELETE FROM platform_settings WHERE key = 'ai_autohold';
--   DELETE FROM _td_migrations WHERE name = '84_agent_power.sql';
BEGIN;

CREATE TABLE IF NOT EXISTS agent_power (
  agent_key    text PRIMARY KEY,
  want         text NOT NULL CHECK (want IN ('on', 'off')),
  auto         boolean NOT NULL DEFAULT false,      -- true = put on hold by the platform (limit reached)
  reason       text,
  resume_at    timestamptz,                         -- auto hold: switched back on at this time
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'failed')),
  requested_by text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  applied_at   timestamptz,
  error        text
);

ALTER TABLE agent_power ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_power FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON agent_power;
CREATE POLICY admin_only ON agent_power USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_power TO techdeva_app;

INSERT INTO platform_settings (key, value) VALUES ('ai_autohold', '1') ON CONFLICT (key) DO NOTHING;

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'agent_power' AND relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify: agent_power missing or RLS not forced';
  END IF;
  RAISE NOTICE 'verify: 84 ok';
END
$verify$;

COMMIT;
