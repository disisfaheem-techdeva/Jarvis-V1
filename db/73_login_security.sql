-- 73_login_security.sql
-- Safer sign-in (Faheem, 23 Oct 2026).
--   login_attempts           every email + password sign-in, right or wrong, kept 30
--                            days: the lockout counts from here (it survives restarts
--                            and works across API processes) and the owner's Alerts
--                            show wrong passwords on their business's logins.
--   users.email_verified_at  when the person proved the email is theirs (the link in
--                            the email they were sent). NULL = not yet; nobody is
--                            locked out for it.
-- Only TechDeva's admin connection reads login_attempts: it holds emails and
-- addresses of people who are not signed in. Additive: no row changed or deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS login_attempts (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  email      text NOT NULL CHECK (length(email) <= 200),
  ip         text NOT NULL DEFAULT '' CHECK (length(ip) <= 64),
  ok         boolean NOT NULL,
  user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
  tenant_id  uuid REFERENCES tenants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS login_attempts_email_at ON login_attempts (lower(email), at DESC);
CREATE INDEX IF NOT EXISTS login_attempts_ip_at ON login_attempts (ip, at DESC);
CREATE INDEX IF NOT EXISTS login_attempts_tenant_at ON login_attempts (tenant_id, at DESC) WHERE tenant_id IS NOT NULL;

ALTER TABLE login_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_attempts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON login_attempts;
CREATE POLICY admin_only ON login_attempts USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, DELETE ON login_attempts TO techdeva_app;
GRANT USAGE ON SEQUENCE login_attempts_id_seq TO techdeva_app;

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'login_attempts') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on login_attempts';
  END IF;
  RAISE NOTICE 'verify ok: login attempts recorded; email verification ready (nobody locked out)';
END
$verify$;

COMMIT;
