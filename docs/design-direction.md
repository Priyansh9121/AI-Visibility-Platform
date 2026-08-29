# Design Direction — Epic 0 Proposal (SIGNED OFF; see the status note)

Nothing in `packages/design-system` is built until this is signed off.

> **Status, added in Epic 7.1.** The heading said "AWAITING APPROVAL" for the
> whole life of the project. It has been stale since Epic 0.4: all five items in
> §6 are built, and this document's own rule is that nothing in
> `packages/design-system` is built until they are signed off. (No approval event
> is recorded anywhere in `build-log.md` — the sign-off is inferred from the work
> having proceeded, which is the same evidence every epic since has relied on.)
>
> §6 item 5 asked to build **B (the Luminance Ledger)** as the motif, and only B.
> **A and C were never in the sign-off list** — they appear in §5's
> *Recommendation* prose, which is a proposal, not an approval. `design-system.md`
> §5 nonetheless recorded them as "approved and land in Epic 7", and no epic ever
> built them, because every downstream plan (`product-spec.md` §7's Epic 7
> checklist, `api-contracts.md`'s deferred register, Epic 7's own brief) was
> written from a different list that never mentioned them.
>
> Resolved in **Epic 7.1**: **Direction A is built** (`AnswerShelf`,
> `design-system.md` §5a). **Direction C is half built** — its deliverable, the
> heaviest unclaimed third-party domain, ships as a named fix in the fix beat,
> while the bipartite map itself is deferred with a stated reason
> (`design-system.md` §5b). See `build-log.md`, Epic 7.1.

**Designed from:** the data model and the user goal only. No competitor screen,
screenshot, or markup was referenced. (`docs/ip-safety.md` constraints 1 and 5.)

---

## 0. The two contexts every screen has to survive

Every design decision below falls out of one observation: **this product's main
output is not a dashboard, it's an argument someone makes to another person.**

| Context | Who's looking | Demands |
|---|---|---|
| **Working** | Agency operator, 20 tabs open, running scans back to back | Density, scanability, fast comparison, tolerant of dark UI |
| **Presenting** | A prospect's CMO, in a meeting, often on a printed or PDF'd page | Credibility, high contrast, survives greyscale print, zero "hacker dashboard" energy |

The presenting context is the one that closes deals, so it wins the ties.
That single ruling produces: a **light-first, paper-derived** palette; an
**editorial serif** for the argument; a colour ramp that stays legible in
**greyscale**; and elevation rules that behave like **printed card stock rather
than floating glass**.

It also means the report is a *document*, which is exactly what IP-safety
constraint 3 (narrative report, not a metrics-tile grid) is pushing toward. The
two constraints agree, so I've leaned in hard.

---

## 1. Colour — "Lit / Unlit"

### The core idea

The product measures one thing: **are you present in the answer, or absent?**
So the palette's organising metaphor is literal — **visibility is luminance.**
Dim, low-chroma, dark = invisible to AI engines. Bright, saturated, light = cited
and recommended.

This gives the system something most SaaS palettes don't have: the colour scale
*means* something, rather than being decoration with a legend bolted on. A viewer
who never reads the legend still reads a dark chart as bad news.

### Neutrals — warm paper, not cool grey

Every SEO/analytics tool defaults to a cool blue-grey (hue ~250). Rotating the
neutral axis to a **warm hue ~70** at very low chroma does two things: it reads as
paper/document rather than app chrome, and it makes the product instantly not-look
like the category. Costs nothing, differentiates immediately.

| Token | OKLCH | Use |
|---|---|---|
| `paper-000` | `oklch(0.995 0.004 75)` | Page ground |
| `paper-050` | `oklch(0.978 0.006 75)` | Sunken wells, table zebra |
| `paper-100` | `oklch(0.958 0.008 75)` | Seated surfaces |
| `paper-200` | `oklch(0.925 0.010 75)` | Hairlines, dividers |
| `paper-300` | `oklch(0.870 0.012 75)` | Strong borders, disabled fills |
| `ink-400` | `oklch(0.620 0.014 75)` | Tertiary text, axis labels |
| `ink-600` | `oklch(0.470 0.018 265)` | Secondary text |
| `ink-800` | `oklch(0.285 0.022 265)` | Body text |
| `ink-900` | `oklch(0.185 0.026 265)` | Headings, the score numeral |

