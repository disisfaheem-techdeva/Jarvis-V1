# Countries (Phase 10, 9 Nov 2026)

TechDeva started in Pakistan. This page says what Phase 10 added so a business
can run in another country, and lists what in the code **still assumes Pakistan**.
Each item below has to be fixed (or deliberately kept) before a business launches
outside Pakistan.

## What was added

| Piece | Where | What it does |
|---|---|---|
| `countries` table | `db/97_countries.sql` | 9 countries: PK, AE, SA, QA, OM, GB, CA, US, AU. For each: currency, symbol, time zones, languages, date format, payment methods, and what the tax is called. Everyone can read it. Only the founder can change it (RLS `is_admin()`). |
| `tenant_config.country` | db/97 | The business's country. Sits beside its own time zone, currency and language (db/66). |
| `td_local_today()` | db/97 | "Today" in the business's own time zone. Asia/Karachi is used when none is set. The udhaar ledger and purchases now use it, so their dates are no longer fixed to Karachi. |
| `GET /countries` | `app/countries.py` | The list. Any signed-in login can read it, staff included (roles `ALWAYS_EXACT`). |
| `GET/PUT /business-profile/country` | `app/countries.py` | Reads or chooses the country. Only the owner (or the founder) can choose. Choosing fills in the currency and time zone. The language is filled in only if none is set. Each choice is audited as `business.country`. Every setting can still be changed on its own under Workspace. |
| Country picker | `web/dashboard.html`, Business Settings | The owner chooses a country, then a time zone within it. |

### Tax is not set

No tax rates are seeded. `tax_rate` stays NULL and `tax_validated` false. The
migration refuses to run if any rate is set without being validated. The API
hides any rate that has not been validated. Nothing reads a country's tax into a
bill: the till keeps using the business's own tax setting.

**Decision for Faheem (deferred):** check each country's tax rules with its tax
authority (or an accountant) before launching there. Then decide whether a
country's rate should become the default for new businesses.

## Still Pakistan-only (inventory, 9 Nov 2026)

These counts come from `grep` on `app/`. They are places, not bugs: each one is
correct for Pakistan and wrong for a business elsewhere.

### 1. Time zone written as Asia/Karachi or UTC+5

The fix for each is the same: use `dashboard._tz(cur)` / `countries.local_today(cur)`
in Python, or `td_local_today()` in SQL.

- `app/pos.py`: `KARACHI_DAY`, plus the till's "today" totals (5 places).
- `app/usage.py` (2): daily usage limits reset at midnight Karachi time.
- `app/orders.py` (2), `app/social.py` (2), `app/autopilot.py` (2, including a fixed `+5h` offset),
  `app/console.py` (3: the Jarvis prompt tells the model to use `+05:00`).
- One place each in: `advisor.py` (also a fixed `PKT` offset), `ai_keys.py`, `aidesign.py`,
  `booking.py`, `business.py`, `crm.py`, `dashboard.py` (the default in `_tz`, which is right),
  `growth.py` (also two fixed `+5h` offsets), `jarvis_actions.py`, `marketing/settings.py`,
  `operations.py`, `portal.py`, `sales.py`, `storefront.py`.
- Fixed `+5h` offsets also in `lead_inbox.py`, `plans.py` and `marketing/campaigns.py`
  (an error message's example time).
- `expenses.spent_on` defaults to the UTC day (reported earlier; not changed).

### 2. Currency written as PKR

- Python defaults: `adpilot.py` (2), `advisor.py`, `billing.py`, `booking.py` (3), `brain.py`,
  `business.py`, `categories.py`, `founder.py`, `marketing/settings.py`, `operations.py` (2).
- Column defaults `DEFAULT 'PKR'` / `'Asia/Karachi'` in `db/14`, `15`, `24`, `26`, `66`, `78`.
  New products, services and invoices are stored as PKR unless told otherwise.
  These should follow `tenant_config.currency`. That is a new migration and needs
  a decision on what happens to rows already stored as PKR (nothing should be
  rewritten).
- `billing.py` and `founder.py` hold **TechDeva's own prices** in PKR. Pricing in
  other currencies is a business decision for Faheem, not a code change.

### 3. "Rs" in words the platform writes

About 35 places. The biggest are `pos.py` (8), `orders.py` (5), and the Jarvis
replies: `jarvis_actions.py` (3), `jarvis_pos.py`, `jarvis_udhaar.py`,
`jarvis_purchase.py`, `jarvis_shop.py`, `ledger.py`, `purchases.py`, `register_import.py`,
`pos_shift.py` (2), `pos_refund.py`, `storefront.py` (2), `categories.py` (2),
`booking.py`, `setup_reader.py`, `agents/guards.py`, `orchestrator/rules.py`.
The dashboard has about 29 more. These should use the country's `currency_symbol`.
Do this in one shared money formatter, not file by file.

### 4. Languages, payments and other settings

- The workspace language picker offers English and Urdu only. Arabic (for the Gulf)
  and French (for Canada) need translations first.
- Payment methods: the till and udhaar know cash, JazzCash, EasyPaisa, bank and
  card. The `payment_methods` column in `countries` is not read by the till yet.
- Date format: dates are shown in the browser's own format. `countries.date_format`
  is not used yet.
- Phone numbers: WhatsApp and lead handling assume `+92` in places (not counted here).
- The Karyana units (seer, maund, pao, dozen) and Urdu product aliases are Pakistani.
  That is correct for the Karyana type.

## Suggested order (when Faheem chooses the first country outside Pakistan)

1. The shared money formatter, using `currency_symbol` (removes most of item 3).
2. Time zones: replace every fixed Asia/Karachi and `+5h` with the business's zone (item 1).
3. Currency column defaults follow the business (item 2; needs a decision on old rows).
4. Validate that country's tax, then turn it on (needs a decision).
5. Languages and payment methods for that country.
