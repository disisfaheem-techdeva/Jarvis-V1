#!/bin/bash
# Read-only: is anything from one business showing up in another?
#  1. the founder account(s) and the business each is attached to (should be none)
#  2. which business is TechDeva's own ("home")
#  3. per business: staff, logins, services, TechDeva work and sales rows
#  4. the founder's last 25 actions and the business each one was saved under
# Names and counts only; no passwords, tokens, phone numbers or messages.
cd "$(dirname "$0")/../.." || exit 1
Q() { docker compose exec -T db psql -U postgres -d techdeva -P pager=off -c "$1"; }
echo "== 1. founder accounts (business should be EMPTY)"
Q "SELECT u.email, coalesce(t.name, '') AS attached_business FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id WHERE u.role = 'techdeva_admin'"
echo "== 2. TechDeva's own business (home)"
Q "SELECT t.name, t.id FROM platform_settings p JOIN tenants t ON t.id::text = p.value WHERE p.key = 'home_tenant_id'"
echo "== 3. rows per business"
Q "SELECT t.name, t.is_test AS test,
   (SELECT count(*) FROM staff s WHERE s.tenant_id = t.id) AS staff,
   (SELECT count(*) FROM users x WHERE x.tenant_id = t.id) AS logins,
   (SELECT count(*) FROM services x WHERE x.tenant_id = t.id) AS services,
   (SELECT count(*) FROM td_catalog x WHERE x.tenant_id = t.id) AS td_catalog,
   (SELECT count(*) FROM td_tasks x WHERE x.tenant_id = t.id) AS td_tasks,
   (SELECT count(*) FROM sales_leads x WHERE x.tenant_id = t.id) AS sales_leads,
   (SELECT count(*) FROM social_posts x WHERE x.tenant_id = t.id) AS posts
   FROM tenants t ORDER BY t.is_test, t.name"
echo "== 4. founder's last 25 actions and where they were saved"
Q "SELECT to_char(e.at, 'DD Mon HH24:MI') AS at, e.action, coalesce(t.name, '-') AS saved_under
   FROM executions e LEFT JOIN tenants t ON t.id = e.tenant_id
   WHERE e.actor IN (SELECT id::text FROM users WHERE role = 'techdeva_admin')
   ORDER BY e.at DESC LIMIT 25"
