-- 62: "Sign out" really ends that login on the server (audit, 5 Oct 2026)
--
-- Before: signing out only deleted the browser's cookie; a copy of the login
-- token kept working until it expired. Every login now carries an id; signing
-- out records it here until the moment it would have expired anyway, and
-- auth.verify_token refuses it. ("Sign out everywhere" stays: db/56.)
-- Platform-wide table, admin-only. Additive.
BEGIN;
CREATE TABLE IF NOT EXISTS signed_out_tokens (
  jti         text PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{16,64}$'),
  expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS signed_out_tokens_exp ON signed_out_tokens (expires_at);
ALTER TABLE signed_out_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE signed_out_tokens FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON signed_out_tokens;
CREATE POLICY admin_only ON signed_out_tokens USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, DELETE ON signed_out_tokens TO techdeva_app;
COMMIT;
