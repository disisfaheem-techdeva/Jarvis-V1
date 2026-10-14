-- Instant Grill orders. Used when DATABASE_URL is set (see src/lib/store/postgres.ts).
-- The full order is a JSONB document; the fields the admin filters on are real columns.
CREATE TABLE IF NOT EXISTS orders (
  id          text PRIMARY KEY,                 -- IG-XXXXXX
  created_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  status      text NOT NULL CHECK (status IN ('pending','confirmed','shipped','delivered','cancelled','returned')),
  phone       text NOT NULL,                    -- normalised 03XXXXXXXXX
  city        text NOT NULL,
  total       integer NOT NULL CHECK (total >= 0), -- whole rupees
  doc         jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS orders_status_created_idx ON orders (status, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC);
CREATE INDEX IF NOT EXISTS orders_phone_idx ON orders (phone);  -- repeat-customer and fraud checks
