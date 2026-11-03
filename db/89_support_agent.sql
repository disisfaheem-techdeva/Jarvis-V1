-- 89_support_agent.sql
-- The support agent (app/support_agent.py): every 15 minutes it audits the
-- platform and every live business, fixes on its own only what can reach
-- nobody, and leaves everything else here for one click.
--
-- support_findings  one OPEN row per (business, key); tenant_id NULL = the
--                   platform itself. audience 'techdeva' rows are internal
--                   (a crashed task, an unreachable container): the business
--                   owner never sees them, the founder sees everything.
-- support_log       what was fixed, when, and by whom (the agent or a person).

BEGIN;

CREATE TABLE IF NOT EXISTS support_findings (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE,
  key           text NOT NULL,
  audience      text NOT NULL DEFAULT 'owner',
  severity      text NOT NULL,
  title         text NOT NULL,
  detail        text NOT NULL DEFAULT '',
  fix           text,
  fix_label     text NOT NULL DEFAULT '',
  args          jsonb NOT NULL DEFAULT '{}'::jsonb,
  status        text NOT NULL DEFAULT 'open',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  closed_at     timestamptz,
  closed_by     text,
  CONSTRAINT support_findings_audience_ck CHECK (audience IN ('owner','techdeva')),
  CONSTRAINT support_findings_severity_ck CHECK (severity IN ('low','medium','high')),
  CONSTRAINT support_findings_status_ck CHECK (status IN ('open','fixed','gone')),
  CONSTRAINT support_findings_closed_ck CHECK (status = 'open' OR closed_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS support_findings_open_platform_uniq
  ON support_findings (key) WHERE status = 'open' AND tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS support_findings_open_tenant_uniq
  ON support_findings (tenant_id, key) WHERE status = 'open' AND tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS support_findings_tenant_idx ON support_findings (tenant_id, status);

CREATE TABLE IF NOT EXISTS support_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid REFERENCES tenants(id) ON DELETE CASCADE,
  audience    text NOT NULL DEFAULT 'owner',
  at          timestamptz NOT NULL DEFAULT now(),
  what        text NOT NULL,
  by_whom     text NOT NULL,
  finding_key text,
  CONSTRAINT support_log_audience_ck CHECK (audience IN ('owner','techdeva'))
);
CREATE INDEX IF NOT EXISTS support_log_recent_idx ON support_log (tenant_id, at DESC);

ALTER TABLE support_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_findings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON support_findings;
CREATE POLICY tenant_isolation ON support_findings
  USING (is_admin() OR (tenant_id = current_tenant() AND audience = 'owner'))
  WITH CHECK (is_admin() OR (tenant_id = current_tenant() AND audience = 'owner'));

ALTER TABLE support_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE support_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON support_log;
CREATE POLICY tenant_isolation ON support_log
  USING (is_admin() OR (tenant_id = current_tenant() AND audience = 'owner'))
  WITH CHECK (is_admin() OR (tenant_id = current_tenant() AND audience = 'owner'));

GRANT SELECT, INSERT, UPDATE ON support_findings, support_log TO techdeva_app;

DO $verify$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM pg_class
   WHERE relname IN ('support_findings', 'support_log') AND relforcerowsecurity;
  IF n <> 2 THEN RAISE EXCEPTION 'verify failed: row level security is not forced on both support tables'; END IF;
  RAISE NOTICE 'verify ok: support tables ready, tenant isolated, internal rows hidden from businesses';
END $verify$;

COMMIT;
