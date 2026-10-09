# TechDeva platform — working notes for Claude

## Any UI, page or dashboard work

Every TechDeva screen, existing or new, for TechDeva or for a tenant, uses the
design system in [`design-system/`](design-system/README.md). Read that README
before building or changing a screen. In short:

- Use tokens only (`design-system/tokens.css`): no raw hex, no one-off fonts,
  radii or shadows. Change a token there, then run `python3 design-system/sync.py`.
  Never edit the generated copies.
- New portal or founder-console screens go in `techdeva-web/` and are built
  from `techdeva-web/src/components/ui` (see `techdeva-web/DESIGN.md`).
- Pages served by FastAPI (`web/`) are Tailwind too: utilities in the markup,
  JS-rendered classes via `@apply` in `web/styles/<page>.css`, one built
  stylesheet at `/assets/td.css`. Steps in the README.
- Honest data: a missing figure shows "—" with a reason, never 0. Loading,
  empty, error, signed-out and unreachable states are different screens.
- WCAG 2.2 AA, 44px touch targets, no sideways scroll at 390px, and
  reduced-motion support.
- Before pushing UI work, `python3 design-system/sync.py --check` must pass, as
  must `npm run lint` and `npm run build` in `techdeva-web/`.

The `/techdeva-design-system` skill in `.claude/skills/` loads these rules
automatically for UI tasks.

## Next.js

`techdeva-web` runs a Next.js version with breaking changes. Read
`techdeva-web/AGENTS.md` and the docs in `techdeva-web/node_modules/next/dist/docs/`
before writing Next-specific code.
