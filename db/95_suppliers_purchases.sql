-- 95: suppliers and purchases (9 Nov 2026; Phase 6 of the Jarvis plan)
--
-- A karyana buys stock on credit from its wholesalers and pays them later:
-- "Bilal Traders se 50 kilo cheeni 160 rupay kilo li, 3,000 de diye".
--
--   suppliers                one row per wholesaler
--   purchase_bills           one bill from a supplier: date, the supplier's own bill
--   purchase_bill_items      number, total; lines of product, quantity, unit cost.
--                            Each line puts stock in through the existing ledger
--                            (stock_movements kind 'purchase' with its unit cost), so
--                            inventory and its history need nothing new.
--   supplier_ledger_entries  append-only, like the udhaar ledger (db/94): a bill is
--                            owed to the supplier (positive), a payment negative,
--                            opening and adjustment either; a mistake is reversed.
--   supplier_balances        what the shop owes each supplier.
--
-- A bill is never edited or deleted. Voiding one adds the opposite stock movements
-- and the opposite ledger entry, and marks it void; both stay in the history.
--
-- Additive. Rollback:
--   DROP VIEW supplier_balances; DROP TABLE supplier_ledger_entries, purchase_bill_items,
--   purchase_bills, suppliers; DROP FUNCTION td_append_only();
BEGIN;

