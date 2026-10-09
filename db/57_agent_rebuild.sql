-- 57_agent_rebuild.sql
-- "Rebuild agent" in the founder console: the request goes back to the build
-- queue with rebuild = true, and the server's provision-queue removes the old
-- agent before building it again. Cleared when the new agent is ready.
BEGIN;
ALTER TABLE agent_requests ADD COLUMN IF NOT EXISTS rebuild boolean NOT NULL DEFAULT false;
COMMIT;
