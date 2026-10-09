-- 33_scheduled_posts.sql
-- A post can be approved for a time instead of now. The worker publishes it
-- when the time comes; until then the owner can take it back to a draft.
-- Additive: one nullable column, one more allowed status.
BEGIN;

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS scheduled_at timestamptz;
ALTER TABLE social_posts DROP CONSTRAINT IF EXISTS social_posts_status_ck;
ALTER TABLE social_posts ADD CONSTRAINT social_posts_status_ck CHECK (status IN
  ('draft','scheduled','publishing','published','partial','failed','discarded'));
ALTER TABLE social_posts DROP CONSTRAINT IF EXISTS social_posts_scheduled_ck;
ALTER TABLE social_posts ADD CONSTRAINT social_posts_scheduled_ck CHECK (
  status <> 'scheduled' OR scheduled_at IS NOT NULL);
CREATE INDEX IF NOT EXISTS social_posts_due_idx ON social_posts (scheduled_at)
  WHERE status = 'scheduled';

COMMIT;
