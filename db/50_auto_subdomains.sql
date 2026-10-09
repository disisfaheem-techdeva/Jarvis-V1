-- 50_auto_subdomains.sql
-- Every business gets its own address automatically (Faheem, 2 Oct 2026):
-- new signups in code (app/subdomains.py); here, every existing non-test
-- business that has none. Same rule as the app: name from the business
-- name, a number added when reserved or taken. Only fills empty addresses.
BEGIN;
DO $fill$
DECLARE r record; base text; cand text; n int;
BEGIN
  FOR r IN SELECT id, name FROM tenants WHERE subdomain IS NULL AND NOT coalesce(is_test, false) ORDER BY created_at LOOP
    base := trim(both '-' from regexp_replace(lower(coalesce(r.name, '')), '[^a-z0-9]+', '-', 'g'));
    base := trim(both '-' from left(regexp_replace(base, '^[^a-z]+', ''), 28));
    IF length(base) < 3 THEN base := CASE WHEN base = '' THEN 'client' ELSE base || '-biz' END; END IF;
    cand := base; n := 1;
    WHILE cand IN ('www','api','portal','crm','mail','admin','app','dashboard','login','pin','techdeva','founder',
                   'status','static','cdn','ftp','smtp','webmail','cpanel')
          OR EXISTS (SELECT 1 FROM tenants WHERE subdomain = cand)
          OR cand !~ '^[a-z][a-z0-9-]{1,30}[a-z0-9]$' LOOP
      n := n + 1; cand := left(base, 28) || '-' || n;
    END LOOP;
    UPDATE tenants SET subdomain = cand WHERE id = r.id;
    RAISE NOTICE 'address % for %', cand, r.name;
  END LOOP;
END
$fill$;
COMMIT;
