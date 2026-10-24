-- 75_jarvis_conversations.sql
-- Jarvis remembers conversations (Faheem, 24 Oct 2026). Until now the chat lived
-- only in the open page: a refresh lost it, and the "recent conversation" the
-- model saw was whatever the browser sent. Now every message is stored, and the
-- history Jarvis reads comes from here.
--   jarvis_conversations  one per person per thread; belongs to a business AND a user
--   jarvis_messages       what the person said and what Jarvis answered, in order
-- Row-level security keeps each business's rows to itself; the API also checks
-- the user, so a team member never sees the owner's (or anyone else's) chats.
-- Additive: no existing table is changed.
BEGIN;

CREATE TABLE IF NOT EXISTS jarvis_conversations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       text NOT NULL DEFAULT '' CHECK (length(title) <= 120),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jarvis_conversations_user ON jarvis_conversations (tenant_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jarvis_messages (
  id              bigserial PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES jarvis_conversations(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user','jarvis')),
  text            text NOT NULL CHECK (length(text) <= 4000),
  kind            text,                                  -- question, action, post, confirm, error ...
  meta            jsonb NOT NULL DEFAULT '{}'::jsonb,    -- e.g. the post's real publishing status
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jarvis_messages_conv ON jarvis_messages (conversation_id, id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['jarvis_conversations','jarvis_messages'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;
GRANT USAGE ON SEQUENCE jarvis_messages_id_seq TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'jarvis_messages')
     OR NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'jarvis_conversations') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on the Jarvis tables';
  END IF;
  RAISE NOTICE 'verify ok: Jarvis conversations are stored per business and per person';
END
$verify$;

COMMIT;
