# TechDeva design system

Direction: **cinematic, calm data.** The canvas is deep black lit by violet,
with fine film grain and glass surfaces edged in a thin glowing rim. Headline
numbers are large and confident, and motion is choreographed. The data itself
stays calm: tables, lead lists, approvals, billing and forms are high contrast,
static and fast. Approve and reject are instant and unmistakable.

Everything here lives in `src/app/globals.css` (tokens) and
`src/components/ui/` (primitives). If a page needs a colour, radius or font
that isn't a token, add the token here first. Pages never use raw hex values.

## Principles

1. **Show what can be proven, not a green tick.** A missing number renders as
   "—" with a reason, never as 0. Every metric gets an as-of time and a source.
   A connection is "live" only when the evidence says so.
2. **Cinematic shell, calm data.** Ambient light, glass and motion belong to
   headers, hero metrics and marketing. Inside tables, forms and the approval
   queue, nothing moves except the state change the user just caused.
3. **Plain language.** Write for a clinic owner, not a developer. Agents are
   "your campaign manager", not `CAMPAIGN_MANAGER`. Raw JSON never reaches a
   client screen.
4. **Accessible by construction.** AA contrast on every token pairing, a
   visible focus ring on everything, 44px touch targets, semantic HTML, and
   reduced-motion support built into every animated primitive.

## Tokens

There are three layers. Components read only from layers 2 and 3.

### 1. Primitive (`--p-*`)

The raw values. Never referenced from a component.

| Group | Tokens |
| --- | --- |
| Ink (neutral, slight violet bias) | `--p-ink-0` `#050408` … `--p-ink-950` `#fbfaff` |
| Violet (brand) | `--p-violet-100` … `--p-violet-950`; `500` is the historic `#8b5cf6` |
| Status | `--p-emerald-*`, `--p-amber-*`, `--p-rose-*`, `--p-sky-*` |
| Radius | `--p-radius-xs` 6 · `sm` 8 · `md` 12 · `lg` 16 · `xl` 24 |
| Motion | `--p-ease-out` (expo-out), `--p-ease-in-out`, `--p-dur-fast` 120ms · `base` 220ms · `slow` 600ms |

### 2. Semantic

Swapped wholesale by `:root[data-theme="light"]`. Dark is the default.

| Token | Meaning | Dark | Light |
| --- | --- | --- | --- |
| `--bg` | Page canvas | ink-0 | ink-950 |
| `--bg-elevated` | Raised canvas (dialogs, inputs) | ink-50 | white |
| `--surface` / `-2` / `-3` | Cards, hovered rows, chips | ink-100/150/200 | white / #f6f5fb / #eeecf6 |
| `--surface-glass` | Cinematic glass panels | ink-150 @ 62% | white @ 72% |
| `--fg` | Primary text | ink-900 | #15131d |
| `--fg-muted` | Secondary text (9.7:1) | ink-700 | #4b4859 (8.9:1) |
| `--fg-subtle` | Tertiary text, still AA (5.9:1) | ink-600 | #615d70 (6.3:1) |
| `--line` / `--line-strong` | Hairlines / control borders | white @ 8% / 16% | ink @ 10% / 20% |
| `--accent` | Brand fill; white text reads 5.7:1 | violet-600 | violet-700 (7.1:1) |
| `--accent-ink` | Violet text on the canvas (10.5:1) | violet-300 | violet-700 |
| `--accent-tint` / `-line` / `-glow` | Selected backgrounds, rims, light | violet @ 14/38/32% | lower |
| `--success-*` `--warning-*` `--danger-*` `--info-*` | `-ink` text, `-tint` bg, `-fill` solid | 300 shades | 700 shades |
| `--ring` | Focus ring | violet-400 | violet-700 |
| `--ambient-1/2`, `--grain-opacity` | The violet light source and film grain | | |

Tailwind exposes these as utilities through `@theme inline`: `bg-bg`,
`bg-surface`, `text-fg`, `text-fg-muted`, `text-fg-subtle`, `border-line`,
`bg-accent`, `text-accent-ink`, `bg-success-tint text-success`,
`bg-danger-fill`, and so on.

### 3. Component

| Token | Use |
| --- | --- |
| `--btn-radius`, `--btn-h-sm/md/lg` (36/44/52) | Button. `md` is the default and meets the 44px target |
| `--card-radius`, `--card-bg`, `--card-border`, `--card-glass-*` | Card (solid / glass) |
| `--input-radius`, `--input-h` (44), `--input-bg`, `--input-border` | Input, Select, Textarea |
| `--badge-radius`, `--table-row-h` (52), `--nav-item-h` (44) | Badge, Table, nav |
| `--focus-ring` | Double ring (canvas gap + violet) for buttons |

