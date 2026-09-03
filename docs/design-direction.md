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

> ### BUILT — Epic 9.19, 2026-08-29. Which screen is on which side, and how wide.
>
> This table stated a split for six epics and never said which screen was
> which, so one of them was on the wrong side of it for six epics without
> anybody being able to look it up. **Settings** — an operator's account and
> seat roster, never printed, never handed to a prospect — was rendering at
> `--avp-report-width`, which is the measure a *document* is read at. It is the
> only width mistake that existed; the dashboard and clients were checked
> directly rather than assumed, and both have been `wide` since Epic 9.13.
>
> | Screen | Context | Width | Arrival motion |
> |---|---|---|---|
> | `/dashboard` | Working | `--avp-app-max` (90rem) | No |
> | `/clients` | Working | `--avp-app-max` | No |
> | `/settings` | Working | `--avp-app-max` — **corrected in 9.19** | No |
> | `/scans/{id}/report`, `/share/{token}` | Presenting | `--avp-report-width` (52rem) | No — §4 |
> | `/` (landing), auth, `/welcome` | Presenting | `--avp-report-width` | Yes — §4, Epic 9.16 |
>
> Widening a screen is not free, and 9.19 paid for it rather than declaring it
> done: at 90rem the seat roster's Remove button and the clients list's status
> badge were both stranded mid-table, and the invite form's email field ran
> nearly the full viewport. Alignment and `--avp-form-width` fixed those in the
> same pass. **Screenshots before and after in `docs/screenshots/epic-9-19/`.**

> ### Amended — Epic 9.21. A client's own space, and a width that was not the problem.
>
> Epic 9.20 added `/clients/{id}` and its Sources and Rankings trends. All three
> are **Working** and take `--avp-app-max`, like every other screen in that
> column above: Overview is a six-column scan history that earns it, and the
> local nav must not change width between siblings.
>
> Those two trend screens were reported as reading sparse — *"a lot of space on
> the left and right… looks like an old newspaper."* **The width was not the
> cause, and it was measured before anything was changed.** The chart was not a
> narrow island in a wide column; it filled **96%** of it (1152px of 1200px).
> What was wrong was the chart's own type: `width: 100%` over a fixed viewBox
> scales type with the box, so an 11px axis label rendered at **17.6px**,
> larger than the page's 14px body copy. The smallest type on the screen had
> become the largest thing on it.
>
> Bounding the chart at its drawn width fixed it, and the second candidate —
> narrowing these two screens' bodies below the Working cap — was **tried and
> reverted**, because it moved nothing: every child of those sections
> (`max-w-headline`, `max-w-measure`, the capped figure) already caps itself, so
> constraining the parent changed no pixel. Verified by toggling each change
> independently in a live browser.
>
> **The rule this leaves behind, worth stating once:** a screen looking sparse
> is not evidence that its container is too wide. Measure the type before
> touching the width. See `design-system.md` §5d and
> `docs/screenshots/epic-9-21/`.

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