CREATE TABLE IF NOT EXISTS suppliers (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  phone       text CHECK (phone IS NULL OR length(phone) <= 40),
  notes       text CHECK (notes IS NULL OR length(notes) <= 500),
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS suppliers_tenant_name ON suppliers (tenant_id, lower(btrim(name)));

CREATE TABLE IF NOT EXISTS purchase_bills (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supplier_id  uuid NOT NULL REFERENCES suppliers(id),
  number       text NOT NULL,                                    -- ours: PB-0001
  supplier_ref text CHECK (supplier_ref IS NULL OR length(supplier_ref) <= 60),   -- the supplier's own bill number
  bought_on    date NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Karachi')::date),
  total        numeric(14,2) NOT NULL CHECK (total >= 0),
  note         text CHECK (note IS NULL OR length(note) <= 300),
  source       text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','jarvis','import')),
  client_ref   text CHECK (client_ref IS NULL OR client_ref ~ '^[A-Za-z0-9_-]{8,64}$'),
  created_by   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  voided_at    timestamptz,
  void_reason  text CHECK (void_reason IS NULL OR length(void_reason) <= 250),
  voided_by    text
);
CREATE UNIQUE INDEX IF NOT EXISTS purchase_bills_tenant_number ON purchase_bills (tenant_id, number);
CREATE UNIQUE INDEX IF NOT EXISTS purchase_bills_client_ref ON purchase_bills (tenant_id, client_ref) WHERE client_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS purchase_bills_tenant_date ON purchase_bills (tenant_id, bought_on DESC);

CREATE TABLE IF NOT EXISTS purchase_bill_items (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  bill_id           uuid NOT NULL REFERENCES purchase_bills(id) ON DELETE CASCADE,
  product_id        uuid NOT NULL REFERENCES products(id),
  quantity          numeric(12,3) NOT NULL CHECK (quantity > 0),
  unit_cost         numeric(14,2) NOT NULL CHECK (unit_cost >= 0),
  line_total        numeric(14,2) NOT NULL CHECK (line_total >= 0),
  stock_movement_id uuid REFERENCES stock_movements(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS purchase_bill_items_bill ON purchase_bill_items (bill_id);
CREATE INDEX IF NOT EXISTS purchase_bill_items_product ON purchase_bill_items (product_id);

CREATE TABLE IF NOT EXISTS supplier_ledger_entries (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supplier_id  uuid NOT NULL REFERENCES suppliers(id),
  kind         text NOT NULL CHECK (kind IN ('opening','bill','payment','adjustment')),
  amount       numeric(14,2) NOT NULL CHECK (amount <> 0 AND abs(amount) <= 100000000),
  bill_id      uuid REFERENCES purchase_bills(id),
  method       text CHECK (method IS NULL OR method IN ('cash','bank','jazzcash','easypaisa','card','cheque','other')),
  note         text CHECK (note IS NULL OR length(note) <= 300),
  occurred_on  date NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Karachi')::date),
  source       text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','jarvis','import')),
  reverses     uuid UNIQUE REFERENCES supplier_ledger_entries(id),
  client_ref   text CHECK (client_ref IS NULL OR client_ref ~ '^[A-Za-z0-9_-]{8,64}$'),
  created_by   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind IN ('opening','bill') AND amount > 0) OR (kind = 'payment' AND amount < 0) OR kind = 'adjustment'),
  CHECK (reverses IS NULL OR kind = 'adjustment'),
  CHECK (method IS NULL OR kind = 'payment'),
  CHECK (kind <> 'bill' OR bill_id IS NOT NULL),                     -- a bill entry points at its bill
  CHECK (bill_id IS NULL OR kind IN ('bill','adjustment'))           -- and only it, or its reversal
);
CREATE INDEX IF NOT EXISTS supplier_ledger_supplier ON supplier_ledger_entries (tenant_id, supplier_id, occurred_on, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS supplier_ledger_client_ref ON supplier_ledger_entries (tenant_id, client_ref) WHERE client_ref IS NOT NULL;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['suppliers','purchase_bills','purchase_bill_items','supplier_ledger_entries'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON suppliers, purchase_bills TO techdeva_app;
GRANT SELECT, INSERT ON purchase_bill_items, supplier_ledger_entries TO techdeva_app;

-- One guard for append-only money tables: no UPDATE; a DELETE only as part of a
-- cascade (the whole business being deleted). TG_ARGV[0] says what to do instead.
CREATE OR REPLACE FUNCTION td_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' OR pg_trigger_depth() <= 1 THEN
    RAISE EXCEPTION '% is append-only: %', TG_TABLE_NAME, COALESCE(TG_ARGV[0], 'add a reversing entry instead')
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN OLD;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS supplier_ledger_append_only ON supplier_ledger_entries;
CREATE TRIGGER supplier_ledger_append_only BEFORE UPDATE OR DELETE ON supplier_ledger_entries
  FOR EACH ROW EXECUTE FUNCTION td_append_only('add a reversing entry instead');
DROP TRIGGER IF EXISTS purchase_bill_items_append_only ON purchase_bill_items;
CREATE TRIGGER purchase_bill_items_append_only BEFORE UPDATE OR DELETE ON purchase_bill_items
  FOR EACH ROW EXECUTE FUNCTION td_append_only('void the bill instead');

-- A bill is changed only to void it, once; its figures never change.
CREATE OR REPLACE FUNCTION purchase_bill_only_void() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF pg_trigger_depth() <= 1 THEN
      RAISE EXCEPTION 'purchase_bills are never deleted: void the bill instead' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.voided_at IS NOT NULL
     OR NEW.voided_at IS NULL
     OR (NEW.id, NEW.tenant_id, NEW.supplier_id, NEW.number, NEW.supplier_ref, NEW.bought_on, NEW.total, NEW.note,
         NEW.source, NEW.client_ref, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
        (OLD.id, OLD.tenant_id, OLD.supplier_id, OLD.number, OLD.supplier_ref, OLD.bought_on, OLD.total, OLD.note,
         OLD.source, OLD.client_ref, OLD.created_by, OLD.created_at) THEN
    RAISE EXCEPTION 'a purchase bill can only be voided, once' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS purchase_bill_only_void ON purchase_bills;
CREATE TRIGGER purchase_bill_only_void BEFORE UPDATE OR DELETE ON purchase_bills
  FOR EACH ROW EXECUTE FUNCTION purchase_bill_only_void();

CREATE OR REPLACE VIEW supplier_balances WITH (security_invoker = true) AS
SELECT s.tenant_id, s.id AS supplier_id, s.name, s.phone, s.active,
       COALESCE(l.owed, 0)::numeric(14,2) AS owed,
       COALESCE(l.entries, 0) AS entries,
       COALESCE(b.bills, 0) AS bills,
       COALESCE(b.bought, 0)::numeric(14,2) AS bought,
       l.last_at AS last_activity
  FROM suppliers s
  LEFT JOIN (SELECT supplier_id, sum(amount) AS owed, count(*) AS entries, max(created_at) AS last_at
               FROM supplier_ledger_entries GROUP BY supplier_id) l ON l.supplier_id = s.id
  LEFT JOIN (SELECT supplier_id, count(*) AS bills, sum(total) AS bought
               FROM purchase_bills WHERE voided_at IS NULL GROUP BY supplier_id) b ON b.supplier_id = s.id;
GRANT SELECT ON supplier_balances TO techdeva_app;

DO $verify$
DECLARE unprotected text;
BEGIN
  SELECT string_agg(c.relname, ', ') INTO unprotected
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relname IN ('suppliers','purchase_bills','purchase_bill_items','supplier_ledger_entries')
     AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
  IF unprotected IS NOT NULL THEN
    RAISE EXCEPTION 'MIGRATION UNSAFE - RLS/FORCE missing on: %', unprotected;
  END IF;
  IF to_regclass('supplier_balances') IS NULL THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - supplier_balances missing';
  END IF;
  RAISE NOTICE 'OK: suppliers, purchase bills (void only), supplier ledger (append-only), balances.';
END
$verify$;
COMMIT;
