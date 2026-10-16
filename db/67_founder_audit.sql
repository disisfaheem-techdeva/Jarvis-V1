-- 67_founder_audit.sql
-- Founder (TechDeva-wide) actions get audit rows with no business attached.
--
-- Until now executions.tenant_id was NOT NULL, so audit.record() for a
-- platform-wide action (a founder setting, a plan price) was refused by the
-- database and only printed to the log. Those actions now store tenant_id NULL.
--
-- Who may write or read such a row is decided by the EXISTING policy, which
-- this migration does not touch:
--     tenant_isolation: USING / WITH CHECK (is_admin() OR tenant_id = current_tenant())
-- A NULL tenant_id never equals current_tenant(), so only a session with
-- app.role = 'techdeva_admin' (is_admin()) can insert or see one; a business
-- session can do neither. The block at the end fails if that policy changed.
--
-- The CHECK below is about the row itself: an AI agent always acts inside a
-- business, so only a human (the founder) or the system may act without one.
-- (It cannot test app.role: a CHECK must hold for the row alone, or a restore
-- of a dump would fail.) Existing rows all have a tenant_id, so it validates.
-- No row is changed or deleted.
BEGIN;

ALTER TABLE executions ALTER COLUMN tenant_id DROP NOT NULL;

ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_tenant_or_founder;
ALTER TABLE executions ADD CONSTRAINT executions_tenant_or_founder
    CHECK (tenant_id IS NOT NULL OR actor_type IN ('human', 'system'));

COMMIT;

DO $verify$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n FROM pg_policies
     WHERE tablename = 'executions' AND policyname = 'tenant_isolation' AND cmd = 'ALL'
       AND qual = '(is_admin() OR (tenant_id = current_tenant()))'
       AND with_check = '(is_admin() OR (tenant_id = current_tenant()))';
    IF n <> 1 OR (SELECT count(*) FROM pg_policies WHERE tablename = 'executions') <> 1 THEN
        RAISE EXCEPTION 'executions policies are not the expected single tenant_isolation policy';
    END IF;
    IF NOT (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = 'executions') THEN
        RAISE EXCEPTION 'executions must have forced row-level security';
    END IF;
END
$verify$;
