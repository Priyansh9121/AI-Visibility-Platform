# North Star — Competitive, Product, Physical and Commercial Architecture

---

## 0. Status and how to use this document

**This is a NORMATIVE reference.** Read it at the start of every brief, the same
way `ip-safety.md` is read — not optional context, not background reading, not
"skim if time allows." A brief that has not been checked against this document
has not been scoped.

It does **not** outrank `ip-safety.md`. Where the two touch, `ip-safety.md`
wins, per its own authority clause. Nothing here is an exception to it.

### The labelling convention

Every section and every load-bearing claim in this document carries one of four
labels. They are not decoration — they say what you are allowed to build against.

| Label | Meaning |
|---|---|
| **[DECIDED]** | The founder and the AI assistant agreed. Treat as settled. Build against it. |
| **[HYPOTHESIS]** | Professional judgement, not validated against a real customer. **Pricing numbers especially.** Do not build irreversible things against a hypothesis without saying so in the brief. |
| **[OPEN]** | An unresolved fork. Requires an explicit founder decision **before** anyone builds against it. An [OPEN] item silently resolved by an implementation choice is a governance failure. |
| **[UNVERIFIED]** | Asserted in the source conversation, but **not** confirmable against a real commit, build-log entry, or file in this repo at the time of writing. Verify before relying on it. |

### The re-validation trigger

**This document was written on 2026-08-27, before a single paying customer
exists.** Not one number in section 5 has met a real buyer. It must be revisited
after each of:

1. **The first 3–5 real pilot agency conversations.** (`product-spec.md` §7,
   Epic 9: *"Pilot with 3-5 real agencies, collect feedback"* — currently `[ ]`.)
2. **The first real pricing objection or negotiation.** The first time someone
   says a number back to you is the first evidence section 5 has ever had.
3. **Epic 9's acceptance criterion actually being met** — *"pilot agencies
   successfully generate and send at least one real prospect report."* Note the
   word **send**: there is no export and no share link today (section 3, Layer 5).

Until all three have happened, **every commercial number in this document is
[HYPOTHESIS], not fact, and must read that way.** If you find yourself quoting a
section 5 figure as though it were measured, stop — you are doing the thing this
document exists to prevent.

### Why this document exists at all

Everything in it previously lived only in a chat conversation between the
founder and an AI assistant. This project has one hard-won lesson about that:
`design-direction.md` §5's recommendation was upgraded to "approved" in
`design-system.md` §5, then **nothing downstream ever read it again for five
epics** (Epic 0.6 → Epic 7.1). The Answer Shelf and Source Map were owed the
whole time and nobody knew. See `build-log.md` Epic 7.1, *"five epics of a
promise nobody read"*, and the status note now at the head of
`design-direction.md`.

A decision that lives in someone's memory of a conversation is not a decision.
It is a liability with a delay fuse.

---

## 1. The competitive goal — [DECIDED]

**The goal, stated plainly:** an AI visibility platform built for the founder's
own agency to use with and sell to clients, explicitly positioned to **exceed
AIClicks, Searchable, and PromptWatch.**

### The caveat, stated before anything else — [important]

**The competitor feature descriptions in section 2 come from the founder's own
product research — navigating those products live — NOT from this project's own
independently sourced or web-verified investigation.** No live competitor
research was performed as part of writing this document, and none is recorded
anywhere in `build-log.md`.

Treat every competitor claim in section 2 as **"the founder's understanding as of
2026-08-27"**, not as verified fact. Products change; a feature list read once
and written down decays. **Re-verify via live research before treating any
competitor claim here as current.** That research is a separate, future,
explicitly-scoped task — it was deliberately out of scope here.

### `ip-safety.md` #1 and #5, restated in this context — [DECIDED, non-negotiable]

This document exists to understand **the market gap**. It is a map of where
value is unclaimed. It is not, and must never be read as, a licence to copy.

> **#1** — *Never design any screen from a competitor's screenshot or "make it
> like X but better." Design from the data model and user goal only.*
>
> **#5** — *Never inspect or copy competitor source code, HTML/CSS/JS, or DOM
> structure. Looking at a rendered competitor page for UX research is fine;
> lifting its code is not, even "as a reference."*

Concretely, for this document:

- Knowing that a competitor **has** a citations feature tells you a category is
  contested. It tells you **nothing** you may use about how their screen looks,
  flows, or is built.
- "Exceed AIClicks" means exceed on **outcome for the agency operator**. It does
  not mean feature-match their surface.
- **Nothing in this document is an exception to `ip-safety.md`.** If a brief
  cites this document as justification for a design decision that would fail
  constraint 1 or 5, the brief is wrong, not the constraint.

---

## 2. Competitor landscape, as understood

> ### ⚠ SECTION-LEVEL LABEL: [founder-sourced, NOT independently verified]
>
> Everything in this section is the founder's recollection of navigating three
> live products. **None of it has been verified by this project.** No web
> research, no live product access, and no citation to a primary source exists
> for any claim below. Do not quote this section as evidence in a customer-facing
> document, a positioning claim, or a "competitors don't have X" assertion
> without re-verifying first.

### 2.1 The per-vendor feature detail — [WEB-SOURCED, dated 2026-09-09]

**Status change from the original [GAP: not transcribed].** The rows in §2.2
below are now filled, but from a **different source than the founder's live
navigation** originally requested: they are compiled from each vendor's public
marketing pages, product docs, and third-party reviews (G2, Trustpilot,
Trakkr, Clutch, and independent GEO-tool review blogs), retrieved via web
search on **2026-09-09**. Per-source citations are listed under the table.

**This does not upgrade the row to the same status as founder-sourced content
elsewhere in §2, and it is not a substitute for the founder's own hands-on
navigation.** Treat it as a distinct, citable, but still-external source with
its own known weaknesses:

- Marketing copy overstates; review-site content can be sponsored, dated, or
  reflect a version of the product that has since changed.
- Two sources materially disagreed on Searchable's content-generation
  capability (see the row note) — this is recorded, not resolved, because
  resolving it requires either live product access or founder judgement.
- None of this is a substitute for `ip-safety.md` #1/#5: nothing below
  describes a screen, a flow, or any code — only claimed capability, per the
  bucket definitions in §2.2.

**Still open:** the founder's own live-navigation research, which would be the
higher-trust source `2.1` was originally scoped for. If it's ever produced, it
should sit alongside this table, not silently replace it — the two are
different kinds of evidence and both are worth keeping.

### 2.2 The four categories the analysis is organised by — [DECIDED shape]

Whatever each product calls its own features, the founder's analysis sorts them
into four buckets. This framing is the durable part; the per-vendor rows are not.

| Category | What belongs here |
|---|---|
| **Visibility / insight** | Measuring whether and how a brand appears in AI answers, and turning that into a readable finding |
| **Sources / citations** | Which domains the engines actually cite, and who owns them |
| **Content / distribution** | Producing and publishing material intended to change the above |
| **Outreach / agent tooling** | Getting the finding in front of a human who will act on it, and whatever is labelled "agent" |

