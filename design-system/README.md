# TechDeva design system

**The one visual standard for every TechDeva surface:** the client portal, the
founder console, the marketing site, the FastAPI-served pages in `web/`, the
cockpit, and every dashboard built from now on, for TechDeva or for any tenant.
If you are building a screen, you are using this. There is no second palette.

Direction: **cinematic, calm data.** The canvas is deep black lit by violet,
with fine film grain and glass surfaces edged in a thin glowing rim. Headline
numbers are large and confident, and motion is choreographed. The data itself
stays calm: tables, lead lists, approvals, billing and forms are high contrast,
static and fast. Approve and reject are instant and unmistakable.

## Files

| File | What it holds |
| --- | --- |
| `tokens.css` | Every colour, radius, size, motion and chart token, dark and light |
| `effects.css` | The cinematic layer: grain, ambient light, glass, rim, glow edge, reveals, skeletons, route transitions, reduced motion |
| `components.css` | `.td-*` classes for plain-HTML pages (button, card, metric, badge, form, table, notice, empty state, nav item) |
| `sync.py` | Pushes the above into every surface; `--check` fails if anything is stale |

**Edit only these files.** Then run `python3 design-system/sync.py`. Copies
elsewhere carry a "GENERATED" header and are overwritten.

## Where it's used, and how

| Surface | How it gets the system |
| --- | --- |
| `techdeva-web/` (Next.js) | `src/app/design-system/{tokens,effects}.css` (generated), bridged to Tailwind in `globals.css`. React primitives in `src/components/ui` — see `techdeva-web/DESIGN.md` |
| `web/*.html` (served by FastAPI) | tokens + effects + components inlined between `@td-design-system` markers; legacy variable names alias to tokens |
| `dashboard-cockpit.html` | same as `web/` |

### Building a new dashboard or app

1. **Inside the portal or founder console?** Add a route in `techdeva-web` and
   build it from `src/components/ui`. This is the default for anything new.
2. **A standalone HTML page** (served by FastAPI, an artifact, an email-able
   report): add its path to `HTML_TARGETS` in `sync.py`, put an empty
   `<style>` in `<head>`, run the script, give `<body>` the class `td`, and use
   the `.td-*` classes. Never hand-copy hex values.
3. **A new app or framework** (another Next app, a mobile web view): add its
   CSS location to `COPY_TARGETS` in `sync.py` and import the generated
   `tokens.css` and `effects.css`. Map tokens into that framework's theme the
   way `techdeva-web/src/app/globals.css` does for Tailwind.
4. **A tenant-branded dashboard:** keep this system's structure, states and
   rules. Swap only the accent primitives (`--p-violet-*`) for the tenant's hue,
   and re-run the contrast and chart checks below before shipping.

## Principles (non-negotiable)

1. **Show what can be proven, not a green tick.** A missing number renders as
   "—" with a reason, never as 0. Every metric gets an as-of time and a source.
   A connection is "live" only when the evidence says so.
2. **Truthful states.** Loading, empty, signed out, error and "couldn't reach
   the server" are different screens. Never tell a signed-in user they are
   signed out because a request failed. Say when nothing was changed.
3. **Cinematic shell, calm data.** Ambient light, glass and motion belong to
   headers, hero metrics and marketing. Inside tables, forms and approval
   queues, nothing moves except the state change the user just caused.
4. **Plain language.** Write for a clinic owner, not a developer. Agents are
   "your campaign manager", not `CAMPAIGN_MANAGER`. Raw JSON never reaches a
   client screen.
5. **Accessible by construction.** WCAG 2.2 AA contrast, a visible focus ring
   on everything, 44px touch targets on phones, labels on every field,
   semantic HTML, and reduced-motion support.
6. **Mobile is first-class.** On phones, content starts at the top of the
   screen (bottom tab bar, not a stacked sidebar), there is no sideways
   scrolling, and tables become stacked rows with direct actions.

## Tokens

There are three layers, plus chart tokens. Components read only from layers 2
and 3.

**1. Primitive (`--p-*`)** — raw values, never used directly by a component:
the ink scale (`--p-ink-0` … `--p-ink-950`, a neutral with a slight violet
bias), the violet brand scale (`--p-violet-100` … `--p-violet-950`; 500 is the
historic `#8b5cf6`), status hues, radii (6/8/12/16/24) and motion (expo-out,
120/220/600ms).

**2. Semantic** — what a value means. These swap wholesale under
`<html data-theme="light">`; dark is the default.

