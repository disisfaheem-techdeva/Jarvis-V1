-- 28_team_attendance.sql
-- HR details on staff, and a daily attendance register, so every client
-- dashboard has the Team / Attendance / HR sections the Beauty clinic CRM has.
-- Nothing is deleted: a day's attendance is corrected in place, never removed.
BEGIN;

ALTER TABLE staff ADD COLUMN IF NOT EXISTS salary            numeric(12,2);
ALTER TABLE staff ADD COLUMN IF NOT EXISTS joining_date      date;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS emergency_contact text;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS address           text;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS hr_notes          text;
ALTER TABLE staff DROP CONSTRAINT IF EXISTS staff_salary_ck;
ALTER TABLE staff ADD CONSTRAINT staff_salary_ck CHECK (salary IS NULL OR salary >= 0);

CREATE TABLE IF NOT EXISTS staff_attendance (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  staff_id    uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  day         date NOT NULL,
  status      text NOT NULL,
  check_in    time,
  check_out   time,
  notes       text,
  marked_by   text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_attendance_status_ck CHECK (status IN
    ('present','late','half_day','leave','absent')),
  CONSTRAINT staff_attendance_one_per_day UNIQUE (tenant_id, staff_id, day)
);

CREATE INDEX IF NOT EXISTS staff_attendance_day_idx ON staff_attendance (tenant_id, day);

ALTER TABLE staff_attendance ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_attendance FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON staff_attendance;
CREATE POLICY tenant_isolation ON staff_attendance
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

GRANT SELECT, INSERT, UPDATE ON staff_attendance TO techdeva_app;

COMMIT;