Note the deliberate hue **hand-off at `ink-400`**: light neutrals are warm (paper),
dark neutrals are cool (ink). Warm dark greys read muddy and cheap in print; cool
dark greys read like actual printed ink. Nobody consciously notices; everybody
feels it.

### The visibility ramp — the most important decision in the palette

Used for the score, the sub-dimension bars, and any "how visible" encoding.

| Stop | OKLCH | Band | Means |
|---|---|---|---|
| `vis-00` | `oklch(0.42 0.045 35)` | 0–19 | Absent — dark, nearly desaturated clay |
| `vis-25` | `oklch(0.545 0.115 45)` | 20–39 | Barely there — ember |
| `vis-50` | `oklch(0.655 0.135 65)` | 40–59 | Emerging — amber |
| `vis-75` | `oklch(0.735 0.125 150)` | 60–79 | Established — green-teal |
| `vis-100` | `oklch(0.815 0.115 195)` | 80–100 | Beacon — lit cyan |

Three properties this ramp has on purpose:

1. **Monotonic in lightness** (0.42 → 0.82). It therefore survives greyscale
   printing and photocopying, which the traffic-light red→amber→green ramp does
   not — red and green are near-identical greys. The report gets printed. This
   matters.
2. **Colour-vision-deficiency safe.** The warm→cool traverse (orange → teal) is
   the one axis deuteranopes and protanopes reliably keep. ~8% of men would read a
   red/green score wrong.
3. **Chroma rises with lightness**, so "more visible" is simultaneously lighter
   *and* more saturated — the illumination metaphor is doubly encoded.

**Rule attached to it:** ramp colours are **fill-only, never text.** The high end
is too light to hit 4.5:1 on paper. Labels on a ramp fill are `ink-900` or
`paper-000`, picked by a luminance threshold at L≈0.62.

### Client vs competitor — a semantic rule, not a palette

Competitor series must **not** use the visibility ramp. If a competitor renders in
"good green" it implies we're endorsing them, and if it renders in "bad red" the
report looks like a hatchet job and loses credibility with the CMO — which is the
one thing the report cannot afford.

- **The client / prospect** is always `beacon-600` (`oklch(0.545 0.125 200)`), the
  brand accent. Solid fill. One brand, one colour, everywhere in the product.
- **Competitors** are always drawn from a neutral slate family, separated by
  *lightness and fill pattern* rather than hue: solid → 45° hatch → dot → outline.
  Non-judgmental, unlimited series count, and it prints in black and white.

The argument the report makes is "here is the gap," and the gap reads more
honestly when your competitors are drawn plainly.

### Semantics

Kept deliberately narrow so they never fight the visibility ramp for meaning:
`success oklch(0.55 0.12 150)` · `warn oklch(0.70 0.13 75)` ·
`danger oklch(0.55 0.17 25)` · `info` = `beacon-600`. Semantic colours are for
*system state* (a scan failed, a quota is nearly used) — never for score values.

### Dark mode

Not a colour inversion. The visibility ramp **keeps its direction** — dim stays
bad, lit stays good — because that mapping is the concept. Only the neutral axis
flips (ink becomes ground, paper becomes text), and the ramp gains ~0.04 lightness
at the low end to stay visible against a dark ground. Dark mode is for the working
context; the presenting/export context is always light.

---

## 2. Typography

All OFL-licensed, all from Google Fonts (constraint 4). Three faces, three jobs.

| Role | Face | Why |
|---|---|---|
| **Editorial** — report headings, the score, pull quotes | **Fraunces** (variable) | A serif is the single loudest signal that this is a *document making an argument*, not a metrics grid. Fraunces is variable with `opsz` and `SOFT`/`WONK` axes, so it can be set warm and characterful at display sizes without being a stiff Times-alike. It has genuine personality — which is what makes the artifact feel like ours. |
| **Interface** — labels, tables, controls, body | **IBM Plex Sans** | Deliberately *not* Inter. Inter is the default of the entire category and reads as generic-SaaS. Plex has slightly more character, is unambiguously legible at 12–14px, ships true **tabular figures** (mandatory — we have comparison tables full of numbers that must align), and its designed-together mono is a free win. |
| **Evidence** — prompt strings, cited URLs, schema snippets | **IBM Plex Mono** | Same skeleton as Plex Sans, so evidence blocks sit inside body copy without a visual seam. Monospacing marks "this is verbatim machine output" — a useful honesty signal given constraint 7 limits what we may quote. |

