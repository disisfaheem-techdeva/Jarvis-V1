-- 90: POS, counter billing (3 Nov 2026)
--
-- A till sale is an ordinary order (db/60) with source 'pos' and pickup
-- fulfilment: taken, handed over and paid at the counter. So invoices,
-- payments, Daily Closing, P&L and stock see till money exactly as they see
-- every other sale; there is no second ledger.
--
--   open bill   an order left 'confirmed' with no payment (a table, a held cart)
--   udhaar      delivered with part (or no) payment: a receivable, recognised
--               only when the last rupee lands (orders._recognise)
--   shift       one cashier's drawer from opening float to counted close
--   refund      money handed back for lines of a paid sale; never a negative
--               payment, so payments stay "money received" and every report
--               that shows money net of refunds subtracts pos_refunds itself
--
-- Additive only: new tables, nullable columns, a wider source CHECK.
-- Rollback (nothing outside the POS reads these):
--   DROP TABLE pos_refund_items, pos_refunds, pos_cash_moves, pos_settings CASCADE;
--   ALTER TABLE payments DROP COLUMN pos_shift_id;
--   DROP TABLE pos_shifts CASCADE;
--   ALTER TABLE order_items DROP COLUMN note, DROP COLUMN kot_at;
--   ALTER TABLE orders DROP COLUMN pos_mode, DROP COLUMN table_label, DROP COLUMN cash_tendered,
--     DROP COLUMN change_given, DROP COLUMN shift_id, DROP COLUMN register, DROP COLUMN tax,
--     DROP COLUMN tax_rate, DROP COLUMN tax_mode;
--   -- only once no row has source 'pos':
--   ALTER TABLE orders DROP CONSTRAINT orders_source_check;
--   ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source IN
--     ('whatsapp_ai','website','facebook','instagram','manual','dashboard','api'));
BEGIN;

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_source_check;
ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source IN
  ('whatsapp_ai','website','facebook','instagram','manual','dashboard','api','pos'));

