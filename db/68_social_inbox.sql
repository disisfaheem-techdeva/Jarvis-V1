-- 68_social_inbox.sql
-- Social agent: Facebook Messenger, Instagram DMs and comments, answered by the
-- business's AI with the same facts its WhatsApp agent uses, captured as Leads,
-- and a serious client flagged to the owner (email + dashboard).
--
-- Meta sends every DM and comment to one webhook (/meta/webhook). meta_pages
-- says which business a Page / Instagram account belongs to. The owner can take
-- over any conversation (ai_paused); the AI then stays quiet in it.
-- Additive only. Forced RLS on tenant tables; meta_pages is admin-only.

-- leads that came from Facebook or Instagram (outside the transaction: enum
-- values cannot be added and used in one)
ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'facebook';
ALTER TYPE lead_source ADD VALUE IF NOT EXISTS 'instagram';

BEGIN;

-- which business a Facebook Page (and its Instagram account) belongs to
CREATE TABLE IF NOT EXISTS meta_pages (
  page_id     text PRIMARY KEY CHECK (page_id ~ '^[0-9]{3,30}$'),
  ig_id       text CHECK (ig_id IS NULL OR ig_id ~ '^[0-9]{3,30}$'),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  page_name   text NOT NULL DEFAULT '',
  subscribed  boolean NOT NULL DEFAULT false,      -- Meta sends this Page's messages/comments to us
  last_error  text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS meta_pages_ig ON meta_pages (ig_id) WHERE ig_id IS NOT NULL;
ALTER TABLE meta_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE meta_pages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON meta_pages;
DROP POLICY IF EXISTS tenant_read ON meta_pages;
CREATE POLICY admin_only ON meta_pages USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY tenant_read ON meta_pages FOR SELECT USING (tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON meta_pages TO techdeva_app;

-- per business: OFF until the owner (or TechDeva) switches it on. The AI answers
-- a business's customers only once someone has chosen that for that business.
CREATE TABLE IF NOT EXISTS social_agent_settings (
  tenant_id        uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  enabled          boolean NOT NULL DEFAULT false,
  reply_comments   boolean NOT NULL DEFAULT true,
  alert_email      text CHECK (alert_email IS NULL OR length(alert_email) <= 160),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

-- one conversation: a person's DMs, or one person's comments under one post
CREATE TABLE IF NOT EXISTS social_threads (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platform          text NOT NULL CHECK (platform IN ('facebook','instagram')),
  kind              text NOT NULL CHECK (kind IN ('dm','comment')),
  page_id           text NOT NULL,
  person_id         text NOT NULL,                  -- Messenger PSID / Instagram IGSID / commenter id
  person_name       text NOT NULL DEFAULT '',
  post_id           text NOT NULL DEFAULT '',       -- comments: the post or media they commented on
  ai_paused         boolean NOT NULL DEFAULT false, -- the owner took over
  intent            text,
  score             int CHECK (score IS NULL OR score BETWEEN 0 AND 100),
  hot               boolean NOT NULL DEFAULT false, -- a serious client
  hot_alerted_at    timestamptz,
  needs_owner       text,                           -- why the AI left it to the owner
  lead_id           uuid REFERENCES leads(id) ON DELETE SET NULL,
  last_in_at        timestamptz,
  last_message_at   timestamptz NOT NULL DEFAULT now(),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, platform, kind, person_id, post_id)
);
CREATE INDEX IF NOT EXISTS social_threads_recent ON social_threads (tenant_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS social_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  thread_id     uuid NOT NULL REFERENCES social_threads(id) ON DELETE CASCADE,
  author        text NOT NULL CHECK (author IN ('customer','ai','owner')),
  visibility    text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public')),
  text          text NOT NULL CHECK (length(text) <= 4000),
  external_id   text,                               -- Meta's message / comment id
  status        text NOT NULL DEFAULT 'received'
                CHECK (status IN ('received','sent','failed','held','skipped')),
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS social_messages_ext ON social_messages (tenant_id, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS social_messages_thread ON social_messages (thread_id, created_at);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['social_agent_settings','social_threads','social_messages'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

COMMIT;