| Category | Searchable | AIClicks | PromptWatch |
|---|---|---|---|
| **Visibility / insight** | Tracks mentions, share of voice, avg. position, and sentiment across ChatGPT, Gemini, Perplexity, and Google AI Overviews; refreshed on a recurring cadence; scores an "AI Visibility Score." Explicitly narrower than a full SEO suite — no keyword rank tracking, backlink research, or technical SEO audit. | Tracks mentions/positioning across 8–10+ engines (ChatGPT, Perplexity, Gemini, Google AI, Claude, Grok, Mistral, DeepSeek, Meta AI, Copilot, TikTok AI per various listings — vendor claims are inconsistent on the exact count). Distinguishing feature: centralizes and versions prompts like a structured, trackable "prompt CMS" rather than loose docs/chats. Offers country-level/localized tracking. | Distinguishing feature: tracks **real user prompts**, not keyword abstractions, and states it collects data by scraping the actual consumer chat UIs (ChatGPT, Gemini, AI Overviews, Perplexity), not only official APIs — meaning it captures what a real user would see, which can differ from an API response. ~24hr refresh cycle. Includes "Agent Analytics" — tracking real AI-crawler (GPTBot, etc.) visits to a site as a leading indicial signal. |
| **Sources / citations** | Explicitly separates "who gets named" from "who gets cited," surfacing citation gaps scored as prioritized Opportunities by impact/severity. Includes an "AI Crawler Setup" check confirming bots (OAI-SearchBot, PerplexityBot) can actually access the site — a baseline check, not just an outcome metric. | Connects citations back to source domains ("source intelligence"); no public detail found on citation-authority weighting or a distinct citation-graph feature (Epic 10 on our own roadmap lists "Citation authority graph" as a not-yet-built moat feature — this may be a genuine gap across all three vendors, not just internal). | Citation analysis extends to **off-site/earned media** — Reddit threads, YouTube videos, third-party domains — not just the client's own site. This is broader than the other two as documented; positioned as showing "why AI cites X instead of you" rather than only "does AI cite you." |
| **Content / distribution** | **Conflicting claims across sources.** Independent reviews (Search Atlas) state Searchable does **not** deploy fixes or generate content — findings convert to human tasks only. Searchable's own marketing copy claims a "content engine" that generates blog posts/landing pages "engineered for AI citation." Not resolved here — flagged as a discrepancy to verify directly rather than build against. | Has both an automated and a managed layer: the **software** tier includes AI blog generation as part of its plan; a separate **paid managed-services tier ($1,699+/mo)** adds a human team that writes content, fixes gaps, and delivers a monthly action plan. Notable: part of "AIClicks" is literally a services business, not pure software. | "Optimize" tool gives content-improvement suggestions on existing pages; content briefs can be configured with live web screenshots, search results, and news context. Maps existing content against AI answers to show coverage vs. gaps (closer to our own "Why engine" concept in Epic 12, not yet built by us either). |
| **Outreach / agent tooling** | "Actions" feature converts findings into a prioritized task list (by impact/severity), exportable to a connected project-management tool — implementation is always human-executed, never auto-deployed. Markets a "personal AI agent" that reportedly learns brand voice/products/goals over time, though no independent review corroborates this beyond the vendor's own site. | Managed-services layer functions as the "outreach" mechanism — a human account team rather than agentic tooling. No MCP or agent-protocol integration found in public docs. | Publishes an API for programmatic access to visibility/citation data and explicitly supports **MCP (Model Context Protocol)** for structured tool-to-tool communication — the only one of the three with a documented agent-protocol integration. Also offers a JS tracking snippet plus server-log analysis for enterprise-grade AI-crawler detection. |

**Sourcing note on scale claims (treat as vendor-reported, not verified):**
AIClicks claims 900+ tracked brands and 400+ agencies (~40% of its customer
base) as of an August 2026 write-up. PromptWatch claims figures ranging from
1,840 organisations to 7,000+ brands depending on the source and date, plus a
€6M seed round and €2M ARR reported around September 2025 — the spread between
those two brand-count figures was not reconciled and should not be quoted as a
single number.

**Sources (retrieved 2026-09-09):** searchable.com (marketing + pricing +
features pages), searchatlas.com/blog/searchable-ai-review, g2.com (Searchable
and AIclicks review pages), trustpilot.com/review/aiclicks.io,
trakkr.ai/reviews/aiclicks-review, clutch.co/profile/aiclicks,
docs.aiclicks.io, citedindex.com/aiclicks, promptwatch.com (about + product
pages), and independent review posts on aiso.blog, generatemore.ai, radarkit.ai,
brandonleuangpaseuth.com, ai-search-tools.com, and indexly.ai.

### 2.3 The cross-cutting observation — [DECIDED, as an analytical conclusion]

**All three products reduce to the same shape: measurement → insight →
distribution → outreach, organised as data.** Their own labelling differs and is
mostly marketing; the underlying shape does not.

This matters because it says where the contest actually is. If every competitor
has the same four-stage skeleton, then feature-count parity is not a strategy —
**the differentiation has to be in the quality of one stage, not the presence of
all four.** Section 3 states which stage this product is betting on.

### 2.4 "AI agents" — [OPEN, unresolved in both directions]

**Observation:** most competitor "Agent" labelling appears to describe
conventional pipelines — scheduled crawls, chat-over-stored-data, single-shot
generation — rather than genuinely agentic multi-step autonomous behaviour: a
system that *plans*, *decides*, and *acts with judgement across multiple steps*.

**This is [OPEN] and is deliberately not resolved here.** Two failure modes,
both real:

- **Building an agent because competitors say "agent."** Adding the label to an
  existing pipeline for parity's sake buys nothing and costs a rewrite. This
  codebase already has the discipline to resist it — see `build-log.md` Epic 8.0,
  *"the model may not decide what is wrong, only how to say it and how much it
  matters"*, where the candidate set is enforced by `accept()` in
  `services/fix_generator.py` rather than requested in a prompt.
- **Refusing an agent because the label is overused.** There may be a place
  where multi-step autonomy genuinely earns its complexity.

**The decision to make, deliberately, later:** where — if anywhere — a real agent
is worth its complexity in this product. Not "should we have agents." **That
decision is not made in this document and must not be assumed by any brief that
cites it.**

---

## 3. Product architecture — the seven-layer model

> **Section label:** the seven-layer **shape** is [DECIDED]. The **relative
> priority** between layers is [HYPOTHESIS] — see §3.8.
>
> Every build-status claim below was verified on 2026-08-27 against real commits,
> real `build-log.md` entries, and the actual source tree. Full suite green at
> the time of writing: **922 tests** (api 605, workers 13, shared-types 53,
> design-system 99, web 152).

### Layer 0 — Trust substrate — [DECIDED] · **BUILT, and genuinely differentiating**

**Its job:** make it structurally impossible for the product to store, render or
republish third-party copyrighted content, and impossible for one agency to see
another's data. Not by policy — by test.

**Build status: SHIPPED and enforced.** Verified:

- `apps/api/tests/test_ip_safety.py` — **76 tests collected**, including **sweep tests**
  that walk every SQLAlchemy model and every response schema rather than
  checking a hand-listed set. Examples verified in the file:
  `test_no_column_named_like_raw_content`, `test_no_unbounded_text_columns`,
  `test_engine_result_stores_a_digest_not_the_answer`,
  `test_report_projection_exposes_no_third_party_prose` (which asserts
  `checked >= 10`, so the sweep cannot silently stop covering the module).
- `apps/api/tests/test_tenant_isolation.py` — 4 tests that build **two** agencies
  with real data in both and assert neither can read the other's, plus session
  revocation on agency deletion and user suspension.
- `apps/api/tests/test_env_template.py` — asserts no credential-shaped string can
  reach the committed `.env.example`. Written after a real incident
  (`build-log.md` Epic 2.0, resolved, no exposure).
- The facts-only boundary is architectural, not advisory: `EngineAnswer`,
  `CrawlResult` and `SerpResult` are **plain dataclasses with no SQLAlchemy
  mapping and no persistence path** (`build-log.md` Epic 4.3).

