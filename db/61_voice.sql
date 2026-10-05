-- 61: Voice: the AI phone agent (5 Oct 2026)
--
-- A business's own phone number (a SIP line from a licensed operator) rings the
-- platform's phone exchange (Asterisk, ops/voice); the voice bridge
-- (app/voice_bridge.py) answers with the business's facts through Gemini Live
-- and writes one row here per call: who called, how long, what was said, and
-- the outcome. Step 1 is incoming calls; outgoing calls to approved warm leads
-- come next, using the same tables.
--
-- voice_numbers.did is unique across ALL businesses (a number rings exactly one
-- business). Calls are only ever answered for an enabled number. A daily
-- minutes cap protects the business's budget.
-- Additive only. Forced RLS.

-- a lead that came from a phone call (outside the transaction: enum values
-- cannot be added and used in one)
ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'phone_ai';

BEGIN;

CREATE TABLE IF NOT EXISTS voice_numbers (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  did                 text NOT NULL CHECK (did ~ '^[0-9]{7,15}$'),   -- digits only, e.g. 924235761234
  label               text NOT NULL DEFAULT '' CHECK (length(label) <= 80),
  enabled             boolean NOT NULL DEFAULT false,
  assistant_name      text NOT NULL DEFAULT 'Zara' CHECK (length(assistant_name) BETWEEN 1 AND 40),
  greeting            text NOT NULL DEFAULT '' CHECK (length(greeting) <= 400),
  max_call_minutes    int NOT NULL DEFAULT 8 CHECK (max_call_minutes BETWEEN 1 AND 30),
  max_minutes_per_day int NOT NULL DEFAULT 60 CHECK (max_minutes_per_day BETWEEN 0 AND 2000),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS voice_numbers_did_uniq ON voice_numbers (did);
CREATE INDEX IF NOT EXISTS voice_numbers_tenant ON voice_numbers (tenant_id);

CREATE TABLE IF NOT EXISTS calls (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  number_id      uuid REFERENCES voice_numbers(id) ON DELETE SET NULL,
  direction      text NOT NULL DEFAULT 'inbound' CHECK (direction IN ('inbound','outbound')),
  from_number    text NOT NULL DEFAULT '' CHECK (length(from_number) <= 40),
  to_number      text NOT NULL DEFAULT '' CHECK (length(to_number) <= 40),
  status         text NOT NULL DEFAULT 'in_progress'
                 CHECK (status IN ('in_progress','completed','failed','refused','no_answer')),
  started_at     timestamptz NOT NULL DEFAULT now(),
  ended_at       timestamptz,
  duration_s     int CHECK (duration_s IS NULL OR duration_s >= 0),
  transcript     jsonb NOT NULL DEFAULT '[]',     -- [{who: caller|agent, text}]
  caller_name    text NOT NULL DEFAULT '' CHECK (length(caller_name) <= 120),
  outcome        text NOT NULL DEFAULT 'unknown'
                 CHECK (outcome IN ('interested','booked','ordered','callback','not_interested','wrong_number','question_answered','unknown')),
  summary        text NOT NULL DEFAULT '' CHECK (length(summary) <= 2000),
  follow_up      text NOT NULL DEFAULT '' CHECK (length(follow_up) <= 300),
  lead_id        uuid REFERENCES leads(id) ON DELETE SET NULL,
  reviewed       boolean NOT NULL DEFAULT false,
  model          text,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calls_tenant_started ON calls (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS calls_number_day ON calls (number_id, started_at);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['voice_numbers','calls'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

COMMIT;
