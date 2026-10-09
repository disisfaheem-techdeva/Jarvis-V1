-- 44_ad_autopilot.sql
-- The growth planner's hands on the ad accounts (Pro), all owner-controlled:
--   ad_pilot_settings  the owner's rules: auto-pause limit, daily budget caps
--                      the planner may launch within, website, where to advertise
--   ad_drafts          campaigns the planner wrote (text, picture, budget) and
--                      whether they were launched
--   ad_actions         every pause / resume / launch, by the owner or the planner
-- Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS ad_pilot_settings (
  tenant_id            uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  auto_pause           boolean NOT NULL DEFAULT false,
  max_cost_per_result  numeric CHECK (max_cost_per_result IS NULL OR max_cost_per_result > 0),
  min_spend            numeric NOT NULL DEFAULT 0 CHECK (min_spend >= 0),
  meta_daily_cap       numeric NOT NULL DEFAULT 0 CHECK (meta_daily_cap >= 0),
  google_daily_cap     numeric NOT NULL DEFAULT 0 CHECK (google_daily_cap >= 0),
  country              text NOT NULL DEFAULT 'PK',
  city                 text NOT NULL DEFAULT '',
  radius_km            int NOT NULL DEFAULT 15 CHECK (radius_km BETWEEN 1 AND 80),
  website_url          text NOT NULL DEFAULT '',
  last_check_at        timestamptz,
  last_auto_launch_at  timestamptz,
  updated_by           text NOT NULL DEFAULT '',
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ad_drafts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider      text NOT NULL CHECK (provider IN ('meta','google')),
  service       text NOT NULL DEFAULT '',
  copy          jsonb NOT NULL DEFAULT '{}'::jsonb,
  image_token   text,
  daily_budget  numeric NOT NULL CHECK (daily_budget > 0),
  status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','launched','paused','failed','discarded')),
  external      jsonb NOT NULL DEFAULT '{}'::jsonb,
  error         text,
  created_by    text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  launched_at   timestamptz
);
CREATE INDEX IF NOT EXISTS ad_drafts_status_idx ON ad_drafts (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS ad_actions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider       text NOT NULL CHECK (provider IN ('meta','google')),
  kind           text NOT NULL CHECK (kind IN ('pause','resume','launch')),
  campaign_id    text NOT NULL DEFAULT '',
  campaign_name  text NOT NULL DEFAULT '',
  reason         text NOT NULL DEFAULT '',
  by_who         text NOT NULL CHECK (by_who IN ('owner','planner')),
  status         text NOT NULL CHECK (status IN ('done','failed')),
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ad_actions_recent_idx ON ad_actions (tenant_id, created_at DESC);

DO $rls$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ad_pilot_settings','ad_drafts','ad_actions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) '
                   'WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END
$rls$;

DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname IN ('ad_pilot_settings','ad_drafts','ad_actions')
             AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: ad autopilot tables tenant isolated';
END
$verify$;

COMMIT;