| Token | Meaning | Contrast |
| --- | --- | --- |
| `--bg`, `--bg-elevated` | Page canvas; raised canvas (dialogs, inputs) | |
| `--surface`, `-2`, `-3`, `--surface-glass` | Cards; hovered rows and chips; glass panels | |
| `--fg` | Primary text | |
| `--fg-muted` | Secondary text | 9.7:1 dark / 8.9:1 light |
| `--fg-subtle` | Tertiary text, still AA | 5.9:1 / 6.3:1 |
| `--line`, `--line-strong` | Hairlines; control borders | |
| `--accent` | Brand fill; white text on it | 5.7:1 / 7.1:1 |
| `--accent-ink` | Violet text on the canvas | 10.5:1 / 7.1:1 |
| `--accent-tint`, `-line`, `-glow` | Selected backgrounds, rims, light | |
| `--success-*`, `--warning-*`, `--danger-*`, `--info-*` | `-ink` text, `-tint` background, `-fill` solid | all AA |
| `--ring` | Focus ring | |

**3. Component** — `--btn-h-sm/md/lg` (36/44/52), `--btn-radius`,
`--card-radius`, `--card-bg`, `--input-h` (44), `--input-bg`,
`--input-border`, `--badge-radius`, `--table-row-h` (52), `--nav-item-h` (44)
and `--focus-ring`.

**4. Charts** — `--chart-1` … `--chart-8`, `--chart-grid`, `--chart-axis` and
`--chart-midpoint`. See "Charts" below.

## Typography

| Role | Face | Use |
| --- | --- | --- |
| Display | Bricolage Grotesque 500–700 | Page titles, hero headlines, headline metrics |
| Body / UI | Geist | Everything else |
| Data / labels | Geist Mono | Identifiers, timestamps, console labels |

Scale: display (40→68px, -3.5% tracking), metric (40→56px), page h1 (24→30px),
card title (16–18px), body (15px in the app, 16–17px on marketing pages),
secondary (14px), label (12px, uppercase, +6% tracking). Numbers in columns
always use `tabular-nums`. Reading text stays at or under 65 characters wide.

## Layout and surfaces

- Use a 4px spacing grid. Cards are padded 20/24px, app gutters are 24/32px,
  and marketing sections step 64 → 80 → 112px.
- **Solid card**: every data surface. **Glass card** (`.td-glass .td-rim`):
  heroes, greeting headers and closing CTAs only.
- **`.td-ambient`**: the violet light behind cinematic headers. **`.td-grain`**:
  static film grain on the body (one layer, no per-frame cost).
- The founder console may go further (grid texture, live-signal badges,
  severity stripes). Client screens stay calmer.

## Motion

| Pattern | Rule |
| --- | --- |
| Staggered reveal (`.td-stagger`, `--i`) | Heroes and metric tiles only; 600ms expo-out, 70ms step |
| Count-up numbers | Start on view, about 1.1s; screen readers always get the final value |
| Route transition | Fade out 160ms, rise in 320ms |
| Skeleton | Reserves the final size so nothing shifts on load |

Never animate tables, lead rows, approval cards or form fields. Under
`prefers-reduced-motion` everything renders in its final state.

## Charts

- **Pick the form first.** One headline number is a metric tile, not a chart.
  Use one y-axis only, and never a dual-axis chart.
- **Categorical series use `--chart-1` … `--chart-8` in that fixed order**, never
  cycled or re-ranked, and colour follows the entity, not its rank. Violet
  leads. Validated against `--surface` in both themes: adjacent colour-blind
  ΔE ≥ 8.4 and normal-vision ΔE ≥ 19.3. Scatter, bubble and map charts use
  three series at most (slots 1–3 pass all-pairs); fold the rest into
  "Other" or facet.
- **In light mode slots 3, 4 and 5 sit below 3:1** against the surface, so
  charts must show direct labels or offer a table view.
- **Sequential (magnitude)** uses one hue, the violet primitives light → dark.
  **Diverging** uses `--chart-7` ↔ `--chart-8` with `--chart-midpoint`.
- **Status colours are not series colours.** Good, warning and critical use the
  status tokens and always come with an icon and a word.
- Text in charts uses text tokens, never the series colour. With two or more
  series a legend is always present; up to four may also be labelled
  directly. Line and area charts get a crosshair tooltip.
- Changing the palette means re-running the dataviz validator in both themes.

## Copy

- Name things the way the owner recognises them: "Connections", "Cost per lead".
- A control says exactly what happens ("Approve"), and the result confirms
  it ("Approved — your AI team carries it out from here").
- An error says what went wrong, what to do next, and whether anything changed.
- Sentence case everywhere. No all-caps statuses.

## Ship checklist

- [ ] Only tokens: no raw hex, no one-off fonts, radii or shadows
- [ ] `python3 design-system/sync.py --check` passes
- [ ] axe-core WCAG 2.2 AA: no violations, in dark and light
- [ ] 390px wide: no sideways scroll, 44px targets, content at the top
- [ ] Loading, empty, error, signed-out and unreachable states are designed
- [ ] Missing numbers show "—" with a reason and an as-of time
- [ ] Works with reduced motion
