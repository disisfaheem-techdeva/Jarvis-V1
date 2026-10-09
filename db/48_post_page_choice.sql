-- 48_post_page_choice.sql
-- A Facebook login can manage many Pages. The owner now picks the Page a
-- post goes to; it is stored on the post so a scheduled post goes to the
-- Page chosen when it was scheduled. Additive: one nullable column.
BEGIN;
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS page_id text;
COMMIT;
