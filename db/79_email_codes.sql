-- 79_email_codes.sql
-- Email verification by 6-digit code (Faheem, 25 Oct 2026).
--   email_codes                  one row per code sent. Only an HMAC of the code is kept
--                                (app/email_codes.py); 10 minutes, 5 wrong tries, then dead.
--                                The resend limits count rows here, so they survive restarts.
--   email_verification_backfill  who was marked verified by THIS migration, so the
--                                rollback can undo exactly that and nothing else.
-- From now on /auth/login refuses an unverified email. So that nobody who could sign in
-- yesterday is locked out today, every EXISTING user is marked verified here.
-- users.email_verified_at already exists (db/73). Admin-only: codes belong to people
-- who are not signed in yet. Additive: no row deleted.
--
-- Rollback (as postgres, -d techdeva):
--   BEGIN;
--   UPDATE users u SET email_verified_at = NULL
--     FROM email_verification_backfill b
--    WHERE u.id = b.user_id AND u.email_verified_at = b.verified_at;
--   DROP TABLE IF EXISTS email_verification_backfill;
--   DROP TABLE IF EXISTS email_codes;
--   DELETE FROM _td_migrations WHERE name = '79_email_codes.sql';   -- only where that table exists
--   COMMIT;
BEGIN;

CREATE TABLE IF NOT EXISTS email_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose     text NOT NULL DEFAULT 'verify_email' CHECK (length(purpose) <= 40),
  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  attempts    int NOT NULL DEFAULT 0,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_codes_user_purpose ON email_codes (user_id, purpose);

ALTER TABLE email_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_codes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON email_codes;
CREATE POLICY admin_only ON email_codes USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON email_codes TO techdeva_app;

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;

CREATE TABLE IF NOT EXISTS email_verification_backfill (
  user_id      uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  verified_at  timestamptz NOT NULL
);
ALTER TABLE email_verification_backfill ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_verification_backfill FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON email_verification_backfill;
CREATE POLICY admin_only ON email_verification_backfill USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT ON email_verification_backfill TO techdeva_app;

-- BACKFILL: everyone who exists now keeps signing in exactly as before.
WITH done AS (
  UPDATE users SET email_verified_at = now()
   WHERE email_verified_at IS NULL
  RETURNING id, email_verified_at
)
INSERT INTO email_verification_backfill (user_id, verified_at)
SELECT id, email_verified_at FROM done
ON CONFLICT (user_id) DO NOTHING;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'email_codes') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on email_codes';
  END IF;
  IF EXISTS (SELECT 1 FROM users WHERE email_verified_at IS NULL) THEN
    RAISE EXCEPTION 'verify failed: an existing user is still unverified and would be locked out';
  END IF;
  RAISE NOTICE 'verify ok: email codes ready; % existing user(s) marked verified',
    (SELECT count(*) FROM email_verification_backfill);
END
$verify$;

COMMIT;