> ### BUILT — Epic 9.24, 2026-09-01. A second palette, for the other half of §0.
>
> §0 has split every screen into **Presenting** and **Working** since Epic 0.
> The Report's restraint is argued there and is untouched. What was never
> argued is why **every Working screen shared it** — and for nine epics they
> did: one teal accent on a warm-grey ground, from the dashboard to a client's
> Rankings. That was caution applied past the point the split asks for. The
> Report gets printed, photocopied and read across a conference table; the
> dashboard is an operator's own console with twenty tabs open, and none of
> those three reasons has ever applied to it.
>
> So there are two palettes now, and the second is **additive** — no token in
> this section changed, and the Report's token set gained nothing.
>
> **`bench-*` — the operator's bench.** Six categorical accents, hues 258 /
> 275 / 292 / 309 / 326 / 343, at chroma **0.185** against `beacon-600`'s
> 0.125. Four stops each (`050` wash, `100` line, `600` solid, `700` press).
> Used for sidebar iconography, stat tiles, section chips, and competitor
> series on Working-screen charts.
>
> | Claim | Number | Where it is checked |
> |---|---|---|
> | Wider hue range than one accent | 85° across six, vs 0° for `beacon` alone | `tokens.test.ts` |
> | More saturated | C 0.185 vs 0.125 — 1.48× | `tokens.test.ts` |
> | Cannot be mistaken for a score or a state | ≥30° from every ramp stop, `beacon` and all four semantics | `tokens.test.ts`, per hue per meaning |
>
> **Where the hues came from.** Generated from `ui-ux-pro-max`'s palette
> database — 192 palettes, 446 chromatic entries once near-neutrals and unusable
> lightnesses are dropped, converted to OKLCH and bucketed by hue. Its own top
> recommendation for "dense analytics dashboard" was `#1E40AF` / `#3B82F6` /
> `#DBEAFE`, which is precisely the cool blue-grey this section rotated the
> neutral axis away from, so **no hex value was imported**. What was taken is
> where the database's chromatic mass actually sits *and* where sRGB still has
> chroma to spend: hues 235–255 are chroma-starved (max C 0.12–0.14 at L 0.55),
> which is why the arc starts at 258.
>
> **The rule that keeps the two apart.** The buffer above is not a convention
> to remember — a bench hue is nowhere near a meaning-bearing hue on the wheel,
> so a chip cannot be read as a score. And a **score is never wrapped in a
> categorical hue**: `Median visibility`, `Latest`, `Best` and
> `Technical foundation` are the four tiles whose value is a measurement, and
> all four are deliberately unaccented. The ramp is already the colour language
> for a score; two colour languages on one tile invite the reading that the
> chrome says something about the number.
>
> **One chart, two contexts.** `seriesStyle(role, index, palette)` takes a
> third argument defaulting to `'report'`. Competitors are neutral slate on the
> Report and bench hues on a Working screen; the client is `beacon-600` in
> both, and the §1 dash patterns are unconditional, so greyscale and CVD
> reading survive the richer palette. It is a rendering decision on ONE
> component — `render.test.tsx` strips the paint attributes and asserts the two
> renderings are otherwise identical, character for character.
>
> **The default is the guarantee.** The Report never opts out; it never opts
> in. A chart added to it tomorrow is restrained because someone would have to
> type `palette="working"` to make it otherwise.
>
> **Proven, not promised.** `reportIsolation.test.ts` scans every report
> surface and fails if one names a bench token, in any of the five forms one
> can be written; it also fails if the Working screens STOP using the layer, so
> it cannot go green by the feature being reverted. Live: the report document
> (`article.avp-report`) and the whole of `/share/{token}` were captured before
> and after and are **byte-identical** — same SHA-256, 2,833,684 and 2,957,105
> bytes. `docs/screenshots/epic-9-24/`.
>
> **Density, the way 9.21 said to do it.** The empty margins were closed by
> giving the space something to hold, never by narrowing a container: the
> dashboard's three header figures became six tiles, a client's two became
> four, and the Sources and Rankings charts gained a value table beside them.
> `TrendChart`'s 9.21 width bound is untouched — the table fills the column the
> bound leaves over, which is the only correct way to use it.


