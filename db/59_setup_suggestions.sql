-- 59: Jarvis reads the client's own Facebook page and website (5 Oct 2026)
--
-- When a business has a connected Facebook page or a website on record, the
-- scheduler reads them, turns what it finds into the same preview as "Set up my
-- business with Jarvis" (type, services and prices, hours, facts) and keeps it
-- here. Nothing reaches the business's records until the owner presses Save on
-- that preview. A new read happens only when the sources change (a page is
-- connected, the website changes), never on a loop.
-- Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS setup_suggestions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  sources      jsonb NOT NULL DEFAULT '[]',     -- [{kind: facebook|website, ref, ok, note}]
  fingerprint  text NOT NULL,                   -- which sources were read; a change triggers a new read
  draft        jsonb NOT NULL DEFAULT '{}',     -- setup_reader draft
  source_text  text NOT NULL DEFAULT '' CHECK (length(source_text) <= 40000),
  status       text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','saved','dismissed','nothing_found','failed')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  decided_at   timestamptz
);
CREATE INDEX IF NOT EXISTS setup_suggestions_tenant ON setup_suggestions (tenant_id, created_at DESC);

ALTER TABLE setup_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE setup_suggestions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON setup_suggestions;
CREATE POLICY tenant_isolation ON setup_suggestions
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON setup_suggestions TO techdeva_app;

COMMIT;
