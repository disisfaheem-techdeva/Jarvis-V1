-- 80_ai_keys.sql — API Manager (founder console): AI provider keys, which
-- WhatsApp agent uses which key, and how much each agent used (26 Oct 2026).
--
-- Until now one GEMINI_API_KEY in the platform .env was copied into every
-- agent, so all businesses shared one free-tier quota and nobody could see it
-- run out. This stores more keys (encrypted), lets the founder give each agent
-- its own, and keeps a daily count of each agent's model calls.
--
-- Founder-only: every table has forced RLS with admin_only (is_admin()), the
-- same policy as email_codes (79). No business session can see a key.
-- Keys are stored only as app/crypto.py AES-256-GCM envelopes, never plaintext.
-- Only new tables; no existing row or table is changed.
--
-- ROLLBACK (only these new tables; nothing else depends on them):
--   BEGIN;
--   DROP TABLE IF EXISTS ai_agent_usage;
--   DROP TABLE IF EXISTS ai_agent_live;
--   DROP TABLE IF EXISTS ai_key_assignments;
--   DROP TABLE IF EXISTS ai_keys;
--   DELETE FROM _td_migrations WHERE name = '80_ai_keys.sql';
--   COMMIT;
BEGIN;

CREATE TABLE IF NOT EXISTS ai_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider      text NOT NULL CHECK (provider IN ('gemini', 'groq', 'openrouter', 'mistral', 'cerebras')),
    label         text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 80),
    owner_note    text CHECK (owner_note IS NULL OR length(owner_note) <= 200),
    secret        jsonb NOT NULL,          -- crypto.encrypt_json envelope {"v":1,...}; never the raw key
    last4         text NOT NULL,
    fingerprint   text NOT NULL,           -- sha256 prefix, so the same key cannot be added twice
    status        text NOT NULL DEFAULT 'untested'
                  CHECK (status IN ('untested', 'ok', 'rate_limited', 'invalid', 'error', 'disabled')),
    last_test_at  timestamptz,
    last_error    text,
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    disabled_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ai_keys_fingerprint_uniq ON ai_keys (fingerprint);

-- The founder's choice per agent. key_id NULL = the platform .env key.
-- A key in use cannot be deleted (RESTRICT): unassign it first.
CREATE TABLE IF NOT EXISTS ai_key_assignments (
    agent_key     text PRIMARY KEY,
    key_id        uuid REFERENCES ai_keys(id) ON DELETE RESTRICT,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'failed')),
    requested_by  text,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    applied_at    timestamptz,
    error         text
);

-- What each agent actually runs with, reported by ops/agent-template/ai-keys-sync.sh.
CREATE TABLE IF NOT EXISTS ai_agent_live (
    agent_key     text PRIMARY KEY,
    live_last4    text,
    running       boolean,
    seen_at       timestamptz NOT NULL DEFAULT now()
);

-- Daily model-call counts per agent, read from each agent's own log (counts
-- only: never a message). model '' holds failures the log does not tie to a model.
CREATE TABLE IF NOT EXISTS ai_agent_usage (
    day           date NOT NULL,
    agent_key     text NOT NULL,
    model         text NOT NULL DEFAULT '',
    ok            integer NOT NULL DEFAULT 0,
    rate_limited  integer NOT NULL DEFAULT 0,
    overloaded    integer NOT NULL DEFAULT 0,
    failed        integer NOT NULL DEFAULT 0,
    key_last4     text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, agent_key, model)
);

ALTER TABLE ai_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_keys FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_keys;
CREATE POLICY admin_only ON ai_keys USING (is_admin()) WITH CHECK (is_admin());

ALTER TABLE ai_key_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_key_assignments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_key_assignments;
CREATE POLICY admin_only ON ai_key_assignments USING (is_admin()) WITH CHECK (is_admin());

ALTER TABLE ai_agent_live ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_agent_live FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_agent_live;
CREATE POLICY admin_only ON ai_agent_live USING (is_admin()) WITH CHECK (is_admin());

ALTER TABLE ai_agent_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_agent_usage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON ai_agent_usage;
CREATE POLICY admin_only ON ai_agent_usage USING (is_admin()) WITH CHECK (is_admin());

GRANT SELECT, INSERT, UPDATE, DELETE ON ai_keys, ai_key_assignments, ai_agent_live, ai_agent_usage TO techdeva_app;

COMMIT;

DO $verify$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ai_keys', 'ai_key_assignments', 'ai_agent_live', 'ai_agent_usage'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            RAISE EXCEPTION '% missing', t;
        END IF;
        IF NOT (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = t) THEN
            RAISE EXCEPTION '% must have forced row-level security', t;
        END IF;
        IF (SELECT count(*) FROM pg_policies WHERE tablename = t AND policyname = 'admin_only'
              AND qual = 'is_admin()' AND with_check = 'is_admin()') <> 1 THEN
            RAISE EXCEPTION '% must have exactly the admin_only policy', t;
        END IF;
    END LOOP;
    RAISE NOTICE 'verify ok: API Manager tables ready, founder-only';
END
$verify$;