> ### BUILT — Epic A, 2026-09-02. Tone, and the colour language it is NOT allowed to borrow.
>
> The Sentiment tab draws how each engine described the client. Tone is
> **ordinal** — positive is better than negative — and this palette already has
> exactly one ordinal idea, the visibility ramp, which is reserved for score
> values. So the question was which language tone is allowed to speak.
>
> **It speaks position, not hue.** Positive sits above a waterline, negative
> below, neutral straddles it. That gets the ordinal reading for free in
> greyscale, under colour-vision deficiency, and on a printer — the same three
> properties §1 demands of the ramp, achieved by geometry rather than luminance.
> Negative additionally carries a 45° hatch, which is §1's competitor-series
> rule applied to a direction instead of a series.
>
> **Hue is therefore free to encode the ENGINE**, which is genuinely
> categorical, and it takes `bench-*` — the same index per engine that the
> Prompts screen uses, shared from `lib/client/engines.ts` rather than copied.
> Two screens disagreeing about which hue is ChatGPT would make an operator
> re-learn the mapping on every tab change.
>
> **`success` / `danger` were considered for tone and rejected.** §1 reserves
> the semantics for SYSTEM STATE precisely so a red chip is never read as a bad
> score — and sentiment *is* one of the five scored dimensions, at 15%. A red
> bar on a Working screen beside a visibility figure is the exact confusion that
> rule exists to prevent.
>
> **The waterline is placed by the data, not at the middle.** Centring it was
> the first design, on the reasoning that all-positive and all-negative should
> be mirror images. Measured on the real `plausible.io` history that was a bad
> trade: tone there is overwhelmingly positive, so the lower half held one
> hatched sliver and 45% of the figure was empty. The two halves are now sized
> by the largest stack in each direction, which keeps the property that actually
> matters — **one unit scale**, so five positive and five negative are the same
> length — and drops the dead space. The mirror-image property survives; only
> the zero line moves. Found in a live browser, not by a test.
>
> **Motion: 320ms, not 600ms.** The bars grow out of the waterline they are
> measured against, which ties the animation to real data arriving rather than
> decorating it. It runs at `--avp-duration-layout`, NOT the report's
> `--avp-duration-reveal`: §4's Epic 9.16 note excludes Working screens from
> performing on every load, and 600ms on a tab an operator reopens all day is a
> performance. The roadmap asked for "bars animating from zero on load"; this is
> that request honoured at a speed the Working context can carry.


> ### BUILT — Epic B, 2026-09-02. Answer gaps, and the seventh accent that filled the layer.
>
> The Answer gaps tab draws which questions somebody else owns: prompts down,
> brands across, a count per cell. It is a **table**, not a chart, and that is
> the decision worth recording — the data has two categorical axes and a small
> integer, with no continuous dimension anywhere in it. A chart would have had
> to invent one. An SVG heatmap would also have meant drawing a table and then
> hiding a second copy of it for screen readers; a real `<table>` makes the
> visible representation and the accessible one the same object.
>
> **The subject is `beacon`, rivals stay neutral, and `absent` takes `warn`.**
> §1's "one brand, one colour" holds here as on every trend. Rivals do not take
> bench hues: on this screen a rival is not a category to be told apart from
> other rivals, it is the thing that took an answer, and six rivals in six hues
> would say otherwise. `absent` takes `warn` rather than `danger` because a
> rival owning an answer is a finding to act on, not a system fault — and a
> table where nine of twenty-four rows were red would read as an outage.
>
> **The state that is not a gap gets drawn as neither.** A prompt where no
> engine named ANY brand is not a question this client lost; it is a question
> with no commercial answer. Its chip is dashed and grey, its row recedes, and
> it sorts below every real verdict. This is not a nicety: on the worst real
> client measured it is **10 prompts beside 9 genuine absences**, so folding
> them would have reported 19 of 24 as gaps. Epic A drew the same line around
> `unclassified` tone, and the layout derives "is a gap" from the row KIND
> rather than from the absence count precisely because the two states have
> identical counts.
>
> **The seventh accent, and the wall behind it.** A seventh client section would
> have wrapped `benchAccent` back to `cobalt` and given two items in the same
> strip one colour, so the layer grew to `crimson` at hue 355. Deriving where it
> could go turned up the constraint that ends the layer: the 30° meaning buffer
> and the sRGB gamut at the shared chroma table together leave **one arc, 256.5°
> to 355°**, and 355 is its last seat. Blue is the binding half — at hue 241 the
> chroma ceiling is 0.128 against the 0.185 the table requires, so an accent
> there would render clamped and duller than its neighbours, which reads as
> rank. design-system.md §6 carries the arithmetic. **Alerts, Crawler activity
> and Prompt discovery would have hit this wall**, and the choice was between
> relaxing the buffer, letting chroma vary, or grouping the nav — decided
> separately in Epic B.1 below, deliberately not inside this epic.
>
> **The pressure showed up immediately.** Four stat tiles at crimson 355 and
> magenta 326 — 29° apart at one lightness and chroma — read as the same pink in
> two small chips at opposite ends of a row, and on a client where both figures
> were `0` they were indistinguishable. Fixed by unaccenting the tile that is
> not a finding, which matches how its rows are already drawn and is the more
> correct answer regardless.
>
> **Motion: one fill, no stagger — a rule broken and then kept.** The first
> draft staggered the cells 28ms per row capped at 12, putting arrival at
> **656ms**, and cited §4's Working-screen rule in the comment directly above
> the rule that broke it. Running the animation gate over it killed it three
> ways: 656ms is performing on every load; a cascade depicts data arriving
> progressively when this grid arrives whole from one response; and `28ms` was
> hand-typed, the only stagger token being reveal-tier and belonging to the
> Report. What survives is the part tied to data — cells fill from transparent
> to their intensity, once, together, at `--avp-duration-layout`. The fill IS
> the value, so watching it arrive is watching the measurement land.
>
> **A control that vanished under the pointer.** Switching scans reset the whole
> body to a loading state, which unmounted the picker that had just been used.
> The grid now dims to 0.45 with `aria-busy` and keeps its controls in place.
> Found by driving the real screen, not by a test — and only visible at all once
> the fetch was held artificially, because locally it returns faster than the
> eye.


