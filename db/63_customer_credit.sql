-- 63: An advance on a cancelled appointment becomes the customer's credit (audit, 5 Oct 2026)
--
-- Before: cancelling a visit that had an advance left its bill part-paid, so the
-- visit and the finance pages showed money owed for a visit that never happened.
-- Now cancelling settles that bill at the advance (the business keeps it; no
-- money is rewritten) and the advance is the customer's credit. A later booking
-- can use it as a reduction on its bill, recorded here, so the same money is
-- never counted twice. Credit available = advances on cancelled visits - uses.
-- Additive. Forced RLS.
BEGIN;
CREATE TABLE IF NOT EXISTS customer_credit_uses (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id  uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  invoice_id   uuid REFERENCES invoices(id) ON DELETE SET NULL,
  amount       numeric(14,2) NOT NULL CHECK (amount > 0),
  created_by   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customer_credit_uses_customer ON customer_credit_uses (customer_id);
ALTER TABLE customer_credit_uses ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_credit_uses FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON customer_credit_uses;
CREATE POLICY tenant_isolation ON customer_credit_uses USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());
GRANT SELECT, INSERT ON customer_credit_uses TO techdeva_app;
COMMIT;
