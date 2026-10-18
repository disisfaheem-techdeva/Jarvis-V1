-- Ad drafts for shops as well as clinics (2026-10-18).
--   destination: where the ad's button goes. 'whatsapp' opens a WhatsApp chat
--                (as every campaign did before); 'website' opens the business's
--                website with an "Order now" button.
--   video_token: a video uploaded for this campaign (social_videos.token). When
--                set, the ad runs as a video ad; the photo, if any, is its cover.
ALTER TABLE ad_drafts ADD COLUMN IF NOT EXISTS destination text NOT NULL DEFAULT 'whatsapp';
ALTER TABLE ad_drafts DROP CONSTRAINT IF EXISTS ad_drafts_destination_ck;
ALTER TABLE ad_drafts ADD CONSTRAINT ad_drafts_destination_ck CHECK (destination IN ('whatsapp', 'website'));
ALTER TABLE ad_drafts ADD COLUMN IF NOT EXISTS video_token text;
