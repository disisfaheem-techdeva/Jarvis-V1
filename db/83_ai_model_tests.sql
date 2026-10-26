-- 83: API Manager test bench (Rafay, 26 Oct 2026, Hermes training phase 2).
--
-- Before an agent is moved to another key or model, the founder runs a few
-- customer questions through a COPY of that agent (its SOUL, memories and
-- skills; no WhatsApp session, no chats) with that key, on the server
-- (ops/agent-template/model-bench.sh, cron). The answers, times and errors
-- are kept here. A Groq free key that failed every message with 413 on
-- 26 Oct is what this catches before a customer does.
--
-- ROLLBACK (by hand, techdeva database only):
--   DROP TABLE IF EXISTS ai_model_tests;
--   DELETE FROM _td_migrations WHERE name = '83_ai_model_tests.sql';
BEGIN;

CREATE TABLE IF NOT EXISTS ai_model_tests (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_key    text NOT NULL,
  key_id       uuid REFERENCES ai_keys(id) ON DELETE CASCADE,      -- NULL = the platform .env key
  model        text,                                               -- what was tested (copied, the key's model may change)
  questions    jsonb NOT NULL DEFAULT '[]'::jsonb,
  results      jsonb,                                              -- [{q, a, ms, ok, error}]
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
  passed       int,
  total        int,
  error        text,
  requested_by text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ai_model_tests_status_idx ON ai_model_tests (status, requested_at);

ALTER TABLE ai_model_tests ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_model_tests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_model_tests;
CREATE POLICY admin_only ON ai_model_tests USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_model_tests TO techdeva_app;

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ai_model_tests' AND relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify: ai_model_tests missing or RLS not forced';
  END IF;
  RAISE NOTICE 'verify: 83 ok';
END
$verify$;

COMMIT;
