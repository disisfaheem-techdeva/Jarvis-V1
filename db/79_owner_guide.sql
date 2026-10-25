-- 79_owner_guide.sql
-- The owner's full guide for their WhatsApp agent (Faheem, 25 Oct 2026).
-- "Teach your agent" took one short rule, and the agent's lesson list cuts each
-- lesson to 500 characters on one line, so a full prompt with example chats
-- could not be taught. A guide is kept exactly as written (line breaks and
-- examples included) as business_knowledge rows of kind 'guide' (one per part,
-- 2,000 characters each) and reaches the agent through the knowledge sync,
-- which already carries up to 60,000 characters. Hermes itself is not changed.
-- Additive: one more allowed value for business_knowledge.kind.
-- Rollback: UPDATE business_knowledge SET active = false WHERE kind = 'guide'; then
--           restore the check to ('faq','policy','not_offered','info').
BEGIN;

DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'business_knowledge'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%kind%' LOOP
    EXECUTE format('ALTER TABLE business_knowledge DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE business_knowledge ADD CONSTRAINT business_knowledge_kind_check
  CHECK (kind IN ('faq', 'policy', 'not_offered', 'info', 'guide'));

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'business_knowledge_kind_check'
                  AND pg_get_constraintdef(oid) LIKE '%guide%') THEN
    RAISE EXCEPTION 'verify failed: business_knowledge does not accept the guide kind';
  END IF;
  RAISE NOTICE 'verify ok: the owner''s full guide can be stored for the WhatsApp agent';
END
$verify$;

COMMIT;