**Differentiator or table stakes?** **Genuine differentiator**, and an
undervalued one. See section 7 — this is a sellable asset, not just hygiene.

---

### Layer 1 — Delivery — [DECIDED] · **BUILT, table stakes**

**Its job:** auth, seats, async execution, report rendering. The unglamorous
substrate that has to work.

**Build status: SHIPPED.** Verified:

| Capability | Where | Evidence |
|---|---|---|
| Agency + seat-based auth | Epic 1.3, 1.4 | `models/tenancy.py` (`Agency.seat_limit`, default 3, `CheckConstraint seat_limit >= 1`); `services/seats.py`; `tests/test_seats.py`, `tests/test_auth_flow.py`. httpOnly session cookies, Argon2id. |
| Core data model | Epic 1.4 | `models/` — `tenancy`, `client`, `scan`, `prompt`, `competitor`, `engine_result`, `score`, `technical_audit`, `action_item` |
| Narrative report render | Epic 7.0 | score → biggest gap → proof → fix → pitch, per `ip-safety.md` #3. Accepted live on `scan_01M0HDRGJNWNZDSJPP0NC3SV8W` (Help Scout, composite 58.24). |
| E2E timing harness | Epic 9.1 (`2dd1ede`) | `apps/api/scripts/verify_e2e.py`, nine phases timed |
| Bounded engine calls | Epic 9.2 (`f4c58a3`) | `ENGINE_CALL_CEILING = 122.0`, enforced by `asyncio.timeout`, not arithmetic. `tests/test_engine_timeout.py`, 6 cases, 5 fail pre-fix. |
| Agency dashboard | Epic 9.3 (`26be4d0`, `9a3228a`) | list of past scans + re-run |
| Async scan (`202` + status) | Epic 9.5 (`176e7f1`, `0e88dcd`) | `BackgroundTasks` behind an injected `ScanExecutor` seam (`services/scan_executor.py`, `deps.py:40`) |
| One open scan per client | Epic 9.6 (`2fae767`, `b0072a0`) | **partial unique index** in `models/scan.py:103-106`; the double-spend race is closed **by the database**, not by application logic |
| Self-updating dashboard | Epic 9.7 (`5cb7fe2`, `82734c0`) | framework-free polling rules in `apps/web/src/lib/dashboard/polling.ts`, 5s interval, costed |

**Known open, stated honestly:**
- **The 300s scan budget is MISSED.** Best measured run: **361.3s** (Epic 9.2),
  down from 498.2s (Epic 9.1). Over budget by 61.3s (1.20×). `PROMPT_CONCURRENCY`
  remains unraised — Epic 9.1's candidate fix 2, unblocked but unbuilt.
- **`product-spec.md` §7 Epic 9's dashboard checkbox still reads `[ ]`** despite
  Epic 9.3 shipping it. Stale. Correcting it is out of scope here (see §8) but
  it is a real inconsistency, noted rather than silently inherited.
- **No phase-level progress.** A user sees *that* a scan progresses, not *how
  far*. Epic 9.1's nine-stage table is exposed by no endpoint (Epic 9.7).

**Differentiator or table stakes?** **Table stakes.** Must be solid. Nobody buys
this.

---

### Layer 2 — Measurement — [DECIDED] · **PARTIALLY BUILT — the known weak point**

**Its job:** collect real data from multiple AI answer engines.

**Build status: SHIPPED for TWO vendors and three engines.** This heading read
"SHIPPED for one vendor" until 2026-09-08 and was stale — corrected here on the
day it was found wrong, the way Layer 5's status was.

- `EngineAdapter` is a `Protocol` (`services/engines.py`). THREE adapters
  implement it and all three are in `DEFAULT_ENGINES`, so every scan runs all
  of them: `ClaudeParametricAdapter`, `ClaudeSearchAdapter`, and
  `ChatGptAdapter` (`gpt-5.5`, over raw `httpx` — no `openai` SDK, because its
  current major requires a second HTTP stack alongside the pinned `httpx`).
  Nothing in the runner, extraction or persistence layer knows which engines
  exist; adding one is a new class plus a key.
- **Epic 4.2's limitation is now PARTLY closed, and Epic 9.13 says which part.**
  Its words were: *"this is one vendor and one model, so it does not test
  cross-vendor variance... That is credential-bound, not design-bound."* An
  `OPENAI_API_KEY` was provisioned and `chatgpt` joined as the PARAMETRIC
  analogue of `claude` — deliberately not of `claude_search`, because holding
  the mode constant is what makes "Claude names you, ChatGPT does not" a
  statement about the vendors rather than about browsing.
- **Perplexity and Google remain unbuilt.** `Engine.PERPLEXITY`,
  `Engine.GEMINI` and `Engine.GOOGLE_AI_OVERVIEW` exist in the enum and have no
  adapter behind them; the router refuses any engine absent from
  `ENGINE_REGISTRY` at the request, before a scan row exists. A grounded OpenAI
  engine is a fourth adapter for a later brief, not a variant of this one.
- **AI Overviews via SerpApi: MEASURED AND DECLINED, not pending.** Two
  investigations on 2026-08-25 (`d39f70b`, `d2d5f40`): usable content was **0 of
  4** on this product's real prompt shapes, retrieval costs two SerpApi searches
  per prompt, and the quota is 250/month against ~48 searches per scan. Marked
  `[—]` in `product-spec.md` §7 for exactly this reason.

**Differentiator or table stakes?** **Table stakes, and now at par rather than
below it.** Two vendors in the same parametric mode, plus one grounded engine,
is enough to make a cross-vendor claim honestly — which is what Layer 3's
cross-LLM sentiment comparison was blocked on. Multi-engine remains catch-up
work rather than a moat; the difference is that its absence no longer undercuts
Layer 3's best claims.

---

### Layer 3 — Insight — [DECIDED] · **PART BUILT — and the intended point of differentiation**

**Its job:** turn collected facts into a finding a human acts on.

**What is SHIPPED:**

| Capability | Status | Evidence |
|---|---|---|
| Composite score, deterministic | ✅ Epic 5 | `services/scoring.py`; `Decimal` throughout (Epic 5.1 caught a float in the weighted-sum path); re-scored 5× to prove determinism |
| Share of Voice | ✅ Epic 5.2 | `services/scoring.py:295` `share_of_voice()`. Deviation 1 (no-competitor SoV) recorded in `scoring-spec.md` v1.1. |
| Sentiment | ✅ Epic 4.3 | `services/extraction.py`; `Sentiment` enum in `models/engine_result.py:74`. Stored as a **label**, never the answer text. |
| **The Answer Shelf** | ✅ **Epic 7.1** (`82342e0`, `039a64e`, `6f953f3`) | Per-prompt ordinality. `ReportProofOut.prompt_shelf` (`services/report.py:548`); `AnswerShelf` component; layout test `answerShelfLayout.test.ts`. |
| Unclaimed-domain finding | ✅ Epic 7.1 | `unclaimed_cited_domains` (`services/report.py:547`), computed from the **full** citation set before the evidence table's `MAX_CITED_DOMAINS = 12` cap (`services/report.py:74`) — the cap would have truncated the real finding on the Help Scout scan |

**The Answer Shelf is the clearest already-shipped differentiator in the
product.** Its correctness condition is load-bearing and enforced: *every
answered row carries exactly one subject mark* — a marker at the ordinal the
answer gave it, **or an explicit empty notch, never nothing.** Epic 7.1 states
why that matters: *"A row that renders nothing when the subject is missing does
not look like a bug. It looks like a clean report."*

