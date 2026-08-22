# IP Safety Constraints

**Status:** Standing constraints. Apply to every task, every session, no exceptions.
**Authority:** This document is normative. If any other doc, ticket, or instruction
conflicts with it, this document wins until the project owner explicitly overrides it
in writing.

---

## DESIGN / IP SAFETY — read before writing any UI code

1. Never design any screen from a competitor's screenshot or "make it like
   X but better." Design from the data model and user goal only.
2. Build the proprietary design system (Epic 0) BEFORE any screen work.
   Every screen must import from it — no ad hoc Tailwind defaults on
   customer-facing screens.
3. Structure primary screens as a narrative report (score → biggest gap →
   proof → fix → pitch), not a generic metrics-tile dashboard.
4. Only use permissively licensed visual assets: Lucide icons (MIT) or
   custom-drawn, Google Fonts (OFL) or purchased/licensed fonts. No icon
   packs or illustration kits sourced from competitor products.
5. Never inspect or copy competitor source code, HTML/CSS/JS, or DOM
   structure. Looking at a rendered competitor page for UX research is
   fine; lifting its code is not, even "as a reference."
6. Every dependency must be MIT/Apache-2.0/BSD licensed. Flag anything
   GPL/AGPL and stop for my explicit sign-off before adding it.
7. Scraped data from AI engines or competitor pages is for FACTS ONLY
   (mention counts, citation presence, schema presence, structural
   signals). Never store, render, or republish a competitor's actual
   copyrighted text, images, or marketing copy anywhere in the product.
8. No verbatim competitor marketing copy anywhere — not in our marketing
   site, onboarding, or in-app microcopy, not even as lightly-edited
   "inspiration text."
9. Before marking any UI-related epic complete, self-check against this
   list and tell me explicitly: "IP-safety check passed: [what you
   verified]."

---

## Operational notes (how these constraints are enforced in this repo)

### Constraint 6 — dependency licensing
- Allowed without asking: **MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, CC0**.
- Fonts under **OFL-1.1** are allowed (font licence, not a code licence).
- **Stop and ask** for: GPL (any version), AGPL, LGPL, SSPL, BUSL, Elastic License,
  "source available" / commons-clause licences, or any package with no declared licence.
- Every epic that adds dependencies must record the added packages and their licences
  in `/docs/build-log.md`.

### Constraint 7 — the facts-only data rule
Scraped or model-returned content may be persisted **only** as structured facts:
- boolean presence / absence
- counts and ordinal positions (e.g. "mentioned 3rd")
- URLs and domains that were cited
- names of entities mentioned
- structural signals (schema.org types present, heading counts, word counts, etc.)

Raw AI-engine answer text and raw competitor page HTML may exist **transiently** inside a
worker process for the sole purpose of extracting the facts above. It must not be written
to a durable store, returned by an API, or rendered in the UI. Where a raw snippet is
genuinely required as evidence, store a **short quotation of our own client's content only**,
or a link out to the source — never competitor prose.

### Constraint 9 — the self-check gate
No UI-related epic may be reported as complete without an explicit line in the epic
summary reading `IP-safety check passed: …` enumerating what was verified.
