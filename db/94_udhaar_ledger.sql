-- 94: the udhaar ledger (8 Nov 2026; ADR-004 in the TechDeva docs repo)
--
-- A karyana keeps credit that is not a till bill: the opening balance from the old
-- register, "Ahmed ke account mein 1,500 likh do", and the money Ahmed pays back.
-- Till udhaar stays exactly as it is (an unpaid POS bill, app/pos.py). This table
-- holds the rest, and customer_udhaar adds the two, so no screen and no Jarvis
-- answer works the balance out differently.
--
--   customer_ledger_entries  append-only. opening and credit are positive (owed to
--                            the shop), payment is negative, adjustment either.
--                            A mistake is never edited: a reversing adjustment
--                            points at it (reverses), and both stay in the history.
--                            An UPDATE is refused; a DELETE is refused unless it
--                            comes from deleting the whole business (a cascade).
--                            A customer with entries cannot be deleted on its own.
--   customer_udhaar          per customer: ledger, till udhaar (POS bills delivered
--                            and not fully paid: total less payments, as the till's
--                            own Bills list counts it), and the balance. Reads with
--                            the caller's row-level security (security_invoker).
--
-- Additive: one table, one trigger, one view. Rollback:
--   DROP VIEW customer_udhaar; DROP TABLE customer_ledger_entries;
--   DROP FUNCTION customer_ledger_append_only();
BEGIN;

CREATE TABLE IF NOT EXISTS customer_ledger_entries (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id  uuid NOT NULL REFERENCES customers(id),
  kind         text NOT NULL CHECK (kind IN ('opening','credit','payment','adjustment')),
  amount       numeric(14,2) NOT NULL CHECK (amount <> 0 AND abs(amount) <= 100000000),
  method       text CHECK (method IS NULL OR method IN ('cash','bank','jazzcash','easypaisa','card','other')),
  note         text CHECK (note IS NULL OR length(note) <= 300),
  occurred_on  date NOT NULL DEFAULT ((now() AT TIME ZONE 'Asia/Karachi')::date),
  source       text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','jarvis','import')),
  reverses     uuid UNIQUE REFERENCES customer_ledger_entries(id),
  client_ref   text CHECK (client_ref IS NULL OR client_ref ~ '^[A-Za-z0-9_-]{8,64}$'),
  created_by   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind IN ('opening','credit') AND amount > 0) OR (kind = 'payment' AND amount < 0) OR kind = 'adjustment'),
  CHECK (reverses IS NULL OR kind = 'adjustment'),
  CHECK (method IS NULL OR kind = 'payment')
);
CREATE INDEX IF NOT EXISTS customer_ledger_customer ON customer_ledger_entries (tenant_id, customer_id, occurred_on, created_at);
-- a resent "write it down" (double tap, lost connection) is the first entry, never a second
CREATE UNIQUE INDEX IF NOT EXISTS customer_ledger_client_ref ON customer_ledger_entries (tenant_id, client_ref)
  WHERE client_ref IS NOT NULL;

ALTER TABLE customer_ledger_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_ledger_entries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON customer_ledger_entries;
CREATE POLICY tenant_isolation ON customer_ledger_entries
  USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT ON customer_ledger_entries TO techdeva_app;

CREATE OR REPLACE FUNCTION customer_ledger_append_only() RETURNS trigger AS $$
BEGIN
  -- depth 1 = a statement aimed at this table; deeper = a cascade (the business is being deleted)
  IF TG_OP = 'UPDATE' OR pg_trigger_depth() <= 1 THEN
    RAISE EXCEPTION 'customer_ledger_entries is append-only: add a reversing entry instead'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN OLD;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS customer_ledger_append_only ON customer_ledger_entries;
CREATE TRIGGER customer_ledger_append_only BEFORE UPDATE OR DELETE ON customer_ledger_entries
  FOR EACH ROW EXECUTE FUNCTION customer_ledger_append_only();

CREATE OR REPLACE VIEW customer_udhaar WITH (security_invoker = true) AS
SELECT c.tenant_id, c.id AS customer_id, c.name, c.phone,
       COALESCE(l.ledger, 0)::numeric(14,2)                       AS ledger,
       COALESCE(t.till, 0)::numeric(14,2)                         AS till,
       (COALESCE(l.ledger, 0) + COALESCE(t.till, 0))::numeric(14,2) AS balance,
       COALESCE(t.bills, 0)                                       AS till_bills,
       COALESCE(l.entries, 0)                                     AS entries,
       GREATEST(l.last_at, t.last_at)                             AS last_activity
  FROM customers c
  LEFT JOIN (SELECT customer_id, sum(amount) AS ledger, count(*) AS entries, max(created_at) AS last_at
               FROM customer_ledger_entries GROUP BY customer_id) l ON l.customer_id = c.id
  LEFT JOIN (SELECT o.customer_id, sum(o.total - COALESCE(p.paid, 0)) AS till, count(*) AS bills,
                    max(o.created_at) AS last_at
               FROM orders o
               LEFT JOIN (SELECT invoice_id, sum(amount) AS paid FROM payments GROUP BY invoice_id) p
                      ON p.invoice_id = o.invoice_id
              WHERE o.source = 'pos' AND o.status = 'delivered' AND o.recognised_at IS NULL
                AND o.customer_id IS NOT NULL
              GROUP BY o.customer_id) t ON t.customer_id = c.id;
GRANT SELECT ON customer_udhaar TO techdeva_app;

DO $verify$
BEGIN
  IF to_regclass('customer_ledger_entries') IS NULL OR to_regclass('customer_udhaar') IS NULL THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - udhaar ledger missing';
  END IF;
  IF NOT (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = 'customer_ledger_entries') THEN
    RAISE EXCEPTION 'MIGRATION UNSAFE - RLS/FORCE missing on customer_ledger_entries';
  END IF;
  RAISE NOTICE 'OK: udhaar ledger (append-only, forced RLS) and customer_udhaar balances.';
END
$verify$;
COMMIT;
