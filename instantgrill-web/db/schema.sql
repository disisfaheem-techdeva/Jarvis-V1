-- Instant Grill orders. Used when DATABASE_URL is set (see src/lib/store/postgres.ts).
-- The full order is a JSONB document; the fields the admin filters on are real columns.
CREATE TABLE IF NOT EXISTS orders (
  id          text PRIMARY KEY,                 -- IG-XXXXXX
  created_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  status      text NOT NULL CHECK (status IN ('new','confirmed','preparing','out_for_delivery','delivered','cancelled')),
  phone       text NOT NULL,                    -- normalised 03XXXXXXXXX
  area        text NOT NULL,                    -- Lahore delivery area
  slot_date   date NOT NULL,                    -- delivery day (Asia/Karachi)
  total       integer NOT NULL CHECK (total >= 0), -- whole rupees
  doc         jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS orders_status_created_idx ON orders (status, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_slot_idx ON orders (slot_date);     -- kitchen prep list per day
CREATE INDEX IF NOT EXISTS orders_phone_idx ON orders (phone);        -- repeat customers
