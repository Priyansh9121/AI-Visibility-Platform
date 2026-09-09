# Design System — Token & Component Reference

Required by product-spec.md §7, Epic 0: *"Document design tokens in
`/docs/design-system.md`."*

**Package:** `packages/design-system` (`@avp/design-system`)
**Style guide:** `corepack pnpm --filter @avp/design-system dev` → http://localhost:4100

Designed from the data model and user goal only. No competitor screen,
screenshot, or markup was referenced, and no competitor code was inspected
(the no-competitor-reference rules).

---

## 0. Epics 14 and 15 — the system as it is actually built now

**Read this section first.** Everything under §§1–4 below describes the paper
system Epic 0 built; from Epic 14 that system is **the report's skin only**.
Epic 14 made the product dark; **Epic 15 made light the default and dark the
opt-in** (`design-direction.md` §8 — the founder's reversal of §7's default,
and only its default). This section is the reference for what is in the code.

### Three scopes, one token set

`tokens.css` declares every role token three times:

| Scope | Selector | What it is |
|---|---|---|
| **Light** | `:root` | **The default** (Epic 15). Epic 14's language on a warm off-white ground with white cards. `light` in `tokens/color.ts`. |
| **Dark** | `[data-theme='dark']` | Epic 14's identity, kept whole as the opt-in. Set on `<html>` by the theme provider. `dark` in `tokens/color.ts`. |
| **Paper** | `.avp-report` | Epic 0's values verbatim, on the report's own root class and nothing else — declared last, so it wins over the dark block when a document sits inside a dark app. The document renders identically in a light app, a dark app, on `/share/{token}` and in a PDF, with **no markup change**. |

`tokens.test.ts` reads each value from the block it belongs to, asserts every
override in the dark and report blocks has a default in `:root`, and asserts
that no theme attribute shares the report's rule — the separation Epic 15
made so the toggle cannot reach the document.

### The theme choice

`ThemeProvider` (`apps/web/src/components/shell/ThemeProvider.tsx`) wraps
`next-themes` with `attribute="data-theme"`, `defaultTheme="light"`,
`enableSystem`, storage key `avp.theme`. `ThemeControl` in Settings offers
Light / Dark / Match system as `aria-pressed` buttons and reads the choice
only after mount so hydration cannot disagree with the server's markup.

### Theme-reactive paint for values that cannot be tokens

A score's ramp colour is interpolated, so it is an inline literal — and with
two themes a literal is wrong in one of them. `rampVars(score)` puts both
ramps' values on the element (`--avp-ramp-light/dark`, `--avp-on-ramp-*`),
class `avp-ramp` lets `components.css` pick one by theme, and paint reads
`var(--avp-ramp)`. Used by `ScoreMeter`, `NavScore`, `NavSubItem`, the
Ledger's Working palette, and — as `heroVars()` — the hero's gradient and
glow. Series paint on Working charts is custom properties outright:
`seriesStyle(…, 'working')` returns `var(--avp-beacon-600)` / `benchVar(i)`.

### The light palette (`light` in `tokens/color.ts`)

| Role | Value | Use |
|---|---|---|
| `surface-sunken / ground / seated / raised` | L 0.955 / 0.975 / 0.995 / 1.0, hue 75 | Sidebar, page, cards, hovered card |
| `surface-void` | `oklch(0.925 0.01 75)` | The unlit part of a ledger or meter |
| `text-primary / body / secondary / tertiary` | ink-900 / ink-800 / ink-600 / `oklch(0.56 0.014 75)` | Tertiary a step darker than ink-400 for 4.6:1 |
| `line-hairline / strong / ink` | L 0.91 / 0.85 (hue 75) / ink-800 | Rules; a chart baseline |
| `beacon-600 / 700` | `oklch(0.53 0.09 200)` / `oklch(0.46 0.078 200)` | Inside sRGB; white text 4.8:1 |
| `signal-600 / 700` | `oklch(0.53 0.125 155)` / `oklch(0.46 0.11 155)` | Beacon's gradient partner |
| `--avp-on-accent / on-danger / on-warn` | paper-000 / paper-000 / ink-800 | Text on a fill |
| `vis-*`, `bench-*`, semantics | Epic 0's values | Drawn for a light ground |
| elevation | soft ink-tinted shadows, no inner highlight | A white card on off-white |

### The dark palette (`dark` in `tokens/color.ts`)

| Role | Value | Use |
|---|---|---|
| `surface-sunken` | `oklch(0.145 0.012 265)` | Sidebar, inputs, meter tracks |
| `surface-ground` | `oklch(0.175 0.012 265)` | The page |
| `surface-seated` | `oklch(0.215 0.014 265)` | Cards, tiles, the hero |
| `surface-raised` | `oklch(0.25 0.015 265)` | A card under the pointer, popovers |
| `surface-void` | `oklch(0.275 0.014 265)` | The unlit part of a ledger or meter |
| `surface-hover` | `oklch(1 0 0 / 0.04)` | A row under the pointer, on any surface |
| `text-primary / body / secondary / tertiary` | L 0.965 / 0.88 / 0.72 / 0.58, hue 75 | Warm text on cool ground |
| `line-hairline / strong / ink` | L 0.275 / 0.35 (hue 265) / 0.72 (hue 75) | Rules; `ink` is a chart baseline |
| `beacon-600` | `oklch(0.80 0.13 200)` | The client's colour, electric stop; `700` lightens |
| `signal-600` | `oklch(0.84 0.17 155)` | Beacon's gradient partner, nothing else |
| `--avp-gradient-accent` | signal-600 → beacon-600 at 135° | Primary buttons. Flat beacon in the paper scope. |
| `--avp-on-accent / on-danger / on-warn` | ink-900 / ink-900 / warn | Text ON a fill, resolved per scope |
| `vis-00 … vis-100` | L 0.50 → 0.87, hues 35 → 195 | The lifted ramp. `visibilityColorDark()`. |
| `success / warn / danger / info` | L 0.78 / 0.82 / 0.70 / 0.80 | System state, brighter for the ground |
| `bench-*` | `benchDark`, 92 % of the sRGB ceiling per hue | Seven categorical accents, unchanged in hue |

`heroGradient(score)`: the ramp at the score → a lighter, slightly less
chromatic stop of the same hue, lightness floored at 0.70. Deterministic,
tested.

### Type

`--avp-font-display: 'Space Grotesk'` joins the three faces. Headings
(`PageHead`, card titles, empty-state titles, nav head), KPI figures
(`--avp-text-kpi`, 30px) and the hero numeral (`--avp-text-hero`, fluid
56–84px, `--avp-tracking-hero` −0.04em) use it. `font-editorial` (Fraunces)
now appears only inside `.avp-report`. Tailwind: `font-display`, `text-kpi`,
`text-hero`, `tracking-hero`, `bg-accent`.

### The shape lock

`--avp-radius-2xl` (16px) for cards, tiles, the hero, empty states and the
GapGrid; `lg` (8px) for buttons, inputs and nav rows; `full` for chips. The
paper scope pins `sm`/`md` back to Epic 0's 3px / 5px.

### Elevation

`seated` = inner 1px top highlight; `raised` = highlight + contact shadow +
soft ground-tinted drop; `lifted`/`overlay` for transient UI. `paperElevation`
in `tokens/elevation.ts` is Epic 0's set, applied through the paper scope.

### Components added or changed

