# White-label partners — what was built, and how to deploy it

Phase 1 of the partner network (1 Nov 2026): an agency can be approved, given
wholesale terms and logins, be handed the businesses it brought, and sign in to
see them. Branding, custom domains, wholesale invoicing and the partner
dashboard screens are **not** in this phase.

## What a partner can and cannot do

| | |
|---|---|
| Sees | its own row, its own wholesale terms, and the **list** of businesses it brought (name, industry, status, address, trial end) |
| Does **not** see | anything inside a customer — no lead, conversation, message, appointment, invoice, revenue row or connected ad account. Not another agency's businesses, logins or rates. Not test businesses. |
| Cannot | create, rename, delete or take over a business. Cannot reach any other route in the platform: `resolve_scope` refuses a partner token outright. |

This is enforced in Postgres, not in Python. A partner session sets
`app.partner_id` and leaves `app.tenant_id` empty, so `is_admin()` is false and
`current_tenant()` is NULL — every tenant-scoped policy therefore matches no
rows at all. `app/db.py` makes "both at once" unreachable rather than a rule
somebody has to remember.

The existing `tenant_self` policy on `tenants` is **not** modified. A second,
SELECT-only policy sits beside it, and Postgres ORs permissive policies. Every
business keeps exactly the isolation it has today, and the migration refuses to
finish if `tenant_self` has gone missing.

## Files

| File | What |
|---|---|
| `db/87_partners.sql` | `partners`, `partner_agreements`, `partner_users`, `tenants.partner_id`, `current_partner()`, the policies |
| `app/partners.py` | founder routes (`/techdeva/partners/...`) and partner routes (`/partner/...`) |
| `app/db.py` | `tenant_session(..., partner_id=)` sets `app.partner_id`; partner and tenant can never both be set |
| `app/auth.py` | `issue_token(role="partner", partner_id=...)`, the `p` claim, and `resolve_scope` refusing partner tokens |
| `app/main.py` | mounts the router |
| `tests/test_partners.py` | 11 tests |

## Routes

Founder (`techdeva_admin` only):

```
POST /techdeva/partners                      {name, slug, country, contact_*}
GET  /techdeva/partners                      list + client counts + terms
POST /techdeva/partners/{id}/status          {status: pending|active|suspended}
POST /techdeva/partners/{id}/agreement       {plan_key, wholesale_price, currency, upside_share_pct, effective_from}
POST /techdeva/partners/{id}/users           {email, name, role} -> set_password_token
POST /techdeva/partners/{id}/clients         {tenant_id, reassign?}
```

Partner:

```
POST /partner/login                          {email, password}  -> td_partner cookie
POST /partner/set-password                   {token, password}
GET  /partner/me
GET  /partner/clients
```

Two decisions worth knowing:

* **A new wholesale rate is a new row**, never an edit. What a partner was
  charged last month stays readable next year.
* **A business already belonging to another agency is not moved** unless the
  caller sends `reassign: true`. Who owns a customer is what ends up in a
  dispute, so it never happens by accident, and the move is audited with the
  previous partner recorded.

## Evidence (scratch copy, 1 Nov 2026)

Postgres 16.15, the app's own `techdeva_app` role (`rolsuper=f`,
`rolbypassrls=f`), forced RLS, the real API through `TestClient`.

* Every migration `00` → `87` applied in order from empty, clean.
  `db/87` printed `verify ok`.
* A hand-written SQL proof as `techdeva_app`: **20 checks** — a partner sees one
  business and not the other agency's; reads zero leads, conversations,
  appointments and integrations; sees only its own agreements; cannot insert a
  business (`insufficient_privilege`), cannot rename one, cannot take over
  another agency's; a direct client and a partner's customer each still see only
  themselves; a partner session with no partner id sees nothing.
* `pytest tests/test_partners.py` → **11 passed**.
* `pytest tests` → **165 passed**, 26 failed, 1 skipped, 35 errors.
  The same run on the unmodified commit `22c70ef` gives **154 passed, 26 failed,
  1 skipped, 35 errors** — identical failures and errors, +11 passes. Nothing
  here broke anything. Those pre-existing failures are this scratch
  environment (no AI key, Meta stubbed, missing seed), the same ones
  `docs/SYSTEM-AUDIT-2026-10-24.md` records.
* The test run leaves no rows behind: partners, agreements and
  `tenants.partner_id` counts all back to 0 afterwards.

**Not verified, and cannot be from a sandbox:** production. Nothing was
deployed, no production migration was run, the live server was never reached
(this environment's network policy denies `api.techdeva.net`). Also untested:
`psycopg` is pinned at `3.2.1` in `app/requirements.txt` and that build is
unavailable for this Python, so `3.2.2` was used locally.

## Deploy (server Claude)

1. `git status` must be clean. Back up the database.
2. Apply **`db/87_partners.sql`** — it is additive and prints `verify ok`.
   It refuses to finish if any existing business already carries a `partner_id`
   or if `tenant_self` has been removed.
3. Rebuild **api only**: `docker compose build api && docker compose up -d api`.
   The worker is untouched by this change.
4. Check:
   * `/health` returns 200;
   * `GET /techdeva/partners` as the founder returns `{"partners": []}`;
   * the clinic's lead count is unchanged, its dashboard opens, Hermes containers
     are still running, Instant Grill is untouched;
   * `SELECT count(*) FROM tenants WHERE partner_id IS NOT NULL` is **0** —
     no existing business was handed to anybody.
5. `pytest tests` on the server, and compare the numbers against the run before
   the deploy. The difference must be +11 passes and nothing else.

## Next phases

| | |
|---|---|
| P2 | partner branding (colours, email identity) and custom domains |
| P3 | wholesale invoicing: active customers × the rate in force, per month |
| P4 | the partner dashboard screens (design system) |
| P5 | partner-scoped support, reusing the complaints engine |

Open commercial decisions that shape P3: which legal entity invoices the
partner, and whether the partner collects from its own customers (recommended —
it removes payment facilitation entirely) or TechDeva collects and pays out.
