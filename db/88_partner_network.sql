-- 88_partner_network.sql
-- The white-label partner network on top of db/87 (Faheem, 3 Nov 2026).
--
-- MONEY MODEL: revenue share, not wholesale. TechDeva collects every payment
-- from a partner's client; each confirmed payment is split by the partner's
-- terms (standard 55% partner / 45% TechDeva, negotiable per partner, never
-- hard-coded). db/87's partner_agreements (wholesale per plan) stays, unused
-- by the engine, so nothing that read it breaks.
--
--   partner_revenue_terms    the split, a floor TechDeva never goes below per
--                            payment, and who carries the gateway fee. A new
--                            deal is a new row; history stays readable.
--   partner_branding         name, colours, logo, support identity, website copy
--   partner_domains          a partner's own domain and whether it is proven theirs
--   partner_provisioning_*   the automatic set-up after approval, step by step,
--                            with attempts and the last error of each step
--   partner_signup_links     branded self-registration links for their clients
--   partner_price_books      what a partner charges, on top of a TechDeva plan
--                            (the plan = limits, TechDeva's; the price = theirs)
--   partner_coupons          partner discounts
--   partner_client_subscriptions  which price a client is on, and its state
--   partner_ledger           one row per money event, with the split frozen at
--                            the time it happened. Refunds and chargebacks are
--                            negative rows, never edits.
--   partner_payouts          what TechDeva paid (or failed to pay) a partner
--
-- WHO MAY WRITE WHAT
-- A partner may write only its own configuration: branding, price books,
-- coupons and signup links (RLS: partner_id = current_partner()).
-- Everything that is money or ownership -- terms, domains, provisioning,
-- subscriptions, ledger, payouts -- a partner may READ and only TechDeva
-- (is_admin(), i.e. a validated server path) may write. A partner therefore
-- cannot mark its own domain verified, change its own split, or write itself
-- a ledger row, whatever bug sits above the database.
--
-- Additive: no existing row is changed or deleted. The one constraint that is
-- replaced (partners.status) is widened; every existing value stays valid.
BEGIN;

-- ------------------------------------------- since when a business is a partner's
-- Only payments made AFTER a business joined a partner are split with it. A
-- business the founder moves to an agency later must not hand that agency a
-- share of what was paid before.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS partner_assigned_at timestamptz;
UPDATE tenants SET partner_assigned_at = now() WHERE partner_id IS NOT NULL AND partner_assigned_at IS NULL;

-- ------------------------------------------------------- partners: lifecycle
CREATE SEQUENCE IF NOT EXISTS partner_code_seq START 1001;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS code text;
UPDATE partners SET code = 'TDP-' || nextval('partner_code_seq') WHERE code IS NULL;
ALTER TABLE partners ALTER COLUMN code SET DEFAULT ('TDP-' || nextval('partner_code_seq'));
ALTER TABLE partners ALTER COLUMN code SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS partners_code_key ON partners (code);

ALTER TABLE partners ADD COLUMN IF NOT EXISTS website        text NOT NULL DEFAULT '';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS applied_via    text NOT NULL DEFAULT 'invited'
  CHECK (applied_via IN ('invited','applied'));
ALTER TABLE partners ADD COLUMN IF NOT EXISTS approved_at    timestamptz;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS decided_reason text NOT NULL DEFAULT '';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS terminated_at  timestamptz;

ALTER TABLE partners DROP CONSTRAINT IF EXISTS partners_status_check;
ALTER TABLE partners DROP CONSTRAINT IF EXISTS partners_status_ck;
ALTER TABLE partners ADD CONSTRAINT partners_status_ck
  CHECK (status IN ('pending','active','suspended','rejected','terminated'));

-- --------------------------------------------------------- the revenue split
CREATE TABLE IF NOT EXISTS partner_revenue_terms (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id          uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  partner_share_pct   numeric NOT NULL DEFAULT 55
                      CHECK (partner_share_pct >= 0 AND partner_share_pct <= 100),
  min_techdeva_amount numeric CHECK (min_techdeva_amount IS NULL OR min_techdeva_amount >= 0),
  floor_currency      text CHECK (floor_currency IS NULL OR floor_currency ~ '^[A-Z]{3}$'),
  fee_bearer          text NOT NULL DEFAULT 'shared'
                      CHECK (fee_bearer IN ('shared','partner','techdeva')),
  effective_from      date NOT NULL DEFAULT current_date,
  notes               text NOT NULL DEFAULT '',
  created_by          text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT partner_terms_floor_has_currency
    CHECK (min_techdeva_amount IS NULL OR floor_currency IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_revenue_terms_day
  ON partner_revenue_terms (partner_id, effective_from);

-- -------------------------------------------------------------- the brand
CREATE TABLE IF NOT EXISTS partner_branding (
  partner_id       uuid PRIMARY KEY REFERENCES partners(id) ON DELETE CASCADE,
  brand_name       text NOT NULL CHECK (length(btrim(brand_name)) BETWEEN 2 AND 80),
  headline         text NOT NULL DEFAULT '',
  tagline          text NOT NULL DEFAULT '',
  about            text NOT NULL DEFAULT '',
  primary_color    text CHECK (primary_color IS NULL OR primary_color ~ '^#[0-9a-fA-F]{6}$'),
  accent_color     text CHECK (accent_color  IS NULL OR accent_color  ~ '^#[0-9a-fA-F]{6}$'),
  logo_mime        text CHECK (logo_mime IS NULL OR logo_mime IN
                               ('image/png','image/jpeg','image/webp','image/svg+xml')),
  logo_bytes       bytea CHECK (logo_bytes IS NULL OR octet_length(logo_bytes) <= 1048576),
  support_email    text NOT NULL DEFAULT '',
  support_phone    text NOT NULL DEFAULT '',
  support_whatsapp text NOT NULL DEFAULT '',
  website_published boolean NOT NULL DEFAULT false,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT partner_branding_logo_pair CHECK ((logo_mime IS NULL) = (logo_bytes IS NULL))
);

CREATE TABLE IF NOT EXISTS partner_domains (
  partner_id  uuid PRIMARY KEY REFERENCES partners(id) ON DELETE CASCADE,
  domain      text NOT NULL CHECK (domain ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$'),
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','failed')),
  token       text NOT NULL,
  checked_at  timestamptz,
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_domains_domain_key ON partner_domains (domain);

-- -------------------------------------------- automatic set-up after approval
CREATE TABLE IF NOT EXISTS partner_provisioning_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id  uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','running','done','failed')),
  attempts    int NOT NULL DEFAULT 0,
  last_error  text,
  next_try_at timestamptz NOT NULL DEFAULT now(),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
-- one unfinished run per partner: approving twice cannot start two set-ups
CREATE UNIQUE INDEX IF NOT EXISTS partner_provisioning_one_open
  ON partner_provisioning_runs (partner_id) WHERE status <> 'done';

CREATE TABLE IF NOT EXISTS partner_provisioning_steps (
  run_id      uuid NOT NULL REFERENCES partner_provisioning_runs(id) ON DELETE CASCADE,
  partner_id  uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  step        text NOT NULL,
  position    int NOT NULL,
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
  attempts    int NOT NULL DEFAULT 0,
  last_error  text,
  detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
  done_at     timestamptz,
  PRIMARY KEY (run_id, step)
);

-- --------------------------------------- what the partner charges its clients
CREATE TABLE IF NOT EXISTS partner_price_books (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id       uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  plan_key         text NOT NULL REFERENCES plans(key),
  name             text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 60),
  price            numeric NOT NULL CHECK (price >= 0),
  currency         text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
  billing_interval text NOT NULL DEFAULT 'month' CHECK (billing_interval IN ('month','year')),
  trial_days       int NOT NULL DEFAULT 0 CHECK (trial_days BETWEEN 0 AND 90),
  active           boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_price_books_name
  ON partner_price_books (partner_id, lower(btrim(name)));

CREATE TABLE IF NOT EXISTS partner_coupons (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id  uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  code        text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{2,30}$'),
  percent_off numeric CHECK (percent_off IS NULL OR (percent_off > 0 AND percent_off <= 100)),
  amount_off  numeric CHECK (amount_off IS NULL OR amount_off > 0),
  max_uses    int CHECK (max_uses IS NULL OR max_uses > 0),
  uses        int NOT NULL DEFAULT 0 CHECK (uses >= 0),
  expires_at  timestamptz,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT partner_coupons_one_kind CHECK ((percent_off IS NULL) <> (amount_off IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_coupons_code ON partner_coupons (partner_id, code);

CREATE TABLE IF NOT EXISTS partner_signup_links (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id    uuid NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  token         text NOT NULL CHECK (token ~ '^[A-Za-z0-9_-]{16,64}$'),
  label         text NOT NULL DEFAULT '',
  price_book_id uuid REFERENCES partner_price_books(id) ON DELETE SET NULL,
  active        boolean NOT NULL DEFAULT true,
  uses          int NOT NULL DEFAULT 0,
  max_uses      int CHECK (max_uses IS NULL OR max_uses > 0),
  expires_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_signup_links_token ON partner_signup_links (token);

-- ---------------------------------------------------- a client's subscription
CREATE TABLE IF NOT EXISTS partner_client_subscriptions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id         uuid NOT NULL REFERENCES partners(id),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  price_book_id      uuid REFERENCES partner_price_books(id) ON DELETE SET NULL,
  plan_key           text REFERENCES plans(key),
  list_price         numeric NOT NULL CHECK (list_price >= 0),
  discount           numeric NOT NULL DEFAULT 0 CHECK (discount >= 0),
  price              numeric NOT NULL CHECK (price >= 0),
  currency           text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  billing_interval   text NOT NULL DEFAULT 'month' CHECK (billing_interval IN ('month','year')),
  coupon_id          uuid REFERENCES partner_coupons(id) ON DELETE SET NULL,
  status             text NOT NULL DEFAULT 'trial'
                     CHECK (status IN ('trial','active','past_due','cancelled')),
  trial_ends_at      timestamptz,
  current_period_end timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT partner_subs_price_adds_up CHECK (price = greatest(list_price - discount, 0))
);
CREATE UNIQUE INDEX IF NOT EXISTS partner_client_subscriptions_tenant
  ON partner_client_subscriptions (tenant_id);

-- --------------------------------------------------------------- the money
CREATE TABLE IF NOT EXISTS partner_payouts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id     uuid NOT NULL REFERENCES partners(id),
  amount         numeric NOT NULL CHECK (amount > 0),
  currency       text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  status         text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','paid','failed','cancelled')),
  method         text NOT NULL DEFAULT 'bank',
  reference      text NOT NULL DEFAULT '',
  failure_reason text,
  created_by     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  paid_at        timestamptz,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS partner_ledger (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id         uuid NOT NULL REFERENCES partners(id),
  tenant_id          uuid REFERENCES tenants(id) ON DELETE SET NULL,
  kind               text NOT NULL CHECK (kind IN ('payment','refund','chargeback','adjustment')),
  source_id          uuid,                      -- client_payments.id for kind = payment
  related_ledger_id  uuid REFERENCES partner_ledger(id),
  gross              numeric NOT NULL,          -- negative for refunds and chargebacks
  gateway_fee        numeric NOT NULL DEFAULT 0 CHECK (gateway_fee >= 0),
  net                numeric NOT NULL,
  partner_share_pct  numeric NOT NULL,
  partner_amount     numeric NOT NULL,
  techdeva_amount    numeric NOT NULL,
  currency           text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  terms_id           uuid REFERENCES partner_revenue_terms(id),
  floor_applied      boolean NOT NULL DEFAULT false,
  needs_review       boolean NOT NULL DEFAULT false,
  review_reason      text,
  payout_id          uuid REFERENCES partner_payouts(id),
  occurred_on        date NOT NULL DEFAULT current_date,
  note               text NOT NULL DEFAULT '',
  created_by         text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT partner_ledger_split_adds_up
    CHECK (round(partner_amount + techdeva_amount, 2) = round(net, 2)),
  -- the fee always costs money: +100 paid with a 3 fee nets 97; a -100
  -- chargeback with a 15 fee costs 115, so its net is -115
  CONSTRAINT partner_ledger_net_is_gross_less_fee
    CHECK (round(net, 2) = round(gross - gateway_fee, 2))
);
-- a confirmed payment is split exactly once, however often the sync runs
CREATE UNIQUE INDEX IF NOT EXISTS partner_ledger_one_per_payment
  ON partner_ledger (source_id) WHERE kind = 'payment';
CREATE INDEX IF NOT EXISTS partner_ledger_partner_idx ON partner_ledger (partner_id, occurred_on DESC);
CREATE INDEX IF NOT EXISTS partner_ledger_unpaid_idx ON partner_ledger (partner_id) WHERE payout_id IS NULL;

-- ----------------------------------------------------------------------- RLS
DO $rls$
DECLARE t text;
BEGIN
  -- partner reads its own rows; only TechDeva writes (money and ownership)
  FOREACH t IN ARRAY ARRAY['partner_revenue_terms','partner_domains','partner_provisioning_runs',
                           'partner_provisioning_steps','partner_client_subscriptions',
                           'partner_ledger','partner_payouts'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS partner_reads_own ON %I', t);
    EXECUTE format($p$CREATE POLICY partner_reads_own ON %I
      USING (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()))
      WITH CHECK (is_admin())$p$, t);
  END LOOP;

  -- partner reads AND writes its own configuration
  FOREACH t IN ARRAY ARRAY['partner_branding','partner_price_books','partner_coupons',
                           'partner_signup_links'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS partner_owns_config ON %I', t);
    EXECUTE format($p$CREATE POLICY partner_owns_config ON %I
      USING (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()))
      WITH CHECK (is_admin() OR (current_partner() IS NOT NULL AND partner_id = current_partner()))$p$, t);
  END LOOP;
END
$rls$;

-- a business owner may see its own subscription row (what it pays), nothing else
DROP POLICY IF EXISTS client_reads_own_subscription ON partner_client_subscriptions;
CREATE POLICY client_reads_own_subscription ON partner_client_subscriptions FOR SELECT
  USING (current_tenant() IS NOT NULL AND tenant_id = current_tenant());

GRANT SELECT, INSERT, UPDATE ON partner_revenue_terms, partner_domains, partner_provisioning_runs,
      partner_provisioning_steps, partner_client_subscriptions, partner_ledger, partner_payouts,
      partner_branding, partner_price_books, partner_coupons, partner_signup_links TO techdeva_app;
GRANT DELETE ON partner_price_books, partner_coupons, partner_signup_links, partner_domains TO techdeva_app;
GRANT USAGE, SELECT ON SEQUENCE partner_code_seq TO techdeva_app;

-- ------------------------------------------------------------------- verify
DO $verify$
DECLARE t text; n int;
BEGIN
  FOREACH t IN ARRAY ARRAY['partner_revenue_terms','partner_branding','partner_domains',
                           'partner_provisioning_runs','partner_provisioning_steps',
                           'partner_price_books','partner_coupons','partner_signup_links',
                           'partner_client_subscriptions','partner_ledger','partner_payouts'] LOOP
    IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = t) THEN
      RAISE EXCEPTION 'verify failed: row level security is not forced on %', t;
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'tenants' AND policyname = 'tenant_self') THEN
    RAISE EXCEPTION 'verify failed: tenant_self on tenants is gone';
  END IF;
  SELECT count(*) INTO n FROM tenants WHERE partner_id IS NOT NULL;
  IF n <> (SELECT count(*) FROM tenants t JOIN partners p ON p.id = t.partner_id) THEN
    RAISE EXCEPTION 'verify failed: a business points at a partner that does not exist';
  END IF;
  IF EXISTS (SELECT 1 FROM tenants WHERE partner_id IS NOT NULL AND partner_assigned_at IS NULL) THEN
    RAISE EXCEPTION 'verify failed: a partner business has no partner_assigned_at';
  END IF;
  IF EXISTS (SELECT 1 FROM partners WHERE code IS NULL) THEN
    RAISE EXCEPTION 'verify failed: a partner has no partner code';
  END IF;
  RAISE NOTICE 'verify ok: partner network ready (terms, branding, domains, provisioning, pricing, ledger, payouts); no existing row changed';
END
$verify$;

COMMIT;
