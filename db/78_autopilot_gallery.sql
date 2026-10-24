-- 78_autopilot_gallery.sql
-- Jarvis Autopilot Gallery (docs/AUTOPILOT-GALLERY.md). Additive only.
--   media_assets         photos and videos a business uploads for its posts (tenant-prefixed storage keys)
--   marketing_campaigns  one drip campaign: its days/times rule, its status, who approved it
--   scheduled_posts      one row per post per platform: caption, time (UTC), status, the human approval,
--                        the platform's own post id once published
--   tenant_config.marketing  per-business settings the captions and uploads follow (limits, banned
--                        phrases, caption language); empty means the defaults written below
--   risk_policies 'publish_post'  why the policy gate does not hold this task: every post carries its
--                        own recorded human approval and the publishing agent refuses any post without one
-- Rollback: DROP TABLE scheduled_posts, marketing_campaigns, media_assets;
--           ALTER TABLE tenant_config DROP COLUMN marketing;
--           DELETE FROM risk_policies WHERE action = 'publish_post' AND tenant_id IS NULL;
BEGIN;

ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS marketing jsonb NOT NULL DEFAULT '{
  "banned_phrases": ["guaranteed results", "100% guaranteed", "100% results", "permanent results",
                     "no side effects", "risk free", "risk-free", "cure", "cures", "miracle",
                     "before and after", "before & after", "instant results", "zero pain", "painless guaranteed"],
  "caption_language": null,
  "upload": {"max_image_mb": 8, "max_video_mb": 100, "max_video_s": 90}
}'::jsonb;

CREATE TABLE IF NOT EXISTS media_assets (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  storage_key    text NOT NULL CHECK (storage_key LIKE 'tenants/%/marketing/%'),
  public_url     text NOT NULL,
  original_name  text CHECK (original_name IS NULL OR length(original_name) <= 200),
  mime_type      text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('image', 'video')),
  width          int,
  height         int,
  duration_s     numeric(8,2),
  bytes          bigint NOT NULL CHECK (bytes > 0),
  sha256         text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  warnings       jsonb NOT NULL DEFAULT '[]',
  vision_summary jsonb,
  service_tag    text,
  created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS media_assets_tenant_sha ON media_assets (tenant_id, sha256);
CREATE UNIQUE INDEX IF NOT EXISTS media_assets_key ON media_assets (storage_key);
CREATE INDEX IF NOT EXISTS media_assets_tenant_created ON media_assets (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS marketing_campaigns (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  status        text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'awaiting_approval', 'active', 'paused', 'completed', 'cancelled')),
  rule_config   jsonb NOT NULL,
  timezone      text NOT NULL DEFAULT 'Asia/Karachi',
  page_id       text,                                  -- the Facebook Page chosen when more than one is connected
  paused_reason text,                                  -- 'meta_auth' when a lost connection paused it
  captioning    text NOT NULL DEFAULT 'pending' CHECK (captioning IN ('pending', 'running', 'done', 'failed')),
  approved_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  approved_at   timestamptz,
  created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS marketing_campaigns_tenant ON marketing_campaigns (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS scheduled_posts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  campaign_id      uuid NOT NULL REFERENCES marketing_campaigns(id) ON DELETE CASCADE,
  media_asset_id   uuid NOT NULL REFERENCES media_assets(id) ON DELETE RESTRICT,
  platform         text NOT NULL CHECK (platform IN ('facebook', 'instagram', 'gbp')),
  caption          text NOT NULL DEFAULT '' CHECK (length(caption) <= 5000),
  hashtags         jsonb NOT NULL DEFAULT '[]',
  caption_source   text NOT NULL DEFAULT 'ai' CHECK (caption_source IN ('ai', 'human_edited')),
  caption_version  int NOT NULL DEFAULT 0,
  flags            jsonb NOT NULL DEFAULT '[]',       -- why a caption needs a person: price_removed, banned_phrase, ...
  flags_overridden boolean NOT NULL DEFAULT false,    -- the owner read the flags and approved anyway
  service_tag      text,
  scheduled_at     timestamptz NOT NULL,
  status           text NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft', 'approved', 'publishing', 'published', 'failed', 'cancelled')),
  attempts         int NOT NULL DEFAULT 0,
  retry_after      timestamptz,
  last_error       text,
  external_post_id text,
  published_at     timestamptz,
  approved_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  approved_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  -- an approved post always says who approved it; nothing else can make it approved
  CONSTRAINT scheduled_posts_approval_ck CHECK (
    status NOT IN ('approved', 'publishing', 'published') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS scheduled_posts_external
  ON scheduled_posts (tenant_id, platform, external_post_id) WHERE external_post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS scheduled_posts_due ON scheduled_posts (tenant_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS scheduled_posts_campaign ON scheduled_posts (campaign_id);

-- a post and its campaign and picture are always the same business's
CREATE OR REPLACE FUNCTION scheduled_posts_same_tenant() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM marketing_campaigns WHERE id = NEW.campaign_id AND tenant_id = NEW.tenant_id)
     OR NOT EXISTS (SELECT 1 FROM media_assets WHERE id = NEW.media_asset_id AND tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'scheduled post must use a campaign and a picture of the same business';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS scheduled_posts_same_tenant ON scheduled_posts;
CREATE TRIGGER scheduled_posts_same_tenant BEFORE INSERT OR UPDATE OF campaign_id, media_asset_id, tenant_id
  ON scheduled_posts FOR EACH ROW EXECUTE FUNCTION scheduled_posts_same_tenant();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['media_assets', 'marketing_campaigns', 'scheduled_posts'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

INSERT INTO risk_policies (tenant_id, action, risk, requires_approval, reason) VALUES
  (NULL, 'publish_post', 'medium', false,
   'Approved per post before it is due: scheduled_posts.approved_by is set only by a person on the approve '
   'endpoint, and the publishing agent refuses any post without it. Holding the task here would act after '
   'the post went out.')
ON CONFLICT (action) WHERE tenant_id IS NULL DO NOTHING;

DO $verify$
BEGIN
  IF (SELECT count(*) FROM pg_class WHERE relname IN ('media_assets', 'marketing_campaigns', 'scheduled_posts')
      AND relforcerowsecurity) <> 3 THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on the gallery tables';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'tenant_config' AND column_name = 'marketing') THEN
    RAISE EXCEPTION 'verify failed: tenant_config.marketing is missing';
  END IF;
  RAISE NOTICE 'verify ok: autopilot gallery tables are stored per business';
END
$verify$;

COMMIT;
