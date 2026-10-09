-- 53_post_videos.sql
-- A post can carry one video instead of pictures: a Facebook video and an
-- Instagram Reel. The video file lives on the media volume (never in the
-- database, so backups stay small); this table records whose it is, its
-- size and length. Forced RLS like every tenant table. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS social_videos (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  token       text NOT NULL UNIQUE CHECK (token ~ '^[0-9a-f]{40}$'),
  byte_size   bigint NOT NULL CHECK (byte_size > 0),
  duration    numeric NOT NULL CHECK (duration > 0),
  width       int NOT NULL DEFAULT 0,
  height      int NOT NULL DEFAULT 0,
  created_by  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS social_videos_tenant_idx ON social_videos (tenant_id, created_at DESC);

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS video_token text;

ALTER TABLE social_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_videos FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON social_videos;
CREATE POLICY tenant_isolation ON social_videos USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON social_videos TO techdeva_app;

DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'social_videos' AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: social_videos row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: social_videos tenant isolated';
END
$verify$;

COMMIT;