> ### BUILT — Epic B.1, 2026-09-02. The nav grew groups so the palette would not have to.
>
> Epic B filled the seventh and last seat in the `bench-*` layer with three
> sections still to come. Three ways out were weighed and two of them cost
> something the palette was built to guarantee: **shrinking the 30° meaning
> buffer** lets a Working chip be misread as a score or a system state — the
> confusion §1 exists to prevent and the one Epic A steered around when it
> rejected `success`/`danger` for tone — and **letting chroma vary per accent**
> makes one accent read as more important than another, which is the equal-
> weight property §6 states structurally.
>
> **Grouping the nav costs neither.** A hue only has to be told apart from the
> others in its own cluster, so each cluster restarts at the first accent and
> none approaches seven. `Measurement` and `Investigation`, with membership
> from the data model rather than from shipping order.
>
> **This was already how the product worked; it just was not written down.**
> `WorkspaceShell`'s sidebar and `ClientSpace`'s strip are on screen together
> and have shared hues 0–3 since Epic 9.24 — Dashboard and Overview are both
> cobalt, Clients and Rankings both violet — and no one has read it as a
> collision, because the two navs are different places doing different jobs.
> A hue was already scoped to its nav. This scopes it to its cluster.
>
> **The trade, said plainly:** two items in the SAME strip can now share a hue,
> separated by a label rather than by being in a different region of the
> screen. That is weaker separation than the existing precedent, and it is the
> mechanism — partitioning the arc between clusters instead would keep every
> hue unique and buy no seats at all.
>
> **Sentiment moved, and that is the interesting part of the taxonomy.** The
> first cut filed it under Analysis on the strength of when it shipped. It is a
> measurement: the labels have been stored since Epic 4 and it is 15% of the
> composite. Crawler activity moved for the same reason — a second data SOURCE
> is not an analysis of the first, and filing it under Analysis would have
> quietly undercut the roadmap's most carefully-argued honesty constraint.
>
> **The refactor immediately caught itself.** Moving Answer gaps from crimson
> to its cluster's first seat collided it with a hardcoded `accent={0}` on the
> next stat tile, and two figures in one row went blue. It reached a browser
> screenshot before a test caught it — because ten accent literals scattered
> through JSX have nowhere to be checked. That is why the nav is now a table in
> `clientNav.ts` with its invariants asserted, and why the tile accents are
> derived from the screen's own rather than typed.
>
> **The labels earn their place at 420px.** At 1440 the strip is one row and
> the grouping is a nicety; narrow, the ten items stack into a block that the
> two labels are the only thing organising. Measured, not assumed.