-- ---------------------------------------------------------------- settings
CREATE TABLE IF NOT EXISTS pos_settings (
  tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by  text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- shifts
CREATE TABLE IF NOT EXISTS pos_shifts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  number         text NOT NULL,
  register       text NOT NULL DEFAULT 'Till 1' CHECK (length(btrim(register)) BETWEEN 1 AND 40),
  opened_by      text,
  opened_at      timestamptz NOT NULL DEFAULT now(),
  opening_float  numeric(14,2) NOT NULL DEFAULT 0 CHECK (opening_float >= 0),
  closed_by      text,
  closed_at      timestamptz,
  counted_cash   numeric(14,2) CHECK (counted_cash IS NULL OR counted_cash >= 0),
  expected_cash  numeric(14,2),
  variance       numeric(14,2),
  notes          text CHECK (notes IS NULL OR length(notes) <= 500),
  CONSTRAINT pos_shifts_closed_counted CHECK (closed_at IS NULL OR (counted_cash IS NOT NULL AND expected_cash IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS pos_shifts_tenant_number ON pos_shifts (tenant_id, number);
-- one open drawer per register
CREATE UNIQUE INDEX IF NOT EXISTS pos_shifts_one_open ON pos_shifts (tenant_id, lower(register)) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS pos_shifts_tenant_opened ON pos_shifts (tenant_id, opened_at DESC);

CREATE TABLE IF NOT EXISTS pos_cash_moves (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  shift_id   uuid NOT NULL REFERENCES pos_shifts(id) ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('in','out')),
  amount     numeric(14,2) NOT NULL CHECK (amount > 0),
  reason     text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 200),
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pos_cash_moves_shift ON pos_cash_moves (shift_id);

-- which drawer a payment went into (null for every payment not taken at the till)
ALTER TABLE payments ADD COLUMN IF NOT EXISTS pos_shift_id uuid REFERENCES pos_shifts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS payments_pos_shift ON payments (pos_shift_id) WHERE pos_shift_id IS NOT NULL;

-- ---------------------------------------------------------------- orders
ALTER TABLE orders ADD COLUMN IF NOT EXISTS pos_mode text
  CHECK (pos_mode IS NULL OR pos_mode IN ('counter','dine_in','takeaway'));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS table_label text
  CHECK (table_label IS NULL OR length(table_label) <= 40);
-- cash handed over and given back, for the receipt and its reprint; the
-- payment row holds only the money kept
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cash_tendered numeric(14,2)
  CHECK (cash_tendered IS NULL OR cash_tendered >= 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS change_given numeric(14,2)
  CHECK (change_given IS NULL OR change_given >= 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shift_id uuid REFERENCES pos_shifts(id) ON DELETE SET NULL;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS register text CHECK (register IS NULL OR length(register) <= 40);
-- tax as charged on this bill: 'exclusive' adds it to the total (and to the
-- invoice), 'inclusive' is the part of the price that was tax (shown, not added)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax numeric(14,2) NOT NULL DEFAULT 0 CHECK (tax >= 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_rate numeric(6,3) CHECK (tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 100));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_mode text CHECK (tax_mode IS NULL OR tax_mode IN ('exclusive','inclusive'));

CREATE INDEX IF NOT EXISTS orders_tenant_pos_open ON orders (tenant_id, created_at DESC)
  WHERE source = 'pos' AND status = 'confirmed';
CREATE INDEX IF NOT EXISTS orders_shift ON orders (shift_id) WHERE shift_id IS NOT NULL;

ALTER TABLE order_items ADD COLUMN IF NOT EXISTS note text CHECK (note IS NULL OR length(note) <= 200);
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS kot_at timestamptz;   -- sent to the kitchen

-- ---------------------------------------------------------------- refunds
CREATE TABLE IF NOT EXISTS pos_refunds (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  number      text NOT NULL,
  order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  shift_id    uuid REFERENCES pos_shifts(id) ON DELETE SET NULL,
  method      text NOT NULL CHECK (method IN ('cash','card','bank','online','other')),
  amount      numeric(14,2) NOT NULL CHECK (amount > 0),
  reason      text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 300),
  restocked   boolean NOT NULL DEFAULT false,
  created_by  text,
  approved_by text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS pos_refunds_tenant_number ON pos_refunds (tenant_id, number);
CREATE INDEX IF NOT EXISTS pos_refunds_order ON pos_refunds (order_id);
CREATE INDEX IF NOT EXISTS pos_refunds_tenant_at ON pos_refunds (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pos_refund_items (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  refund_id     uuid NOT NULL REFERENCES pos_refunds(id) ON DELETE CASCADE,
  order_item_id uuid REFERENCES order_items(id) ON DELETE SET NULL,
  description   text NOT NULL,
  quantity      numeric(12,2) NOT NULL CHECK (quantity > 0),
  amount        numeric(14,2) NOT NULL CHECK (amount >= 0)
);
CREATE INDEX IF NOT EXISTS pos_refund_items_refund ON pos_refund_items (refund_id);
CREATE INDEX IF NOT EXISTS pos_refund_items_line ON pos_refund_items (order_item_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['pos_settings','pos_shifts','pos_cash_moves','pos_refunds','pos_refund_items'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
  END LOOP;
END $$;
-- money records are append-only for the app: no UPDATE or DELETE on refunds or cash moves
GRANT SELECT, INSERT, UPDATE ON pos_settings TO techdeva_app;
GRANT SELECT, INSERT, UPDATE ON pos_shifts TO techdeva_app;
GRANT SELECT, INSERT ON pos_cash_moves, pos_refunds, pos_refund_items TO techdeva_app;

DO $verify$
DECLARE unprotected text;
BEGIN
  IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'orders'
        AND column_name IN ('pos_mode','table_label','cash_tendered','change_given','shift_id','register',
                            'tax','tax_rate','tax_mode')) <> 9 THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - orders POS columns missing';
  END IF;
  SELECT string_agg(c.relname, ', ') INTO unprotected
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relname IN ('pos_settings','pos_shifts','pos_cash_moves','pos_refunds','pos_refund_items')
     AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
  IF unprotected IS NOT NULL THEN
    RAISE EXCEPTION 'MIGRATION UNSAFE - RLS/FORCE missing on: %', unprotected;
  END IF;
  RAISE NOTICE 'OK: POS tables with forced RLS, orders/payments/order_items extended.';
END
$verify$;

COMMIT;
