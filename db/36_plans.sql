-- 36_plans.sql
-- The three plans Faheem set on 29 Sep 2026, and room for a business to have
-- more than one WhatsApp number.
--   plans           : price and limits per plan; the founder edits them in the
--                     console. Everyone may read them (they are the price list),
--                     only the founder may change them.
--   client_accounts : + plan_key (which plan a client is on; none = Starter limits)
--   whatsapp_connections : one row per NUMBER now, not per business. The old
--                     one-row-per-business rule is replaced by one row per
--                     (business, agent). Existing rows are untouched.
-- Additive: no row is changed or deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS plans (
  key               text PRIMARY KEY CHECK (key ~ '^[a-z]{2,20}$'),
  name              text NOT NULL,
  price_pkr         numeric CHECK (price_pkr IS NULL OR price_pkr >= 0),
  max_numbers       int NOT NULL CHECK (max_numbers BETWEEN 1 AND 20),
  max_conversations int NOT NULL CHECK (max_conversations > 0),
  sort              int NOT NULL DEFAULT 0,
  updated_at        timestamptz NOT NULL DEFAULT now()
);
INSERT INTO plans (key, name, price_pkr, max_numbers, max_conversations, sort) VALUES
  ('starter', 'Starter', 20000, 1,  500, 1),
  ('growth',  'Growth',  34999, 2, 1500, 2),
  ('pro',     'Pro',     NULL,  5, 4000, 3)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE plans FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_all ON plans;
CREATE POLICY read_all ON plans FOR SELECT USING (true);
DROP POLICY IF EXISTS admin_write ON plans;
CREATE POLICY admin_write ON plans FOR ALL USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE ON plans TO techdeva_app;

ALTER TABLE client_accounts ADD COLUMN IF NOT EXISTS plan_key text REFERENCES plans(key);

ALTER TABLE whatsapp_connections DROP CONSTRAINT IF EXISTS whatsapp_connections_tenant_key;
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_connections_tenant_agent
  ON whatsapp_connections (tenant_id, agent_key);

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'plans') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on plans';
  END IF;
  IF (SELECT count(*) FROM plans WHERE key IN ('starter','growth','pro')) <> 3 THEN
    RAISE EXCEPTION 'verify failed: the three plans are not there';
  END IF;
  RAISE NOTICE 'verify ok: plans ready; a business may have several WhatsApp numbers';
END
$verify$;

COMMIT;