### Two scales, on purpose

One modular scale can't serve both jobs. UI needs 13px *and* 14px to be different
things; editorial needs headlines that actually leap. So:

**Interface track — ratio 1.125:** `11, 12, 13, 14, 16, 18, 20`
Tight steps because dense UI is about small deliberate distinctions.

**Editorial track — ratio 1.25:** `20, 25, 31, 39, 49, 61, 76`
Wide steps because the report's job is hierarchy you can read across a
conference table.

**Score numeral — its own size, `112px`,** outside both scales. The score is the
single most important object in the product; it earns a bespoke size rather than
being the top rung of a ladder.

Line heights: `1.15` display · `1.35` UI · `1.6` prose · `1.5` mono.
Measure: report prose capped at **68ch**. Tracking: `-0.02em` above 39px,
`0` at body, `+0.01em` on all-caps micro-labels.

---

## 3. Spacing — 4px base, non-linear tail

`2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 56, 72, 96, 128`

Pure 4px multiples give you six near-identical options between 24 and 48 (where
almost nothing needs that precision) and nothing useful past 64 (where layout
actually lives). This scale is fine-grained where components need it and jumps
where sections need it. Above 24 the steps grow ~1.35×, so section breaks read as
clearly intentional rather than "someone typed 44."

Two extra tokens that matter more than the ramp:
- **`rhythm: 8px`** — the vertical grid the report's narrative beats snap to. When
  score / gap / proof / fix / pitch all sit on one rhythm, the page reads as a
  single document rather than five stacked widgets.
- **`beat: 72px`** — the standard gap *between* narrative beats. Larger than any
  intra-beat spacing, so the document's five-part structure is legible from the
  page thumbnail.

---

## 4. Elevation — "paper doesn't float"

The category default is big soft blurred drop shadows — material floating over a
canvas. That reads as generic app chrome and, critically, **shadows disappear when
the report is printed**, taking the entire visual hierarchy with them.

So elevation here is built from **borders and tight offsets**, like stacked card
stock:

| Level | Name | Treatment |
|---|---|---|
| 0 | `flat` | On-ground. Hairline `1px paper-200`. No shadow. |
| 1 | `seated` | Surface shifts to `paper-050`/`paper-100` + hairline. **No shadow at all** — separation by tone. |
| 2 | `raised` | Hairline + `0 1px 0` hard offset, 0 blur. Printed-card edge. |
| 3 | `lifted` | Popovers/menus. Hairline + `0 2px 4px -1px` + `0 6px 12px -4px`, ink at low alpha. |
| 4 | `overlay` | Modals. Scrim `ink-900 / 40%` + level-3 shadow at 1.5× spread. |

Levels 0–2 print correctly. Levels 3–4 are transient UI that never appears in an
export, so they're allowed to use blur.

### The ownable part: emphasis is light, not lift

Because the whole system says *visibility = luminance*, emphasis states don't
raise an element — they **illuminate** it:

- **Focus:** a 2px `beacon-400` ring at 60% + a 4px outer halo at 12%. Never an
  outline offset shift, never a lift.
- **Selected / active:** `inset 0 0 0 1px beacon-600` plus a `beacon` tint wash at
  4% — the element reads as *lit from within*.
- **Score reveal:** the hero animates from `vis-00` to its true value over 600ms
  `cubic-bezier(0.22, 1, 0.36, 1)` — a dim-to-lit dissolve, not a number counting
  up. The metaphor is stated in motion the first time you see the product.

Everything else: 120ms for hovers, 200ms for state, 320ms for layout,
all `ease-out`. Full `prefers-reduced-motion` fallbacks (reveal becomes an
instant paint).

