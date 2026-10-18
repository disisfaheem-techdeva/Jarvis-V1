-- A/B tests of creatives inside one campaign (2026-10-18).
--   creatives: the photos, flyers and videos the owner attached to a campaign
--              draft, in order: [{"kind": "image" | "video", "token": "..."}].
--              The planner picks how many run (one ad each) from the budget.
--   test:      the planner's test once launched: {"status": "testing" | "done",
--              "started_at", "days", "ads": [{ad_id, kind, token}], "results",
--              "winner", "reason", "decided_at", "checked_at"}.
ALTER TABLE ad_drafts ADD COLUMN IF NOT EXISTS creatives jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE ad_drafts ADD COLUMN IF NOT EXISTS test jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ad_drafts_testing_idx ON ad_drafts ((test->>'status')) WHERE test->>'status' = 'testing';
-- the planner's A/B decision is logged with the other ad actions
ALTER TABLE ad_actions DROP CONSTRAINT IF EXISTS ad_actions_kind_check;
ALTER TABLE ad_actions ADD CONSTRAINT ad_actions_kind_check CHECK (kind IN ('pause','resume','launch','ab_winner'));
