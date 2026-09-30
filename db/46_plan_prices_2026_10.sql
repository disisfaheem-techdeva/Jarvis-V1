-- 46_plan_prices_2026_10.sql
-- Faheem's packages (30 Sep 2026), shown on the website as value stacks:
--   Starter 19,999  1 WhatsApp number, 300 AI chats a month
--   Growth  49,999  2 numbers, 1,500 chats, + posting, ad results, Jarvis
--   Pro     64,999  5 numbers, 4,000 chats, + growth advisor & ad autopilot
-- plus a USD price for clients abroad (price_usd), editable in the founder
-- console like the PKR price. A client's own agreed fee (client_accounts.
-- monthly_fee) is NOT changed: existing clients keep what they pay.
BEGIN;

ALTER TABLE plans ADD COLUMN IF NOT EXISTS price_usd numeric CHECK (price_usd IS NULL OR price_usd >= 0);

UPDATE plans SET price_pkr = 19999, price_usd = 99,  max_numbers = 1, max_conversations = 300,  updated_at = now() WHERE key = 'starter';
UPDATE plans SET price_pkr = 49999, price_usd = 199, max_numbers = 2, max_conversations = 1500, updated_at = now() WHERE key = 'growth';
UPDATE plans SET price_pkr = 64999, price_usd = 299, max_numbers = 5, max_conversations = 4000, updated_at = now() WHERE key = 'pro';

DO $verify$
BEGIN
  IF (SELECT count(*) FROM plans WHERE key IN ('starter','growth','pro') AND price_pkr IS NOT NULL AND price_usd IS NOT NULL) <> 3 THEN
    RAISE EXCEPTION 'verify failed: plan prices not set';
  END IF;
  RAISE NOTICE 'verify ok: plan prices 19,999 / 49,999 / 64,999 (USD 99 / 199 / 299)';
END
$verify$;

COMMIT;
