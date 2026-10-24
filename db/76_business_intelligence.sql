-- 76_business_intelligence.sql
-- The Business Intelligence agent (Faheem, 24 Oct 2026): the owner gives a
-- website, the agent reads its public pages (robots.txt and sitemap respected,
-- page and time limits), and the AI writes a business profile in which every
-- claim says where it came from and whether it is on the site, worked out from
-- it, or the AI's guess. The owner corrects and approves it; only the approved
-- profile reaches Jarvis and Create with AI.
--   website_scans               one per "Analyze my business"; status and counts
--   website_pages               each page the scan looked at, read or not, and why
--   business_profiles           one per business: the draft and the approved profile
--   business_profile_versions   every approved version, for history and undo
-- Row-level security keeps each business's rows to itself. Additive: no
-- existing table is changed. Rollback: DROP the four tables (no other table
-- refers to them).
BEGIN;

CREATE TABLE IF NOT EXISTS website_scans (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  url               text NOT NULL CHECK (length(url) <= 500),
  host              text,
  status            text NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','discovering','extracting','analyzing','saving',
                       'completed','completed_with_warnings','failed','cancelled')),
  stage_note        text,
  pages_discovered  integer NOT NULL DEFAULT 0,
  pages_processed   integer NOT NULL DEFAULT 0,
  pages_skipped     integer NOT NULL DEFAULT 0,
  pages_failed      integer NOT NULL DEFAULT 0,
  max_pages         integer NOT NULL DEFAULT 40 CHECK (max_pages BETWEEN 1 AND 200),
  warnings          jsonb NOT NULL DEFAULT '[]'::jsonb,
  error             text,
  cancel_requested  boolean NOT NULL DEFAULT false,
  attempts          integer NOT NULL DEFAULT 0,
  crawl_done        boolean NOT NULL DEFAULT false,     -- pages are saved: a retry only re-runs the analysis
  created_by        uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  heartbeat_at      timestamptz,
  finished_at       timestamptz
);
CREATE INDEX IF NOT EXISTS website_scans_tenant ON website_scans (tenant_id, created_at DESC);
-- one scan at a time per business: a second press of the button returns the running one
CREATE UNIQUE INDEX IF NOT EXISTS website_scans_one_active ON website_scans (tenant_id)
  WHERE status IN ('pending','discovering','extracting','analyzing','saving');

CREATE TABLE IF NOT EXISTS website_pages (
  id            bigserial PRIMARY KEY,
  scan_id       uuid NOT NULL REFERENCES website_scans(id) ON DELETE CASCADE,
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  url           text NOT NULL,
  canonical_url text,
  kind          text,                                  -- home, about, contact, services, pricing, faq, policy, blog, other
  status        text NOT NULL CHECK (status IN ('processed','skipped','failed')),
  http_status   integer,
  note          text,                                  -- why it was skipped or failed, in plain words
  title         text,
  content_hash  text,
  text          text CHECK (length(text) <= 20000),
  data          jsonb NOT NULL DEFAULT '{}'::jsonb,    -- meta, structured data, contacts, links found on the page
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (scan_id, url)
);
CREATE INDEX IF NOT EXISTS website_pages_scan ON website_pages (scan_id, id);

CREATE TABLE IF NOT EXISTS business_profiles (
  tenant_id     uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  draft         jsonb,                                 -- what the latest scan and the owner's edits say
  approved      jsonb,                                 -- what Jarvis and Create with AI use
  status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','changes_waiting')),
  version       integer NOT NULL DEFAULT 0,
  owner_edited  text[] NOT NULL DEFAULT '{}',          -- fields the owner set by hand: a new scan never overwrites them
  last_scan_id  uuid REFERENCES website_scans(id) ON DELETE SET NULL,
  approved_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  approved_at   timestamptz,
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS business_profile_versions (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  version     integer NOT NULL,
  profile     jsonb NOT NULL,
  note        text,
  scan_id     uuid REFERENCES website_scans(id) ON DELETE SET NULL,
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, version)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['website_scans','website_pages','business_profiles','business_profile_versions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;
GRANT USAGE ON SEQUENCE website_pages_id_seq, business_profile_versions_id_seq TO techdeva_app;

DO $verify$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM pg_class
   WHERE relname IN ('website_scans','website_pages','business_profiles','business_profile_versions')
     AND relforcerowsecurity;
  IF n <> 4 THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on all four business intelligence tables';
  END IF;
  RAISE NOTICE 'verify ok: website scans and business profiles are stored per business';
END
$verify$;

COMMIT;
