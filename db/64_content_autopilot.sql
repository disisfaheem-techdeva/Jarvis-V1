-- 64_content_autopilot.sql
-- Content Autopilot: a month of Facebook/Instagram posts planned from the
-- business's own photo and video library, approved a week at a time, and the
-- results of every published post read back from Meta.
--
-- Nothing here posts by itself. A plan only writes DRAFTS (social_posts,
-- status 'draft', with a proposed time in planned_at). The owner's "Approve
-- this week" turns that week's drafts into ordinary scheduled posts, which the
-- existing scheduler publishes (social.publish_due). Additive only; forced RLS.
BEGIN;

CREATE TABLE IF NOT EXISTS content_plans (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  starts_on       date NOT NULL,
  days            int  NOT NULL CHECK (days BETWEEN 1 AND 31),
  posts_per_week  int  NOT NULL CHECK (posts_per_week BETWEEN 1 AND 21),
  goal            text NOT NULL DEFAULT '' CHECK (length(goal) <= 600),
  offers          text NOT NULL DEFAULT '' CHECK (length(offers) <= 1200),
  language        text NOT NULL DEFAULT 'english' CHECK (language IN ('english','mix','urdu')),
  status          text NOT NULL DEFAULT 'generating' CHECK (status IN ('generating','ready','failed')),
  drafted         int  NOT NULL DEFAULT 0,
  error           text,
  created_by      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS content_plans_tenant ON content_plans (tenant_id, created_at DESC);

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS plan_id uuid REFERENCES content_plans(id) ON DELETE SET NULL;
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS planned_at timestamptz;   -- proposed time of a planned draft
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS theme text;
CREATE INDEX IF NOT EXISTS social_posts_planned_idx ON social_posts (tenant_id, planned_at) WHERE planned_at IS NOT NULL;

-- the photo library: a name and tags the planner uses to pick the right photo
ALTER TABLE social_media ADD COLUMN IF NOT EXISTS label text NOT NULL DEFAULT '' CHECK (length(label) <= 120);
ALTER TABLE social_media ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}';
ALTER TABLE social_media ADD COLUMN IF NOT EXISTS in_library boolean NOT NULL DEFAULT false;
-- a video kept in the library is not cleaned up as an unused upload
ALTER TABLE social_videos ADD COLUMN IF NOT EXISTS label text NOT NULL DEFAULT '' CHECK (length(label) <= 120);
ALTER TABLE social_videos ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}';
ALTER TABLE social_videos ADD COLUMN IF NOT EXISTS in_library boolean NOT NULL DEFAULT false;

-- what each published post did, per platform (refreshed while the post is recent)
CREATE TABLE IF NOT EXISTS post_insights (
  post_id      uuid NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platform     text NOT NULL CHECK (platform IN ('facebook','instagram')),
  external_id  text NOT NULL,
  reach        int,
  likes        int NOT NULL DEFAULT 0,
  comments     int NOT NULL DEFAULT 0,
  shares       int NOT NULL DEFAULT 0,
  saves        int NOT NULL DEFAULT 0,
  views        int,
  fetched_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, platform)
);
CREATE INDEX IF NOT EXISTS post_insights_tenant ON post_insights (tenant_id, fetched_at DESC);

-- followers per day, per platform
CREATE TABLE IF NOT EXISTS account_insights (
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platform   text NOT NULL CHECK (platform IN ('facebook','instagram')),
  day        date NOT NULL,
  followers  int  NOT NULL CHECK (followers >= 0),
  PRIMARY KEY (tenant_id, platform, day)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['content_plans','post_insights','account_insights'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

COMMIT;
