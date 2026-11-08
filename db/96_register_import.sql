-- 96: bring the old register (9 Nov 2026; Phase 7 of the Jarvis plan)
--
-- A shopkeeper photographs the pages of the handwritten udhaar register. Each page
-- is read by the AI into rows in a STAGING area. Nothing reaches the udhaar ledger
-- (db/94) until the owner has checked and approved each row and pressed Import.
--
--   ledger_imports        one upload: what the pages hold ("balances": what each
--                         customer owes now; "transactions": credit and payments)
--   ledger_import_pages   the page images, kept PRIVATE here (not the public post
--                         media store) behind row-level security, with what the
--                         AI answered and which model read it
--   ledger_import_rows    each row as read (text, name, phone, amount, date, kind,
--                         confidence) beside what the owner settled on, its flags
--                         (low confidence, no amount, unclear customer, possible
--                         duplicate...), its decision, and the ledger entry it
--                         became. "edited" records whether the owner had to change
--                         what was read: that is how accuracy is measured.
--
-- These are staging records, not money records: rows can be edited until imported.
-- An imported row is locked (its ledger entry is the money record, append-only).
-- Additive. Rollback: DROP TABLE ledger_import_rows, ledger_import_pages, ledger_imports;
BEGIN;

CREATE TABLE IF NOT EXISTS ledger_imports (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  mode          text NOT NULL CHECK (mode IN ('balances','transactions')),
  title         text CHECK (title IS NULL OR length(title) <= 120),
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','imported','discarded')),
  created_by    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  committed_at  timestamptz,
  committed_by  text
);
CREATE INDEX IF NOT EXISTS ledger_imports_tenant ON ledger_imports (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ledger_import_pages (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  import_id   uuid NOT NULL REFERENCES ledger_imports(id) ON DELETE CASCADE,
  page_no     int  NOT NULL CHECK (page_no BETWEEN 1 AND 200),
  mime        text NOT NULL CHECK (mime IN ('image/jpeg','image/png','application/pdf')),
  bytes       bytea NOT NULL,
  byte_size   int NOT NULL CHECK (byte_size > 0 AND byte_size <= 8000000),
  status      text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','read','failed')),
  error       text CHECK (error IS NULL OR length(error) <= 300),
  model       text,
  answer      jsonb,
  read_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (import_id, page_no)
);

CREATE TABLE IF NOT EXISTS ledger_import_rows (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  import_id        uuid NOT NULL REFERENCES ledger_imports(id) ON DELETE CASCADE,
  page_id          uuid NOT NULL REFERENCES ledger_import_pages(id) ON DELETE CASCADE,
  row_no           int  NOT NULL CHECK (row_no >= 1),
  -- as read
  read_text        text CHECK (read_text IS NULL OR length(read_text) <= 500),
  read_name        text,
  read_phone       text,
  read_amount      numeric(14,2),
  read_date        date,
  read_kind        text,
  confidence       numeric(4,3) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  -- as settled (starts as read; the owner may change it)
  name             text CHECK (name IS NULL OR length(name) <= 120),
  phone            text CHECK (phone IS NULL OR length(phone) <= 40),
  amount           numeric(14,2) CHECK (amount IS NULL OR amount > 0),
  entry_date       date,
  kind             text NOT NULL DEFAULT 'opening' CHECK (kind IN ('opening','credit','payment')),
  customer_id      uuid REFERENCES customers(id) ON DELETE SET NULL,
  flags            text[] NOT NULL DEFAULT '{}',
  status           text NOT NULL DEFAULT 'needs_check'
                   CHECK (status IN ('needs_check','ready','approved','rejected','imported')),
  edited           boolean NOT NULL DEFAULT false,
  decided_by       text,
  decided_at       timestamptz,
  ledger_entry_id  uuid REFERENCES customer_ledger_entries(id),
  import_error     text CHECK (import_error IS NULL OR length(import_error) <= 300),
  UNIQUE (page_id, row_no),
  CHECK (status <> 'imported' OR ledger_entry_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ledger_import_rows_import ON ledger_import_rows (import_id, status);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ledger_imports','ledger_import_pages','ledger_import_rows'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

-- an imported row is locked: its ledger entry is now the money record
CREATE OR REPLACE FUNCTION ledger_import_row_locked() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'imported' AND (TG_OP = 'UPDATE' OR pg_trigger_depth() <= 1) THEN
    RAISE EXCEPTION 'an imported register row cannot be changed: correct it in the udhaar ledger'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ledger_import_row_locked ON ledger_import_rows;
CREATE TRIGGER ledger_import_row_locked BEFORE UPDATE OR DELETE ON ledger_import_rows
  FOR EACH ROW EXECUTE FUNCTION ledger_import_row_locked();

DO $verify$
DECLARE unprotected text;
BEGIN
  SELECT string_agg(c.relname, ', ') INTO unprotected
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relname IN ('ledger_imports','ledger_import_pages','ledger_import_rows')
     AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
  IF unprotected IS NOT NULL THEN
    RAISE EXCEPTION 'MIGRATION UNSAFE - RLS/FORCE missing on: %', unprotected;
  END IF;
  RAISE NOTICE 'OK: register import staging (private pages, rows with confidence and flags).';
END
$verify$;
COMMIT;
