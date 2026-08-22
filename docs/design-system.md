# Design System — Token & Component Reference

Required by product-spec.md §7, Epic 0: *"Document design tokens in
`/docs/design-system.md`."*

**Package:** `packages/design-system` (`@avp/design-system`)
**Style guide:** `corepack pnpm --filter @avp/design-system dev` → http://localhost:4100

Designed from the data model and user goal only. No competitor screen,
screenshot, or markup was referenced, and no competitor code was inspected
(`docs/ip-safety.md` #1, #5).

---

## The ruling that drives every decision

This product's main output is not a dashboard — it is **an argument someone
makes to another person.** It is read in two contexts:

| Context | Reader | Demands |
|---|---|---|
| **Working** | Agency operator running scans back to back | Density, scanability, dark-tolerant |
| **Presenting** | The prospect's decision-maker, often on a printed PDF | Credibility, contrast, survives greyscale |

The presenting context closes deals, so it wins ties. That single ruling
produces the light-first paper palette, the editorial serif, a colour ramp that
survives photocopying, and elevation built from borders rather than blur.

It also happens to be where ip-safety.md #3 (narrative report, not metrics
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

All OFL-1.1 via Google Fonts (ip-safety.md #4). Declared in exactly one place:
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
Layout: `--avp-report-width` 832px (PDF export parity) · `--avp-app-max` 1440px.

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

Motion: `120ms` hover · `200ms` state · `320ms` layout · `600ms` reveal.
`prefers-reduced-motion` is honoured globally and in each animated component —
`reveal` degrades to an instant paint, never a slower version of itself.

---

## 5. The Luminance Ledger — signature visualisation

`<LuminanceLedger subjectName dimensions competitors height animate annotateGap />`

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

Directions **A (Answer Shelf)** and **C (Source Map)** are approved and land in
Epic 7 as the *proof* and *fix* beats. The three are consecutive beats of one
argument, not competing hero charts.

---

## 6. Components

| Component | Notes |
|---|---|
| `Button` | `primary \| secondary \| ghost \| danger` × `sm \| md \| lg`. Hover darkens; focus illuminates. |
| `Card` + `CardHeader/Title/Body/Footer` | Print-safe elevations only. `selected` reads as lit from within. |
| `Badge` | **System state only** — never a score. |
| `VisibilityBadge` | The ordinal read of a score. Label colour resolved by luminance. |
| `DataTable<Row>` | Tabular figures, right-aligned numerics, hairline rules. Subject row accented *and* marked `aria-current`. |
| `ScoreDisplay` | The composite at 112px. Dim-to-lit reveal. Renders `—` for null. |
| `ChartFrame` | Shared shell: title, caption, **required** `ariaLabel`, hidden data table. Recharts charts mount inside it too, inheriting the same a11y contract. |
| `ChartPatterns` | SVG pattern defs for competitor series — the B&W fallback. |
| `LuminanceLedger` | The hero. See §5. |
| `ReportPage` / `ReportHeader` / `Beat` / `Prose` / `Evidence` / `FixList` | Narrative report primitives. See §7. |

---

## 7. Report primitives — narrative, not dashboard

ip-safety.md #3 requires primary screens to be a narrative report:
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
of scraped answer text has nowhere to go.** ip-safety.md #7 is enforced by the
prop types rather than by a reviewer noticing.

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
ip-safety.md #2 is enforced mechanically rather than by vigilance.

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
