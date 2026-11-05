-- 91: POS is a separate product, on only for the businesses that take it (5 Nov 2026)
--
-- The till lives at its own address (pos.techdeva.net) and is switched on per
-- business by TechDeva (founder console > POS). Off by default: a business
-- that never asked for a POS sees nothing of it and its /pos/* calls are refused.
-- Sales still land in the business's own orders, invoices and payments, so Day
-- Close and P&L stay one set of books.
--
-- Additive: three nullable/defaulted columns. Rollback:
--   ALTER TABLE pos_settings DROP COLUMN enabled, DROP COLUMN enabled_by, DROP COLUMN enabled_at;
BEGIN;
ALTER TABLE pos_settings ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT false;
ALTER TABLE pos_settings ADD COLUMN IF NOT EXISTS enabled_by text;
ALTER TABLE pos_settings ADD COLUMN IF NOT EXISTS enabled_at timestamptz;

DO $verify$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'pos_settings'
        AND column_name IN ('enabled','enabled_by','enabled_at')) <> 3 THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - pos_settings.enabled missing';
  END IF;
  RAISE NOTICE 'OK: POS on/off per business (default off).';
END
$verify$;
COMMIT;
