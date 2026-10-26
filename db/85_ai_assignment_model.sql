-- 85: a model per WhatsApp agent (Rafay, 26 Oct 2026). The founder picks the
-- key AND one of that key's fetched models for each agent; NULL = the key's
-- default model.
--
-- ROLLBACK (by hand, techdeva database only):
--   ALTER TABLE ai_key_assignments DROP COLUMN IF EXISTS model;
--   DELETE FROM _td_migrations WHERE name = '85_ai_assignment_model.sql';
BEGIN;
ALTER TABLE ai_key_assignments ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE ai_key_assignments DROP CONSTRAINT IF EXISTS ai_key_assignments_model_check;
ALTER TABLE ai_key_assignments ADD CONSTRAINT ai_key_assignments_model_check
  CHECK (model IS NULL OR model ~ '^[A-Za-z0-9._:/@+-]{1,120}$');
DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'ai_key_assignments' AND column_name = 'model') THEN
    RAISE EXCEPTION 'verify: ai_key_assignments.model missing';
  END IF;
  RAISE NOTICE 'verify: 85 ok';
END
$verify$;
COMMIT;