> ### BUILT — Epic E, 2026-09-02. Alerts, and three rules that had to be redefined to fire at all.
>
> The Alerts tab reports what changed between a scan and the scan it was
> compared against. Three things about it were decided by measuring the real
> data rather than by the brief, and each changed the feature.
>
> **A baseline is not "the previous scan".** Of the three consecutive-scan pairs
> in the whole database, two are re-runs 43 minutes and 2h11m apart. Comparing
> across those measures the engines answering nondeterministically, not the
> picture changing — the composite moved −0.93 and +0.68 and net tone moved up
> to 6 points with nothing having happened. So a baseline must be at least 20
> hours older, and a scan without one produces NO alerts rather than alerts
> against whatever ran before it. That guard is the single most important line
> in the feature, and it is stated on the screen rather than buried: the lead
> paragraph tells the operator why close-together scans are not compared.
>
> **"Tone turning negative" never happens.** Across every engine of every scan
> on record the minimum net tone is **+4**, so a rule watching for a sign change
> fires zero times — and would have said nothing about the clearest tone event
> in the data, Notion's net falling 13→6, 12→5 and 10→4 across all three engines
> at once. The rule became a relative decline, and that event is the one thing
> the whole alert surface currently reports.
>
> **"A rival took the citation you lost" cannot occur.** `cites_subject` is
> `domain == subject_domain`, a pure function of the domain string, so a source
> the client owned in one scan is its own in every scan. What is measurable is
> the client's own domain going from cited to uncited.
>
> **An empty feed is two different findings, and the screen refuses to conflate
> them.** Nine of eleven clients have one scan and can never produce an alert;
> "0 alerts" would read as an all-clear for them. The screen reports which
> situation it is — *"Nothing has been checked, which is not the same as nothing
> being wrong"* against *"This is an all-clear, not an absence of data"* — and
> carries `scansCompared / scansTotal` as a tile. The same discipline Epic B's
> `subjectCitable` applies to a claim about citations.
>
> **Annotations mark the trend UNDER the axis, as a triangle.** A mark among the
> lines reads as a data point, and this is not a measurement — it is a note that
> something happened at this reading. Shape rather than colour carries the
> distinction, since every other mark in the chart is a dot or a line, and
> `warn` rather than `danger` for the reason `GapGrid`'s `absent` chip takes it.
>
> **The craft pass found the interaction defect.** Acknowledging removed the row
> from the list instantly, so the rows below jumped under the cursor — and with
> three alerts on one scan that happens twice in a row. The row now stays where
> it is, visibly settled, and filters out on the next load. `emil-design-eng`
> also caught a class name that styled nothing, an engine label styled as the
> least important text in a row when it is the only discriminator between three
> otherwise identical entries, and a silently swallowed failure.


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
> **Epic 9.19 kept that exclusion and drew the line more precisely.** No
> arrival motion is not the same as no motion. A dashboard should not perform
> an entrance on a page checked fifty times a day; it should also not look
> frozen while a scan it is polling finishes underneath the reader. So the
> Working screens now move where something is *actually changing* — an eased
> table hover, a status badge that crossfades tone, a score bar that eases when
> it moves, and a single breathing dot on the one row that is still running —
> and every one of those is spelled in the four durations this section already
> defines. **Zero motion values were added.** The pulse's period is
> `calc(var(--avp-duration-reveal) * 3)`, which is this section's own
> dim-to-lit dissolve slowed to a breath, not a fifth number.
>
> The report acquired none of it. Its width and its exclusion here are
> unchanged, and `ReportView.test.tsx` asserts both — plus a byte-identical
> before/after screenshot in `docs/screenshots/epic-9-19/`.
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
