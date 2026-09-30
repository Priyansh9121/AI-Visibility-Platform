---
name: tells
description: "Use when reviewing or writing AVP marketing, pricing, report and empty/error-state copy and visual patterns for invented facts and AI-writing tells; for /crawler, /privacy and /terms, review only and report to the founder. Not for code review, and not a design-system or layout authority."
---

# Tells (AVP copy review)

A tell is a default nobody at AVP asked for: a number, name, logo, ornament or phrase a model
supplies because it fits every product, not because it is true of this one.

Nothing here names a replacement. A replacement would be the next default. Wording help
(rewriting a flagged line) goes to the built-in `design:ux-copy` skill, except for /crawler, /privacy
and /terms, which are reported to the founder only.

## The authority: AVP's own system, not a mood word

Genjutsu judged every pattern against a per-project "thesis". AVP has no thesis file. The
authority is, in this order:

1. **The facts-only rule** (`CLAUDE.md`, "What must never ship"): copy never invents numbers,
   testimonials, logos or customers. A number is allowed only with a source the page or the docs can
   name (a scan fact, a count read from the database, a limit in code, or the paper itself). Research figures from `docs/north-star.md` section 9.1
   are background from a section that calls itself web-sourced, informational and less vetted:
   a research figure on a page needs the paper itself and the founder's OK, and never appears on the
   landing page or pricing as a benefit. Store or republish no third-party raw text.
2. **`docs/design-system.md` and `docs/design-direction.md`**: what the visual system
   currently names (the current ruling is `docs/design-system.md` section 0 "Type"; the section 2 font
   choice is superseded) (fonts, paper ground, hairlines, mono for evidence, the components in
   `packages/design-system`). A pattern those docs name is not a tell. A mood word ("editorial",
   "premium", "bold") names nothing.
3. **`docs/product-spec.md` and `docs/scoring-spec.md`** for what the product really does and
   measures.

If a pattern is named by none of these, it is a tell. When unsure whether the docs name it, read
the doc section and quote it; do not guess.

## Allow-list: AVP's own system is never a tell

Do not flag any of these; they are the documented system:

- Fonts, each only in its documented role (`docs/design-system.md` section 0 "Type"): Space Grotesk
  (`font-display`: headings, KPI figures, hero numeral), IBM Plex Sans (`font-ui`), IBM Plex Mono
  (`font-mono`: evidence and tabular figures), Fraunces (`font-editorial`) only inside `.avp-report`
  (the founder rejected it for the product, `docs/design-direction.md` section 8). Fraunces anywhere
  else, for example a Fraunces italic accent word on the landing page, IS a tell. Icons: `lucide-react`.
- The paper ground, ink text, hairline rules and OKLCH tokens from `packages/design-system`
  (the report stays paper in both themes).
- Components from `packages/design-system` used for their stated job: `StatTile`, `ScoreMeter`,
  `ShareBar`, `DataTable`, `Checklist`, `Badge`, `LoadingState`, `ErrorState`, `EmptyState`.
  A score bar or a status badge is not a tell when its value comes from a real scan.
- Mono in evidence and tabular contexts. Mono on nav labels or marketing captions is still a
  manual read (Monospace everywhere).

The allow-list covers the system, not what is written in it. A Space Grotesk headline that says
"Supercharge your brand" is still hollow copy.

## How an entry is written

Every entry in `references/web.md` has exactly three fields: **Marker** (what you can see),
**Why it is a tell** (the model behaviour, not taste), **The question** (sends you back to AVP's
data or docs; never a replacement). Report a finding as: marker as found (`file:line` and the
text), then your answer to the question.

## The four families

| Family | The test |
|---|---|
| Invented information | Does this datum come from a scan, the database, the code or a named source? |
| Decorative filler | Does this element carry information, or only texture? |
| Reflex convergence | Did this choice come from the docs, or from a habit? |
| Hollow copy | Does this sentence say something true of AVP and of no other product? |

One element can fail two tests. Report it once, under the family whose question it fails first.

## Surfaces and what to check first

- **Landing `LandingView.tsx` and `/#pricing`**: figures, logos, testimonials, "trusted by",
  urgency counters, prices and plan limits (they must match Stripe/plan config in code, and a
  price or limit change is a founder decision), filler verbs, several labels for one CTA.
- **Report**: every number traces to the scan that produced it (`docs/scoring-spec.md`); no
  engine answer text or competitor copy quoted (facts-only); fix text uses only the scan's own
  numbers (`fix_generator.py` output).
- **`/crawler`** (legal-adjacent: review only, report to the founder, never edit or delete lines, never send them to `design:ux-copy`): only what `AVPScanBot` actually does (`apps/api/src/avp_api/services/crawl.py`).
  No claim it does not keep.
- **`/privacy`, `/terms`**: lawyer-pending drafts. Review only and report to the founder: never
  edit, delete or "de-AI" lines, and never send them to `design:ux-copy`. Check for invented facts
  (a processor, a retention period, a company detail not in the code or docs). Never flag em dashes
  there.
- **Empty and error states** (`EmptyState`, `ErrorState`, `LoadingState`): plain statement of
  what happened and what to do; no invented cause, no cute filler.

## Em dash: OPEN FOUNDER DECISION

Whether AVP copy may use U+2014 is undecided. Until the founder decides: report em dashes
only as an **informational note** (count and surfaces), never as a failure or a problem, never
in the legal pages, and never edit them away. The existing copy already uses them.

## Protocol

1. **While writing**: read a family's test before writing what it governs: numbers and names
   against Invented information, copy against Hollow copy, ornaments against Decorative filler.
2. **When reviewing**: read the visible strings of the surface, including copy held in
   JavaScript data (arrays, objects, content files) and rendered through expressions. Grep can
   miss those; read them.
3. **For each finding**: is it named by the allow-list or the docs? Then it is not a tell (quote
   the line). Does the marker not actually match (a loading spinner reported as "perpetual motion",
   a real scan count)? Then it is not this tell; cite the `file:line`. Otherwise report it.
4. **Fix only what this task wrote.** A tell already in the repo is listed for the founder, not
   changed in a review pass. Fixing shipped copy is its own change with the ui-reviewer. On /crawler,
   /privacy and /terms, report to the founder only; never edit, delete or rewrite.
5. **Numbers**: if a figure has no source, the fix is to delete it or source it, never to
   round it or soften it.

Compose and SwiftUI: not applicable (AVP is web only).

## Manual reads (no pattern can judge them)

- a fake product drawn in divs (a dashboard built from styled boxes instead of the real UI);
- one layout family repeated section after section;
- a headline with a small paragraph floating in the opposite corner;
- register drift between sections;
- monospace spread across labels and navigation beyond evidence and tabular figures;
- copy held in JavaScript data and rendered through expressions.

Read the finished surface once for these and report in the same three fields.

Full catalogue: `references/web.md`.