> **[founder-sourced, unverified]** The claim that no described competitor has an
> equivalent per-prompt ordinal shelf rests entirely on §2's founder research and
> **has not been independently verified.** It is a plausible differentiator, not
> an established one. Do not put it in sales copy before re-verifying.

**What does NOT exist, stated plainly:**

- **Topic / query fan-out modelling: NOT BUILT.** It is `product-spec.md` §7
  **Epic 12** (*"Prompt fan-out/paraphrase clusters"*), unchecked. Repo-wide grep
  for `fanout`/`fan-out`/`paraphrase` finds only that checkbox, two incidental
  prose mentions in `build-log.md`, and a comment in `scan_runner.py:41` about
  concurrency fan-out — an unrelated use of the word.
- **Cross-LLM sentiment comparison: NOT BUILT — but no longer impossible.**
  This bullet read "currently impossible... blocked by Layer 2" until
  2026-09-08, on the strength of the one-vendor claim corrected above. Sentiment
  is computed per engine result and `chatgpt` now runs on every scan beside
  `claude`, deliberately in the same parametric mode, so **the cross-vendor axis
  exists in the data today** — `engine_results` already carries a sentiment and
  a confidence per (prompt × engine), and nothing reads them comparatively.
  What is missing is the comparison and its presentation, which is Layer 3 work
  rather than a Layer 2 dependency. It has stopped being the clearest case of a
  differentiator held hostage and become the clearest case of one that is simply
  unbuilt.

**Differentiator or table stakes?** **The intended differentiator.** See §3.8.

---

### Layer 4 — Prescription — [DECIDED] · **BUILT to its own acceptance; PARTIAL as a layer**

**Its job:** turn the finding into named, scoped, prioritised actions.

**Build status:** Epic 8.0's two acceptance criteria are both met and both `[x]`
in `product-spec.md` §7. `services/fix_runner.py` + `services/fix_generator.py`.
Verified specifics:

- **The arithmetic is ours; only the prose and the judgement are the model's.**
  `build_candidates` produces a **closed** list before any model call, and
  `accept()` **discards** any returned fix whose `candidate_id` does not match —
  enforcement, not a polite request in a prompt. `rank` and `points_upside` are
  taken from the candidate, never from the model.
- **Priority and effort are reasoned, not looked up.** Epic 7's floor had
  `effort` as a constant per detail code — *"add FAQ schema" was `M` for every
  site that has ever been scanned.* Epic 8 replaced that with model-reasoned
  values carrying `priority_reason` / `effort_reason`, which are **debug-only**:
  absent from `PersistableFix`, `ActionItemOut`, the `action_items` table and
  every response. `test_model_reasoning_is_never_persisted_or_returned` walks
  that whole chain.
- 11 injected breakages in the negative-control pass; the near-miss is recorded.

