-- 32_password_resets.sql
-- "Forgot password?" requests. When the server can send email the link goes
-- straight to the person; when it cannot, the request waits here and the
-- founder console shows it so TechDeva can send a fresh link by WhatsApp.
-- The link itself is never stored: it is signed on demand and dies the moment
-- the password changes (auth.issue_setup_token). Admin-only.
BEGIN;

CREATE TABLE IF NOT EXISTS password_reset_requests (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid,
  tenant_id   uuid REFERENCES tenants(id) ON DELETE CASCADE,
  email       text NOT NULL,
  delivered   text NOT NULL DEFAULT 'pending' CHECK (delivered IN ('email','pending','handled')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  handled_at  timestamptz
);
CREATE INDEX IF NOT EXISTS password_reset_requests_created_idx
  ON password_reset_requests (created_at DESC);

ALTER TABLE password_reset_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE password_reset_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON password_reset_requests;
CREATE POLICY admin_only ON password_reset_requests USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE ON password_reset_requests TO techdeva_app;

DO $verify$
DECLARE forced bool;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'password_reset_requests';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  RAISE NOTICE 'verify ok: password_reset_requests is admin-only';
END
$verify$;

COMMIT;
