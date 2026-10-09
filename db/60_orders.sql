-- 60: Orders, the commerce layer (5 Oct 2026)
--
-- Booking = something with a time (treatment, haircut, table, class): appointments.
-- Order   = something sold (cream, clothes, food delivery, a camera): orders.
-- A business can have both.
--
-- Lifecycle:  new -> confirmed -> out_for_delivery -> delivered      (or cancelled)
-- Payment is separate and follows the money, through the existing invoices and
-- payments tables: an order gets an invoice when it is delivered or when the
-- first payment is taken, so Revenue, Daily Closing and P&L see orders exactly
-- as they see everything else, with no second ledger.
--
-- Recognised sale = delivered AND fully paid (recognised_at is set then, and
-- only then is stock taken out). An AI agent can only ever create an order in
-- 'new': confirming, delivering and taking payment are human steps.
--
-- Additive only. Forced RLS like every tenant table.
BEGIN;

CREATE TABLE IF NOT EXISTS orders (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  number           text NOT NULL,
  customer_id      uuid REFERENCES customers(id) ON DELETE SET NULL,
  customer_name    text NOT NULL DEFAULT '' CHECK (length(customer_name) <= 120),
  customer_phone   text NOT NULL DEFAULT '' CHECK (length(customer_phone) <= 40),
  status           text NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','confirmed','out_for_delivery','delivered','cancelled')),
  fulfilment       text NOT NULL DEFAULT 'delivery' CHECK (fulfilment IN ('delivery','pickup')),
  address          text NOT NULL DEFAULT '' CHECK (length(address) <= 500),
  subtotal         numeric(14,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
  delivery_fee     numeric(14,2) NOT NULL DEFAULT 0 CHECK (delivery_fee >= 0),
  discount         numeric(14,2) NOT NULL DEFAULT 0 CHECK (discount >= 0),
  total            numeric(14,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
  source           text NOT NULL DEFAULT 'dashboard'
                   CHECK (source IN ('whatsapp_ai','website','facebook','instagram','manual','dashboard','api')),
  source_ref       text,                          -- e.g. whatsapp chat + message; one order per ref
  agent_key        text,
  chat_session_id  text,
  invoice_id       uuid REFERENCES invoices(id) ON DELETE SET NULL,
  notes            text NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
  cancel_reason    text,
  created_by       text,                          -- user id, or the agent's name
  created_by_type  text NOT NULL DEFAULT 'human' CHECK (created_by_type IN ('human','agent','system')),
  confirmed_at     timestamptz,
  dispatched_at    timestamptz,
  delivered_at     timestamptz,
  cancelled_at     timestamptz,
  recognised_at    timestamptz,                   -- delivered + fully paid: counted as a sale
  stock_deducted_at timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT orders_cancel_has_reason CHECK (status <> 'cancelled' OR cancel_reason IS NOT NULL),
  CONSTRAINT orders_recognised_needs_delivery CHECK (recognised_at IS NULL OR status = 'delivered')
);
CREATE UNIQUE INDEX IF NOT EXISTS orders_tenant_number_uniq ON orders (tenant_id, number);
CREATE UNIQUE INDEX IF NOT EXISTS orders_tenant_source_ref_uniq ON orders (tenant_id, source_ref) WHERE source_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS orders_tenant_status ON orders (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_tenant_created ON orders (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_invoice ON orders (invoice_id);

CREATE TABLE IF NOT EXISTS order_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id  uuid REFERENCES products(id) ON DELETE SET NULL,
  service_id  uuid REFERENCES services(id) ON DELETE SET NULL,
  -- copied when ordered: a later price change never rewrites this order
  description text NOT NULL CHECK (length(btrim(description)) > 0),
  quantity    numeric(12,2) NOT NULL DEFAULT 1 CHECK (quantity > 0),
  unit_price  numeric(14,2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
  line_total  numeric(14,2) NOT NULL DEFAULT 0 CHECK (line_total >= 0)
);
CREATE INDEX IF NOT EXISTS order_items_order ON order_items (order_id);

-- the order's timeline: who did what, when (also mirrored in executions)
CREATE TABLE IF NOT EXISTS order_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text,
  actor_type  text NOT NULL DEFAULT 'human' CHECK (actor_type IN ('human','agent','system')),
  event       text NOT NULL,
  detail      text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS order_events_order ON order_events (order_id, at);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['orders','order_items','order_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

-- ---- a business type for shops that sell products ----
INSERT INTO business_categories (slug, name, blurb, icon, sort_order, labels, dashboard_widgets, pipeline_stages, agent_menu, agent_rules, intake_fields) VALUES
('shop', 'Shop & products', 'Electronics, CCTV, mobiles, clothing, cosmetics, any retail', '🛍️', 60,
 '{"service":"Product","services":"Products","booking":"Visit","bookings":"Visits","customer":"Customer","customers":"Customers"}',
 '["orders_new","orders_waiting","sales_today","new_leads","whatsapp_chats","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Order placed","showed":"Delivered","sold":"Repeat customer","lost":"Lost","fake":"Spam"}',
 '["Our products","Prices","Place an order","Delivery","Location"]',
 'You serve a shop that sells products. Talk only about the products below, at the prices below. Customers ORDER products: never call a product a service and never ask for an appointment day or time for a product. To take an order, ask for the product (from the list only), the quantity, the customer''s name, and whether they want delivery (then the full address) or pickup. Then send the order summary exactly as described under "Taking an order". If asked for a product that is not listed, say plainly the shop does not sell it and suggest a listed one. For stock, delivery time or warranty you have not been told, say the team will confirm.',
 '["Products with prices","Delivery: areas, charge, how long it takes","Pickup: yes or no, and where","Payment methods (cash on delivery, bank, JazzCash, EasyPaisa)","Warranty or returns","Location","What you do NOT sell"]')
ON CONFLICT (slug) DO NOTHING;

COMMIT;
