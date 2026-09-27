-- 27_social_posts.sql
-- Posts Jarvis drafts for a business's Facebook page / Instagram account.
-- A draft never reaches a public page by itself: a person must press
-- Approve & post. Nothing is deleted -- a discarded draft stays on record.
BEGIN;

CREATE TABLE IF NOT EXISTS social_posts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platforms     text[] NOT NULL,
  caption       text NOT NULL,
  image_url     text,
  status        text NOT NULL DEFAULT 'draft',
  results       jsonb NOT NULL DEFAULT '{}'::jsonb,
  source        text NOT NULL DEFAULT 'jarvis',
  created_by    text,
  approved_by   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  published_at  timestamptz,
  CONSTRAINT social_posts_status_ck CHECK (status IN
    ('draft','publishing','published','partial','failed','discarded')),
  CONSTRAINT social_posts_caption_len_ck CHECK (length(caption) BETWEEN 1 AND 2200),
  CONSTRAINT social_posts_image_https_ck CHECK (image_url IS NULL OR image_url LIKE 'https://%'),
  CONSTRAINT social_posts_platforms_ck CHECK (
    cardinality(platforms) >= 1 AND platforms <@ ARRAY['facebook','instagram']::text[])
);

CREATE INDEX IF NOT EXISTS social_posts_tenant_idx ON social_posts (tenant_id, created_at DESC);

ALTER TABLE social_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_posts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON social_posts;
CREATE POLICY tenant_isolation ON social_posts
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

GRANT SELECT, INSERT, UPDATE ON social_posts TO techdeva_app;

COMMIT;
