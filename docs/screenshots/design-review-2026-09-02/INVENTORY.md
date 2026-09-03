# Visual inventory — Sentiment & Answer gaps

Captured 2026-09-02 18:23 AEST. Chromium (Playwright, headed), viewport 1440×900, DPR 2.
Signed in as `review@epic7.example`; client `clnt_01M1B3ANC44XJ12XFA4Y7T1E3S` — **PSM Digital**,
`psmdigitalagency.com.au`, 1 scan (31 Aug), 24 prompts, 3 engines, 5 detected rivals.

Colour values are read from the live CSSOM. Hover/motion values are read from computed style
after triggering the state in the browser, and cross-checked against
`packages/design-system/src/styles/components.css`.

---

## Palette reference (tokens → hex, resolved live)

| Token | Hex | oklch |
|---|---|---|
| `--avp-paper-000` (ground) | `#fffdfa` | `oklch(0.995 0.004 75)` |
| `--avp-paper-050` (sidebar / row hover) | `#faf7f3` | `oklch(0.978 0.006 75)` |
| `--avp-paper-100` (seated / nav hover) | `#f4f0eb` | `oklch(0.958 0.008 75)` |
| `--avp-paper-200` (hairline) | `#eae5df` | `oklch(0.925 0.01 75)` |
| `--avp-paper-300` (strong line) | `#d9d3cc` | `oklch(0.87 0.012 75)` |
| `--avp-ink-400` (tertiary text) | `#8b857d` | warm grey |
| `--avp-ink-600` (secondary text) | `#565b65` | cool grey |
| `--avp-ink-800` (body) | `#252a35` | |
| `--avp-ink-900` (primary) | `#0d121e` | cool near-black |
| `--avp-beacon-600` (the client's own colour) | `#00848d` | teal |
| `--avp-beacon-400` (focus/hover ring) | `#35b9c0` | |
| `--avp-beacon-100` | `#caeef0` | |
| `--avp-warn` | `#cd9130` | amber |
| `--avp-bench-1-600` cobalt | `#1b6cdb` | `-050` `#e2f1ff`, `-100` `#c7e2ff` |
| `--avp-bench-2-600` indigo | `#5660db` | `-050` `#e9efff` |
| `--avp-bench-3-600` violet | `#7754d2` | `-050` `#f0ecff` |
| `--avp-bench-4-600` orchid | `#914ac1` | |
| `--avp-bench-5-600` magenta | `#a540aa` | |

Motion tokens: hover `120ms`, state `200ms`, layout `320ms`, reveal `600ms`.
Easing: standard `cubic-bezier(0.2,0,0.2,1)`, out `cubic-bezier(0,0,0.2,1)`.

---

## Shared chrome (identical on both pages)

**Sidebar** — fixed `15rem` (240px), background `paper-050` `#faf7f3`, 1px `paper-200` right
border, padding `2rem 1.25rem`.

- Eyebrow "AGENCY" — IBM Plex Sans 11px, uppercase, tracking 0.66px, `ink-400`.
- "Scoring Verification" — Fraunces Variable 20px, `ink-900`.
- Four nav rows, each a 24×24 stroked SVG glyph + 14px label. Icons are the only place in the
  sidebar that carries hue, one per destination:
  Dashboard (bar-chart, cobalt `#1b6cdb`), Compare (telescope, indigo `#5660db`, with an 11px
  `ink-400` sub-line "Scan a new business"), Clients (building, violet `#7754d2`),
  Settings (gear, orchid `#914ac1`).
- **Clients** is current: `bench-3-050` `#f0ecff` wash, `inset 2px 0 0` violet left rail,
  weight 600, text lifts to `ink-900`.
- Footer: "1 OF 3 SEATS" (11px uppercase `ink-400`) + a small ghost "Sign out" button.

**Local nav** — sits inside the content column, under the H1. Two clusters, each labelled in
11px uppercase `ink-400`: **MEASUREMENT** (Overview, Report ↗, Sources, Rankings, Sentiment,
Technical) and **INVESTIGATION** (Answer gaps, Prompts, Alerts). 9 items total.
Gap 0.5rem within a cluster, 1.5rem between clusters. A `paper-200` hairline runs under
the whole strip.

Every item except Report carries a 0.5rem dot in its own hue at **opacity 0.45**; the current
item's dot goes to opacity 1 and the item takes its hue's `-050` wash plus an
`inset 0 -2px 0` underline in the `-600`. Report is deliberately unaccented — no dot, a `↗`
glyph instead. Dot hues run cobalt → indigo → violet → orchid → magenta across Measurement,
then restart at cobalt → indigo → violet across Investigation.

**Content column** — `padding: 72px 24px`, `max-width: 90rem`, so 1200px of usable width at
this viewport.

**Not product UI:** the black circle with an "N" at the bottom-left of every screenshot is the
Next.js dev-tools portal (`<nextjs-portal>`), not part of the design.

---

# 1. `/sentiment` — "How the engines talk about this client"

Full page height 1107px against a 900px viewport — roughly one screen plus 200px.

## Layout

Top to bottom in the 1200px column:

1. `← All clients` back link, 13px `ink-600`.
2. **H1 "PSM Digital"** — Fraunces Variable, 31px, weight 600, tracking −0.62px, line-height
   35.65px, `ink-900`.
3. Subtitle `psmdigitalagency.com.au` — **IBM Plex Mono** 13px, `ink-400`. The mono face is used
   for exactly this one string.
4. The local nav strip, then its hairline.
5. **Stat row 1** — three tiles, `auto-fit minmax(11rem, 1fr)`, gap 0.75rem, each 1rem padding,
   8px radius, ~78px tall: **SCANS 1** (accented cobalt — `#e2f1ff` wash, `#c7e2ff` border,
   3px `#1b6cdb` left rail), **LATEST 29** (plain — `#fffdfa` ground, `paper-200` border),
   **BEST 29** (plain).
6. **H2 "How the engines talk about this client"** — Fraunces Variable 25px, **weight 400**,
   wrapping to two lines at a ~20ch measure.
7. Lead paragraph, three lines, ~17px, line-height 1.6, ending "Sentiment is 15% of the
   composite score."
8. **Two-column split.** Left: the chart, 530px wide. Right: the "NET TONE, WHOLE HISTORY"
   ledger, 590px wide.
9. Chart caption, two lines, 13px `ink-600`.
10. **Stat row 2** — three tiles, all with a note line: **ANSWERS WITH A TONE 15** (cobalt),
    **NAMED NO ONE 55** (indigo, `is-emphasis`: 5px rail instead of 3px *and* the value itself
    painted `#5660db`), **ENGINES REPORTING 3** (violet).

**Eye lands first** on the H1 "PSM Digital" — it is the largest thing on screen (31px serif) and
sits at the top of an otherwise empty band. The **SCANS 1** tile pulls second: it is the only
filled colour block above the fold. The chart is third — it is large but low-contrast.

**White space:** in the main column, **86.0% of the viewport is bare ground** `#fffdfa`;
78.7% across the full scroll height. The largest single void is the right half of the
NET TONE block — the ledger is 590px wide but holds only a name at the left and two
right-aligned numbers at the far right (x≈1416), leaving ~400px of empty middle, and below its
three rows the entire right column is blank for ~160px down to the caption.

## Colour

Every distinct colour on screen and where it appears:

- **Ground** `#fffdfa` warm off-white — the whole content column.
- **Sidebar ground** `#faf7f3`.
- **Hairlines** `#eae5df` — under the local nav, tile borders, ledger row separators.
- **Text:** `#0d121e` (H1, H2, tile values, engine names), `#565b65` (lead, captions, nav
  labels), `#8b857d` (all 11px uppercase eyebrows, the mono domain, the "0" axis label,
  "31 Aug").
- **Cobalt** `#1b6cdb` — Dashboard icon, Overview dot, SCANS + ANSWERS-WITH-A-TONE rails,
  and the **Claude** series in the chart and legend.
- **Indigo** `#5660db` — Compare icon, Sources dot, Prompts dot, the NAMED-NO-ONE rail
  **and its numeral 55**.
- **Violet** `#7754d2` — Clients icon (current), Rankings dot, Alerts dot, ENGINES-REPORTING
  rail, and the **Claude + search** series.
- **Orchid** `#914ac1` — Settings icon, and the **Sentiment** dot + underline (current page).
- **Magenta** `#a540aa` — Technical dot, and the **ChatGPT** series.
- **Tile washes** `#e2f1ff` / `#e9efff` / `#f0ecff` — the three accented tile backgrounds.
- **Waterline** `#d9d3cc` — a single 1px horizontal rule across the chart at the zero line.
- **Negative hatch** — `ink-800` `#252a35` at 0.28 opacity plus 2px strokes, in a 4×4 pattern
  rotated 45°. Note this is **neutral ink, not the engine's hue**: `currentColor` inside the
  `<pattern>` resolves against `<defs>`, so all negative blocks render the same grey-black
  regardless of which engine they belong to.

**Things with colour that are not text, border, or background:** the four sidebar icons; the
eight local-nav dots; the three 8px legend dots; the chart bars; the tile left rails (3–5px).
That is the complete list — there are no illustrations, photographs, badges, or coloured
buttons anywhere on the page.

## Typography

Three families. **Fraunces Variable** (editorial serif) for the agency name, H1 and H2 only.
**IBM Plex Sans** for everything else including all numerals. **IBM Plex Mono** for the one
domain string.

Size contrast is modest: H1 31px against a 17px lead and 14px UI text — about **1.8×** from
headline to body, 2.2× to the 14px nav. The 11px uppercase-tracked eyebrow is used seven times
(AGENCY, MEASUREMENT, INVESTIGATION, SCANS, LATEST, BEST, TONE BY ENGINE, NET TONE…) and is the
page's main organising device.

Numerals are **not** given a display treatment: tile values are 20px IBM Plex Sans weight 600
with `tabular-nums` — larger than body text but smaller than the H2.

**Text vs. figures:** 1,083 characters / **193 words** in the whole content column. Against that
sit 6 tile numbers, 3 chart bars, and a 3×3 ledger. The page is prose-light but not
number-dense — the chart is doing most of the visual work while carrying only three data points.

## Density and rhythm

Sparse, and it reads as a **document with a chart in it**, not a dashboard. Gaps between major
blocks are 2rem (`gap-8`); inside the local nav, 1rem. Tile padding is 1rem, tile gap 0.75rem,
rows in the ledger are 8px top/bottom. Nothing is scrollable within itself, nothing is
tabbed, nothing is collapsed. Six numbers, one chart, one small table, and 193 words of copy
spread across 1107px of page.

The chart is the density anomaly: three bars occupy 222 of 720 viewBox units each — together
about 92% of the plot width — so they read as three fat adjacent slabs rather than a series.
There is a single x-axis tick, "31 Aug", because there is one scan.

## Motion — every state triggered

| Trigger | What actually changes | Timing |
|---|---|---|
| Page load — chart | Each bar segment animates `scaleY(0) → 1`. Positive segments grow **up** from the waterline (`transform-origin: bottom`), negative grow **down** (`origin: top`), neutral from centre. | 320ms `cubic-bezier(0,0,0.2,1)`, `forwards`, no stagger |
| Hover anywhere on the chart SVG | **All three bars** drop to `opacity: 0.35`; the bar under the pointer stays at 1. Nothing moves or resizes — opacity only. Measured mid-transition at 0.684, confirming it eases rather than snapping. | 120ms `cubic-bezier(0.2,0,0.2,1)`, gated `@media (hover:hover) and (pointer:fine)` |
| Hover a sidebar nav item | Background `transparent → #f4f0eb`, text `#565b65 → #0d121e`. Accented items take their own `-050` wash instead. | 120ms standard |
| Press a sidebar nav item | `transform: scale(0.97)` | 120ms ease-out |
| Hover a local-nav item | Background `transparent → #f4f0eb`, text darkens to `ink-900`, **and the accent dot lifts `opacity: 0.45 → 0.8`**. | 120ms standard |
| Hover the current local-nav item | **Nothing.** It already sits at its wash with the dot at opacity 1. | — |
| Hover "← All clients" | Colour `#565b65 → #0d121e`; `:active` scales to 0.97. | 120ms ease-out |
| Hover a stat tile | **Nothing.** Tiles declare a 120ms transition on `border-color` and `background` but no `:hover` rule exists — they are not interactive and never respond. | — |
| Tooltips | **None.** Zero `title` attributes on the page. The chart is `aria-hidden` with no `aria-label`s; a visually-hidden `<table>` carries the same figures for screen readers. |
| Reduced motion | Bar segments land at `scaleY(1)` with `animation: none`. |

There is no scan picker on this page and no other interactive control besides the links.

## Reads as unfinished / placeholder

- **The chart is built for a time series and is showing n = 1.** Three enormous bars, one
  x-tick, no trend. The second bar (Claude, cobalt) is a single flat neutral block with no
  positive or negative segment at all — it straddles the waterline as one undifferentiated slab.
- **The right half of the NET TONE block is empty** — ~400px of dead space between each engine
  name and its two numbers, and a blank ~590×160px area beneath the three rows.
- **The two tile rows are inconsistent:** the top three (SCANS / LATEST / BEST) have no note
  lines, the bottom three do, so the rows are visibly different heights (78px vs 118px) for no
  stated reason.
- **The negative hatch does not carry engine colour** while every other segment does — two
  different engines' negative blocks are indistinguishable from each other.
- No icons anywhere outside the four sidebar glyphs; the local nav uses bare dots.

---

# 2. `/gaps` — "Which questions somebody else owns"

Full page height 2841px — about 3.2 viewports.

## Layout

1. Back link, H1, mono subtitle, local nav (identical to Sentiment; **Answer gaps** is current,
   cobalt: `#e2f1ff` wash + `inset 0 -2px 0 #1b6cdb`).
2. **H2 "Which questions somebody else owns"** — Fraunces 25px weight 400, two lines.
3. Lead paragraph, three lines, with the word *nobody* set in italic.
4. **Stat row — four tiles**, all with notes: **RIVALS TOOK 9** (cobalt, `is-emphasis` → 5px
   rail), **PARTLY HELD 0** (indigo, 3px), **NAMED, NOT CITED 4** (violet, 3px),
   **NAMED NO ONE 10** (unaccented — plain ground, `paper-200` border, grey rail).
5. **Controls row.** Left: a 13px weight-600 "Order" label above a 121px-wide select reading
   "Worst first" (the other option is "As asked"). Right, on the same baseline: a 12px `ink-400`
   note, "5 rival columns, from the competitors detected for this scan. A rival never detected
   cannot appear here.", wrapping to two lines at a 490px measure.
   **There is no scan picker** — it only renders when more than one scan exists, and this client
   has one.
6. **The grid** — 1px `paper-200` border, 8px radius, ground background, `tabindex="0"`, its own
   `overflow-x: auto` container (scrollWidth 1150 = clientWidth, so nothing scrolls sideways at
   this viewport).
   - Caption bar: "24 prompts across 3 engines. A cell counts the engines that named that brand
     for that prompt." — 12px `ink-600`, 12/16px padding, hairline beneath.
   - **Sticky header**, 8 columns: PROMPT (min 288px), VERDICT (min 112px), then six brand
     columns at min 88px — PSM DIGITAL 5, CLUTCH 10, WEBFX 4, IGNITE VISIBILITY 2,
     GROWTHMARKETIN… 1, RIGHTLEFTAGENCY 1. Header text 11px weight 600 uppercase tracking
     0.66px `ink-400`; the count beneath each name is 11px weight 400 `ink-400`.
     The header's bottom border is **2px solid cobalt `#1b6cdb`** — the single strongest
     horizontal rule on the page.
   - **24 rows at ~74.5px each**, separated by 1px `paper-200`.
   - Prompt cell: the prompt at 13px `ink-900`, clamped to two lines with ellipsis; beneath it an
     11px uppercase `ink-400` intent tag — COMPARISON / AWARENESS / BOTTOM FUNNEL. One row reads
     "BOTTOM FUNNEL · **CITED**" with CITED in beacon teal `#00848d`.
   - Verdict cell: a chip plus a "3/3" recurrence figure in 11px `ink-400`.
7. **Recurrence block** — an 11px uppercase `ink-400` H3 "RIVALS TAKING ANSWERS, ACROSS EVERY
   SCAN", a two-line 13px caption, then a **571px-wide** table: RIVAL / ANSWERS WON / SCANS,
   with WebFX `webfx.com` 9 / 1, Clutch `clutch.co` 8 / 1, Growthmarketingpro 3 / 1,
   Ignite Visibility 3 / 1. Brand name at 13px `ink-900` with its domain beside it in smaller
   `ink-400`.

**Eye lands first** on the **RIVALS TOOK 9** tile — it is the leftmost filled block, has the
thickest rail (5px), and its numeral is the only large figure above the fold. The cobalt header
rule of the grid pulls second; the sand-coloured cells third.

**White space:** **86.4% of the main column is bare ground** in the viewport, 88.6% across the
full page — the grid *increases* the emptiness rather than filling it, because **121 of its 144
cells are empty** (a `·` middot in `ink-400`, no fill). Only 23 cells carry a number. The
recurrence table at the bottom occupies 571px of a 1200px column, leaving ~630px blank to
its right.

## Colour

- **Ground / sidebar / hairlines / text** — identical to Sentiment.
- **Cobalt `#1b6cdb`** is this screen's accent, used in four places: the current local-nav item's
  underline and dot, the RIVALS TOOK tile rail, the grid's 2px header rule
  (`--avp-gapgrid-accent`), and the cell hover ring.
- **Tile washes** `#e2f1ff` cobalt, `#e9efff` indigo, `#f0ecff` violet; the fourth tile is
  unaccented.
- **Three and only three cell fill families**, all driven by an inline `--avp-gapgrid-fill`
  of 0–1 (the share of engines that named that brand for that prompt):
  - **Subject column** (PSM Digital) — `color-mix(beacon-600, fill × 28%)` → teal. At fill 1
    that renders `#b8dbdb`. Weight 600, and fenced by hairlines on both sides so it reads as its
    own band.
  - **"Took" cells** (a rival won a row the client did not) — `color-mix(warn, fill × 26%)` →
    warm sand `#f2e1c6` at full strength. Observed at four intensities: 1.0 (α .26), 0.667
    (α .173), 0.5 (α .13), 0.333 (α .087).
  - **Every other rival cell** — `color-mix(ink-400, fill × 18%)` → neutral grey `#eae5df`.
    Observed at 1.0 (α .18) and 0.333 (α .06).
- **Verdict chips**, all 11px uppercase tracking 0.66px, 3px radius, 2px/8px padding, 1px border:
  - **ABSENT** — border *and* text both `--avp-warn` `#cd9130` amber, weight 600. 9 of them.
  - **UNCITED** — text `ink-600`, border `paper-200`, weight 400. 4.
  - **COVERED** — text beacon `#00848d`, border `beacon-100` `#caeef0`, weight 400. 1.
  - **NO BRANDS** — text `ink-400`, border `paper-200`, **dashed**, weight 400. 10.
- Danger red appears **nowhere** on this screen; a rival taking an answer is amber, not red.

**Things with colour that are not text, border, or background:** the sidebar icons, the local-nav
dots, the tile rails, and the cell fills. No icons, images, or coloured controls inside the grid
itself.

## Typography

Same three families. Fraunces appears only in the H1 (31px) and H2 (25px, weight 400) — the
entire grid, both tables, all chips and all numerals are IBM Plex Sans. The mono face appears
only in the domain subtitle.

Size contrast headline-to-body is the same ~1.8×, but the working range is compressed: the grid
runs on 13px (prompts, cell counts), 12px (caption), and 11px (headers, chips, intent tags,
recurrence). **The page's smallest type — 11px — carries the verdicts**, which are the
findings.

**Text vs. figures:** 4,490 characters / **828 words** — 4.3× the Sentiment page. Nearly all of
it is prompt text: 24 natural-language questions, each up to two lines. Against that sit 23
numbers in the grid and 8 in the recurrence table. So despite reading as a data table, this
screen is overwhelmingly **text**, and the numbers are sparse.

## Density and rhythm

This one **is** a dashboard, or closer to one: a 24×8 table with a sticky header, a sort control,
and a summary row above it. But the rhythm is loose for a table — rows are 74.5px tall (driven by
two lines of prompt plus an intent tag), cells carry only 8px of padding, and the whole grid runs
2,470px tall. Ten consecutive rows at the bottom are "NO BRANDS" — entirely empty rows of
middots occupying roughly 745px of vertical space with no fill anywhere.

Block gaps are 1.5rem (`gap-6`) here versus 2rem on Sentiment, so the top of the page is slightly
tighter, but the grid's own internal rhythm is airier than its data volume warrants.

## Motion — every state triggered

| Trigger | What actually changes | Timing |
|---|---|---|
| Page load — grid | Every one of the 144 cells animates `background-color: transparent → its fill` via the `avp-gapgrid-fill-in` keyframe. **No stagger** — the whole grid fills at once. | 320ms `cubic-bezier(0,0,0.2,1)`, `backwards` |
| Hover a row | The `<tr>` background goes `transparent → #faf7f3`. Painted on the row *beneath* the cells' own fills, so the fill that encodes the value stays exactly as it was. | 120ms `cubic-bezier(0.2,0,0.2,1)`, gated `@media (hover:hover)` |
| Hover a cell | A **2px solid cobalt `#1b6cdb` outline at `outline-offset: -2px`** — drawn just inside the cell's edge. **The background does not change**, deliberately: the fill carries the value, so changing it on hover would make the reader doubt the number. Applies to empty cells too. | outline appears immediately; the cell's own `background` transition is 200ms and is reserved for a scan swap |
| Hover the Order select | Border `#d9d3cc → #35b9c0` (beacon-400). | 120ms `cubic-bezier(0,0,0.2,1)` |
| Focus the Order select | Border → `#00848d` plus a two-stop ring: `0 0 0 2px rgba(beacon-400,.6)`, `0 0 0 6px rgba(beacon-400,.12)`. | 120ms |
| Change the order | Re-sorts the 24 rows. No transition on row position — the rows jump. |
| Hover a verdict chip | **No visual change** (`transition: all 0s`). It shows a **native browser tooltip** after the OS delay: "Rival named, this client not" / "Named but not cited" / "Named and cited" / "No brand named by anyone". These are the only tooltips on either page. |
| Hover a stat tile | **Nothing** — same as Sentiment. |
| Tab into the page | Focus lands on the **grid container itself** (`tabindex="0"`), which draws `2px solid #1b6cdb` at 2px offset — it is keyboard-scrollable as one unit. |
| Scan swap (unreachable here) | `.avp-gapgrid--pending` sets `opacity: 0.45` over 200ms, dimming the stale grid while the picker stays mounted. Requires >1 scan. |
| Reduced motion | Both the fill-in animation and the cell background transition are disabled. |

## Reads as unfinished / placeholder

- **121 of 144 cells are empty.** The grid is ~84% middots.
- **Ten consecutive "NO BRANDS" rows** at the bottom — 745px of vertical space containing no
  data at all, only the dashed chip and a line of dots.
- **"PARTLY HELD 0"** — a tile whose entire value is zero, still carrying its full accent rail
  and coloured wash, which gives an empty measure the same visual weight as a real one.
- **"GROWTHMARKETIN…"** — the column header is truncated mid-word by an 8rem `max-width`;
  the full name appears only in the table at the bottom of the page.
- The **recurrence table is 571px wide in a 1200px column**, with ~630px of blank ground beside
  it and no visual container.
- **The "Order" select is the only control on the page** — the scan picker the layout allows for
  does not render with one scan, leaving a wide gap between the select and the right-hand note.
- No icons anywhere on the page.
- The Next.js dev badge again sits bottom-left over the sidebar.

---

## Files

`docs/screenshots/design-review-2026-09-02/`

| File | What it shows |
|---|---|
| `sentiment-viewport.png` / `sentiment-full.png` | Resting state, above the fold and full page |
| `gaps-viewport.png` / `gaps-full.png` | Resting state, above the fold and full page |
| `sentiment-hover-tide.png` | Chart with bar 1 hovered — bars 2 and 3 at opacity 0.35 |
| `gaps-hover-row-and-cell.png` | Row wash and the cobalt cell ring, in context |
| `gaps-hover-cell.png` | The cell ring, close |
| `gaps-select-focus.png` | The Order select, hovered + focused |
| `localnav-hover.png` | Local-nav item hover — wash plus dot at opacity 0.8 |

Hover-state images are rendered by applying the hover declarations verbatim from
`components.css`, because Chromium re-styles during `page.screenshot()` and drops both real and
CDP-forced `:hover`. The values in the tables above were measured from live computed style with
the state genuinely active.
