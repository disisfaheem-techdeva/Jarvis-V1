-- 92: each login's own dashboard theme (6 Nov 2026), like Windows > Themes.
-- A look only (wallpaper, accent, light/dark); NULL = "Business default".
-- Additive. Rollback: ALTER TABLE users DROP COLUMN ui_theme;
BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ui_theme text CHECK (ui_theme IS NULL OR length(ui_theme) <= 40);
DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'ui_theme') THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - users.ui_theme missing';
  END IF;
  RAISE NOTICE 'OK: users.ui_theme present.';
END
$verify$;
COMMIT;
