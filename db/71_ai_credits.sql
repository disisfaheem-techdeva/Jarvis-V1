-- 71_ai_credits.sql
-- AI credits (Faheem, 23 Oct 2026): every AI call is counted per business and
-- per agent, against a monthly allowance on the business's plan.
--   plans.ai_credits            monthly AI credits (NULL = no limit)
--   plans.max_concurrent_tasks  agent tasks a business may run at once (stored now,
--                               enforced by the worker in a later change)
--   plans.published             false = not shown to clients yet (Lite and Custom
--                               wait until the prices are checked against real costs)
--   client_accounts.ai_credits_override  a business's own allowance (Custom deals)
--   ai_usage                    one row per AI call: who, which agent, which feature,
--                               model, time taken, success. Never the text itself.
-- Prices are NOT changed here: they stay as set in 46_plan_prices_2026_10.sql and
-- are edited in the founder console. Additive otherwise: no row is deleted.
BEGIN;

ALTER TABLE plans ADD COLUMN IF NOT EXISTS ai_credits int CHECK (ai_credits IS NULL OR ai_credits >= 0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_concurrent_tasks int NOT NULL DEFAULT 2 CHECK (max_concurrent_tasks BETWEEN 1 AND 50);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS published boolean NOT NULL DEFAULT true;

-- allowances from Faheem's proposal; Growth (not in the proposal) sits between Starter and Pro
UPDATE plans SET ai_credits = 2000, max_concurrent_tasks = 2, updated_at = now() WHERE key = 'starter' AND ai_credits IS NULL;
UPDATE plans SET ai_credits = 4000, max_concurrent_tasks = 3, updated_at = now() WHERE key = 'growth'  AND ai_credits IS NULL;
UPDATE plans SET ai_credits = 8000, max_concurrent_tasks = 5, updated_at = now() WHERE key = 'pro'     AND ai_credits IS NULL;
INSERT INTO plans (key, name, price_pkr, max_numbers, max_conversations, sort, ai_credits, max_concurrent_tasks, published) VALUES
  ('lite',   'Lite',   4999,  1, 100,   0,  500, 1,  false),
  ('custom', 'Custom', 29999, 20, 10000, 9, NULL, 10, false)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE client_accounts ADD COLUMN IF NOT EXISTS ai_credits_override int
  CHECK (ai_credits_override IS NULL OR ai_credits_override >= 0);

CREATE TABLE IF NOT EXISTS ai_usage (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid REFERENCES tenants(id) ON DELETE CASCADE,   -- NULL: platform work, no business
  at          timestamptz NOT NULL DEFAULT now(),
  agent       text NOT NULL CHECK (length(agent) BETWEEN 1 AND 40),
  feature     text NOT NULL CHECK (length(feature) BETWEEN 1 AND 60),
  kind        text NOT NULL CHECK (kind IN ('text','photo_read','voice','picture','video')),
  credits     int  NOT NULL DEFAULT 0 CHECK (credits >= 0),
  ok          boolean NOT NULL,
  provider    text,
  model       text,
  ms          int,
  chars_in    int,
  chars_out   int,
  error       text CHECK (error IS NULL OR length(error) <= 300)
);
CREATE INDEX IF NOT EXISTS ai_usage_tenant_at ON ai_usage (tenant_id, at DESC);
CREATE INDEX IF NOT EXISTS ai_usage_at ON ai_usage (at DESC);

ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ai_usage;
CREATE POLICY tenant_isolation ON ai_usage USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT ON ai_usage TO techdeva_app;
GRANT USAGE ON SEQUENCE ai_usage_id_seq TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ai_usage') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on ai_usage';
  END IF;
  IF (SELECT count(*) FROM plans WHERE key IN ('lite','custom') AND NOT published) <> 2 THEN
    RAISE EXCEPTION 'verify failed: Lite and Custom are not there, hidden';
  END IF;
  RAISE NOTICE 'verify ok: AI credits ready; Lite and Custom hidden until published';
END
$verify$;

COMMIT;
