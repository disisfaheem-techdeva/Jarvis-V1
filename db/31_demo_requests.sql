-- 31_demo_requests.sql
-- "Book a demo" on the website posted to an address the API never had, so every
-- request was lost. Requests now land in the existing intake queue (signups),
-- which the founder console already lists under Pipeline & trials, with their
-- own status. Additive only: one enum value, two nullable columns.

ALTER TYPE signup_status ADD VALUE IF NOT EXISTS 'demo_requested';

ALTER TABLE signups ADD COLUMN IF NOT EXISTS contact_name text;
ALTER TABLE signups ADD COLUMN IF NOT EXISTS note text;
