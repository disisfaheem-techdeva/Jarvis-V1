-- 56_session_revocation.sql
-- "Sign out everywhere": a login made before this moment is no longer
-- accepted for this person. Set when they press it, and when they change
-- their password. NULL = nothing revoked. Additive only.
BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_valid_after timestamptz;
COMMIT;
