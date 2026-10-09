-- 30_social_media.sql
-- Pictures a business attaches to a social post in Jarvis.
--
-- Facebook and Instagram publish a picture by FETCHING it from a public https
-- address, so an uploaded picture must be reachable without a login. Each row
-- gets a long random token; the public address is /media/<token>.jpg. Nothing
-- lists these rows publicly, and a token cannot be guessed.
--
-- JPEG only: Instagram's publishing API accepts JPEG and nothing else, and
-- refusing SVG keeps script-carrying files off the API's own domain.
-- Bytes live in Postgres like the logo (brand_assets): one backup covers them.

BEGIN;

CREATE TABLE IF NOT EXISTS social_media (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  token      text NOT NULL UNIQUE CHECK (token ~ '^[0-9a-f]{40}$'),
  mime       text NOT NULL CHECK (mime = 'image/jpeg'),
  bytes      bytea NOT NULL,
  byte_size  int NOT NULL CHECK (byte_size > 0 AND byte_size <= 4194304),
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS social_media_tenant_idx ON social_media (tenant_id, created_at DESC);

ALTER TABLE social_media ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_media FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON social_media;
CREATE POLICY tenant_isolation ON social_media
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, DELETE ON social_media TO techdeva_app;

DO $verify$
DECLARE forced bool;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'social_media';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  RAISE NOTICE 'verify ok: social_media ready, tenant isolated';
END
$verify$;

COMMIT;
