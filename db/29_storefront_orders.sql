-- ============================================================================
-- 29_storefront_orders.sql · Online orders from a tenant's own website
--
-- Generic, not tenant-specific: any client with a storefront (Instant Grill is
-- the first) pushes signed webhook events to /storefront/webhook/{tenant_id}.
-- The webhook secret lives in `integrations` (provider = 'storefront'),
-- encrypted with app/crypto.py like every other credential.
--
-- Same multi-tenant convention as every tenant table:
--   tenant_id NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
--   ENABLE + FORCE ROW LEVEL SECURITY, policy tenant_isolation
-- ============================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS storefront_orders (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id       text NOT NULL,                 -- the site's order number, e.g. IG-7KQ2MX
    status            text NOT NULL CHECK (status IN
                        ('new','confirmed','preparing','out_for_delivery','delivered','cancelled')),
    customer_name     text NOT NULL,
    customer_phone    text NOT NULL,
    area              text,
    address           text,
    landmark          text,
    notes             text,
    slot_date         date,                          -- delivery day (tenant local time)
    slot              text,                          -- 'morning' or 'HH:MM'
    subtotal          integer NOT NULL CHECK (subtotal >= 0),
    delivery_fee      integer CHECK (delivery_fee >= 0),   -- NULL = confirmed on the call
    total             integer NOT NULL CHECK (total >= 0),
    items             jsonb NOT NULL DEFAULT '[]',   -- [{slug,name,unitPrice,qty,lineTotal,chutney,salad}]
    source            jsonb NOT NULL DEFAULT '{}',   -- {utmSource, utmCampaign, referrer}
    rider             text,
    history           jsonb NOT NULL DEFAULT '[]',   -- [{status, at}]
    placed_at         timestamptz NOT NULL,
    site_updated_at   timestamptz NOT NULL,          -- last change on the site; guards out-of-order events
    received_at       timestamptz NOT NULL DEFAULT now(),
    revenue_event_id  uuid REFERENCES revenue_events(id) ON DELETE SET NULL,  -- set once, on delivery
    UNIQUE (tenant_id, external_id)
);
CREATE INDEX IF NOT EXISTS storefront_orders_placed_idx ON storefront_orders (tenant_id, placed_at DESC);
CREATE INDEX IF NOT EXISTS storefront_orders_slot_idx   ON storefront_orders (tenant_id, slot_date);
CREATE INDEX IF NOT EXISTS storefront_orders_status_idx ON storefront_orders (tenant_id, status);
CREATE INDEX IF NOT EXISTS storefront_orders_phone_idx  ON storefront_orders (tenant_id, customer_phone);

-- Catering/bulk enquiries and contact-form messages from the site.
CREATE TABLE IF NOT EXISTS storefront_inbox (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id   text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('enquiry','contact')),
    name          text NOT NULL,
    phone         text,
    email         text,
    payload       jsonb NOT NULL DEFAULT '{}',    -- type, date, people, area, message ...
    created_at    timestamptz NOT NULL,           -- when the visitor sent it
    received_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, external_id)
);
CREATE INDEX IF NOT EXISTS storefront_inbox_created_idx ON storefront_inbox (tenant_id, created_at DESC);

DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['storefront_orders', 'storefront_inbox'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', tbl);
    EXECUTE format($f$
      DROP POLICY IF EXISTS tenant_isolation ON %I;
      CREATE POLICY tenant_isolation ON %I
        USING (is_admin() OR tenant_id = current_tenant())
        WITH CHECK (is_admin() OR tenant_id = current_tenant());
    $f$, tbl, tbl);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'techdeva_app') THEN
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app;', tbl);
    END IF;
  END LOOP;
END $$;

COMMIT;