| Component | Epic 14 |
|---|---|
| `ScoreHero` | **New.** The one figure at the top of a screen: label, gradient numeral, `/100`, band, delta (`+4.2 since last scan`, coloured by the ramp's two ends, never success/danger), meta, and an `aside` slot. Null renders the absence in words, no gradient, no glow. Client component (reveal). |
| `PageHead` | **New.** Eyebrow, title in the display face, `aside` for controls. Every Working screen's first line. |
| `StatTile` | Redrawn as a card: seated surface, 16px, top highlight, hover lifts one tone. The accent is a **dot beside the label**, no longer a left rail; `emphasis` colours the value. |
| `Card` | Seated by default, 16px, `CardHeader` is a flex row so a title and a live badge sit on one line; a `DataTable` directly inside runs edge to edge. |
| `Button` | Primary carries `--avp-gradient-accent` with `--avp-on-accent` text; hover brightens. Secondary lifts to `surface-raised` with a beacon edge. |
| `ScoreMeter`, `NavScore`, `NavSubItem` | Painted from `visibilityColorDark`. |
| `LuminanceLedger` | `palette="working"` (default `'report'`) paints lit segments from the dark ramp and adds `avp-ledger--working`. Void is `--avp-surface-void`. |
| `TrendChart` | `area` (default `false`) fills under the **subject's** line only, gradient from the series colour to transparent, one `<linearGradient>` per instance via `useId`. `seriesStyle(…, 'working')` now returns the dark beacon and `benchColorDark`. |
| `AppShell` | Sidebar is sunken, sticky, full height, 16rem; content padding tightened for density. |

### What the report must never acquire

`ReportView.test.tsx` asserts, for every report fixture, none of:
`avp-ledger--working`, `avp-trend__area`, `avp-hero` — alongside the existing
stagger / partial guards. The report never opts in; it never opts out.

The **compact** guard was amended in Epic 16, on purpose: the score beat now
draws one compact column beside the numeral (§7), so the guard asserts the
gap beat's full column is never compact and that no second compact column
exists, rather than that the document carries none.

### Verified

Epic 14: design system 538/538, web 799/799. Epic 15: design system
601/601, web 802/802. Both typechecks clean at each. Screens verified in Chromium against the
compiled stylesheet from the app's own fixtures rather than a live session —
see `build-log.md`, Epic 14, for why — and the styleguide live on :4100.
`docs/screenshots/epic-14/`.

---

## The ruling that drives every decision (Epic 0 — the report's ruling now)

This product's main output is not a dashboard — it is **an argument someone
makes to another person.** It is read in two contexts:

| Context | Reader | Demands |
|---|---|---|
| **Working** | Agency operator running scans back to back | Density, scanability, dark-tolerant |
| **Presenting** | The prospect's decision-maker, often on a printed PDF | Credibility, contrast, survives greyscale |

The presenting context closes deals, so it wins ties. That single ruling
produces the light-first paper palette, the editorial serif, a colour ramp that
survives photocopying, and elevation built from borders rather than blur.

It also happens to be where the narrative-report rule (narrative report, not metrics
tiles) points. The two agree, so the system leans in hard.

---

## 1. Colour — "Lit / Unlit"

**Metaphor:** the product measures presence vs absence in an AI answer, so
visibility is encoded as **luminance**. Dim + desaturated = invisible.
Bright + saturated = cited. The colour scale *means* something; a reader who
never reads the legend still reads a dark chart as bad news.

All values are OKLCH so lightness steps are perceptually even.

### Neutrals — warm paper, cool ink

| Token | Value | Use |
|---|---|---|
| `--avp-paper-000` | `oklch(0.995 0.004 75)` | Page ground |
| `--avp-paper-050` | `oklch(0.978 0.006 75)` | Sunken wells, zebra |
| `--avp-paper-100` | `oklch(0.958 0.008 75)` | Seated surfaces |
| `--avp-paper-200` | `oklch(0.925 0.010 75)` | Hairlines |
| `--avp-paper-300` | `oklch(0.870 0.012 75)` | Strong borders |
| `--avp-ink-400` | `oklch(0.620 0.014 75)` | Tertiary text, axis labels |
| `--avp-ink-600` | `oklch(0.470 0.018 265)` | Secondary text |
| `--avp-ink-800` | `oklch(0.285 0.022 265)` | Body text |
| `--avp-ink-900` | `oklch(0.185 0.026 265)` | Headings, the score numeral |

The category defaults to a cool blue-grey neutral. Rotating the light end warm
(hue 75) reads as paper rather than app chrome and differentiates at zero cost.

**Note the deliberate hue hand-off at `ink-400`:** light neutrals are warm,
dark neutrals are cool (hue 265). Warm dark greys print muddy; cool dark greys
read like printed ink.

### Visibility ramp — the load-bearing decision

| Token | Value | Band | Means |
|---|---|---|---|
| `--avp-vis-00` | `oklch(0.420 0.045 35)` | 0–19 | Absent |
| `--avp-vis-25` | `oklch(0.545 0.115 45)` | 20–39 | Barely visible |
| `--avp-vis-50` | `oklch(0.655 0.135 65)` | 40–59 | Emerging |
| `--avp-vis-75` | `oklch(0.735 0.125 150)` | 60–79 | Established |
| `--avp-vis-100` | `oklch(0.815 0.115 195)` | 80–100 | Highly visible |

Three properties held on purpose, each asserted in `src/tokens/tokens.test.ts`:

1. **Monotonic in lightness** (0.42 → 0.815) — survives greyscale printing and
   photocopying. The traffic-light red→amber→green ramp does not: red and green
   resolve to near-identical greys. These reports get printed.
2. **CVD-safe** — the warm→cool traverse is the axis deuteranopes and
   protanopes reliably retain. ~8% of men would misread a red/green score.
3. **Chroma rises with lightness** — "more visible" is simultaneously lighter
   *and* more saturated, so the metaphor is doubly encoded.

**HARD RULE: ramp colours are fill-only, never text.** The high end cannot
reach 4.5:1 on paper. Use `onVisibility(score)`, which picks ink or paper by a
luminance threshold at L=0.62, rather than choosing by hand.

API: `visibilityAt(score) → Oklch` · `visibilityColor(score) → string` ·
`onVisibility(score) → string` · `visibilityBand(score) → band name`.

### Brand accent and competitor series

`--avp-beacon-{050,100,400,600,700}` — hue 200. **The client or prospect under
analysis is always beacon.** One brand, one colour, everywhere.

`--avp-competitor-{1..5}` — neutral slate (hue 265), separated by lightness and
by fill pattern (`solid`, `hatch-45`, `dot`, `hatch-135`, `outline`).

**Competitors never take a ramp colour.** A competitor rendered in "good green"
reads as an endorsement; one rendered in "bad red" makes the report look like a
hatchet job and costs it credibility with the client's decision-maker — the one
thing the report cannot afford. Separating by lightness + pattern also keeps
series distinguishable in black and white and supports unlimited series count.

This rule is enforced in one place — `seriesStyle(role, index)` — so components
never pick series colours themselves. Asserted in tests.

### Semantics

`--avp-success` · `--avp-warn` · `--avp-danger` · `--avp-info`.

Deliberately narrow: these describe **system state** (a scan failed, a quota is
nearly spent) and are **never** used for score values. Keeping the semantic and
visibility palettes disjoint is what stops a red error chip from being misread
as "bad score" on a report page.

### Dark mode

Not an inversion. The **visibility ramp keeps its direction** — dim stays bad,
lit stays good, because that mapping is the concept. Only the neutral axis
flips, and the ramp's low end gains lightness so "absent" reads as
dim-but-present rather than as a hole. Dark mode serves the working context;
the presenting/export context is always light.

---

## 2. Typography

All OFL-1.1 via Google Fonts (the licensed-assets rule). Declared in exactly one place:
`GOOGLE_FONTS_HREF` in `src/tokens/typography.ts`.

| Role | Face | Why |
|---|---|---|
| Editorial | **Fraunces** | A serif is the loudest signal that this is a document making an argument. Variable, with `opsz`/`SOFT`/`WONK`, so it sets warm at display sizes without going stiff. |
| Interface | **IBM Plex Sans** | Deliberately *not* Inter — the category default, which reads as generic SaaS. Plex carries more character, stays unambiguous at 12–14px, and ships true **tabular figures**, mandatory for comparison tables. |
| Evidence | **IBM Plex Mono** | Same skeleton as Plex Sans, so evidence blocks sit in body copy without a seam. Monospacing marks verbatim machine output. |

### Two scales, on purpose

One modular ratio cannot serve both jobs. Interface work needs 13px and 14px to
be genuinely different things; editorial work needs headlines that leap across
a conference table.

**Interface track — ratio 1.125:** 11 · 12 · 13 · 14 · 16 · 18 · 20
(`--avp-text-ui-{2xs,xs,sm,base,md,lg,xl}`)

**Editorial track — ratio 1.25:** 20 · 25 · 31 · 39 · 49 · 61 · 76
(`--avp-text-ed-{2xs,xs,sm,md,lg,xl,2xl}`)

**Score numeral — `--avp-text-score`, 112px**, outside both ladders. The score
is the single most important object in the product; it earns a bespoke size.

Line heights `1.15 / 1.35 / 1.6 / 1.5` (display/ui/prose/mono). Tracking
`-0.02em` above 39px, `+0.06em` on all-caps micro-labels. Report prose capped
at `--avp-measure` = 68ch.

`font-variant-numeric: tabular-nums` is set on `body` by default and opted out
of on prose — this product is comparison tables, and proportional digits make a
column of numbers unreadable.

---

## 3. Spacing

`--avp-space-*`: 2 · 4 · 6 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 56 · 72 · 96 · 128

4px base with a **non-linear tail**. Pure 4px multiples give six near-identical
options between 24 and 48, where nothing needs that precision, and nothing
useful past 64, where layout actually lives. Above 24 the steps grow ~1.35×, so
a section break reads as intentional rather than "someone typed 44."

Two tokens matter more than the ramp:

- **`--avp-rhythm` (8px)** — the vertical grid the report's narrative beats snap
  to. When all five beats sit on one rhythm the page reads as a single document
  rather than five stacked widgets.
- **`--avp-beat` (72px)** — the gap *between* beats. Larger than any intra-beat
  spacing, so the five-part structure is legible from the page thumbnail, which
  is how a client first sees an exported PDF.

Radii: `sm 3` · `md 5` · `lg 8` · `xl 12` · `full`.
Layout: `--avp-report-width` 1152px (the on-screen document — Epic 17; the PDF is A4 and reads no token) · `--avp-page-width` 832px (landing, auth, welcome, intake) · `--avp-app-max` 1440px.

---

## 4. Elevation — "paper doesn't float"

| Level | Token | Treatment | Prints? |
|---|---|---|---|
| 0 | `--avp-elev-flat` | Hairline only, no shadow | ✅ |
| 1 | `--avp-elev-seated` | Tone shift + hairline, **no shadow** | ✅ |
| 2 | `--avp-elev-raised` | Hard `0 1px 0` offset, zero blur | ✅ |
| 3 | `--avp-elev-lifted` | Two-layer blur — popovers, menus | ✖ transient |
| 4 | `--avp-elev-overlay` | Scrim + larger blur — modals | ✖ transient |

The category default is a big soft blurred shadow. Two problems: it reads as
generic app chrome, and — decisively — **blurred shadows vanish when the report
is printed**, taking the hierarchy with them. Levels 0–2 are built from borders
and tone so they survive. Levels 3–4 belong to transient UI that never appears
in an export, so they may blur.

`<Card>` deliberately exposes only `flat | seated | raised`.

### The ownable rule: emphasis is light, not lift

Because the system says visibility = luminance, an emphasised element does not
rise toward the viewer — it **illuminates**:

- `--avp-focus-ring` — 2px beacon ring at 60% + 6px halo at 12%. Never an
  outline offset, never a lift. Applied globally in `base.css` so no component
  reimplements it.
- `--avp-selected-ring` / `--avp-selected-wash` — reads as lit from within.
- **Score reveal** — dissolves `vis-00` → true value over 600ms
  `cubic-bezier(0.22, 1, 0.36, 1)`. A count-up says "loading"; an illumination
  says what the product measures.

Motion: `120ms` hover · `200ms` state · `320ms` layout · `600ms` reveal ·
`70ms` stagger between siblings arriving in sequence (Epic 9.16 — the only
motion value added since Epic 0, and a **delay** rather than a duration).
`prefers-reduced-motion` is honoured globally and in each animated component —
`reveal` degrades to an instant paint, never a slower version of itself.

> The global block in `base.css` reset every animation and transition
> **duration** and left every **delay** alone until Epic 9.16. A staggered
> sequence would therefore have honoured the setting by animating instantly and
> then waiting up to half a second before doing it. Both delays are reset now.

> **Epic 9.19 — the Working screens got motion, and added no value to do it.**
> The dashboard, clients and settings are excluded from *arrival* motion and
> stay so; what they gained is motion where something is genuinely changing,
> spelled entirely in the durations above:
>
> | Where | Token | Why it is motion at all |
> |---|---|---|
> | `.avp-table tbody td` hover | `120ms` hover | The one interactive surface still repainting instantly, on the screen a pointer spends the most time on. |
> | `.avp-badge` tone | `200ms` state | The dashboard re-reads itself every 5s; "Running" becomes "Complete" under the reader. |
> | `.avp-meter__track` opacity | `200ms` state | The track survives the switch out of "measuring", so the row comes up rather than blinks. |
> | `.avp-meter__lit` width/fill | `320ms` layout | A score that MOVES eases. A score that ARRIVES does not — that element does not exist while unscored, and a transition never runs on first paint. |
> | `.avp-badge__pulse` | `calc(reveal × 3)` = 1.8s | The only loop in the system. Derived from the reveal rather than being a new number: the dim-to-lit dissolve slowed to a breath. |
>
> The pulse's keyframe **ends fully lit**, because the global reduced-motion
> block collapses animations to one 0.01ms iteration and lands them on their
> final frame. A keyframe ending at 35% would have left a reader who asked for
> less motion looking at a dot that reads as disabled. Measured live in Epic
> 9.19: nine opacity samples, all `1.0`, with `animation-iteration-count: 1`.

---

## 5. The Luminance Ledger — signature visualisation

`<LuminanceLedger subjectName dimensions competitors height animate
staggerDimensions annotateGap />`

> **`staggerDimensions` defaults to `false`, and that default is a guardrail.**
> This is the same component the report renders. With it, bars light one at a
> time as the chart scrolls into view; without it they light together on mount,
> exactly as they have since Epic 0. Neither report route passes it, so the
> document cannot acquire the flourish by omission — enforced by a regression
> test in `ReportView.test.tsx`, not by this note. Note the opposite default to
> `animate`, which is `true` because the dim-to-lit dissolve is the signature
> moment the component exists for.

**The score, drawn as light.** Each sub-score is a segment whose **height is its
weight** (points available) and whose **lit portion is its value** (points
earned). The unearned remainder is left as a dim void with a hairline marking
how far it could have reached. Competitors render as narrow **ghost columns**:
outline only, no ramp colour, a single cap line at their composite.

### The identity that makes it work

```
lit height of segment i = H × (weight_i/100) × (subscore_i/100)
Σ lit heights           = H × score/100
```

**The total lit height of the column is exactly the composite score.** The chart
is not an illustration of the number — it *is* the number, drawn. If that
identity broke the visualisation would be lying, so it is asserted in
`ledgerLayout.test.ts` rather than trusted.

It follows that the largest **unlit** area is the largest available point gain:

```
gap_i = weight_i × (100 − subscore_i) / 100
```

That is the correct "fix this first" signal — it ranks by *recoverable points*,
so a weak-but-lightly-weighted dimension never outranks a mediocre heavy one.
The dashed annotation on the chart **is** the report's "biggest gap" headline;
the chart generates the narrative rather than illustrating it.

### Design choices

- **Heaviest dimension at the bottom** — the column reads as light accumulating
  from the ground up, matching how the score accumulates, and the most
  consequential dimension sits in the most stable visual position.
- **Deterministic tie-break** on lowest index, so equal gaps always resolve the
  same way and a re-run never reshuffles the report's headline.
- **Empty state is INSUFFICIENT_DATA, never zero.** An unrunnable scan must not
  be shown to a client as a bad score.
- **Accessibility** — `role="img"` with a full spoken summary, plus a
  visually-hidden `<table>` giving weight, sub-score, points earned and points
  available per dimension. Exported PDFs carry it into the accessibility tree,
  which some enterprise clients audit.

**Explicitly not a gauge and not a radar chart.** A gauge discards the
per-dimension structure that makes a score actionable. A radar implies the axes
are commensurable and equally weighted, which under §6 they are not — the whole
point is that the weights differ.

### Compact, and partial — Epic 13

`<LuminanceLedger compact />` draws the same column at a third of the width for
a **grid** of them: the label gutter goes (the caller puts the stack order
beside the grid once, instead of five labels per column), the column narrows,
no gap is annotated, and the figure bounds itself at its drawn width so its
12px values are the size actually rendered. The identity is untouched — at one
height a compact column's lit rects are the same heights as a full one's, which
is what makes two of them side by side comparable, and `render.test.tsx`
asserts it.

`LedgerDimension.measured: false` marks a dimension **this subject has no
reading on**. The segment is still drawn — same height, same place, so the
column keeps the shape of the one beside it — but as a hatched void in the
hairline colour, never on the ramp, and the column then makes **no composite
claim**: nothing is spoken as "out of 100", the hidden table's footer says
*No composite — measured on 3 of 5 dimensions*, and no gap is annotated,
because the largest unlit area is a dimension nobody measured rather than a
gap this subject can close. Distinct from `unmeasured`, which is the whole
column: that says *nothing was measured yet*; this says *this dimension is not
measured for this subject*, and the second must not read as the first.

It exists for the one place in the product that draws a rival as a ledger — the
Competitors screen — where a rival is measured on three of the five dimensions
(sentiment is classified toward the subject only, and the technical audit is of
the subject's own site). A three-segment rival column normalised to full height
would be the weight-basis error api-contracts.md warns about under Epic 5,
drawn; this is the honest alternative. Both are opt-ins with the usual
guardrail: neither report route passes anything, and `ReportView.test.tsx`
asserts the document carries neither class.

---

## 5a. The Answer Shelf — the proof beat's visualisation

`<AnswerShelf subjectName rows title caption layoutOptions />`

**An AI answer has a limited number of slots. Who is standing in them?**
Direction A of `design-direction.md` §5, built in Epic 7.1. One row per answer;
ordinal slots hold the brands the engine named, in the order it named them. The
subject is a filled `beacon` marker at its ordinal, rivals are neutral slate with
a print pattern, and a citation in that same answer hangs as a tick beneath.

### The identity that makes it work

```
every answered row carries exactly one subject mark
```

Present, and it is a marker at the ordinal the answer gave it. Absent, and it is
an **explicit empty notch**. Never nothing. Stack the rows and the notches line
up into a vertical band, so the reader sees *the shape of absence* before reading
a word — and a row that silently rendered nothing when the subject was missing
would not look like a bug, it would look like a clean report. Asserted in
`answerShelfLayout.test.ts` rather than trusted, the same way the Ledger's
lit-height identity is.

### Design choices

- **The notch sits in a fixed column**, not at a guessed ordinal. A brand that
  was not named *has* no ordinal, and inventing one would state a fact the answer
  never gave; a fixed column is also what makes the absences align into a band.
- **Presence reads the authoritative `mentioned` flag**, never the length of the
  slot list. `BrandMention.position` is nullable, and deriving presence from the
  slots would render a false absence — telling a client they were not named in an
  answer that named them.
- **Stored ordinals, never re-indexed.** A brand named 5th renders 5th even when
  the brands at 2–4 are not in the competitor set.
- **An unanswered row is a dash, not a hole.** We have no answer to be absent
  from, and a hole there blames the client for our own failed request.
- **The cap never drops the subject.** Past the visible track it falls back to
  the notch column, so a layout constant can never fabricate an absence.
- **Rivals keep one slate shade across the whole shelf**, assigned by first
  appearance, so a brand does not change colour down the page.
- **Rows are labelled with the prompt AND the engine.** Each prompt is asked of
  every engine, so a label carrying only the question renders two rows that read
  as one row printed twice.
- **Accessibility** — mounts inside `ChartFrame`, so `ariaLabel` is required and
  a visually-hidden table gives every row's brands, the subject's place, and
  whether that answer cited it.

---

## 5b. Scope of Directions A and C

Direction **A (Answer Shelf)** is built — §5a above, Epic 7.1.

Direction **C (Source Map)** is **partly built, and partly descoped.** Its stated
deliverable — *"here are the six pages we need to get you onto"* — ships as a
named fix in the fix beat, driven by `proof.unclaimedCitedDomains`: the domains a
scan cited that belong to neither the subject nor any detected competitor. The
**bipartite prompt-to-domain map itself was not built.** It is a visual
re-presentation of data the "Cited instead" table already shows, it is the one
`design-direction.md` flagged as *"the heaviest engineering lift of the three"*
and *"hairballs fast"*, and it is the least likely of the three to survive the
greyscale-print ruling in §0 that decides ties on this product. Deferred with
that reason rather than dropped silently.

**A correction to what this section used to say.** Until Epic 7.1 it read
*"Directions A and C are **approved** and land in Epic 7."* That was wrong twice
over, and the error is worth recording because it is why nothing was built for
five epics. `design-direction.md` §6 — the list of things it actually asked to
have signed off — names **B only**: *"Motif — build B (Luminance Ledger) as the
signature component."* A and C appear in §5's recommendation prose and nowhere in
the sign-off list. This document upgraded a recommendation into an approval, in
Epic 0.6, and then no downstream planning surface ever read it again:
`product-spec.md` §7's Epic 7 checklist lists layout, white-labelling and
PDF/share; `api-contracts.md`'s deferred register lists the same three. Epic 7
built its checklist faithfully. The only place the commitment existed was one
sentence here.

---

## 5c. Reveal — content arrives, Epic 9.16

`<Reveal as index animate />` · `<RevealGroup as step animate />`

**One element fades and rises into place when it reaches the viewport; a group
does the same in sequence, one step apart.** That is the entire behaviour. It
does not scale, bounce, slide in from a side, or parallax — §4's rule that
emphasis is light rather than lift applies to arrival as much as to focus.

It reuses `--avp-duration-reveal` and `--avp-ease-reveal` rather than
introducing a page-motion timing of its own. §4 defines that pairing as *the*
reveal — the dim-to-lit dissolve that states the product's metaphor — so a
second curve for text would have put the page and its signature chart on two
different rhythms.

### One trigger, two shapes

`useRevealOnIntersect` is shared by `Reveal` and by `LuminanceLedger`'s
staggered mode. An `IntersectionObserver` fires its first callback for every
observed element right after `observe()`, with `isIntersecting` already true for
anything on screen — so **content above the fold and content below it need no
separate modes.** The hero reveals on the first callback; a later section
reveals on a later one.

A `RevealGroup` runs **one observer for the set**, not one per child. Observing
each child separately would make the stagger depend on scroll speed — collapsing
when scrolled fast, stretching when scrolled slowly — instead of being a fixed,
authored rhythm.

### The delay is CSS, not JavaScript

`Reveal` sets `--avp-reveal-index` inline and the stylesheet multiplies it by
`--avp-stagger-reveal`. **No millisecond value is computed in any component.** A
call site wanting a different rhythm passes `step`, which overrides the custom
property; it does not do arithmetic. `--avp-reveal-index` and
`--avp-ledger-index` are declared in `components.css` rather than `tokens.css`
because they are per-instance runtime values, not design tokens — a distinction
`tokens.test.ts` enforces.

### It must never hide content — and the default is VISIBLE

**`.avp-reveal` is fully visible by default.** The hidden state applies only
under `.avp-motion-ready`, a class added to `<html>` by a synchronous inline
script in the document head (`apps/web/src/app/layout.tsx`) — which runs before
the first paint, and only if scripting genuinely works.

> **This was inverted in Epic 9.16a, and the original way round was wrong.**
> 9.16 shipped `opacity: 0` as the default and enumerated the ways JS might fail
> to reveal it. The enumeration missed server rendering. `/invite/{token}` and
> `/reset-password/{token}` put their card straight into the HTML, so those two
> screens were **blank from first paint until hydration** — on the two screens
> people open cold, from an email, on a phone. `curl` returned the card markup
> carrying `avp-reveal` and no `--revealed`.
>
> The four screens done first never showed it because they are never
> server-rendered: `/` and `/welcome` both start at `{ kind: 'loading' }` and
> only construct a `Reveal` after a client-side fetch. `curl /` returns zero
> occurrences of `avp-reveal`. **The precedent was only accidentally safe**, and
> copying it faithfully was not enough.

| Guarantee | Mechanism |
|---|---|
| Server-rendered HTML | Visible by default. The hidden state needs a class only a script can add. |
| JavaScript disabled | Same mechanism — the inline script never runs, so nothing hides. Verified with a JS-disabled browser, not inferred. |
| Bundle fails or is slow | Same again. The page is readable while it waits, and animates if it arrives. |
| Reduced motion | CSS paints `.avp-reveal` revealed with `!important`, no transition. Not a faster animation — none. |
| No `IntersectionObserver` | The hook reveals immediately. |
| `animate={false}` | Starts revealed — what every static render and test gets. |

The inline script is deliberately **not** a React effect: an effect runs after
hydration, and hydration is precisely the window this exists to cover. If the
script is ever deleted, nothing breaks visibly — the product simply stops
animating, which is the correct failure direction.

`tokens.test.ts` asserts the default stays visible.

### Where it is applied, and where it is deliberately not

**Applied:** the landing page (hero as a staggered group, each section as a
unit, the step list, the pricing card, the ledger's bars) and the auth and
onboarding cards.

**Not applied, on purpose:** the client-facing report, Settings, and the
dashboard shell. The report is a document — it gets printed and PDF'd, and
`design-direction.md` §0 makes the presenting context win ties. Settings and the
dashboard are dense, functional screens where arrival motion costs attention and
buys nothing.

> **Still true after Epic 9.19, and worth stating precisely.** That epic gave
> the Working screens hover, status and live motion, and gave them **no
> `Reveal`**. The distinction it turns on: *arrival* motion performs on every
> load, which a dashboard checked fifty times a day should not do; the motion
> 9.19 added fires only when something has actually changed since the last
> paint. `ReportView.test.tsx` asserts the report acquires neither.

`PageSection` takes `stagger` (default **false**) to sequence its own four
parts, because they are props rather than children and a caller cannot wrap
them. A section that should reveal as one unit needs nothing from that prop —
wrap the whole `<PageSection>` in a `<Reveal>`.

---

## 5d. TrendChart — the first time series, Epic 9.20

`<TrendChart points series ariaLabel title caption unit yMax height />`

### Why neither existing chart could be it

`LuminanceLedger` is a **snapshot**: one stacked column whose segment heights
are the §6 weights and whose lit fraction is one scan's value. Its correctness
condition — total lit height *is* the composite — is a statement about a single
measurement, and there is no axis in it for time. `AnswerShelf` is ordinal
position within one scan's answers. Neither can carry a second scan, so this is
a new shape rather than a variant of one.

### The colour rule is §1's, unchanged

The client is always `beacon-600`, solid, 2.5px. Competitors come from
`seriesStyle('competitor', i)` — the same neutral slate family the Ledger's
ghost columns and the DataTable use — and **the visibility ramp is never
touched**. A rival in "good green" reads as an endorsement; one in "bad red"
reads as a hatchet job.

`seriesStyle` returns a *fill* pattern name, because it was written for bars.
A line has no fill to hatch, so each name maps to the dash that carries the same
intent: `solid · 6 3 · 1.5 3 · 9 3 2 3 · 3 3`. The point is §1's own — five
neutral greys are one grey in greyscale print; five dash patterns are five
lines.

### A null is a gap, never a zero

The single load-bearing rule. A competitor set is re-detected per scan, so a
rival can be present, absent, then present again. Joining through zero asserts a
collapse that was never measured; dropping the series shows fewer rivals than
the client has, silently. So `trendLayout.segments()` breaks the line into runs
of consecutive readings, a lone run draws as a dot, and the hidden data table
prints **"not measured"** rather than a blank cell.

### Two things a live browser found that no test could

- **Label collision.** Five rivals within twelve points of each other stacked
  their names into an unreadable block. `spreadLabels()` pushes them apart
  without reordering, so tracing a line to its name never crosses another.
- **Gutter width.** `analytics-alternatives.com` ran off the right edge at the
  original 108-unit padding. It is 176 now, with `truncateLabel` as the backstop
  — and only the *drawn* label is shortened; the data table keeps the full name.

### It cannot render larger than it was drawn — Epic 9.21

`.avp-trend__svg` is `width: 100%` over a fixed viewBox, so the chart scales its
**type and its strokes** with its container. Shipped in 9.20 without a bound, and
measured on the live Rankings screen a day later:

| | authored | rendered in a 1200px Working column |
|---|---|---|
| SVG | 720 × 340 | **1152 × 544** (scale **1.6×**) |
| axis tick | 11px | **17.6px** — against 14px body copy |
| subject stroke | 2.5px | **4.0px** |

The smallest type on the page had become the largest thing on it, which is what
made the screen read as sparse and oversized. Exactly the failure §5d's own
sibling — Epic 9.19's `EmptyState` ledger figure — had already been fixed for.

The fix is `style={{ maxWidth: layout.width }}` on the figure, so one viewBox
unit is at most one CSS pixel. **Derived from the layout rather than declared as
a token**, because the two must be the same number: a caller passing
`layoutOptions.width` would otherwise be squeezed by a cap that had not moved
with it — the same bug in the other direction, type *smaller* than drawn. A
constant would need a test to stop it drifting; this cannot drift.

It is a `max-width`, so the chart still scales down: measured 1:1 at every
column from 784px up, and 0.77× at a 600px viewport without overflowing.

Same accessibility contract as every chart here: `ariaLabel` is required and a
hidden data table is rendered, both via `ChartFrame`.

---

## 5e. LocalNav — navigation scoped to one record, Epic 9.20

`AppShell`'s sidebar is **agency-wide**: every item in it is about the whole
account, across every client at once. Depth about ONE client cannot go there
without either changing what those items mean or inventing a global "selected
client" this product does not have.

So `LocalNav` is a second level of the same tree, nested inside the first: a
heading naming the record, a link back to the list it came from, meta figures,
and a horizontal strip of destinations that are all inside it. An operator can
always tell whose space they are standing in.

**Deliberately not a tab widget.** These are pages with their own URLs, not
panels behind a `role="tablist"` — a tab control that swaps `aria-selected` on
navigation lies to a screen reader about what just happened. Same rule as
`NavItem`: every item is an `<a>` with a real href, `aria-current="page"` marks
the active one, and there is no disabled variant.

`external` marks a destination that *leaves* the record's space. The client's
Report uses it: it is a real path into `/scans/{id}/report`, the document that
already exists, rather than a second copy rendered inside the record frame.

### `LocalNavGroup` — clusters, Epic B.1

The strip is now **clustered**, and every item lives in a group:

```tsx
<LocalNav title={…}>
  <LocalNavGroup label="Measurement">
    <LocalNavItem … accent={0} />
  </LocalNavGroup>
  <LocalNavGroup label="Investigation">
    <LocalNavItem … accent={0} />   {/* restarts — see §6 */}
  </LocalNavGroup>
</LocalNav>
```

**Why groups rather than more hues** is §6's argument: the bench arc holds seven
accents and a client's space is heading for ten sections, and grouping is the
only way out that costs nothing the palette was built to guarantee. An accent
is therefore **cluster-relative** — each cluster restarts at 0.

**The clusters are `Measurement` and `Investigation`**, and membership follows
the data model rather than shipping order. Sentiment is Measurement: its labels
have been stored since Epic 4 and it is 15% of the composite, which makes it a
measured dimension, not a derivation. Investigation holds what an operator does
with the record: Answer gaps derives over measured rows, Prompts creates new
ones.

> **Epic F — the seat is taken, and the section is not called what B.1 expected.**
> B.1 held Measurement's last seat (accent 5) for "Crawler activity" and
> described it as "a second data *source* — first-party server logs". The
> reasoning held; the data source did not exist. Epic F found this product
> ingests no server logs at all, and built the section against the one
> first-party source that IS readable — the site's own `robots.txt`, already
> fetched by every technical audit.
>
> So the section shipped as **AI crawlers**, not Crawler activity, and the
> label change is load-bearing rather than cosmetic: it answers what a crawler
> *may* do, and a nav item promising activity would be a claim the screen
> behind it cannot honour. The cluster filing is unchanged and was right for
> the reason B.1 gave — it reads a source, it does not analyse one.
>
> **Measurement is now full**: six items, no free seat. A seventh section there
> is a palette decision, not a nav edit, and `clientNav.test.ts`'s projection
> — now `measurement: 0` — is where that will surface. Investigation still has
> four free seats, one of them earmarked for Prompt discovery (G).

**Structure, not decoration.** A group renders a `<ul>` with an accessible name
and each item is an `<li>`, so a screen reader reports "Measurement, list, 6
items" rather than encountering ten undifferentiated links. The label is
quieter than the items it names — it is a wayfinding aid, not a destination.

**The `<li>` is a plain flex item, never `display: contents`.** That property
would remove its box and let the `<a>` be the flex child directly, which is
tidier CSS and a real hazard: several browsers have shipped bugs where it
strips `<li>` from the accessibility tree, taking the list semantics this
grouping exists to provide with it. Epic 9.16 refused the same property on
`.avp-reveal-group` for a related reason.

**Clusters are separated by space before they are separated by label** —
`--avp-space-6` between groups against `--avp-space-1` between items. A
grouping that only reads once you have read its labels is not doing the work.
Measured across viewports: the strip is one row at 1440px and stacks cleanly at
420px, where the labels earn their place most.

The table lives in `apps/web/src/components/client/clientNav.ts` as data rather
than JSX, which is what makes `clientNav.test.ts` able to assert that no cluster
repeats an accent, none outgrows the layer, and the roadmap's three remaining
sections still fit. Ten `accent={…}` props scattered through markup have
nowhere to be checked — which is how Epic B's crimson/magenta collision reached
a live browser.

---

## 6. Components

| Component | Notes |
|---|---|
| `Button` | `primary \| secondary \| ghost \| danger` × `sm \| md \| lg`. Hover darkens; focus illuminates. |
| `Card` + `CardHeader/Title/Body/Footer` | Print-safe elevations only. `selected` reads as lit from within. |
| `Badge` | **System state only** — never a score. `live` (default off) adds a breathing dot for a state that is still happening. Tone crossfades over `200ms`. |
| `VisibilityBadge` | The ordinal read of a score. Label colour resolved by luminance. |
| `MetaChip` | **A fact, never a state and never a score** — Epic 16.1. Sentence case, an `aria-hidden` Lucide glyph, the seated tone and a hairline; no `tone`, no `accent`, so it cannot be tinted into either of the other two pills. One token-only rule draws it on the paper scope (`ReportMetaItem` is this with the report's class) and on both Working themes. Use it wherever several short facts would otherwise sit in one line at one weight. |
| `DataTable<Row>` | Tabular figures, right-aligned numerics, hairline rules. Subject row accented *and* marked `aria-current`. |
| `ScoreDisplay` | The composite at 112px. Dim-to-lit reveal. Renders `—` for null. `badge` (default off) renders the `VisibilityBadge` under the band from the **same rounded score** as the numeral — Epic 16. |
| `ChartFrame` | Shared shell: title, caption, **required** `ariaLabel`, hidden data table. Recharts charts mount inside it too, inheriting the same a11y contract. |
| `ChartPatterns` | SVG pattern defs for competitor series — the B&W fallback. |
| `LuminanceLedger` | The hero. See §5. `unmeasured` (default off) draws the column as shape only and stops it claiming a score nobody took. |
| `LoadingState` / `ErrorState` / `EmptyState` | The three things a screen says when it has no content to show. See §6a. |
| `Reveal` / `RevealGroup` | Arrival motion. Client-only. See §5c. |
| `AnswerShelf` | The proof beat's shelf of ordinal slots. See §5a. |
| `TrendChart` | One line per series across a client's scan history. The first time-series shape here. See §5d. |
| `LocalNav` / `LocalNavItem` | Navigation scoped to ONE record, nested inside the agency shell. See §5e. |
| `ReportPage` / `ReportHeader` / `Beat` / `Prose` / `Evidence` / `FixList` | Narrative report primitives. See §7. |
| `ReportMetaItem` / `ScoreBlock` | The byline's shaped facts, and the framed score-plus-explanation unit — Epic 16. See §7. |

---

## 6a. The three empty voices — `LoadingState`, `ErrorState`, `EmptyState`

A screen with no content to show is saying one of exactly three things, and each
has one treatment app-wide. Epic 9.11 unified the first two — it replaced four
bespoke loading paragraphs and five bespoke error cards. Epic 9.19 added the
third, which had been four hand-written variants of the same idea, three of them
a bare `<span>` inside a table cell.

| | Says | Treatment |
|---|---|---|
| `LoadingState` | "wait" | Names the work. **No spinner, no progress bar** — this product cannot measure progress on any of its long operations, and a bar that fills on a timer is a lie. |
| `ErrorState` | "that did not work" | Seated card. Title is a statement, never a status code. Machine code small and last. |
| `EmptyState` | "there is nothing here yet" | Dashed hairline, editorial title, an action, and an optional data-drawn figure. |

### Why `EmptyState` is dashed rather than seated

A dashed stroke already means one specific thing in this system: **an absence
that is itself the finding.** `.avp-shelf__notch` draws the rank nobody is
standing in, `.avp-ledger__gap-zone` outlines the points not earned, and
`.avp-ledger--empty` frames a scan with nothing to score. An empty screen is
that same fact at page scale, so it takes the same stroke instead of a new one.
`ErrorState` stays a seated card because an error is a thing that *happened*.

### The `figure` slot is not an icon slot

The licensed-assets rule excludes icon packs and illustration kits, and this system's
house style is that a graphic is **data drawn as illustration** — the Luminance
Ledger and the Answer Shelf both are. So what a caller passes is the shape of
the data that will exist once the screen is not empty.

The dashboard's brand-new-agency state passes a `LuminanceLedger` with the five
real §6 weights and every sub-score at zero: the column draws as an unlit void
with the weights named beside it. That is the palette's organising idea —
visibility is luminance — applied to the one screen in the product where
nothing is lit yet.

**`unmeasured` is what makes it honest.** Sub-scores of zero already draw the
right picture, but the chart would still tell a screen reader *"AI Visibility
Score: 0 out of 100"* and tabulate five measured zeroes. The prop suppresses the
composite claim, the gap annotation and the per-dimension values, and names the
chart as the structure of a scan rather than the result of one. It defaults to
**false**, so neither report call site is affected — the same guardrail
`staggerDimensions` uses, and asserted the same way.

The figure is capped at `22rem`, near the Ledger's natural 344 units. A chart
drawn from a viewBox scales its **type** with its box: 13px dimension labels
become 30px stretched across a Working screen's full column, which is larger
than the page's own headline.

---

## 7. Report primitives — narrative, not dashboard

The narrative-report rule requires primary screens to be a narrative report:
**score → biggest gap → proof → fix → pitch.** These primitives make that the
path of least resistance.

`BeatId` is a union of exactly those five ids, so a beat cannot be invented ad
hoc. `<Beat>` numbers itself from `BEAT_SEQUENCE`, and the spacing between beats
(`--avp-beat`) exceeds any spacing inside one.

**Beat headings are claims, not categories.** "You appear in fewer than half the
answers buyers see" does the work; "Mention Rate" does not. The eyebrow carries
the structural label so the heading is free to argue.

`<Evidence>` takes `engine`, `prompt`, and `findings: {label, value}[]` — and
nothing else. There is no free-text body prop and no children, so **a paragraph
of scraped answer text has nowhere to go.** The facts-only rule is enforced by the
prop types rather than by a reviewer noticing.

### The byline and the score beat — Epic 16

**`<ReportMetaItem icon mono>`.** The header's five facts — domain, industry,
prompt count, engine count, scan date — used to sit in one grey run at one
weight and read as a log line under the title. Each now has a shape: the
seated tone, a hairline and the chip radius the shape lock reserves for
chips, with a Lucide glyph at 13px that is `aria-hidden` because the text is
the fact. Level-1 elevation only, so it prints; never on the ramp, so it can
never be read as a score. `mono` is for the domain.

**`<ScoreBlock figure>`.** A rule above, a rule below, and the figure and
its prose in two columns between them, collapsing to one under 40rem. The
figure is `ScoreDisplay` with `badge` — the badge painted from the same
rounded score as the digits, which is what makes the tie structural rather
than a coincidence of two interpolations a fraction apart — and the Luminance
Ledger drawn `compact` at its side. The full column still follows in the gap
beat with its gutter labels and gap annotation; the compact one is the same
chart at the size of the number, so the number is never seen without the
shape that explains it. Drawn only when there is a score. Print-safe
throughout: hairlines, the paper ramp, no fill that would not survive
greyscale.

---

## 8. Consuming the system

```ts
// apps/web — tailwind.config.ts
import preset from '@avp/design-system/tailwind-preset'
export default { presets: [preset], content: ['./src/**/*.{ts,tsx}'] }
```

```tsx
import '@avp/design-system/styles.css'
import { Button, LuminanceLedger, Beat } from '@avp/design-system'
```

The preset uses `theme` (**replace**), not `theme.extend`, for colour, spacing,
font family/size, shadow and radius. Tailwind's stock palette is **removed**, so
`bg-slate-500` and `text-blue-600` simply do not compile. Off-system colour
cannot ship by accident, because the class does not exist — that is how
the design-system-only rule is enforced mechanically rather than by vigilance.

The system's own components are authored in plain CSS over the token custom
properties, not Tailwind utilities, so the package renders in Next.js, in the
Vite style guide, and in the PDF export path without dragging a Tailwind build
into each.

**Added in Epic 7:** `lineHeight` (`leading-display | ui | prose | mono`). The
`--avp-leading-*` tokens existed in `tokens.css` from Epic 0 but were never
listed in the preset, so `leading-prose` compiled to nothing — a class that
looks applied and does nothing, which is the worst failure mode a design system
has. `tokens.test.ts` now checks every `var()` the preset references against the
stylesheet, so a token exposed in one place and missing from the other fails
CI.

---

## 9. Token parity

`src/tokens/*.ts` and `src/styles/tokens.css` are two hand-maintained copies of
the same palette, which would diverge within a month. `src/tokens/tokens.test.ts`
parses the CSS and fails on any drift.

---

## 6. The Working-screen accent layer — `bench-*`, Epic 9.24

design-direction.md §1's Epic 9.24 note carries the full argument and the
numbers. This is where the layer lives and how to use it.

### The tokens

**Seven** categorical accents at hues **258 / 275 / 292 / 309 / 326 / 343 /
355**, four stops each. Six shipped in Epic 9.24; `crimson` at 355 was added by
Epic B, and is the last one the arc can hold — see "The layer is full" below.

| Index | Key | Name | Hue |
|---|---|---|---|
| 0 | `1` | cobalt | 258 |
| 1 | `2` | indigo | 275 |
| 2 | `3` | violet | 292 |
| 3 | `4` | orchid | 309 |
| 4 | `5` | magenta | 326 |
| 5 | `6` | rose | 343 |
| 6 | `7` | crimson | 355 |


| Stop | Light | Dark | Use |
|---|---|---|---|
| `050` | L 0.955 C 0.035 | L 0.245, clamped | Chip / tile wash |
| `100` | L 0.905 C 0.062 | L 0.325, clamped | Hairline on a washed surface |
| `600` | L 0.55 C 0.185 | L 0.785, clamped | The solid — icon, rail, chart series |
| `700` | L 0.46 C 0.158 | L 0.855, clamped | Hover / press |

Light stops are one lightness and one chroma across all six hues, built from a
single table in `tokens/color.ts` so that "every accent is the same weight" is
structural — two accents differing in lightness would make one read as more
important, and categorical colour must not imply rank.

**Dark mode cannot reuse that table.** Blue at hue 258 cannot be both light and
saturated inside sRGB, so every dark stop takes 92% of the gamut ceiling at its
own lightness. That is why `[data-theme='dark']` carries 24 literal values
rather than a formula. Every pair clears 4.5:1 in both themes; hover *deepens*
in light and *lightens* in dark, for the reason the neutral axis flips.

### The layer is full at seven — Epic B

Two hard constraints bound where an accent may sit, and solving both across the
wheel leaves exactly **one arc, 256.5° to 355° — 98.5° wide**:

1. **`BENCH_HUE_BUFFER`** — at least 30° from every meaning-bearing hue (the
   five visibility stops, `beacon`, and the four semantics), so a chip cannot
   be read as a score or as a system state.
2. **The sRGB gamut at the shared chroma table.** `600` needs C 0.185 at
   L 0.55 and `700` needs C 0.158 at L 0.46. Blue cannot hold that: at hue 241
   the ceiling is **0.128**, so an accent placed there renders clamped and
   visibly duller than its neighbours — which reads as rank, the one thing
   categorical colour must not imply. This is why the arc does not extend into
   blue however much room the 30° buffer leaves there.

Seven accents sit ~16° apart in that arc. `crimson` at 355 is its last seat,
exactly 30° from `danger` — the buffer's stated threshold, met rather than
exceeded.

**An eighth cannot be added without giving something up.** Ten accents in 98.5°
is ~11° apart, below what hue alone separates at fixed lightness and chroma.
`tokens.test.ts` holds the arithmetic — including a gamut check that fails if
any accent's `600` or `700` stop leaves sRGB.

**Resolved by Epic B.1: the nav grew groups, not the layer.** Three options were
weighed — relax the buffer, let chroma vary, or cluster the nav — and only the
third costs nothing real. Shrinking the buffer lets a chip be misread as a
score or a system state; varying chroma makes one accent read as more important
than another, which is the property this table exists to guarantee. Clustering
touches neither: **a hue only has to be told apart from the others in its own
cluster**, so each cluster restarts at accent 0 and none approaches seven.

That is not a new idea in this product, it is an existing one written down.
`WorkspaceShell`'s sidebar and `ClientSpace`'s strip are on screen together and
have shared hues 0–3 since Epic 9.24 — Dashboard and Overview are both cobalt,
Clients and Rankings both violet — and nobody has read it as a collision,
because the two navs are different places doing different jobs. A hue was
already scoped to its nav; B.1 scopes it one level further, to its cluster.

**The cost, stated plainly:** two items in the SAME strip can now share a hue,
separated by a group label rather than by sitting in a different region of the
screen. That is weaker separation than the sidebar/strip precedent, and it is
the deliberate trade — repetition across clusters is the mechanism that buys
the seats. Partitioning the arc between clusters instead would keep every hue
unique and buy nothing at all.

See `LocalNavGroup` in §5e and `apps/web/src/components/client/clientNav.ts`,
where the cluster table and its invariants live.

**The pressure is already visible.** Epic B's first draft put four stat tiles
at accents 6 (crimson 355), 0, 2 and 4 (magenta 326). Twenty-nine degrees apart
at the same lightness and chroma, in two small chips at opposite ends of a row,
crimson and magenta read as the same pink — and on a client where both figures
were `0` the tiles were indistinguishable. The fix was to unaccent the tile
that is not a finding, which was the more correct answer anyway.

### Using it

```tsx
<NavItem href="/clients" label="Clients" accent={2} />   // sidebar
<LocalNavItem href={`${base}/sources`} label="Sources" accent={1} />
<StatTile label="Clients" value="12" accent={1} />       // a COUNT
<StatTile label="Median visibility" value="56" />        // a SCORE — no accent
<TrendChart points={p} series={s} palette="working" />   // competitor hues
```

Accent indices are **fixed by name, never by array position** —
`WorkspaceShell`'s `ACCENT` record and `ClientSpace`'s are both keyed by
section. An operator who has learnt where the violet icon is should not have to
re-learn it because somebody reordered the markup.

Components read the accent as a **custom property**, not a colour literal
(`benchVar`, not `benchColor`), so chrome follows a theme switch. `benchColor`
is for an SVG fill resolved at render time, where a literal is correct.

### Two rules that are not style preferences

1. **A score is never wrapped in a categorical hue.** The visibility ramp is
   already the colour language for a score. `Median visibility`, `Latest`,
   `Best` and `Technical foundation` are unaccented for this reason.
2. **The Report never references these tokens.** Not a convention —
   `tokens/reportIsolation.test.ts` scans every report surface and fails on any
   of the five ways a bench token can be written, and separately fails if the
   Working screens stop using the layer.

### `StatTile` / `StatRow`

`DashboardView` and `ClientsView` each kept a private two-span `Stat` helper,
and `ClientsView`'s carried the condition for moving it: "two call sites would
be a component built for a coincidence. If a third screen wants it, that is the
point to move it." Epic 9.24 was that third screen. `StatRow` is
`auto-fit`/`minmax` rather than a fixed column count, so the same tiles fill a
90rem Working column and reflow on a narrow viewport without a breakpoint.

`StatTile` renders `<div><dt/><dd/></div>` inside `StatRow`'s `<dl>` — the
grouping HTML defines for a description list. Deliberately not an `<a>`.

### The off-scale guard

The Tailwind preset REPLACES the spacing scale, so `w-40` and `h-2.5` do not
exist and compile to **nothing** — the element silently gets no size, and no
test catches it because jsdom applies no stylesheet. Two were found by eye in
one afternoon during this epic, one of them (`w-40` on Technical's VerdictBar)
shipped in Epic 9.22 and survived a review and a screenshot pass.
`reportIsolation.test.ts` now greps every spacing utility against the preset's
actual scale.

---

## 7. SentimentTide — tone, as a diverging tide. Epic A.

design-direction.md §1's Epic A note carries the colour argument. This is the
component.

### The shape

One group per scan, one bar per engine. Positive above the waterline, negative
below, neutral straddling it. `sentimentTideLayout.ts` holds the arithmetic and
is tested without a renderer — the split `ledgerLayout.ts` established.

```tsx
<SentimentTide
  points={tidePoints(history)}      // oldest scan first
  engineAccent={ENGINE_ACCENT}      // shared with the Prompts screen
  engineLabel={{ claude_search: 'Claude + search' }}
  animate
  ariaLabel="Tone toward Plausible Analytics by engine across 3 scans."
/>
```

### Four buckets, and only three of them are drawn

| Bucket | Drawn | Why |
|---|---|---|
| `positive` | Above the line | — |
| `neutral` | Straddling the line | Half above, half below, so it implies neither direction |
| `negative` | Below the line, hatched | Pattern as well as position, for greyscale and CVD |
| `unclassified` | **No** | The subject was never named, so tone was never asked |

`unclassified` reaches the reader as a count — in the hidden data table and in
the caller's tiles, in words. A fourth rect on a chart of tones would be read as
a fourth tone, and folding it into `neutral` would report a brand nobody
mentioned as having been described indifferently. The layout type gives it
`{ count }` and no geometry, so it cannot be drawn by accident.

### An engine that failed gets no column

Not four zeros. A flat column on the waterline reads as "described you
neutrally" rather than "was down". `layout.missing` reports the absence and the
hidden table says `did not answer` in words.

### The waterline moves

Sized by the largest stack in each direction rather than centred — see the
design-direction note. One unit scale is preserved; only the zero line moves.
Found by looking at real data in a browser, after the centred version left 45%
of the figure empty.

### The negative hatch is painted per engine — design review, 2026-09-02

A single shared `<pattern>` filled with `currentColor` rendered perfectly and
was wrong. A paint server resolves `currentColor` against the element that
DEFINES it — `<defs>`, which inherits nothing from the `<g class="avp-tide__bar">`
that sets `color` per engine. So every negative block came out the same
`ink-800` grey while every positive and neutral segment beside it was
engine-coloured, and two engines' negative tone were indistinguishable.

The chart now emits one `<pattern>` per engine, with the colour baked in at
definition time. The other obvious fix does not work: a `<rect fill="url(#id)">`
cannot reach inside a pattern to recolour it. Bounded by the engine registry —
three today, six at most — so it is a handful of extra defs, not a per-bar cost.

`negativePatternId(engine)` is exported so a test resolves the same id the chart
emits. The old assertion checked only that a pattern was REFERENCED, which is
why it kept passing while the colour was wrong; the new one reads the resolved
fill out of two engines' patterns and asserts they differ.

**Latent, and stated rather than fixed:** the ids assume ONE tide per document.
That assumption predates this change — the single shared pattern had it too —
and holds while the chart appears once on its own screen.

### What it inherits

The Epic 9.21 bound (`maxWidth: layout.width`, derived from the layout, never
declared), the `ChartFrame` accessibility contract, and the null-vs-zero
discipline every chart in this system carries.

## 8. GapGrid — the answer-gap grid. Epic B.

Prompts down, brands across, one small integer per cell: how many of that
prompt's engines named that brand.

```tsx
<GapGrid
  rows={gaps.rows}
  brands={gaps.brands}
  sort="recurrence"
  accent={6}
  animate
  pending={pending}
  caption="24 prompts across 3 engines…"
/>
```

### It is a real `<table>`, and that is the point

Every other chart in this system is an SVG inside `ChartFrame`, which gives it
`role="img"`, an `aria-label` and a visually-hidden table equivalent. This one
is not, because it does not need to be: the data IS tabular, so the accessible
representation and the visible one can be the same object. An SVG heatmap here
would mean drawing a table and then hiding a second copy of it for screen
readers — two things to keep in step instead of one. `<th scope>` on both axes,
so a cell is announced with its prompt and its brand rather than as a bare
number.

### Colour is never the only carrier

Every cell prints its count; intensity is a second reading of the same number.
Every row prints its verdict as a word, not a colour. That is what keeps the
grid legible in greyscale and under CVD — the rule `VisibilityBadge` and the
nav's current state both follow.

### The subject is `beacon`; rivals stay neutral

design-direction.md §1: one brand, one colour, everywhere. The client under
analysis is `beacon` here exactly as on every trend. Rivals do **not** take
bench hues — on this screen a rival is not a category to be told apart from
other rivals, it is the thing that took an answer, and colouring six of them
six ways would say otherwise. The screen's own bench accent belongs to the
SCREEN: the header rule and nothing else.

`absent` takes `warn`, not `danger`. A rival owning an answer is a finding to
act on, not a system fault, and §1 reserves `danger` for state. A table where
nine of twenty-four rows were red would read as an outage.

### `no_brands` is drawn as neither a gap nor a win

Its chip is dashed and grey, its row recedes, and it sorts below every real
verdict. `layoutGapGrid` derives `isGap` from the row KIND, never from
`absentOn` — the two states have identical `absentOn`, which is exactly how a
regression here would go unnoticed.

### A truncated header keeps its full name

`.avp-gapgrid__brand-name` truncates at 8rem, so "Growthmarketingpro" renders as
"Growthmarketin…". The header carries `title={brand.name}`, extending the
affordance `.avp-gapgrid__chip` already uses for its verdict rather than
inventing one — before it, the full string was reachable only by scrolling to
the rivals table at the bottom of the page.

### Motion: one fill, no stagger

Cells fill from transparent to their intensity at `--avp-duration-layout`
(320ms), once, together. The first draft staggered them 28ms per row capped at
12, putting arrival at 656ms — which is performing on every load, the thing
§4's Epic 9.16 note excludes Working screens from. It also depicted something
that did not happen: the grid arrives whole, from one response, so a cascade
was decoration. `tokens.test.ts` now asserts the rule carries no
`animation-delay`.

`pending` dims to 0.45 and sets `aria-busy` while a different scan is fetched,
rather than the screen unmounting to a loading state — which in the first draft
took the scan picker down with it, so the control an operator had just used
vanished from under their pointer.

### It scrolls inside itself

`overflow-x: auto` on the container. A grid of 24 prompts × 8 brands is wider
than a narrow viewport and the PAGE must never scroll sideways — the rule Epic
9.21 set for the charts. Verified at 420px: page `false`, grid `true`.

## 9. TrendChart annotations — Epic E

A scan that produced an alert gets a marker on the trend it appears in.

```tsx
<TrendChart points={points} series={series}
  annotations={alertAnnotations(alerts)} />   // { [stamp]: 'what happened' }
```

### No extension point was added to `trendLayout` for this

It already had one. `TrendLayout.columns` is the x position of every point,
which is exactly and only what an axis marker needs — so annotation is a
rendering concern sitting on top of the existing layout, not a second thing the
layout has to know about. `trendLayout.test.ts` asserts `columns` stays aligned
with `points` and that stamps are unique, which is what makes keying by stamp
safe.

### Keyed by stamp, never by index

A series can change length between renders; an index cannot survive that. The
stamp is `HistoryScanOut.scanned_at`, and the alert feed returns the **same**
`COALESCE(finished_at, created_at)` expression so the two key together. They did
not in the first draft — the feed used `created_at`, the two differed by the
scan's duration, and every marker silently failed to draw. Found by counting
markers in a live browser, now guarded by a cross-endpoint test.

### Drawn under the axis, as a triangle

**Under the axis, not on the plot.** A mark among the lines reads as a data
point, and an annotation is not a measurement — it is a note that something
happened at this reading.

**A triangle, because every other mark in this chart is a dot or a line.** Shape
carries the distinction, so it survives greyscale and CVD without spending a
colour. It takes `warn` for the reason `GapGrid`'s `absent` chip does: a finding
to act on is not a system fault, and §1 reserves `danger` for state. The date
under an annotated column is emphasised too, so the marker is not the only thing
carrying it — weight, not hue.

**Several alerts on one scan collapse to one marker with a count.** Notion's
real event produced three, one per engine; three triangles stacked on one date
would read as three separate events.

**The annotation also becomes a `Notes` row in the hidden data table.** The
marker's `<title>` is reachable by a pointer and not by a screen reader — the
SVG is `aria-hidden` precisely because that table is its accessible equivalent,
so an annotation living only in the SVG would be visible to nobody who needs
the table.

**Working screens only.** The Report never passes `annotations`: an annotated
document is a different artefact from the one design-direction.md §0 argues for,
and `palette` already defaults to `'report'` so a chart dropped into it stays
unannotated by omission rather than by discipline.

## 10. Alert row — Epic E

One entry in a client's alert feed, built from `Card` + `Badge` + `Button`.

**An acknowledged row recedes; it does not disappear.** `.avp-alert.is-acknowledged`
drops to `opacity: 0.6` and returns to 1 on hover. The first draft removed the
row from the list the moment it was acknowledged — and the real data puts three
alerts on one scan, so acknowledging the first made the other two jump under the
cursor. The fix was not to animate the exit but to not have one.

**Opacity, not a colour swap.** Every word in the row is still true after
somebody has read it; greying the text would say the finding had expired rather
than been seen. `tokens.test.ts` asserts the rule carries `opacity` and no
`color`.

**The transition runs at the HOVER tier**, not the state tier, because the same
declaration serves both the settling of an acknowledged row (occasional) and the
hover that lifts it back (frequent). The frequent trigger wins; a large opacity
delta still reads at 120ms. `border-color` was in that transition list until the
motion audit found `.avp-card` already carries the same hairline, so that half
animated nothing.

**The acknowledge button holds its widest label's width** (`.avp-alert__ack`).
"Acknowledging…" is wider than "Acknowledge", so without it the button grows on
click and the row's right edge jumps — a layout fix, not a motion one, because
animating a jump is worse than not jumping.
