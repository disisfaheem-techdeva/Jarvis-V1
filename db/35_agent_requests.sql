-- 35_agent_requests.sql
-- A business asks for its own WhatsApp agent from the dashboard.
-- TechDeva approves (each agent is two containers on the live server, so a
-- stranger's trial signup must not be able to start them on its own), then
-- ops/agent-template/provision-queue.sh on the server builds it with
-- new-agent.sh and reports back. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS agent_requests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_phone   text NOT NULL CHECK (owner_phone ~ '^[0-9]{10,15}$'),
  home_name     text NOT NULL,
  business_name text NOT NULL,
  assistant     text NOT NULL DEFAULT 'Zara',
  status        text NOT NULL DEFAULT 'requested'
                CHECK (status IN ('requested','approved','building','ready','failed','rejected')),
  agent_key     text CHECK (agent_key IS NULL OR agent_key ~ '^[a-z][a-z0-9-]{1,30}$'),
  error         text,
  requested_by  text,
  decided_by    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_requests_status_idx ON agent_requests (status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS agent_requests_one_open
  ON agent_requests (tenant_id) WHERE status IN ('requested','approved','building');

ALTER TABLE agent_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_requests;
CREATE POLICY tenant_isolation ON agent_requests
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE ON agent_requests TO techdeva_app;

DO $verify$
BEGIN
  IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = 'agent_requests') THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced';
  END IF;
  RAISE NOTICE 'verify ok: agent_requests tenant isolated';
END
$verify$;

COMMIT;
