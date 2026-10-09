# techdeva-web on the TechDeva design system

The principles, tokens, typography, motion, chart rules and ship checklist live
in **[`../design-system/README.md`](../design-system/README.md)**, the one
standard for every TechDeva surface. This file covers only how this Next.js app
consumes them.

## Tokens in this app

`src/app/design-system/tokens.css` and `effects.css` are **generated**. Edit
`design-system/*.css` at the repo root and run `python3 design-system/sync.py`.
`src/app/globals.css` imports them and bridges them to Tailwind through
`@theme inline`, so you write utilities rather than hex values:

| Need | Utility |
| --- | --- |
| Canvas, surfaces | `bg-bg`, `bg-bg-elevated`, `bg-surface`, `bg-surface-2`, `bg-surface-3` |
| Text | `text-fg`, `text-fg-muted`, `text-fg-subtle`, `text-accent-ink`, `text-on-accent` |
| Lines | `border-line`, `border-line-strong`, `border-accent-line` |
| Brand fill | `bg-accent`, `hover:bg-accent-hover`, `bg-accent-tint` |
| Status | `text-success bg-success-tint`, `bg-success-fill`, `text-warning`, `text-danger`, `bg-danger-fill`, `text-info` |
| Type | `font-display`, `font-mono`, `text-display`, `text-metric`, `text-label` |
| Cinematic layer | `td-ambient`, `td-glass td-rim`, `td-stagger` (+ `--i`), `td-skeleton` |
| Light-only tweaks | the `light:` variant |

## Components (`src/components/ui`)

| Component | Notes |
| --- | --- |
| `Button`, `ButtonLink` | Variants `primary`, `secondary`, `ghost`, `danger`, `approve`. Use `ButtonLink` for navigation and `Button` for actions |
| `Card` (+ `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter`) | `tone="solid" \| "glass"` |
| `Input`, `Textarea`, `Select`, `Label`, `Field` | `Field` wires label, hint and error to the control by id |
| `Badge` | Status chip; always has words |
| `Table`, `THead`, `TBody`, `TR`, `TH`, `TD` | Scrolls in its own box; 52px rows |
| `Dialog` | Native `<dialog>`; `side="bottom"` for phone sheets |
| `Tabs`, `TabPanel` | WAI-ARIA tabs; share an `id` |
| `EmptyState`, `Notice`, `Skeleton`, `LoadingRegion`, `PageHeader` | States and page tops |
| `CountUp`, `GlowCard`, `useReducedMotion` (`motion.tsx`) | Motion primitives; count-up and pointer glow ported from 21st.dev without the motion dependency |
| `ThemeToggle`, `Logo` | Brand chrome |

App building blocks in `src/components`: `SessionGate` (loading, signed-out and
unreachable states), `SidebarLayout` with `PortalShell` and `FounderShell`
(desktop sidebar, phone top bar and bottom tab bar), `MetricCard`,
`ReadableValue` (agent output as rows, never raw JSON), `AuthShell` and
`PasswordInput`, `FounderBits`, and `MarketingPage` with `site/SiteHeader`
and `site/SiteFooter`.

Plain-language names for API identifiers (lead status, source, agent, kind)
live in `src/lib/labels.ts`. Extend them there, not inline.
