-- 97: countries (9 Nov 2026; Phase 10 of the Jarvis plan, foundation only)
--
-- One platform, many countries: a business has a country, and the country gives
-- its currency, symbol, time zone, languages, date format and the payment methods
-- people there use. The business's own workspace settings (db/66: tenant_config
-- timezone, currency, language) are filled from the country when it is chosen and
-- can still be changed one by one.
--
-- TAX IS NOT SEEDED. tax_name says what the tax is called; tax_rate stays NULL and
-- tax_validated false until each country's rules are checked against its tax
-- authority before launch there (master prompt, section 14). Nothing reads a
-- country's tax into a bill.
--
-- Additive. Rollback: ALTER TABLE tenant_config DROP COLUMN country; DROP TABLE countries;
BEGIN;

CREATE TABLE IF NOT EXISTS countries (
  code             text PRIMARY KEY CHECK (code ~ '^[A-Z]{2}$'),
  name             text NOT NULL,
  currency         text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  currency_symbol  text NOT NULL,
  timezones        text[] NOT NULL CHECK (cardinality(timezones) >= 1),
  languages        text[] NOT NULL DEFAULT '{en}',
  date_format      text NOT NULL DEFAULT 'DD/MM/YYYY',
  payment_methods  text[] NOT NULL DEFAULT '{cash,card,bank}',
  tax_name         text,
  tax_rate         numeric(5,2) CHECK (tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate < 100)),
  tax_validated    boolean NOT NULL DEFAULT false,
  active           boolean NOT NULL DEFAULT true,
  sort_order       int NOT NULL DEFAULT 100
);
ALTER TABLE countries ENABLE ROW LEVEL SECURITY;
ALTER TABLE countries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_all ON countries;
CREATE POLICY read_all ON countries FOR SELECT USING (true);
DROP POLICY IF EXISTS admin_write ON countries;
CREATE POLICY admin_write ON countries FOR ALL USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT ON countries TO techdeva_app;
GRANT INSERT, UPDATE ON countries TO techdeva_app;   -- RLS: founder only

-- the master prompt's target markets; Pakistan first
INSERT INTO countries (code, name, currency, currency_symbol, timezones, languages, date_format, payment_methods, tax_name, sort_order) VALUES
 ('PK', 'Pakistan',             'PKR', 'Rs',  '{Asia/Karachi}', '{ur,en}', 'DD/MM/YYYY', '{cash,jazzcash,easypaisa,bank,card}', 'Sales tax', 10),
 ('AE', 'United Arab Emirates', 'AED', 'AED', '{Asia/Dubai}',   '{ar,en}', 'DD/MM/YYYY', '{cash,card,bank}',                    'VAT',       20),
 ('SA', 'Saudi Arabia',         'SAR', 'SAR', '{Asia/Riyadh}',  '{ar,en}', 'DD/MM/YYYY', '{cash,card,bank}',                    'VAT',       30),
 ('QA', 'Qatar',                'QAR', 'QAR', '{Asia/Qatar}',   '{ar,en}', 'DD/MM/YYYY', '{cash,card,bank}',                    NULL,        40),
 ('OM', 'Oman',                 'OMR', 'OMR', '{Asia/Muscat}',  '{ar,en}', 'DD/MM/YYYY', '{cash,card,bank}',                    'VAT',       50),
 ('GB', 'United Kingdom',       'GBP', '£',   '{Europe/London}', '{en}',   'DD/MM/YYYY', '{card,cash,bank}',                    'VAT',       60),
 ('CA', 'Canada',               'CAD', '$',   '{America/Toronto,America/Vancouver,America/Edmonton,America/Winnipeg,America/Halifax}',
                                                                  '{en,fr}', 'YYYY-MM-DD', '{card,cash,bank}',                  'GST/HST',   70),
 ('US', 'United States',        'USD', '$',   '{America/New_York,America/Chicago,America/Denver,America/Los_Angeles}',
                                                                  '{en}',    'MM/DD/YYYY', '{card,cash,bank}',                  'Sales tax', 80),
 ('AU', 'Australia',            'AUD', '$',   '{Australia/Sydney,Australia/Melbourne,Australia/Brisbane,Australia/Perth,Australia/Adelaide}',
                                                                  '{en}',    'DD/MM/YYYY', '{card,cash,bank}',                  'GST',       90)
ON CONFLICT (code) DO NOTHING;

-- the business's country, beside its own time zone, currency and language (db/66)
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS country text REFERENCES countries(code);

-- "today" in the business's own time zone (Asia/Karachi when none is set), for
-- records dated today (udhaar ledger, purchases). Runs as the caller: RLS applies.
CREATE OR REPLACE FUNCTION td_local_today() RETURNS date LANGUAGE sql STABLE AS $$
  SELECT (now() AT TIME ZONE COALESCE(
            (SELECT timezone FROM tenant_config WHERE tenant_id = current_tenant()), 'Asia/Karachi'))::date
$$;
GRANT EXECUTE ON FUNCTION td_local_today() TO techdeva_app;

DO $verify$
BEGIN
  IF (SELECT count(*) FROM countries) < 9 THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - countries not seeded';
  END IF;
  IF EXISTS (SELECT 1 FROM countries WHERE tax_rate IS NOT NULL AND NOT tax_validated) THEN
    RAISE EXCEPTION 'MIGRATION UNSAFE - a tax rate is set without being validated';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'tenant_config' AND column_name = 'country') THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - tenant_config.country missing';
  END IF;
  RAISE NOTICE 'OK: 9 countries (no tax rates: validate per country before launch); tenant_config.country.';
END
$verify$;
COMMIT;
