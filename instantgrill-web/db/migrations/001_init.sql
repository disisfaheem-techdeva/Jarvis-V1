-- Instant Grill schema. Run in the Supabase SQL editor (or psql) in order.
-- The website talks to Postgres from the server only, with the DATABASE_URL
-- connection (a privileged role). Row-level security is ON with no policies,
-- so Supabase's public anon/authenticated API keys can read or write nothing.

CREATE TABLE IF NOT EXISTS orders (
  id          text PRIMARY KEY,                 -- IG-XXXXXX
  created_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  status      text NOT NULL CHECK (status IN ('new','confirmed','preparing','out_for_delivery','delivered','cancelled')),
  phone       text NOT NULL,                    -- normalised 03XXXXXXXXX
  area        text NOT NULL,
  slot_date   date NOT NULL,                    -- delivery day (Asia/Karachi)
  total       integer NOT NULL CHECK (total >= 0),
  doc         jsonb NOT NULL                    -- full order document
);
CREATE INDEX IF NOT EXISTS orders_status_created_idx ON orders (status, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_slot_idx ON orders (slot_date);
CREATE INDEX IF NOT EXISTS orders_phone_idx ON orders (phone);

-- Customers are derived from orders, so they can never drift out of sync.
CREATE OR REPLACE VIEW customers AS
  SELECT phone,
         (array_agg(doc->'customer'->>'name' ORDER BY created_at DESC))[1] AS name,
         count(*)::int AS orders_count,
         max(created_at) AS last_order_at
  FROM orders
  WHERE status <> 'cancelled'
  GROUP BY phone;

CREATE TABLE IF NOT EXISTS enquiries (
  id          text PRIMARY KEY,
  created_at  timestamptz NOT NULL,
  doc         jsonb NOT NULL                    -- type, name, phone, date, people, area, message
);

CREATE TABLE IF NOT EXISTS contact_messages (
  id          text PRIMARY KEY,
  created_at  timestamptz NOT NULL,
  doc         jsonb NOT NULL                    -- name, phone, email, message
);

-- Owner edits on top of the menu in src/lib/catalog.ts (NULL = keep catalog value).
CREATE TABLE IF NOT EXISTS product_overrides (
  slug        text PRIMARY KEY,
  price       integer CHECK (price > 0),
  sold_out    boolean,
  active      boolean,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS delivery_areas (
  name        text PRIMARY KEY,
  fee         integer CHECK (fee >= 0),         -- NULL = confirmed on the call
  active      boolean NOT NULL DEFAULT true,
  sort        integer NOT NULL DEFAULT 0
);

ALTER TABLE orders            ENABLE ROW LEVEL SECURITY;
ALTER TABLE enquiries         ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_messages  ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_areas    ENABLE ROW LEVEL SECURITY;
-- The view runs with the caller's rights, so RLS on orders protects it too.
ALTER VIEW customers SET (security_invoker = true);
-- Supabase's public API roles get nothing (skipped on plain Postgres, where they do not exist).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON orders, enquiries, contact_messages, product_overrides, delivery_areas, customers FROM anon, authenticated;
  END IF;
END $$;
