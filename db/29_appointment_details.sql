-- 29_appointment_details.sql
-- The fields the Beauty clinic's appointment form records, so every client's
-- appointment form matches it. Money stays on the invoice (one source of
-- truth); the appointment points at its bill.
BEGIN;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS department       text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booked_by        text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS assisted_by      text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS procedure_by     text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_method   text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS total_sessions   integer NOT NULL DEFAULT 1;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS next_appointment date;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS invoice_id       uuid REFERENCES invoices(id) ON DELETE SET NULL;
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_sessions_ck;
ALTER TABLE appointments ADD CONSTRAINT appointments_sessions_ck CHECK (total_sessions BETWEEN 1 AND 100);
CREATE INDEX IF NOT EXISTS appointments_invoice_idx ON appointments (invoice_id);
CREATE INDEX IF NOT EXISTS appointments_starts_idx ON appointments (tenant_id, starts_at);
COMMIT;
