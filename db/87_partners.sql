-- 87_partners.sql
-- White-label partners: an agency that sells TechDeva under its own name
-- (Faheem, 1 Nov 2026).
--   partners            the agency itself: name, address slug, whether it is
--                       approved, and who to contact.
--   partner_agreements  what that agency pays TechDeva for each plan. One row
--                       per (partner, plan, effective_from), so a renegotiated
--                       rate is a new row and the old one stays as history.
--                       Every partner may be on different terms, by design.
--   partner_users       who signs in to the partner dashboard. A separate
--                       identity space from `users`: a partner is not a
--                       business, and must never resolve to one.
--   tenants.partner_id  which agency brought this business. NULL = TechDeva's
--                       own direct client, which is every existing row.
--
-- WHAT A PARTNER MAY SEE
-- A partner sees its own businesses' ROWS IN `tenants` (name, plan, status) and
-- nothing inside them. No lead, conversation, message, appointment or invoice of
-- a customer is readable by the partner, because none of the tenant-scoped
-- policies are touched here: with app.role = 'partner', is_admin() is false and
-- current_tenant() is NULL, so those tables return no rows at all. Supporting a
-- customer means acting as that customer's owner through an audited path, the
-- same way the founder already does -- not a standing window into their data.
--
-- The existing `tenant_self` policy on `tenants` is NOT modified. A second,
-- SELECT-only policy is added beside it; Postgres combines permissive policies
-- with OR, so the clinic, Instant Grill and every other business keep exactly
-- the isolation they have today.
--
-- Additive: no row is changed or deleted, and no existing policy is replaced.
BEGIN;

-- ---------------------------------------------------------------- the agency
CREATE TABLE IF NOT EXISTS partners (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 120),
  slug          text NOT NULL,
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','active','suspended')),
  country       text NOT NULL DEFAULT '',
  contact_name  text NOT NULL DEFAULT '',
  contact_email text NOT NULL DEFAULT '',
  contact_phone text NOT NULL DEFAULT '',
  notes         text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  -- the slug becomes part of an address later, so it follows the same rule and
  -- the same reserved list as tenants.subdomain (db/41)
  CONSTRAINT partners_slug_ck CHECK (
    slug ~ '^[a-z][a-z0-9-]{1,30}[a-z0-9]$'
    AND slug NOT IN ('www','api','portal','crm','mail','admin','app','dashboard','login','pin',
                     'techdeva','founder','status','static','cdn','ftp','smtp','webmail','cpanel',
                     'partner','partners'))
);
CREATE UNIQUE INDEX IF NOT EXISTS partners_slug_key ON partners (slug);

-- ------------------------------------------------- what the agency pays us
CREATE TABLE IF NOT EXISTS partner_agreements (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id       uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  plan_key         text NOT NULL REFERENCES plans(key),
  wholesale_price  numeric NOT NULL CHECK (wholesale_price >= 0),
  currency         text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
  upside_share_pct numeric NOT NULL DEFAULT 0
                   CHECK (upside_share_pct >= 0 AND upside_share_pct <= 100),
  effective_from   date NOT NULL DEFAULT current_date,
  notes            text NOT NULL DEFAULT '',
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_agreements_term
  ON partner_agreements (partner_id, plan_key, effective_from);

-- --------------------------------------------- who signs in to the dashboard
CREATE TABLE IF NOT EXISTS partner_users (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id           uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  email                text NOT NULL CHECK (position('@' in email) > 1),
  name                 text NOT NULL DEFAULT '',
  password_hash        text,
  role                 text NOT NULL DEFAULT 'partner_owner'
                       CHECK (role IN ('partner_owner','partner_staff')),
  active               boolean NOT NULL DEFAULT true,
  sessions_valid_after timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_users_email_key
  ON partner_users (lower(btrim(email)));

-- ------------------------------------------- which agency brought a business
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS partner_id uuid REFERENCES partners(id);
CREATE INDEX IF NOT EXISTS tenants_partner_idx ON tenants (partner_id) WHERE partner_id IS NOT NULL;

-- ------------------------------------------------------------------ identity
-- app.partner_id is set per request beside app.tenant_id / app.role
-- (app/db.py). Unset or blank means "not a partner", never "every partner".
CREATE OR REPLACE FUNCTION current_partner() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.partner_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

-- ----------------------------------------------------------------------- RLS
ALTER TABLE partners ENABLE ROW LEVEL SECURITY;
ALTER TABLE partners FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS partner_self ON partners;
CREATE POLICY partner_self ON partners
  USING (is_admin() OR (current_partner() IS NOT NULL AND id = current_partner()))
  WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE ON partners TO techdeva_app;

ALTER TABLE partner_agreements ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_agreements FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS partner_reads_own_terms ON partner_agreements;
CREATE POLICY partner_reads_own_terms ON partner_agreements
  USING (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()))
  WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON partner_agreements TO techdeva_app;

ALTER TABLE partner_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS partner_users_own ON partner_users;
CREATE POLICY partner_users_own ON partner_users
  USING (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()))
  WITH CHECK (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()));
GRANT SELECT, INSERT, UPDATE, DELETE ON partner_users TO techdeva_app;

-- A partner may READ the tenant rows of the businesses it brought. Added
-- BESIDE tenant_self, never instead of it, and SELECT only: a partner cannot
-- create, rename or reassign a business.
DROP POLICY IF EXISTS partner_sees_own_clients ON tenants;
CREATE POLICY partner_sees_own_clients ON tenants FOR SELECT
  USING (current_partner() IS NOT NULL AND partner_id = current_partner());

-- ------------------------------------------------------------------- verify
DO $verify$
DECLARE n int;
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'partners') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on partners';
  END IF;
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'partner_agreements') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on partner_agreements';
  END IF;
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'partner_users') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on partner_users';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'tenants' AND column_name = 'partner_id') THEN
    RAISE EXCEPTION 'verify failed: tenants.partner_id missing';
  END IF;

  -- the policy that protects every existing business must still be there
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE tablename = 'tenants' AND policyname = 'tenant_self') THEN
    RAISE EXCEPTION 'verify failed: tenant_self on tenants was removed';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE tablename = 'tenants' AND policyname = 'partner_sees_own_clients') THEN
    RAISE EXCEPTION 'verify failed: partner_sees_own_clients on tenants missing';
  END IF;

  -- a partner may only read tenants, never write them
  IF EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tenants'
             AND policyname = 'partner_sees_own_clients' AND cmd <> 'SELECT') THEN
    RAISE EXCEPTION 'verify failed: partner_sees_own_clients must be SELECT only';
  END IF;

  -- no existing business was handed to anybody
  SELECT count(*) INTO n FROM tenants WHERE partner_id IS NOT NULL;
  IF n <> 0 THEN
    RAISE EXCEPTION 'verify failed: % existing business(es) already carry a partner_id', n;
  END IF;

  -- current_partner() must answer NULL when nothing is set, not blow up
  PERFORM set_config('app.partner_id', '', true);
  IF current_partner() IS NOT NULL THEN
    RAISE EXCEPTION 'verify failed: current_partner() is not NULL when unset';
  END IF;

  RAISE NOTICE 'verify ok: partners ready; every existing business stays TechDeva-direct (partner_id NULL) and tenant_self untouched';
END
$verify$;

COMMIT;
