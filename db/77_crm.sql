-- 77_crm.sql
-- CRM upgrade (Faheem, 24 Oct 2026; docs/CRM-UPGRADE.md). Additive only:
-- nothing existing is dropped, renamed or rewritten.
--   leads             + company, last_contacted_at, lost_reason, converted_customer_id,
--                       converted_at, status_changed_at (all optional)
--   crm_activities    follow-ups, calls, meetings, visits and notes a PERSON does,
--                     linked to a lead and/or a customer of the same business.
--                     (leads.followup_at stays the AI agent's own follow-up.)
--   crm_settings      per business: the inactivity reminder and lead assignment rules
-- Rollback: DROP TABLE crm_activities, crm_settings; the new lead columns can stay
-- (nothing reads them unless set) or be dropped.
BEGIN;

ALTER TABLE leads ADD COLUMN IF NOT EXISTS company text CHECK (company IS NULL OR length(company) <= 160);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_contacted_at timestamptz;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS lost_reason text CHECK (lost_reason IS NULL OR length(lost_reason) <= 300);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS converted_customer_id uuid REFERENCES customers(id) ON DELETE SET NULL;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS converted_at timestamptz;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status_changed_at timestamptz;
CREATE INDEX IF NOT EXISTS leads_tenant_created ON leads (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS leads_tenant_owner ON leads (tenant_id, assigned_operator);
CREATE INDEX IF NOT EXISTS leads_tenant_email ON leads (tenant_id, lower(email)) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_activities (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id      uuid REFERENCES leads(id) ON DELETE CASCADE,
  customer_id  uuid REFERENCES customers(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('task','call','meeting','visit','whatsapp','email','note')),
  title        text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  body         text CHECK (body IS NULL OR length(body) <= 4000),
  due_at       timestamptz,
  done_at      timestamptz,
  outcome      text CHECK (outcome IS NULL OR length(outcome) <= 500),
  priority     text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
  assigned_to  uuid REFERENCES users(id) ON DELETE SET NULL,
  created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  source       text NOT NULL DEFAULT 'person' CHECK (source IN ('person','jarvis','rule')),
  rule_key     text,                                   -- set when a CRM rule made it, so it is made once
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (lead_id IS NOT NULL OR customer_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS crm_activities_open ON crm_activities (tenant_id, due_at) WHERE done_at IS NULL;
CREATE INDEX IF NOT EXISTS crm_activities_lead ON crm_activities (lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS crm_activities_customer ON crm_activities (customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS crm_activities_assignee ON crm_activities (tenant_id, assigned_to) WHERE done_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS crm_activities_rule_once ON crm_activities (tenant_id, rule_key) WHERE rule_key IS NOT NULL;

-- A foreign key is checked past row-level security, so it alone would let an
-- activity point at another business's lead or customer. This refuses it.
CREATE OR REPLACE FUNCTION crm_activity_same_business() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
  IF NEW.lead_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM leads WHERE id = NEW.lead_id AND tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'crm activity: the lead is not in this business' USING ERRCODE = 'foreign_key_violation';
  END IF;
  IF NEW.customer_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM customers WHERE id = NEW.customer_id AND tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'crm activity: the customer is not in this business' USING ERRCODE = 'foreign_key_violation';
  END IF;
  IF NEW.assigned_to IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.assigned_to AND tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'crm activity: the person is not in this business' USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END $f$;
DROP TRIGGER IF EXISTS crm_activities_same_business ON crm_activities;
CREATE TRIGGER crm_activities_same_business BEFORE INSERT OR UPDATE ON crm_activities
  FOR EACH ROW EXECUTE FUNCTION crm_activity_same_business();

CREATE TABLE IF NOT EXISTS crm_settings (
  tenant_id        uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  inactivity_days  integer CHECK (inactivity_days IS NULL OR inactivity_days BETWEEN 1 AND 90),   -- NULL = off
  assign_mode      text NOT NULL DEFAULT 'off' CHECK (assign_mode IN ('off','round_robin')),
  assign_pool      uuid[] NOT NULL DEFAULT '{}',
  assign_cursor    integer NOT NULL DEFAULT 0,
  rules_since      timestamptz NOT NULL DEFAULT now(),     -- rules act on leads from this moment, never on old ones
  updated_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at       timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['crm_activities','crm_settings'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

DO $verify$
BEGIN
  IF (SELECT count(*) FROM pg_class WHERE relname IN ('crm_activities','crm_settings') AND relforcerowsecurity) <> 2 THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on the CRM tables';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'converted_customer_id') THEN
    RAISE EXCEPTION 'verify failed: leads.converted_customer_id is missing';
  END IF;
  RAISE NOTICE 'verify ok: CRM follow-ups and settings are stored per business; leads kept as they were';
END
$verify$;

COMMIT;
