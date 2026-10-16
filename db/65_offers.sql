-- 65_offers.sql
-- Offers: a discount the business runs on everything, one service, one product
-- or one package, optionally inside a date window. Read and written through the
-- generic /business/offers router (app/business.py), owner only.
--
-- Nothing here publishes or applies an offer anywhere: it is the owner's list.
-- Additive only; forced RLS with the same tenant_isolation policy as
-- 14_business_layer.sql. Grants come from 00_role.sql's default privileges.
BEGIN;

CREATE TABLE IF NOT EXISTS offers (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title          text NOT NULL CHECK (length(btrim(title)) > 0),
    description    text,
    discount_type  text NOT NULL CHECK (discount_type IN ('percent','amount')),
    discount_value numeric NOT NULL CHECK (discount_value > 0),
    applies_to     text NOT NULL DEFAULT 'all'
                   CHECK (applies_to IN ('all','service','product','package')),
    item_id        uuid,
    starts_on      date,
    ends_on        date,
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz DEFAULT now(),
    updated_at     timestamptz DEFAULT now(),
    CONSTRAINT offers_window CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on)
);
CREATE INDEX IF NOT EXISTS offers_tenant_idx ON offers (tenant_id, active, ends_on);

ALTER TABLE offers ENABLE ROW LEVEL SECURITY;
ALTER TABLE offers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON offers;
CREATE POLICY tenant_isolation ON offers FOR ALL
    USING (is_admin() OR tenant_id = current_tenant())
    WITH CHECK (is_admin() OR tenant_id = current_tenant());

COMMIT;

DO $verify$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class c
                    WHERE c.relname = 'offers' AND c.relrowsecurity AND c.relforcerowsecurity
                      AND EXISTS (SELECT 1 FROM pg_policy p
                                   WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation')) THEN
        RAISE EXCEPTION 'MIGRATION UNSAFE - RLS or tenant_isolation missing on offers';
    END IF;
    IF NOT has_table_privilege('techdeva_app', 'offers', 'SELECT, INSERT, UPDATE, DELETE') THEN
        RAISE EXCEPTION 'MIGRATION INCOMPLETE - techdeva_app cannot use offers';
    END IF;
END
$verify$;
