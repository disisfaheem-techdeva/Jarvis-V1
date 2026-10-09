-- 42_techdeva_work.sql
-- TechDeva's own work, run from the founder console:
--   td_catalog          what TechDeva sells (plans and extras) with prices
--   td_client_services  which client has which service, at what price, since when
--   td_tasks            delivery work per client, assigned to TechDeva's team
--
-- Every row belongs to TechDeva's OWN business on the platform (the "home"
-- tenant, chosen once in the founder console and kept in platform_settings
-- 'home_tenant_id'). So the usual tenant fence applies: TechDeva's team, who
-- log in to that business with their PINs, see this work; no client ever
-- does; the founder (is_admin) sees and edits all of it. Client names are
-- copied onto the rows because TechDeva's team cannot read other tenants.
-- Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS td_catalog (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  category     text NOT NULL DEFAULT 'service',
  price        numeric CHECK (price IS NULL OR price >= 0),
  billing      text NOT NULL DEFAULT 'monthly' CHECK (billing IN ('monthly','one_time')),
  description  text NOT NULL DEFAULT '',
  plan_key     text,
  active       boolean NOT NULL DEFAULT true,
  sort_order   int NOT NULL DEFAULT 100,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS td_client_services (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  client_tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  client_name      text NOT NULL DEFAULT '',
  catalog_id       uuid NOT NULL REFERENCES td_catalog(id),
  service_name     text NOT NULL DEFAULT '',
  price            numeric CHECK (price IS NULL OR price >= 0),
  billing          text NOT NULL DEFAULT 'monthly' CHECK (billing IN ('monthly','one_time')),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','ended')),
  started_on       date NOT NULL DEFAULT current_date,
  ended_on         date,
  notes            text NOT NULL DEFAULT '',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS td_client_services_client_idx ON td_client_services (client_tenant_id, status);

CREATE TABLE IF NOT EXISTS td_tasks (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  client_tenant_id  uuid REFERENCES tenants(id) ON DELETE CASCADE,
  client_name       text NOT NULL DEFAULT '',
  client_service_id uuid REFERENCES td_client_services(id) ON DELETE SET NULL,
  title             text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  notes             text NOT NULL DEFAULT '',
  assignee_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
  assignee_name     text NOT NULL DEFAULT '',
  due_on            date,
  status            text NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done')),
  created_by        text NOT NULL DEFAULT '',
  done_at           timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS td_tasks_status_idx ON td_tasks (tenant_id, status, due_on);

DO $rls$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['td_catalog','td_client_services','td_tasks'] LOOP
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
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname IN ('td_catalog','td_client_services','td_tasks')
             AND NOT relforcerowsecurity) THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: TechDeva work tables tenant isolated';
END
$verify$;

COMMIT;
