-- 90: POS, counter billing (3 Nov 2026)
--
-- A till sale is an ordinary order (db/60) with source 'pos' and pickup
-- fulfilment: taken, handed over and paid at the counter. So invoices,
-- payments, Daily Closing, P&L and stock see till money exactly as they see
-- every other sale; there is no second ledger.
--
-- A restaurant table (or a held retail cart) is an order left 'confirmed'
-- with no payment: the "open tab". Settling it delivers it and takes the
-- money, which recognises the sale and takes the stock out, once.
--
-- Additive only: a wider source CHECK and four nullable columns.
-- Rollback (nothing else depends on these):
--   ALTER TABLE orders DROP COLUMN pos_mode, DROP COLUMN table_label,
--     DROP COLUMN cash_tendered, DROP COLUMN change_given;
--   -- only once no row has source 'pos':
--   ALTER TABLE orders DROP CONSTRAINT orders_source_check;
--   ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source IN
--     ('whatsapp_ai','website','facebook','instagram','manual','dashboard','api'));
BEGIN;

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_source_check;
ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source IN
  ('whatsapp_ai','website','facebook','instagram','manual','dashboard','api','pos'));

ALTER TABLE orders ADD COLUMN IF NOT EXISTS pos_mode text
  CHECK (pos_mode IS NULL OR pos_mode IN ('counter','dine_in','takeaway'));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS table_label text
  CHECK (table_label IS NULL OR length(table_label) <= 40);
-- what the customer handed over in cash and what went back: for the receipt
-- and its reprint. The payment row holds only the money kept.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cash_tendered numeric(14,2)
  CHECK (cash_tendered IS NULL OR cash_tendered >= 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS change_given numeric(14,2)
  CHECK (change_given IS NULL OR change_given >= 0);

CREATE INDEX IF NOT EXISTS orders_tenant_pos_open ON orders (tenant_id, created_at DESC)
  WHERE source = 'pos' AND status = 'confirmed';

DO $verify$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'orders'
        AND column_name IN ('pos_mode','table_label','cash_tendered','change_given')) <> 4 THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - orders POS columns missing';
  END IF;
  RAISE NOTICE 'OK: orders accept source pos, POS columns present.';
END
$verify$;

COMMIT;
