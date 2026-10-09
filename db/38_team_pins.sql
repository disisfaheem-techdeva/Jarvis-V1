-- 38_team_pins.sql
-- Team members log in with a PIN the owner gives them (as in the Beauty
-- Aesthetic CRM), on the business's shared screen or on their own phone with
-- the business code. Each PIN login is a real users row of that business
-- (appointments, day closing and approvals record who did it), so everything
-- that checks users keeps working.
--   users.pin_hash      PBKDF2 hash of the PIN; NULL = no PIN login
--   users.staff_id      which staff member this login belongs to (optional)
--   tenants.login_code  short business code for PIN login on a personal phone;
--                       made when the owner first opens Team logins
--   pin_attempts        every PIN try, for the lockout and for the owner
-- Additive: nothing existing is changed.
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_hash text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_set_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS staff_id uuid REFERENCES staff(id) ON DELETE SET NULL;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS login_code text;
CREATE UNIQUE INDEX IF NOT EXISTS tenants_login_code_key ON tenants (login_code) WHERE login_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS pin_attempts (
  id         bigserial PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
  ip         text NOT NULL,
  ok         boolean NOT NULL,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pin_attempts_recent_idx ON pin_attempts (tenant_id, at DESC);

ALTER TABLE pin_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE pin_attempts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pin_attempts;
CREATE POLICY tenant_isolation ON pin_attempts
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT ON pin_attempts TO techdeva_app;
GRANT USAGE ON SEQUENCE pin_attempts_id_seq TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'pin_attempts') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on pin_attempts';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'pin_hash') THEN
    RAISE EXCEPTION 'verify failed: users.pin_hash missing';
  END IF;
  RAISE NOTICE 'verify ok: team PIN logins ready';
END
$verify$;

COMMIT;