## Typography

| Role | Face | Where |
| --- | --- | --- |
| Display | Bricolage Grotesque 500–700, self-hosted in `src/app/fonts` (`font-display`) | Page titles, hero headline, headline metrics |
| Body / UI | Geist (`font-sans`) | Everything else |
| Data / labels | Geist Mono (`font-mono`) | Step numbers, technical identifiers, timestamps |

Scale: `text-display` (clamp 40→68px, -3.5% tracking) for heroes,
`text-metric` (clamp 40→56px) for headline numbers, `text-3xl` for page h1,
`text-lg` for card titles, `text-[15px]` for body copy in the app, `text-sm`
for secondary text, and `text-label` (12px, uppercase, +6% tracking) for
eyebrows and table headers. Numbers in columns always use `tabular-nums`.

## Spacing and layout

A 4px grid. Sections step 64 → 80 → 112px on marketing pages, cards use 20 or
24px padding, and app pages use 24 or 32px gutters. Lay out siblings with
`gap`, not margins. Reading text stays at or under 65ch.

## Surfaces

- **Solid card** (`<Card>`): every data surface.
- **Glass card** (`<Card tone="glass">`, `.td-glass .td-rim`): hero panels,
  the closing CTA and the dashboard greeting only.
- **`.td-ambient`**: the violet light source behind cinematic headers.
- **`.td-grain`**: static film grain on `<body>`, one composited layer with no
  per-frame cost.
- **`GlowCard`**: border light that follows the pointer (ported from 21st.dev
  "Glowing Effect"). Fine pointers only, off under reduced motion.

## Motion

| Pattern | Primitive | Rules |
| --- | --- | --- |
| Staggered reveal | `.td-stagger` + `--i` on children | 600ms expo-out, 70ms step; marketing heroes and dashboard tiles only |
| Count-up numbers | `<CountUp>` (ported from 21st.dev "Number Ticker") | Starts on view, 1.1s; renders the final value under reduced motion; screen readers get the final value |
| Route transition | React `<ViewTransition>` with class `td-page` | Fade-out 160ms, rise-in 320ms; off under reduced motion |
| Loading | `<Skeleton>` | Reserves final size; shimmer off under reduced motion |

Never animate tables, lead rows, approval cards or form fields. The global
`prefers-reduced-motion` rule reduces every animation and transition to near
zero, so any new motion must still read correctly with no motion at all.

## Components (`src/components/ui`)

| Component | Notes |
| --- | --- |
| `Button`, `ButtonLink` | Variants `primary`, `secondary`, `ghost`, `danger`, `approve`. Use `ButtonLink` for navigation and `Button` for actions |
| `Card` (+ `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter`) | `tone="solid" \| "glass"` |
| `Input`, `Textarea`, `Select`, `Label`, `Field` | `Field` wires the label, hint and error to the control with ids. Visible labels always; placeholders are examples, not labels |
| `Badge` | Status chip. Always has words; colour is never the only signal |
| `Table`, `THead`, `TBody`, `TR`, `TH`, `TD` | Scrolls inside its own box. 52px rows |
| `Dialog` | Native `<dialog>`: focus trap, Escape and inert background for free. `side="bottom"` gives a mobile sheet |
| `Tabs`, `TabPanel` | WAI-ARIA tabs with arrow, Home and End keys |
| `EmptyState` | What will appear, why it isn't here yet, and what to do next |
| `Skeleton`, `LoadingRegion` | Visual placeholder plus a screen-reader status |
| `Notice` | Inline info, success, warning, danger and offline messages with an optional action |
| `PageHeader` | One h1 and one plain sentence per page |
| `CountUp`, `GlowCard`, `useReducedMotion` | Motion primitives |
| `ThemeToggle`, `Logo` | Brand chrome |

App-level building blocks: `SessionGate` (loading, signed-out and unreachable
states, each a different truthful screen), `SidebarLayout` (desktop sidebar,
mobile top bar and bottom tab bar), and `MarketingPage` (site header, hero
band, closing CTA, footer).

## Copy rules

- Name things the way the owner recognises them: "Connections" (not
  integrations config), "Cost per lead" (not CPL alone).
- A control says exactly what happens ("Approve and launch"), and the result
  confirms it ("Approved").
- An error says what went wrong and what to do next, and says when nothing was
  changed.
- Sentence case everywhere. No all-caps statuses.