**Why the layer is nonetheless PARTIAL:** the product produces a fix list and
then loses interest. **Nothing tracks whether a fix was done, and nothing
re-measures its effect.** `ActionItemStatus` exists on the model but no
before/after mechanism does — that is Epic 11 territory (*"Before/after ROI
report generator"*, `[ ]`).

**Differentiator or table stakes?** **Already differentiated.** Named,
effort-and-impact-scored actions with an enforced candidate boundary is a
materially different artifact from raw data with a chart on it.

---

### Layer 5 — Distribution / Action — [DECIDED] · **PARTIALLY BUILT**

**Its job:** content generation, outreach, and getting the artifact in front of
the human who decides.

**Build status: PARTIALLY BUILT, and it no longer blocks Epic 9's acceptance
criterion.** This heading read NOT STARTED until Epic 9.21 and was stale by
three epics — corrected here rather than left for a later reader, the way Epic
7.1 corrected `design-direction.md` and `design-system.md` in the commit that
made them wrong.

What exists: the public share link (`POST /scans/{scanId}/share`,
`GET /reports/{token}`, Epic 9.8), PDF export on both the authenticated and the
public path (Epic 9.14), and expiry plus revocation on the link (Epic 9.21).
White-label branding shipped in Epic 9.22 with the token-override policy
written rather than assumed: an agency may set a logo and ONE accent colour,
and the accent reaches chrome only. The visibility ramp, beacon, competitor and
semantic token families are not overridable by any code path, because the
report's palette is notation and an agency free to recolour it changes what the
score means.

What does not exist: a custom domain (deferred — it needs DNS verification,
certificate issuance and routing before it does anything, and a column with
none of that behind it is a field that looks built), an agency logo in the PDF
(the writer has no image support by design, and adding it reopens a closed
dependency survey), and outreach content generation, which is untouched and
remains §7 Epic 10.

- All seven `product-spec.md` §7 Epic 10 items are `[ ]`.
- **`PDF export + shareable web link` is `[ ]`** — Epic 7's own deferred item,
  now re-pointed at "the Epic 9 send path (slice 3)." **Epic 9's acceptance is
  *"generate and send at least one real prospect report"* and cannot be met
  without one.**
- White-label branding is `[~]`: agency **name + slug** only. Logo, domain and
  colours are deferred — they need new columns *and* a written policy on which
  tokens an agency may override, because the visibility ramp is load-bearing
  (`build-log.md` Epic 7.0).

**Differentiator or table stakes?** **Lowest technical differentiation of any
layer** — competitors have rough equivalents. Build it cheaply and late.

**The exception that matters:** cheap and late ≠ optional. **It is the mechanism
for Epic 9's own acceptance criterion.** See section 6, where this reframes
Epic 10's priority.

---

### Layer 6 — Compounding — [HYPOTHESIS] · **DOES NOT EXIST · Phase 3+ at the earliest**

**Its job:** cross-client benchmarks and category-level insight — what only a
platform holding data across many agencies can say. *"Your citation strength is
in the bottom quartile for B2B SaaS support tools"* is a sentence no
single-tenant tool can produce.

**Build status: does not exist, and is not on the roadmap in any form.**
Verified: cross-client benchmarking appears in **no** `product-spec.md` epic —
not Epic 11, not even Epic 12 ("Moat Features"), whose five items are causal
experimentation, the "why" engine, fan-out clusters, ads intelligence, and
backlink/social audits.

> **[founder-sourced, unverified]** The claim that this exists in **no** described
> competitor feature set comes from §2's research and is not independently
> verified.

**Two hard preconditions, both currently unmet:**

1. **Real data volume across many agencies.** With one agency and no paying
   customers, a cross-client benchmark is a sample of one wearing a lab coat. It
   does not "work badly" at low volume — **it produces confidently wrong
   numbers**, which is worse than producing none.
2. **Anonymisation rigour at least equal to `ip-safety.md`'s existing facts-only
   discipline, designed and tested BEFORE any aggregate ships.** Aggregating
   across tenants is the single most dangerous thing in this document: it points
   directly at the invariant `test_tenant_isolation.py` exists to protect. Any
   Layer 6 work needs its own sweep tests proving no client is re-identifiable
   from an aggregate, written to the same standard as `test_ip_safety.py` —
   **written first, not retrofitted.**

**Flag: Phase 3+ consideration. Not now, and not soon.**

---

### 3.8 The priority claim — [HYPOTHESIS, explicitly NOT decided]

**This is professional judgement. It has never been tested against a customer.**

- **Layer 3 (Insight) is the intended point of competitive differentiation.**
  If §2.3 is right that all three competitors share the same four-stage
  skeleton, parity across stages is not a strategy — depth in one is. Layer 3 is
  the bet.
- **Layers 0, 1 and 2 must be solid, but are not where this product wins.**
  Layer 0 is the partial exception: it is table stakes as engineering and a
  differentiator as a *sales asset* (section 7). Layer 2 is the current drag —
  it is holding a Layer 3 capability hostage (cross-LLM sentiment).
- **Layer 5 is a parity feature: build it cheaply and last** — *except* insofar
  as it is the mechanism for Epic 9's acceptance criterion. See section 6.

**What would falsify this:** pilot agencies who buy on breadth of engine coverage
rather than depth of finding, or who never open the insight beat and only want
the outreach draft. Either result moves the bet. **That evidence does not exist
yet, which is exactly why this is [HYPOTHESIS].**

---

## 4. Physical / deployment architecture

> ### ⚠ SECTION-LEVEL LABEL: [HYPOTHESIS, pre-revenue]
>
> **There is no deployed environment.** Not staging, not production. Every
> vendor named below is a candidate, not a decision. `infra/deploy/README.md`
> already states the principle this section obeys: *"infrastructure-as-code
> written against a hypothetical environment is guaranteed to be wrong."*

### 4.1 The proposed topology — [HYPOTHESIS]

| Component | Proposal | Reasoning |
|---|---|---|
| **Web** | Next.js on Vercel | `apps/web` is already Next.js; the platform is the path of least resistance for it |
| **API** | FastAPI on a **persistent-process host** (Fly.io / Render — **neither chosen**) | **Explicitly NOT serverless.** Two hard blockers, both verified in this repo: Playwright drives a real browser (`services/crawl.py:185`, `async_playwright()`), and `BackgroundTasks` runs *in the API process* (`deps.py:40`, `services/scan_executor.py:187`). A serverless function that returns `202` and then dies takes the scan with it. |
| **Database** | Managed Postgres | Postgres 17. **`psycopg2`/`psycopg3` are unavailable** — LGPL-3.0, on `ip-safety.md` #6's stop-and-ask list. Everything uses `asyncpg`, **including Alembic**. Any host must accept that. |
| **Cache / queue** | Managed Redis | Sessions `/0`, Celery broker `/1`, results `/2` — same instance or three coordinated ones, so flushing a wedged queue does not sign every user out (`infra/deploy/README.md`) |
| **CI/CD** | GitHub Actions, **gating deploys on the existing test suite** | See §4.3 — this does not exist |
| **Error tracking** | Not chosen | — |
| **Secrets** | The host's native secret manager. **Never in git.** | `tests/test_env_template.py` enforces this locally today; a deployed environment must inject every variable in `apps/api/.env.example` from a real secret store |

**Already fixed and non-negotiable wherever this deploys** (from
`infra/deploy/README.md`, verified): `APP_SECRET` must not be the dev
placeholder — the app **refuses to boot** in `staging`/`production` otherwise;
`SESSION_COOKIE_SECURE=true` is forced in those environments;
`CORS_ALLOW_ORIGINS` must name real origins (cookies reject
wildcard-with-credentials); migrations run before the API starts.

### 4.2 The BackgroundTasks → Celery trigger — [DECIDED trigger, HYPOTHESIS threshold]

Epic 9.4 (`618cce8`) chose FastAPI `BackgroundTasks` over Celery deliberately,
and stated the price openly:

> *"It runs in the API process: it does not survive a restart or deploy, and it
> does not scale past one instance. Both are real — and neither is a constraint
> today. Epic 9 is pre-pilot, single instance. The honest consequence is the
> stale-scan reaper: choosing the cheap executor makes the reaper **mandatory**
> rather than merely prudent."*

**Revisit that decision when — and not before — either of:**

1. **~10–15 concurrent agencies actively scanning.** *(Threshold is
   [HYPOTHESIS]; the shape of the trigger is [DECIDED].)*
2. **A real requirement that a deploy must not drop an in-flight scan.**

**Not before.** Building for scale that is not yet needed is a named risk in this
document, consistent with Epic 9.4's own reasoning. The migration is cheap *by
construction* — `orchestrator.py` exists, the executor is injected via
`deps.scan_executor`, and moving to Celery is **one new implementation of a
protocol that is already written**, not a rewrite. Epic 1.2 built that seam for
exactly this moment.

### 4.3 CI/CD is ABSENT — and is the first thing that must exist — [DECIDED]

**Verified: there is no `.github` directory in this repository. No workflows, no
CI, no gate of any kind.** `infra/deploy/README.md` lists "CI pipeline
definition" under *"What is deliberately not here yet."*

**Nothing stops a broken commit from reaching a production environment.** Today
that is harmless — no production environment exists. **The moment one does, it
stops being harmless.**

The suite this would gate is real and worth gating: **922 tests**, verified green
on 2026-08-27 (api 605, workers 13, shared-types 53, design-system 99, web 152).
Plus `ruff` clean and `mypy` at its pre-existing 38 errors.

> **This is the first piece of physical architecture that needs to exist, and it
> is prior to and separate from any feature work.** A deploy target without a
> gate in front of it converts every one of those 922 tests from a safety net
> into a formality. **It needs its own scoping brief** (§8) — it is named here,
> not scoped here.

**One live-infrastructure note, learned the hard way while writing this
document.** The suite requires the dev cluster — Postgres on **55433** and Redis
on **6379**, via `./infra/db/scripts/dev_cluster.sh up`. Without it the API suite
does not fail cleanly: **186 tests `ERROR` on `ConnectionRefused` and the passing
count silently drops from 605 to 398.** `pytest` does exit `1`, correctly — but a
run piped through `tail` reports the **pipe's** exit code, not `pytest`'s, and
reads as a clean pass at a glance.

Two consequences for the CI definition in §4.3, both concrete:

- **Assert the expected test *count*, not just the exit status.** A suite that
  quietly runs two-thirds of itself is the failure mode here, and an exit code
  alone will not catch a future variant of it.
- **Never pipe the test command through `tail`/`head` in a gate.** Use
  `PIPESTATUS`, `set -o pipefail`, or no pipe at all.

---

## 5. Commercial architecture

> ### ⚠ SECTION-LEVEL LABEL: [HYPOTHESIS THROUGHOUT]
>
> **No customer has ever seen a price. Every number below is a proposal.**
> The only figures here that are *measured* are the unit-cost inputs in §5.1,
> and even those come from two scans on two domains.

### 5.1 Unit economics — the real measured numbers, and the one that does not exist

**MEASURED — verified in `build-log.md`:**

| Input | Value | Source |
|---|---|---|
| Anthropic calls per 24-prompt scan | **96** | Epic 9.1 (`basecamp.com`): 48 engine + 41 sentiment + 1 classify + 4 co-citation + 1 prompt-gen + 1 fix-gen |
| Anthropic calls per 24-prompt scan | **103** | Epic 9.2 (`linear.app`): same split, 48 sentiment — every answer mentioned the subject |
| Model | `claude-opus-5` | both runs |
| SerpApi searches per scan | **6** | Epic 9.1 and 9.2, both — *"exactly what `build_queries()` emits"* |
| SerpApi plan today | **Free Plan, $0.00/month, 250 searches/month** | read from `account.json`, not inferred (Epic 9.0 / investigation `d39f70b`) |
| Wall-clock per scan | **361.3s** best measured | Epic 9.2 |

**NOT MEASURED — [UNVERIFIED]:**

> **No per-scan dollar cost has ever been recorded in this project.** A
> `~$4–7/scan` figure was asserted in the source conversation. Grepping every
> `$`-prefixed number in all 6,650 lines of `build-log.md` returns **three
> matches, all of them SerpApi's `$0.00/month` Free Plan.** No token counts were
> captured either — only call counts — so the figure cannot even be reconstructed
> after the fact.
>
> **Do not quote a per-scan dollar cost until one is measured.** The call counts
> above are real and are the honest basis for any estimate. Capturing token usage
> per scan is cheap, obvious future work and is the precondition for every number
> in §5.3 meaning anything.

**Two cost facts that ARE solid, and both point the same way:**

1. **Multi-engine will raise cost roughly linearly in engines.** 48 of the ~100
   calls are engine calls. Adding a second vendor does not add 5% — it adds
   something near another 48 calls plus its sentiment pass. **Any pricing model
   built on today's single-vendor cost will be wrong in the direction that
   hurts.**
2. **The SerpApi free tier is NOT viable for paying customers.** 250
   searches/month ÷ 6 per scan ≈ **41 scans per month across the entire
   platform** — not per agency, *total*. **A paid SerpApi plan is a hard
   prerequisite before any real pilot billing begins**, and its cost is a COGS
   line that does not exist in any estimate today.

### 5.2 The pricing model — [HYPOTHESIS]

**Recommended: scan-metered / tiered**, following the standard martech-SaaS
pattern (Ahrefs, SEMrush and peers all meter the expensive primitive and tier
around it).

**The reasoning is a cost structure fact, not a preference:** per-scan COGS is
**variable and provider-denominated** — ~100 model calls plus 6 SerpApi searches,
both billed by a third party, both rising with multi-engine. **Flat "unlimited"
pricing against a variable third-party COGS is unsafe without a hard cap.** One
agency running bulk prospecting overnight (which is literally Epic 10's
*"Bulk CSV upload + overnight batch scoring"*) can invert the margin on a flat
plan in a single night.

**The metered primitive should be the scan**, because the scan is already the
unit the whole system is built around: `Scan` is a first-class model, it has a
lifecycle (`queued → running → succeeded/partial/failed`), it is already
constrained to one open scan per client by a database index (Epic 9.6), and it
is the thing that costs money. **Metering something the schema does not already
treat as a unit would be inventing a billing concept; metering the scan is
recognising one that exists.**

### 5.3 The published price — [DECIDED] · and the tier structure above it — [HYPOTHESIS]

> **UPDATED 2026-08-28, Epic 9.15.** This section previously read
> *"Illustrative tier structure — [HYPOTHESIS — numbers are placeholders]"* and
> instructed the reader **"Do not quote them."** That instruction is now wrong
> for one number and still right for every other, so the section is split rather
> than relabelled wholesale. Read both halves.

#### 5.3.1 What is now real — [DECIDED, on the founder's explicit instruction]

**One plan, $29/month, 3 seats, published on the public landing page.**

| | |
|---|---|
| Price | **$29.00 USD / month**, recurring, no annual option |
| Seats | **3** — `Agency.seat_limit`'s existing default, not a new entitlement |
| Where it is published | `apps/web/src/components/marketing/LandingView.tsx`, Pricing section |
| Where the number lives in code | `PricingCard.tsx`'s `PLAN_PRICE_USD`, and the Stripe Price object |
| Payment processor | **Stripe** — chosen and built, Epic 9.15 |
| Stripe mode | **TEST MODE.** `sk_test_…` / `price_…` with `livemode: false` |
| Real money moved | **None. Not one cent.** See the caveat below |

**This supersedes the [HYPOTHESIS] flag that used to cover this number, and it
did so by founder decision rather than by evidence.** The re-validation trigger
at §0 has NOT fired: no pilot conversation has happened, no pricing objection
has been heard, and §5.1's per-scan dollar cost is still unmeasured. So the
$29 figure is **decided, not validated** — those are different things, and the
distinction is the whole reason this document has labels. What changed is that
the founder chose to publish a number and charge against it; what did not
change is that nobody has yet paid it or argued about it.

> **⚠ The price is live. The payments are not.**
>
> The Stripe account backing this (`PSM Digital sandbox`) has
> `charges_enabled: false` and `details_submitted: false` — business
> verification is not done, and finishing it is the founder's call, whenever he
> is ready. Until then a real card cannot be charged even if one were entered.
> **Going live is a key swap, not a code change**, by deliberate design: Epic
> 9.15 ships no branch that behaves differently on a live key.

**There is no paywall, and adding one is a separate decision nobody has made.**
Sign-up, the onboarding wizard, scanning, scoring and reporting are all exactly
as free as they were before this price existed. Subscribing is how an agency
pays for the product; it is not how an agency unlocks it. Any brief that wants
to gate a feature behind `subscription_status` is proposing a new product
decision and must say so.

#### 5.3.2 What is still a placeholder — [HYPOTHESIS, unchanged]

**The multi-tier structure below has met no customer and does not exist in
code.** Only ONE plan is built and sold. These three rows remain shape, not
price. **Do not quote them.** They also rest on a per-scan cost that has never
been measured (§5.1) — so the margin implied by any of them is currently
unknowable, which is equally true of the $29 above.

| Tier | Seats | Scans / month | Intended buyer |
|---|---|---|---|
| **Starter** | 1–3 | low | A solo operator or a small agency prospecting occasionally |
| **Agency** | 3–10 | mid | The core case: an agency prospecting continuously and servicing retained clients |
| **Agency Pro** | 10+ | high | Bulk prospecting, batch scoring, multiple account managers |

Where the single shipped plan sits against them is deliberately not answered
here. It is 3 seats, which is Starter's range, at a price the table never
proposed — because the table was drawn before the price was chosen and has not
been redrawn against it. **Redrawing it is a pricing exercise, not a
documentation one**, and it belongs after the first real pilot conversation.

**This maps onto tenancy that already exists** and requires no schema invention:
`Agency.seat_limit` (`models/tenancy.py:59`, default 3,
`CheckConstraint seat_limit >= 1`) is already enforced **transactionally** in
`services/seats.py`, with `test_concurrent_signups_cannot_exceed_the_limit`
covering the race. Invited-but-not-accepted users already occupy a seat,
deliberately — *"otherwise an agency could issue unlimited invitations and
overshoot its plan the moment they accept."* **The seat half of billing is
already built and tested.** Only the scan-metering half is missing.

### 5.4 The concrete NEW engineering work — none of it built — [scope, not schedule]

| # | Work | Notes |
|---|---|---|
| 1 | **`Plan` / `Subscription` model tied to `Agency`** | **BUILT, minimally — Epic 9.15.** `Agency` now carries `stripe_customer_id`, `stripe_subscription_id`, `subscription_status` and `subscription_current_period_end`. There is still no `Plan` ENTITY and no tier concept, because there is one plan; if a second ever exists, that table is the work. The claim this row used to make — *"no `plan`, `subscription`, `billing` or `stripe` identifier appears anywhere in `models/`"* — is no longer true. |
| 2 | **`UsageRecord`, persisted at the moment a scan is billed** | **Same discipline as `EngineResult`: persist the fact when it happens.** A usage count derived by re-querying scans at invoice time is a reconstruction, and reconstructions disagree with reality exactly when a dispute makes it expensive. Write the billing event when it occurs. |
| 3 | **Stripe metered / usage-based billing integration** | **Vendor DECIDED and integrated: Stripe — Epic 9.15.** The `stripe` Python SDK is MIT (verified against the LICENSE file in the sdist, not the classifier) and is recorded in the licence ledger. **The METERED half is still not built** and is what this row now means: what ships is flat monthly subscription billing — Checkout, a webhook, and the hosted Billing Portal. Usage-based billing needs row 2's `UsageRecord` first. |
| 4 | **A billing-failure state on `Agency`, rendered as honestly as `partial` is today** | The precedent is live and load-bearing: `ScanStatus.PARTIAL` renders as a real, visible degradation, and Epic 9.2 corrected the dashboard's earlier assumption that `partial` was routine. **Never silent degradation.** An agency whose payment failed must see that, in those words, not discover it as features quietly not working. |
| 5 | **[OPEN] — what happens to a scan already in flight when payment fails** | See below. |

#### [OPEN] — the in-flight scan on payment failure

**Requires an explicit founder decision. It must not be resolved by whoever
implements billing first.**

**Recommendation (not a decision): never kill an in-flight scan. Block only new
scans.**

The reasoning: a scan is ~360 seconds of real third-party spend that has
*already been incurred* by the time a payment fails. Killing it wastes money
that is gone anyway and destroys the artifact the agency is mid-way through
putting in front of a prospect — the single worst moment to degrade. Blocking
new scans achieves the commercial goal without that. It also matches the
existing architecture: Epic 9.6 made the *creation* path the enforcement point
(the partial unique index), and there is no kill path for a running scan today
in any case.

**But it is a product and commercial call, not an engineering one, and it is
recorded as [OPEN] until the founder decides.**

---

## 6. Customer lifecycle — mapped onto the EXISTING roadmap — [DECIDED mapping]

> **This section proposes NO new epics.** It names what `product-spec.md` §7's
> existing Epic 9/10/11/12 structure **already is**, read commercially. The
> roadmap is unchanged. If a future brief wants to change it, that is a separate,
> explicitly-scoped brief (§8).

| Epic | Lifecycle phase | Its existing acceptance criterion, verbatim |
|---|---|---|
| **Epic 9** | **Activation** | *"pilot agencies successfully generate and send at least one real prospect report"* |
| **Epic 10** | **Sales Enablement** | *"each feature independently testable; prioritize based on pilot agency requests first"* |
| **Epic 11** | **Retention** | *"an existing pilot agency completes a full 90-day before/after cycle using the platform"* |
| **Epic 12** | **Expansion** (and the natural home of Layer 6) | *"each is a standalone module added without disrupting core scan pipeline"* |

### The Epic 10 reframe — worth stating explicitly

**Epic 10 is closer to critical-path than a pure technical-novelty ranking would
suggest.** §3.8 ranks Layer 5 lowest on differentiation, and that ranking is
correct *on its own terms*. It is also **not the same question as sequencing.**

Epic 10 is the mechanism by which a scan becomes a closed deal. **A scan without
a way to help close the deal it supports is evidence without a mechanism.** And
the dependency is not abstract: **Epic 9's own acceptance criterion contains the
word "send," and there is no export and no share link** (Layer 5). Epic 9 cannot
be marked complete without a piece of Layer 5 existing.

**Read together: low differentiation, high necessity.** Build it cheaply — but
do not read "lowest differentiation" as "latest in the order." Those are
different axes, and conflating them would strand Epic 9 indefinitely.

### Lifecycle signals worth capturing — [OPEN, future work, NOT built]

Concrete and measurable, and **none of them exist today**:

- **Churn signal:** a paying account whose scan has not been re-run in **60+
  days**. The data is already there — `Scan` carries timestamps and
  `ScanTrigger` — but nothing watches it.
- **Expansion signal:** an agency hitting its scan cap **two months running**.
  Requires the `UsageRecord` from §5.4 to exist first.

**These are named so they are not re-invented later, not scheduled.** Each needs
its own brief.

---

## 7. Trust as a sellable asset — [DECIDED]

**The facts-only, test-enforced data discipline is not only internal hygiene. It
is a sales asset, and this document records it so it does not get lost.**

The market context is live and specific: **AI tools that scrape and repackage
competitor content are a real reputational and legal risk for marketing
agencies.** An agency putting a third-party AI tool's output in front of a
client's CMO is putting its own name on that output. The question *"where did
this come from, and can we be sued for it?"* is one an agency principal actually
asks.

**This product has an unusually good answer, and it is enforced rather than
promised:**

> **"Every claim in this report is independently verifiable, and we never store
> or reproduce anyone's copyrighted content."**

That claim is backed by real, checkable mechanism — not a privacy-policy
paragraph:

- `ip-safety.md` constraint 7 restricts persistence to **structured facts only**:
  booleans, counts, ordinals, cited URLs and domains, entity names, structural
  signals.
- `test_ip_safety.py`'s **sweep tests** walk every model and every response
  schema, so a *newly added* column or field is covered without anyone
  remembering to add a test — which is the failure mode a hand-listed set has.
- The boundary is architectural: `EngineAnswer`, `CrawlResult` and `SerpResult`
  are plain dataclasses with **no persistence path at all**.
- Every UI epic in `build-log.md` carries an explicit
  `IP-safety check passed: …` line enumerating what was verified — constraint 9's
  gate, honoured in every entry through Epic 9.7.

**Where it belongs:** in actual sales materials, once Layer 5 exists to carry
them. **Not built now.** Recorded here as a real, defensible asset — earned over
nine epics of discipline — that would be easy to under-sell precisely because it
was never built *as* a feature.

---

## 8. Governance — the drift-check rule — [DECIDED]

### 8.1 The rule

**Every future brief must state, in one line, before scoping proceeds:**

> **Which architectural layer(s) (§3) does this touch, and which lifecycle phase
> (§6) does it serve?**

**If a brief cannot answer this cleanly — if it touches multiple layers or
multiple phases without a clear reason — that is the signal to split it before
building.**

This is not a new discipline; it is a name for one the project already uses.
Both of the most recent multi-part epics were split on exactly this instinct:

- **Epic 7.1** → three commits (`82342e0`, `039a64e`, `6f953f3`): the projection
  stops aggregating away per-answer data; then the component; then mounting it
  and correcting the docs.
- **Epic 9.5** → two commits (`176e7f1`, `0e88dcd`): the endpoint and the row's
  visibility; then the test migration and the seam that keeps it small. Epic 9.4
  sized it in advance and said so: *"a single-session brief, but not a small one,
  and it needs an internal commit split."*

A brief spanning, say, multi-engine adapters (Layer 2) *and* billing (§5.4) *and*
a deploy pipeline (§4.3) is three briefs wearing one coat. **Split it.**

### 8.2 What this document does NOT do

**It does not commit to a build order or a timeline for anything in sections
3–6 beyond what `product-spec.md` §7's existing Epic 9–12 structure already
states.**

Sequencing remains subject to the same brief-by-brief scoping discipline used
throughout this project — the approach Epic 9.0 (`1730aa1`) took to reading
Epic 9's real acceptance criteria before scoping, and Epic 9.4 (`618cce8`) took
to scoping the async scan by first establishing what `apps/workers` actually was.

**Explicitly: a future epic that touches multi-engine work, fan-out modelling,
billing, or deployment still requires its OWN scoping brief before
implementation.** This document provides **the shape to scope against, not a
shortcut around scoping.** Citing north-star.md is not a substitute for reading
the code, measuring the thing, and stating the trade-off — which is what every
entry in `build-log.md` actually does, and why it is worth reading.

### 8.3 Keeping this document honest

The two mechanisms that make this a living document rather than a one-time essay
are **§0's re-validation trigger** and **§8.1's drift-check rule**. Both are
worthless if unread.

Three specific decay risks, named so they are noticed:

1. **§2 is a snapshot with no expiry date on it.** Competitor products change.
   §2.1 was filled 2026-09-09, but from web-sourced material, not the founder's
   own live navigation — see §2.1's status note. That fill has its own expiry:
   marketing pages and reviews are wrong less obviously than a blank cell, which
   makes them easier to trust past their shelf life.
2. **§5's numbers are pre-customer and will be wrong.** The question is not
   *whether* they change but whether anyone updates them when they do.
3. **§3's build-status claims were verified on 2026-08-27 and start decaying
   immediately.** Every epic after Epic 9.7 makes at least one of them staler.
   **When an epic changes a layer's status, update §3 in the same commit** — the
   way `design-direction.md` and `design-system.md` were both corrected inside
   Epic 7.1 rather than left to be found by someone else five epics later.

**The whole reason this document exists is that a decision living only in a
conversation is a liability with a delay fuse. A document that stops being true
is the same liability, wearing a doc's authority.**

---

## 9. Research literature and upgrade candidates — [WEB-SOURCED, dated 2026-09-09]

**Status: informational, not scoped.** Everything in this section is background
material gathered from academic literature, competitor product research, and
public video content. **Per §8.1, none of it is pre-scoped into a build order.**
Every candidate below still needs its own brief stating which architectural
layer (§3) and lifecycle phase (§6) it touches before anyone builds against it.
This section exists so the research isn't lost, not to shortcut §8's discipline
— the same caution §8.2 gives for this whole document applies doubly to a
section that is, by its own admission, less vetted than the rest of it.

### 9.1 Primary academic sources

This is a young field — these four papers are close to the whole primary-source
literature as of 2026-09-09, not a curated subset of a larger canon:

- **Aggarwal et al., "GEO: Generative Engine Optimization"** (arXiv:2311.09735,
  KDD 2024) — the foundational paper. Introduces generative engines as
  synthesizing answers from multiple sources rather than ranking pages, and
  reports GEO techniques boosting visibility up to 40% in its benchmark
  (GEO-bench), with effectiveness varying by domain.
- **Martinez, "Optimizing Visibility in Generative Engines: A Critical Survey
  (2023–2026)"** (arXiv:2607.14035) — reviews 45 studies. **Read this one
  first; it is the most directly useful paper for this product.** Its core
  findings, load-bearing for §9.3 below: the foundational paper's 40% gain is
  conditional on a source already being present in a fixed context and does not
  establish organic discoverability; topical relevance and context position are
  the most reproducible levers; citation-oriented rewrites can impair
  retrieval; and commercial audits show low cross-engine source overlap and
  substantial run-to-run variability.
- **Kumar & Lakkaraju, "Manipulating Large Language Models to Increase Product
  Visibility"** (arXiv:2404.07981, Harvard) — demonstrates that a "strategic
  text sequence" embedded in a product page can raise its odds of being an
  LLM's top recommendation. Relevant defensively (§9.3, item 5), not as a
  technique to offer clients.
- **"EcoGEO: Trajectory-Aware Evidence Ecosystems for Web-Enabled LLM Search
  Agents"** (arXiv:2605.12887) — extends the manipulation question into
  agent-search-trajectory modeling; cites consideration-set choice modeling
  (Horowitz & Louviere, 1995) as a lens on how an LLM narrows candidates down to
  the few it names, which bears on the Share of Voice dimension in
  `scoring-spec.md`.

### 9.2 Competitor set gap

**Two additional direct competitors surfaced that are not in §2's set:**
**Profound** and **Scrunch AI** (distinct from Scrunch Influencer Marketing, a
different product under a similar name). Also relevant but a different
competitive category: **Semrush's AI Toolkit** and **Keyword.com's AI Rank
Tracker** — incumbent SEO platforms bolting AI-visibility tracking onto an
existing suite, a different threat model than a visibility-only pure-play.
**[OPEN]:** whether to extend §2.2's table to five-plus vendors, or keep the
original three and track the rest separately, is a founder call, not resolved
here.

### 9.3 Upgrade candidates — each **[OPEN]**, none scoped

1. **Run-to-run variability as a first-class signal, not noise.** The survey's
   documented finding of substantial run-to-run variability in commercial
   audits is currently invisible in this product's score: `scoring-spec.md`
   computes one deterministic number from one `EngineResult` set. Running each
   prompt multiple times per scan and surfacing a confidence range (or a
   stability sub-metric) would measure something no competitor is documented
   doing. **Touches:** Layer 2 (engine execution) and the scoring engine (§5.1
   `scoring-spec.md`) — likely a large brief, given determinism requirement 1
   ("no RNG... sort every collection by an explicit key") would need to extend
   to multi-run aggregation without breaking reproducibility.
2. **Paraphrase-robustness testing in prompt generation.** Since generic
   heuristics transfer poorly and reproducibility depends on rewording per the
   survey, generating 2–3 semantically equivalent paraphrases per prompt intent
   and aggregating results would harden exactly the kind of tautology
   `scoring-spec.md` v2 already fixed for a different reason (brand-named
   prompts inflating Mention Rate). **Touches:** `prompts.py` generator only —
   plausibly a contained brief.
3. **A distinct "absorption/fidelity" sub-dimension**, separate from Sentiment.
   The critical survey's own contribution is a visibility vector separating
   discoverability, citation, absorption, and economic outcome. Sentiment
   currently asks *how positively* an engine portrays the brand;
   absorption/fidelity would ask whether the portrayal is *accurate* — a
   different failure mode (a glowing but factually wrong description scores
   well on Sentiment today and shouldn't). **Touches:** scoring formula
   directly — a formula-version-bump change per rule 5, same treatment as v2/v2.1.
4. **Cross-engine source-overlap as a client-facing insight**, not just an
   internal QA signal. If low source overlap between engines is a documented,
   reproducible finding, showing a client "ChatGPT and Perplexity cite
   completely different domains for you" is a sharper, more defensible insight
   than a single blended score, and the underlying data (per-engine citations)
   already exists in `engine_result_citations` per `scoring-spec.md`'s Citation
   Strength section. **Touches:** report generation (Epic 7) — likely a small,
   contained brief since no new data collection is needed.
5. **A manipulation-risk check on competitor pages** — detecting
   conspicuously engineered text patterns of the kind demonstrated in Kumar &
   Lakkaraju as a "Sources/citations" signal. **Explicitly detection only —
   `ip-safety.md` and this document's own §1 caveat both prohibit ever
   suggesting a client emulate the technique.** This is the one candidate here
   with a live IP-safety question attached: detecting the pattern requires
   reading and characterizing a competitor's live page content, which needs
   sign-off against `ip-safety.md` constraints before it is scoped, not after.
6. **A citation-oriented-rewrite warning inside the Epic 8 fix generator.**
   If citation-heavy rewrites can impair retrieval per the survey, a fix
   recommendation that says "add more citations" without qualification could
   make a client's page worse. This is a small, contained change (a caveat in
   the fix-generation prompt) rather than a new feature — but it should be
   checked against real behavior before being asserted as a rule, not assumed
   from the paper alone.

### 9.4 Video/practitioner sources (informational; lowest evidence tier)

Consistent with the evidence-hierarchy idea in §9.1: practitioner videos and
courses reflect current market narrative and sales language, not measured
results, and should be weighted well below the academic sources above.
Retrieved 2026-09-09: a Profound product review and hands-on walkthrough
(youtube.com/watch?v=FTQsWzpeSSc), a GEO ranking-factors explainer
(youtube.com/watch?v=gReszNnykpg), a Semrush-sponsored GEO tactics video
(youtube.com/watch?v=M-RZMEvak8U), a short current crash-course
(youtube.com/watch?v=9nHvH7MlME4), and an agency-framed GEO/AEO strategy video
(youtube.com/watch?v=57ezDalCHA4). Useful for benchmarking sales and pitch
language (§7); not a substitute for §9.1 when a technical claim is being made.