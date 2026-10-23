-- 74_agent_settings.sql
-- Agents on and off (Faheem, 23 Oct 2026). One row per business and agent the
-- owner switched; no row = on, so every business keeps running exactly as now.
-- A switched-off agent's tasks are skipped (recorded as skipped, so a pipeline
-- carries on) and no new work is given to it. Quality checks cannot be switched
-- off (app/agent_admin.py refuses it). Additive: no row changed or deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_settings (
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent       text NOT NULL CHECK (agent ~ '^[a-z_]{2,40}$'),
  enabled     boolean NOT NULL DEFAULT true,
  updated_by  uuid,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, agent)
);

ALTER TABLE agent_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_settings;
CREATE POLICY tenant_isolation ON agent_settings USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_settings TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'agent_settings') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on agent_settings';
  END IF;
  RAISE NOTICE 'verify ok: agent switches ready; every agent stays on until an owner switches it';
END
$verify$;

COMMIT;