> ### BUILT — Epic 9.16, 2026-08-29. Where each of these actually lives.
>
> This note exists because of this document's own history. §5's Directions A
> and C were recorded as approved and then **not read again for five epics**,
> and the status note at the head of this file is the correction. A motion
> section that says what should happen and never says whether it did is the same
> failure with a different subject, so:
>
> | Value | Where it is read today |
> |---|---|
> | `600ms` / `cubic-bezier(0.22, 1, 0.36, 1)` — the reveal | `--avp-duration-reveal` / `--avp-ease-reveal`. Read by `ScoreDisplay`'s numeral, the Luminance Ledger's bars, values and gap annotation, and — from 9.16 — by `Reveal`. |
> | `120ms` hover · `200ms` state · `320ms` layout | `components.css`, on buttons, cards, nav items and fields. |
> | The score reveal as dim-to-lit dissolve, not a count-up | `LuminanceLedger` and `ScoreDisplay`. Built in Epic 0, unchanged. |
> | `prefers-reduced-motion` fallbacks | Globally in `base.css`, and per-component via `lib/motion.ts`. |
>
> **Epic 9.16 added exactly one value to this section: a `70ms` stagger**
> (`--avp-stagger-reveal`), for siblings arriving in sequence. It is a delay,
> not a duration — the reveal already has a duration, and this says how far
> apart the members of a group begin. It is the only motion value this project
> has added since Epic 0.
>
> It also fixed a hole in the last row of that table. The global
> `prefers-reduced-motion` block reset every animation and transition
> **duration** and left every **delay** untouched, so the first staggered thing
> ever shipped would have honoured the setting by animating instantly and then
> waiting up to half a second before doing it. Both delays are reset now.
>
> **What was deliberately NOT given arrival motion**, so a later brief does not
> assume otherwise: the client-facing report (`ReportPage`, `Beat`,
> `AnswerShelf`, and both `/scans/{id}/report` and `/share/{token}`), Settings,
> and the dashboard shell. §0's ruling is that the presenting context wins ties,
> and §5's Direction C was declined partly because motion does not survive
> becoming a document. The Luminance Ledger sits on both sides of that boundary
> — the marketing page and the report render the same component — so its
> staggered mode is an opt-in prop defaulting to off, enforced by a regression
> test rather than by this paragraph. See `design-system.md` §5c and
> `build-log.md` Epic 9.16.
>
> **Corrected in Epic 9.16a.** The reveal originally defaulted to hidden and
> was revealed by script. That is safe only on screens which are never
> server-rendered, which the first four happened to be and the next two were
> not — `/invite/{token}` and `/reset-password/{token}` shipped blank until
> hydration. The default is now VISIBLE, and the hidden state is gated on a
> class a synchronous inline script adds before first paint. See
> `design-system.md` §5c.
>
> > **On "editorial but kinetic"**, the phrase the direction was chosen under:
> that is a genre name from a conversation, not a reference to anybody's site.
> The technique — content fades and rises, staggered by a fixed delay per
> sibling, triggered by scroll position — is generic. It was derived from this
> product's own content and Epic 0's own tokens; no real company's page, markup
> or stylesheet was inspected, measured or referenced. ip-safety.md #1 and #5
> apply here exactly as they apply to the three named competitors in
> `north-star.md` §2.

---

## 5. Signature visualisation — three directions

Constraint: explicitly not a gauge and not a radar chart. Both are generic, both
compress the interesting structure out of the data, and radar in particular
implies the axes are commensurable when ours aren't.

Each direction below is checked against a real question: *does the data model
actually produce this, and does it advance a specific beat of the
score → gap → proof → fix → pitch narrative?*

### Direction A — **The Answer Shelf**

*An AI answer has a limited number of slots. Who's standing in them?*

One horizontal row per tracked prompt. Along each row, ordinal slots hold the
brands the engine actually named, in order. The client renders as a filled
`beacon` marker; competitors as neutral slate markers; citation presence hangs as
a small anchor tick beneath. When the client isn't mentioned, its slot is drawn as
an **explicit empty notch**, not omitted.

Stack 30 prompt rows and the client's absences form a vertical band of holes down
the page. You see *the shape of absence* before you read a single word.

- **Data:** per prompt — ordered mention list, citation booleans. Facts only.
- **Beat:** **PROOF.** This is the receipt.
- **Risk:** needs a sensible cap and grouping past ~40 prompts.

