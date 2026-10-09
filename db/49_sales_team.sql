-- 49_sales_team.sql
-- TechDeva's own sales team (Part 22), in TechDeva's own business (the
-- "home" tenant, like td_tasks):
--   sales_leads     businesses an operator is working on, with the next
--                   follow-up date and, once won, the client they became
--   sales_contacts  every contact an operator logs (WhatsApp, call, visit...)
--   sales_meetings  meetings booked with a lead, in person or online
--   sales_people    each operator's targets
-- Operators reach these rows through their own PIN login to TechDeva's
-- business (RLS: their tenant); the founder (is_admin) sees all. Salaries
-- stay on staff and are read only by the founder's routes. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS sales_leads (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  business_name     text NOT NULL CHECK (length(business_name) BETWEEN 1 AND 160),
  contact_name      text NOT NULL DEFAULT '',
  phone             text NOT NULL DEFAULT '',
  city              text NOT NULL DEFAULT '',
  business_type     text NOT NULL DEFAULT '',
  source            text NOT NULL DEFAULT 'other'
                    CHECK (source IN ('whatsapp','call','walk_in','referral','facebook','instagram','website','other')),
  status            text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','contacted','meeting','proposal','won','lost')),
  plan_interest     text NOT NULL DEFAULT '',
  next_followup_at  timestamptz,
  client_tenant_id  uuid REFERENCES tenants(id) ON DELETE SET NULL,
  client_name       text NOT NULL DEFAULT '',
  won_at            timestamptz,
  notes             text NOT NULL DEFAULT '',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sales_leads_owner_idx ON sales_leads (tenant_id, owner_user_id, status);
CREATE INDEX IF NOT EXISTS sales_leads_followup_idx ON sales_leads (tenant_id, next_followup_at)
  WHERE status NOT IN ('won','lost');

CREATE TABLE IF NOT EXISTS sales_contacts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id     uuid NOT NULL REFERENCES sales_leads(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  channel     text NOT NULL CHECK (channel IN ('whatsapp','call','visit','meeting','email','other')),
  outcome     text NOT NULL DEFAULT '',
  note        text NOT NULL DEFAULT '',
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sales_contacts_user_idx ON sales_contacts (tenant_id, user_id, at);
CREATE INDEX IF NOT EXISTS sales_contacts_lead_idx ON sales_contacts (lead_id, at);

CREATE TABLE IF NOT EXISTS sales_meetings (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id     uuid NOT NULL REFERENCES sales_leads(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  starts_at   timestamptz NOT NULL,
  mode        text NOT NULL DEFAULT 'in_person' CHECK (mode IN ('in_person','online')),
  place       text NOT NULL DEFAULT '',
  meet_link   text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','done','no_show','cancelled')),
  notes       text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sales_meetings_when_idx ON sales_meetings (tenant_id, starts_at);

CREATE TABLE IF NOT EXISTS sales_people (
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  contacts_per_day    int NOT NULL DEFAULT 0 CHECK (contacts_per_day BETWEEN 0 AND 1000),
  leads_per_month     int NOT NULL DEFAULT 0 CHECK (leads_per_month BETWEEN 0 AND 10000),
  meetings_per_month  int NOT NULL DEFAULT 0 CHECK (meetings_per_month BETWEEN 0 AND 10000),
  clients_per_month   int NOT NULL DEFAULT 0 CHECK (clients_per_month BETWEEN 0 AND 10000),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id)
);

DO $rls$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sales_leads','sales_contacts','sales_meetings','sales_people'] LOOP
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
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname IN ('sales_leads','sales_contacts','sales_meetings','sales_people')
             AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: sales team tables tenant isolated';
END
$verify$;

COMMIT;
