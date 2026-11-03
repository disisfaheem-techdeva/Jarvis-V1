# White-label partner network

TechDeva → **partners** (agencies, TechDeva's direct customers) → **partner clients**
(businesses the partner brings) → each client's own isolated workspace.

Phase 1 (`db/87`, deployed 3 Nov 2026) added partners, partner logins and the
partner level of row-level security. Phase 2 (`db/88`, this document) adds the
money, the automatic set-up, client provisioning, pricing and the two partner
screens.

## Deploy

```bash
cd /opt/techdeva-platform/techdeva-platform && bash ops/deploy.sh feat/partner-layer
```

`ops/deploy.sh` stops before changing anything unless the tree is clean, the
branch fast-forwards from what is deployed, and a database backup was written.
It applies only new migrations (each must print `verify ok`), rebuilds only the
services whose code changed (**api and worker** this time: the scheduler, which
splits payments and retries set-ups, runs in the worker), waits for `/health`,
compares businesses/leads/users/appointments/payments before and after, and
prints the rollback command.

Settings (all optional; listed in `docker-compose.yml` for api and worker, because
a value only in `.env` never reaches the process):

| Variable | Default | What |
|---|---|---|
| `PARTNER_DEFAULT_SHARE_PCT` | `55` | the partner's share on standard terms |
| `PARTNER_AUTO_PAYOUTS` | `true` | gather each partner's reviewed earnings into a payout once a day |
| `PARTNER_SITE_BASE` | `OAUTH_REDIRECT_BASE` | public origin for partner sites and links, e.g. `https://portal.techdeva.net` |
| `PARTNER_DOMAIN_TARGETS` | — | the server's public IP(s); a partner domain is verified when it resolves to one. Without it, verification says it is not configured |

## What happens, automatically

**An agency applies** (`POST /partner/apply`, public) → `pending`, nothing set up.

**TechDeva approves** (`POST /techdeva/partners/{id}/approve`) → a provisioning run,
six idempotent steps, each with attempts and its last error:
`revenue_terms` (55/45) → `branding` → `price_books` (one per TechDeva plan with a
USD price, ready to sell) → `signup_link` → `owner_login` (invite emailed when mail
is configured) → `website` (published at `/p/{slug}`). A failing step stops the run
and is retried by the scheduler after 2, 4, 8… minutes (max 6 hours, 6 attempts);
done steps are never repeated. `GET …/provisioning` shows every step.

**A partner adds a client** (`POST /partner/clients`) or **a business registers
through a partner link** (`/p/{slug}/join/{token}`) → the platform's own signup
provisioning (`persistence.provision_tenant_from_signup`) with `partner_id` in the
same INSERT; then the subscription (price, coupon), `client_accounts` (fee and plan,
so `/billing` shows the real amount and limits follow the package), the partner's
trial length on `tenants.trial_ends_at`, and a branded invitation or branded
sign-in code.

**A client pays** — recorded in `client_payments` exactly as for direct clients
(founder confirms the payment claim). The scheduler splits it into `partner_ledger`.

## Money rules

- The split is the partner's terms **in force on the payment date**, frozen into the
  row. A renegotiation is a new `partner_revenue_terms` row; history never moves.
- `min_techdeva_amount` (+ currency): TechDeva never takes less from one payment, so a
  deep partner discount cannot make a client cost TechDeva money. In another currency
  it is not converted — the row is held for review.
- `fee_bearer`: `shared` (fee comes off before the split), `partner`, or `techdeva`.
- `partner_amount + techdeva_amount = net`, to the cent, enforced by a constraint.
- Refunds and chargebacks (`POST /techdeva/partner-ledger/{id}/reverse`) are new
  negative rows at the original payment's terms (its real ratio if a floor applied).
  More than was paid cannot be reversed. A chargeback fee is a cost on top.
- Only payments made **after** a business joined the partner are split
  (`tenants.partner_assigned_at`).
- No terms on file, or a currency mismatch → recorded, `needs_review`, never paid out
  until cleared (`…/clear-review` with a note).
- Payouts: once a day, one per partner and currency, of reviewed unpaid earnings.
  `paid` needs the transfer reference; `failed` needs a reason and releases its rows
  into the next payout.

**Payouts are statements, not transfers.** Moving the money needs a payment provider
and a TechDeva entity in a country that provider serves (see "Still open").

## Who can do what (enforced in Postgres)

| | partner owner | partner staff | business owner | founder |
|---|---|---|---|---|
| own clients' list, subscriptions, ledger, payouts | read | read | — | all |
| a client's leads, chats, bookings, integrations | **no** | **no** | own only | all |
| terms, ledger, payouts, domain status | read | read | — | write |
| branding, price books, coupons, sign-up links | write | read | — | write |
| create a client | yes | yes | — | yes |
| another partner's anything | **no** | **no** | **no** | all |

A business owner sees its own subscription row and nothing about its agency.

## Screens

- `/partner` — the partner console (TechDeva-branded: the partner is TechDeva's
  customer): overview, clients, packages and coupons, sign-up links, brand and
  website, money.
- `/p/{slug}` — the partner's website, sign-up and welcome pages. One template for
  every partner; nothing on it, in its data or in its emails names TechDeva.

## Evidence (scratch Postgres 16, `techdeva_app` without BYPASSRLS, 3 Nov 2026)

- Migrations `00`→`88` apply clean from empty; `db/88` twice is harmless.
- `tests/test_partner_network.py` 20 + `tests/test_partners.py` 11 = **31 passed**.
- Full suite **185 passed**, 26 failed, 35 errors; the same failures and errors exist
  on `22c70ef` without this work (no AI key, Meta stubbed on scratch). No new failure.
- Browser (Chromium, axe-core 4.10 WCAG 2.2 AA): partner site 20 checks and partner
  console 28 checks at 1440/390 px, dark and light — no violations, no sideways
  scroll, 44 px targets, no "TechDeva" on the partner site. Self-registration run end
  to end in the browser.
- Defects found by these checks and fixed before commit: chargeback fee sign; a
  self-registered client who could never sign in; a page helper that would have
  crash-looped the API at import; `hidden` losing to `.td-btn` (a "Sign out" shown
  while signed out); "wrong password" shown as "sign in again".

## Still open

| | Why | Who |
|---|---|---|
| Real money movement (gateway, automatic transfers) | needs a TechDeva entity a provider serves and a provider choice | Faheem |
| Partner custom domains: TLS + routing | verification is built; Traefik routing per domain is host work (like `ops/setup/sync_subdomains.sh`) | server |
| Branded business dashboard and login | a partner's client still signs in at `/dashboard`, which is TechDeva-branded | next phase |
| Branded email sending domain | mail shows the partner's name, but the address is `MAIL_FROM` until the partner's domain has SPF/DKIM | per partner |
| Meta consent screen says TechDeva | Meta shows the app owner; per-partner apps need their own review | decision |
| Partner-scoped support | reuse the complaints engine with `partner_id` | next phase |
| Partner acquisition CRM | use TechDeva's own CRM as a tenant — no second CRM | next phase |
| `/assets/td.css` contains `.page-techdeva` selectors | one shared stylesheet; visible only to someone reading the CSS | low |
