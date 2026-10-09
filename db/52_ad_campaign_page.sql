-- 52_ad_campaign_page.sql
-- A Facebook login can manage many Pages. Like posts (48), each Facebook &
-- Instagram campaign now stores the Page it runs from; the launch used to
-- take the first Page on the login. Additive: one nullable column.
BEGIN;
ALTER TABLE ad_drafts ADD COLUMN IF NOT EXISTS page_id text;
COMMIT;
