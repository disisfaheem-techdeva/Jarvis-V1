# TechDeva platform — rules for Claude working on this server

You are working on the LIVE production server of TechDeva (72.61.203.194), usually
because Faheem asked through his desktop assistant Jarvis. Real businesses and a
live clinic depend on this machine. There is no staging copy.

## Map
- This folder: the multi-tenant platform. `app/` = FastAPI API (container
  techdeva-platform-api-1, built from app/), `web/` = static dashboards
  (bind-mounted, live instantly: dashboard.html = client dashboard,
  techdeva.html = founder console), `techdeva-web/` = Next.js portal
  (container techdeva-platform-web-1), `db/` = numbered SQL migrations.
- Database: Postgres 16 in techdeva-platform-db-1, database `techdeva`.
  Every tenant table uses forced Row-Level Security (tenant_isolation policy).
  The app connects as `techdeva_app`, which must NEVER have BYPASSRLS.
- Code talks to the DB only through db.tenant_session(tenant_id, role).
- Audit: every meaningful write calls audit.record(...).
- NOT this folder, do not touch unless explicitly asked: the clinic CRM
  (/opt/beauty-dashboard, crm.techdeva.net) and the Hermes/Zara WhatsApp
  containers (hermes-workspace-ofsd-*). They serve paying customers.

## Rules that do not bend
1. Protect the clinic and Hermes above everything. If a change might affect
   them, stop and say so instead.
2. Before changing anything: `git status` must be clean, or commit what is
   there first. Make the smallest change that fixes the problem.
3. Never read out, print, copy or commit secrets: .env*, *.key, tokens,
   passwords. Never change the database role's privileges.
4. Never delete or rewrite real business data. Test with a throwaway tenant
   and delete everything it created afterwards; prove the counts are back.
5. After a code change: rebuild ONLY the affected service
   (`docker compose build api && docker compose up -d api`, or `web`),
   then prove it: /health 200, and the specific behaviour you changed,
   called for real. A file that compiles is not a fix.
6. Never say "fixed" or "done" without that evidence. Say what you could
   not verify.
7. Finish with a local commit (`git commit -m "<what and why>"`) — never
   push anywhere — and report: what was wrong, what changed
   (`git diff --stat HEAD~1`), how you verified it, and anything Faheem
   must decide.
8. Money, pricing, who-sees-what, or anything a client's customers will
   see: propose it and stop. Faheem decides.

## Any UI, page or dashboard work

Every TechDeva screen, existing or new, for TechDeva or for a tenant, uses the
design system in [`design-system/`](design-system/README.md). Read that README
before building or changing a screen. In short:

- Use tokens only (`design-system/tokens.css`): no raw hex, no one-off fonts,
  radii or shadows. Change a token there, then run `python3 design-system/sync.py`.
  Never edit the generated copies.
- Next.js screens (`techdeva-web/`) are built from
  `techdeva-web/src/components/ui` (see `techdeva-web/DESIGN.md`).
- Pages served by FastAPI (`web/`) are Tailwind too: utilities in the markup,
  JS-rendered classes via `@apply` in `web/styles/<page>.css`, one built
  stylesheet at `/assets/td.css` (committed, so `web/` stays live instantly
  after a pull; the API must be restarted once for that route to exist).
- Honest data: a missing figure shows "—" with a reason, never 0. Loading,
  empty, error, signed-out and unreachable states are different screens.
- WCAG 2.2 AA, 44px touch targets, no sideways scroll at 390px, and
  reduced-motion support.
- `python3 design-system/sync.py --check` must pass after any UI change, as
  must `npm run lint` and `npm run build` in `techdeva-web/`.

The `/techdeva-design-system` skill in `.claude/skills/` loads these rules
automatically for UI tasks.
