-- 47_post_multiple_images.sql
-- A post can carry several pictures (Facebook multi-photo post, Instagram
-- carousel; both allow up to 10). image_url stays as the first picture so
-- older code paths (Jarvis drafts, the scheduler) keep working unchanged.
-- Additive: one column with a default, backfilled from image_url.
BEGIN;

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS image_urls text[] NOT NULL DEFAULT '{}';
UPDATE social_posts SET image_urls = ARRAY[image_url]
 WHERE image_url IS NOT NULL AND cardinality(image_urls) = 0;
ALTER TABLE social_posts DROP CONSTRAINT IF EXISTS social_posts_images_ck;
ALTER TABLE social_posts ADD CONSTRAINT social_posts_images_ck CHECK (cardinality(image_urls) <= 10);

COMMIT;