> **Built in Epic 7.1** as `AnswerShelf`. The data was right: `BrandMention`
> already carried the ordinal and `Citation` the attribution, so no pipeline work
> was needed — only a per-prompt projection, which `ReportProofOut` had been
> aggregating away. The cap landed at 20 prompts, kept in whole prompts so a row
> is never missing one of its engines. See `design-system.md` §5a.

### Direction B — **The Luminance Ledger** ← recommended hero

*The score, drawn as light.*

The score is a vertical column composed of stacked sub-dimension segments. Each
segment is lit in proportion to how much of that dimension has been earned — the
earned part in the visibility ramp, the unearned part left as a dim unlit void
with a hairline showing its full possible extent. Competitors appear as narrow
**ghost columns**: outline only, unfilled, no ramp colour.

The single largest unlit segment is auto-annotated — and that annotation **is** the
"biggest gap" headline. The chart doesn't illustrate the narrative; it generates it.

- **Data:** the per-dimension breakdown that `scoring-spec.md` already requires
  the engine to return alongside the composite. No new pipeline work.
- **Beat:** **SCORE → BIGGEST GAP**, in one object.
- **Why hero:** it's the concept made visible. The palette, the metaphor, and the
  narrative structure are all the same idea, so the product feels authored rather
  than assembled. It also degrades gracefully — greyscale, 200px wide, or printed.
- **Risk:** breaks down past ~7 sub-dimensions. Constrains the scoring model, which
  I'd argue is a feature.

### Direction C — **The Source Map**

*Where do AI engines actually get their answers about this category — and are you there?*

A bipartite map: prompts on the left, the domains those answers cited on the right,
edges weighted by citation frequency. The client's domain is `beacon`. Competitor
domains are neutral. **Third-party sources nobody owns** — a subreddit, a review
site, an industry roundup — surface as the heaviest unclaimed nodes.

Those unclaimed nodes are the deliverable. They convert directly into "here are the
six pages we need to get you onto," which is the agency's actual product.

- **Data:** cited domains per answer + frequency. Facts only — domains and counts,
  never the cited page's text.
- **Beat:** **FIX → PITCH.**
- **Risk:** hairballs fast. Needs top-N aggregation and an "other" bucket. The
  heaviest engineering lift of the three.

> **Half built in Epic 7.1.** The *deliverable* shipped: the heaviest unclaimed
> domain is now a named fix in the fix beat ("Get onto eesel.ai — the source these
> answers keep citing"), which is the "here are the six pages we need to get you
> onto" this section argued for. The *map* did not. It re-presents data the
> "Cited instead" table already shows, it is the hairball risk named directly
> above, and it is the least likely of the three to survive §0's greyscale-print
> ruling. Deferred with that reason. See `design-system.md` §5b.
>
> One thing this section did not anticipate: the proof beat's citation table
> **ranks competitor-attributed domains above unattributed ones on purpose**, so
> the heaviest unclaimed domain — the whole point of this direction — was being
> ranked *below* one-citation rivals and could be truncated off the end. The fix
> reads its own field, computed before that ordering and before the cap.

### Recommendation

**Build B as the hero now**, in Epic 0, as the signature component. Ship A and C
as the proof and fix sections in the report epic.

They aren't three competing hero charts — they're three consecutive beats of the
same argument, and each one hands off to the next: *B* says you're at 34 and your
worst dimension is citations; *A* proves it prompt by prompt; *C* says here are the
eleven pages that would fix it. That progression is the product.

---

## 6. What I need signed off

1. Palette direction — warm paper neutrals + the lit/unlit visibility ramp, and
   the client-is-beacon / competitors-are-neutral rule.
2. Type — Fraunces + IBM Plex Sans + IBM Plex Mono, two-track scale.
3. Spacing — non-linear 4px scale, plus the `rhythm` / `beat` tokens.
4. Elevation — borders-and-offsets over blurred shadows; emphasis-as-light.
5. Motif — build **B (Luminance Ledger)** as the signature component.

Once approved I build the token layer, the component skeleton (buttons, cards,
tables, chart primitives, report layout primitives), the Luminance Ledger, and the
style guide page.
