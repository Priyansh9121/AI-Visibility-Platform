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

---

## 6. Components

| Component | Notes |
|---|---|
| `Button` | `primary \| secondary \| ghost \| danger` × `sm \| md \| lg`. Hover darkens; focus illuminates. |
| `Card` + `CardHeader/Title/Body/Footer` | Print-safe elevations only. `selected` reads as lit from within. |
| `Badge` | **System state only** — never a score. `live` (default off) adds a breathing dot for a state that is still happening. Tone crossfades over `200ms`. |
| `VisibilityBadge` | The ordinal read of a score. Label colour resolved by luminance. |
| `DataTable<Row>` | Tabular figures, right-aligned numerics, hairline rules. Subject row accented *and* marked `aria-current`. |
| `ScoreDisplay` | The composite at 112px. Dim-to-lit reveal. Renders `—` for null. |
| `ChartFrame` | Shared shell: title, caption, **required** `ariaLabel`, hidden data table. Recharts charts mount inside it too, inheriting the same a11y contract. |
| `ChartPatterns` | SVG pattern defs for competitor series — the B&W fallback. |
| `LuminanceLedger` | The hero. See §5. `unmeasured` (default off) draws the column as shape only and stops it claiming a score nobody took. |
| `LoadingState` / `ErrorState` / `EmptyState` | The three things a screen says when it has no content to show. See §6a. |
| `Reveal` / `RevealGroup` | Arrival motion. Client-only. See §5c. |
| `AnswerShelf` | The proof beat's shelf of ordinal slots. See §5a. |
| `TrendChart` | One line per series across a client's scan history. The first time-series shape here. See §5d. |
| `LocalNav` / `LocalNavItem` | Navigation scoped to ONE record, nested inside the agency shell. See §5e. |
| `ReportPage` / `ReportHeader` / `Beat` / `Prose` / `Evidence` / `FixList` | Narrative report primitives. See §7. |

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

ip-safety.md #4 rules out icon packs and illustration kits, and this system's
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
