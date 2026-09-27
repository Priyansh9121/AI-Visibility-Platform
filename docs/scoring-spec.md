# AI Visibility Score — Specification

**Source of truth:** `docs/product-spec.md` §6. This file elaborates it and is
the working reference for the scoring engine (Epic 5).

**Status:** formula recorded (from §6). Implementation lands in Epic 5, in
`apps/api` (Python). Epic 0 consumes the *shape* of this formula for the
Luminance Ledger visualisation.

---

## Composite formula

Each sub-score is normalised to 0–100, then combined as a weighted sum:

```
score = Σ (weight_i × subscore_i) / 100
```

| # | Sub-score | Weight | Inputs |
|---|---|---|---|
| 1 | **Mention Rate** | 30% | % of **awareness** prompts where the brand appears (v2) |
| 2 | **Share of Voice** | 25% | Brand mentions ÷ total mentions, over **awareness** prompts (v2) |
| 3 | **Citation Strength** | 20% | Authority of the domains cited in **awareness** answers that name the brand (v4), against the best-cited brand in the same scan — measured since v3 from a two-tier authority input; excluded (`NO_AWARENESS_POPULATION`) when the set has no awareness prompts, (`NO_AUTHORITY_DATA`) when the awareness answers cite nothing tiered, and (`NO_COMPETITOR_SET`) when there is no rival to normalise against |
| 4 | **Sentiment** | 15% | Weighted positive/neutral/negative across all mentions |
| 5 | **Technical Foundation** | 10% | Schema presence, structured data, content freshness |

### The scoring population — v2

**Mention Rate and Share of Voice count `awareness` prompts only. The other
three dimensions count every answered result.**

A prompt set is generated across three intents, and `prompts.py`'s generator is
instructed to name the subject brand in `comparison` and `bottom_funnel`
questions — 55% of a set by quota. Its own system prompt gives the reason: *"A
question that names the brand can only confirm the brand exists; it cannot
reveal whether the brand gets discovered."* A mention is a text match, so those
questions register a mention almost regardless of what the engine knows.
Measured across every stored scan: **255 of 256 answered rows on a brand-named
prompt registered a mention, against 386 of 497 where the prompt did not name
it.** An engine answering *"I don't have any knowledge of a product called
Zorblex Inbox"* was recorded as a mention at position 1.

The two dimensions are scoped because of what they are FOR. `product-spec.md`'s
Epic 0 states the first of the product's two jobs as *"prospecting — prove to a
stranger they are invisible in AI answers"*, and invisibility is an unprompted
property that a brand-named question cannot evidence.

Share of Voice is scoped for a reason of its own rather than by analogy: the
tautology inflates its NUMERATOR specifically, because a comparison prompt
names the subject and usually one rival, so the subject takes a guaranteed hit
while the remaining competitors appear only if the engine volunteers them. That
pulls the ratio toward `1/(1 + named)` whatever the real standing. The
objection that a buyer weighing named options is itself a competitive signal is
real but does not apply to this implementation, which counts PRESENCE rather
than airtime; an airtime-weighted Share of Voice would deserve it reconsidered.

**Sentiment keeps every answered result. Decided, not pending.** Sentiment
asks how an answer PORTRAYS the brand, which is a real signal whether the
buyer named it or not — unlike Mention Rate and Share of Voice, which answer
*"was this discovered"*, a claim a brand-named question cannot support. The
two dimensions were scoped because the tautology made them measure the
question instead of the engine; sentiment has no such defect, because the
thing it measures does not depend on who raised the subject.

**Citation Strength keeps every answered result — v2 through v3; awareness
only since v4.** v2 closed this question for both dimensions together and
said reopening it needed a reason of its own. This is that reason, and it is
Citation Strength's alone. Sentiment's argument does not carry over: since v3
Citation Strength is attribution by co-occurrence — a brand is credited the
tiered sources cited in answers that name it — and a brand-named question
MANUFACTURES the co-occurrence. 55% of a set names the subject by
instruction, so under v3 the subject accumulated nearly the whole scan's
tiered sources whatever its standing. Measured on the 23 stored scans v3
scored: **14 read 100; helpwise.io read 90.63 with an awareness Mention Rate
of 0**; on awareness prompts alone five clients fell to 0 (`build-log.md`,
2026-09-27, part two). Portrayal is a property of the answer; co-occurrence
with a source is a property of the question that produced the answer. So the
two dimensions differ on purpose: sentiment measures what an engine says
about a brand it was made to discuss, which is real; Citation Strength on a
brand-named prompt measures what a search returned for the brand's own name,
which is not authority. Rivals are scored on the same population, so the
comparison column keeps one meaning.

The measurement backs that up and is worth stating, because it points the other
way from the fix: awareness-only sentiment is **higher** on every scan with a
population, by +1.03 to +25.86 points. Engines recommend a brand they surface
unprompted and hedge about one they are made to discuss. Scoping sentiment
would therefore RAISE scores rather than correct them, which is the signature of
a change that flatters rather than fixes.

That is a finding in its own right — how an engine talks about a brand depends
on whether it chose to bring it up — and it is the subject of its own question,
not of this one. **Closed for v2.** Reopening it needs a reason of its own, not
consistency with a fix aimed at a different defect.

**No awareness prompts in a set excludes Mention Rate, Share of Voice and
(since v4) Citation Strength and redistributes their weight**, with
`NO_AWARENESS_POPULATION`. Scoring the
absence 0 would punish a client for the shape of a prompt set they did not
choose — the same reasoning v1.1 applied to Share of Voice with no competitor
set. Unreachable through the generator (45% awareness quota) and through
`fallback_prompts` (five awareness shapes).

Full numbers and method: `build-log.md`, *"Mention Rate may be measuring the
prompt, not the engine — sized, not fixed"*.

### Citation Strength — measured from a two-tier authority input (v3)

**Since v3 (2026-09-27) Citation Strength is scored.** The input v2.1 was
waiting for is `services/domain_authority.py`, and it comes in two tiers
that are honest about their limits in different ways:

- **Tier A — curated, structural.** `.gov`, `.edu`, `.mil` and their country
  forms (`.gov.uk`, `.ac.uk`, `.gov.au`, `.gc.ca`, `.go.jp`, `.gouv.fr`, …)
  by suffix; Wikipedia and Wikidata by name; the two major wire services, AP
  and Reuters, because most other outlets draw from them — a structural fact,
  not a trust ranking; and the cross-industry trust and review infrastructure
  that exists to aggregate signals about businesses: Better Business Bureau,
  Trustpilot, G2, Capterra. Deliberately **not** on the list: any general
  "credible news" set, Reddit and forums, and industry-specific authorities
  (WebMD, IEEE, a bar association) — a generic list cannot do those well,
  and Tier B is what is meant to catch them.
- **Tier B — platform frequency.** A domain qualifies when scans have cited
  it across at least **3 distinct client industries** and at least **5
  distinct clients**, with `Client.industry` as the grouping key. Both
  thresholds are **first guesses, not measured ones** — 27 clients across 12
  labels is not a base to derive them from — and are to be revisited once
  real scan volume exists. It is a live aggregate, not a table: 15 ms on
  5,787 citation rows, measured before deciding.
- **Everything else is unclassified**: no tier, zero authority weight, the
  same as its absence before v3.

**The limitation, stated plainly and in the report copy, not only here.**
`Client.industry`'s classification confidence score was found uncalibrated
(build-log Epic 2.6/2.8, Finding 2 — five real sites scored 0.97, 0.97,
0.97, 0.97, 0.96, effectively one value) and remains unfixed, so there is no
reliable way to screen a mis-classified client's industry out of the Tier B
count. The label itself is more workable — the earlier over-generalisation
(Finding 1) was addressed with prompt tuning — but industry-spread is a
coarse proxy that cannot currently filter out a bad classification. On the
day v3 shipped the rule admitted 70 domains, a good part of them help-desk
vendors that the fine-grained labels count as seven industries.

**The formula.** A brand's *citing domains* are the distinct third-party
sources cited in **awareness** results (v4; every answered result under v3)
that name it, less its own domain (a brand is not a third party to itself). Each carries its tier's value —
`AUTHORITY_VALUES`: **curated 100, platform 50, unclassified 0**, ratios
chosen to state the ordering and no more precision than the proxy has, the
way neutral sentiment sits at 50. The subject's sum is normalised against
the largest sum in the scan, its own or a rival's:

```
citation_strength = 100 × Σ tier(subject's citing domains)
                          / max over brands in the scan of Σ tier(citing domains)
```

Attribution is by co-occurrence — an answer naming two brands credits its
sources to both — which is the limit of what facts-only storage supports.
Every rival's comparison figure is its own sum against the same denominator,
so the best-cited brand in the table reads 100.

**Three exclusions, distinct facts, in order of precedence (v4).**
`NO_AWARENESS_POPULATION` when the set has no awareness prompts — a fact
about the population, and the code the other discovery dimensions use.
`NO_AUTHORITY_DATA` when the awareness answers cite no domain with a tier —
a fact about the input; there is nothing to measure with, and such a scan
behaves exactly as under v2.1. Brand-named answers citing tiered sources do
not rescue it. `NO_COMPETITOR_SET` when tiered domains were cited but no
competitor set exists — a fact about the comparison: with no rival, "the
best in the scan" is the subject itself and any classifiable citation would
score 100 — the v1 Share of Voice defect, given the v1.1 answer. Population,
then input, then comparison: each later question presumes the earlier had
an answer.

**The tiers are inputs and are in the digest.** Tier B moves as scans
accumulate, so the same results re-scored later can score differently under
the same formula; `inputs_digest` records the tier of every cited domain
that has one, so the change is attributable (rule 1).

**Stored `v2.1`, `v2` and `v1.1` rows are not re-scored** (rule 5).

### Citation Strength — excluded until there was an authority source (v2.1)

**From 2026-09-08 to v3, Citation Strength was excluded from the composite
with reason `NO_AUTHORITY_DATA`, and its 20 points redistributed across the
other dimensions.** The table above asks for "number **and** authority" of citing
domains, and this system has no authority source. The stand-in that filled the
dimension since Epic 5 divided the distinct domains citing the subject — in
practice the subject's own domain, 0 or 1 — by every distinct third-party
domain the engines cited in the scan. The most any brand can score under that
arithmetic is `1/N`, and on a real 24-prompt scan `N` was 152.

Measured across every stored score before the change: **maximum 3.70, median
0.88, mean 1.1** (33 rows, v1.1 and v2). Every rival on a comparison table
showed the identical figure, because each owns one domain. The pitch beat
summed the dimension's gap into the points its fixes could recover — about 20
of the 63 it promised on that scan — from a dimension nobody could earn.

**What a citation IS — v2.2 (2026-09-12).** A `Citation` row is a source the
answer cited: the answer's own inline citations, on every engine, with the
results a search merely retrieved standing in only when the answer cited
nothing inline. Rows written by the grounded Claude engine before that date
hold the retrieved set instead, and say so through their `engine_version`
(no `/direct` suffix). They are not rewritten; see the changelog.

**This was an interim, not a redesign.** What Citation Strength should measure
— citation share against the best-cited *single* brand, the rate of answers
that cite the subject at all, or something an authority feed makes possible —
was its own decision with its own brief, and v3 above is that decision. Until
then a stand-in that could not be earned was left out, the same treatment v1.1 gave Technical Foundation before
Epic 6 could measure it. The citations themselves are unaffected: the proof
beat's citation tables and the unclaimed-domain fix read the persisted
`engine_result_citations` rows directly and never depended on this score.

The reason code is `NO_AUTHORITY_DATA` rather than `NOT_YET_MEASURED` because
the codes name causes: `NOT_YET_MEASURED` says a capability is on its way; this
says an input does not exist. The same code was previously a degradation flag
on every score; a score computed under v2.1 does not carry the flag, because a
dimension that is left out is not "rougher", and stored v2 rows keep it with
copy that describes what that formula actually did.

Effect on the composite, from the stored v2 sub-scores: pirsch.io 27.28 →
33.93, zammad.com 41.19 → 51.08, plausible.io 57.62 → 71.83. **Stored v2 rows
are not re-scored** (rule 5); a scan re-scored under v2.1 gains a second row
and the report says so through `previousFormulaVersions`.

Weights sum to 100. Per-industry weight tuning is deferred until real data
exists (§6); until then all industries use the table above.

---

## Why the weights are also a layout

The Luminance Ledger (Epic 0 hero component) draws each sub-score as a segment
whose **height is its weight** and whose **lit fraction is its normalised value**.
This yields an identity worth stating explicitly, because the component's
correctness depends on it:

```
lit height of segment i = H × (weight_i/100) × (subscore_i/100)
Σ lit heights           = H × Σ(weight_i × subscore_i)/10000
                        = H × score/100
```

**The total lit height of the column is exactly the composite score.** The
visualisation is not an illustration of the number — it is the number, drawn.

It follows that the largest *unlit* area is the largest available point gain:

```
gap_i = weight_i × (100 − subscore_i) / 100        // points left on the table
biggest gap = argmax_i gap_i
```

This is the correct "fix this first" signal — it ranks by recoverable points, so
a weak-but-lightly-weighted dimension never outranks a mediocre heavy one. The
report's "biggest gap" narrative beat is derived from this, not authored
separately.

---

## Determinism requirements

Epic 5 acceptance: *"score recalculates correctly and deterministically from a
given EngineResult set; unit tests cover edge cases."* Non-negotiable rules:

1. **No wall-clock, no RNG, no `set`/`dict` iteration-order dependence.** Sort
   every collection by an explicit, total key before aggregating.
2. **Fixed rounding, applied once.** Sub-scores are computed at full precision,
   rounded half-up to 2dp for storage; the composite is computed from the
   *stored* sub-scores so a displayed breakdown always re-sums to the displayed
   total. Round the composite half-up to an integer for display only.
3. **Use `Decimal`, not float,** for the weighted sum. Binary floats make
   `0.30 × 33.33` platform-fragile at the rounding boundary. This is enforced by
   `test_weighted_sum_is_decimal_exact_at_rounding_boundaries` (three known
   cases where float lands a hundredth low) and by a source-level guard. Both
   were added *after* a deliberately injected float passed all 42 original
   tests — see `build-log.md` Epic 5.1.
4. **The LLM is never in the scoring path.** Sentiment classification is an LLM
   call, but it happens upstream and its output (`positive|neutral|negative`) is
   persisted on the EngineResult. Scoring reads the stored label. Re-scoring an
   existing EngineResult set never re-invokes a model.
5. **Version the formula.** Every `Score` row stores `formula_version`. Changing
   weights creates a new version rather than silently altering history —
   otherwise before/after ROI reporting (Epic 11) is meaningless.

## Defined edge cases

These are the unit-test targets named by the Epic 5 acceptance criterion.

| Case | Required behaviour |
|---|---|
| Zero mentions anywhere | Mention Rate = 0, Share of Voice = 0. Sentiment has no population — it is **excluded and its weight redistributed proportionally across the remaining sub-scores**, not scored as 0. A brand with no mentions has no sentiment; scoring it 0/100 would double-punish the same absence. |
| All competitors tied with brand | Share of Voice = 100 / (1 + n_competitors), exactly. |
| No competitors detected | **v1.1:** Share of Voice is **excluded and its weight redistributed**, with `NO_COMPETITOR_SET` on `degradation_flags`. (v1 said "Share of Voice = 100, flag the scan" — see the changelog for why that changed.) |
| Zero prompts in set | Score is **not** 0 — it is `null` with reason `INSUFFICIENT_DATA`. An unrunnable scan must never render as a bad score. |
| Citations present but no cited domain has an authority tier | **v3:** excluded under `NO_AUTHORITY_DATA` and the weight redistributed — there is no input to measure with. (v1–v2 fell back to a raw domain count that in practice was the subject's own domain over every domain cited, and could not exceed 1/N; v2.1 excluded it.) |
| Tiered domains cited, no competitor set | **v3:** excluded under `NO_COMPETITOR_SET` — normalised against nobody, the subject would score 100 for any classifiable citation, which is the v1 Share of Voice defect. |
| Sub-score > 100 from an input bug | Clamp to 100 **and** raise — a silent clamp hides pipeline defects. |

## Tuning changelog

| Date | Version | Change |
|---|---|---|
| 2026-09-27 | `v4` | **Citation Strength is scored on awareness prompts only, and market participants are excluded from Tier B.** Two changes, one bump. (1) The population moves to `awareness_only`, the one Mention Rate and Share of Voice have used since v2, for a reason of this dimension's own: attribution is by co-occurrence, and a brand-named question manufactures it — under v3, 14 of 23 stored scans read 100 and helpwise.io read 90.63 with an awareness Mention Rate of 0. Rivals are scored on the same population. Sentiment stays on every answered result, and the population section says why the two now differ. `NO_AWARENESS_POPULATION` joins the exclusions, ahead of `NO_AUTHORITY_DATA` and `NO_COMPETITOR_SET`. (2) `load_platform_domains` refuses any domain that is a non-deleted `Client.domain` or a `Competitor.domain` anywhere on the platform: an engine cites a market participant because it is an option, not a reference. Structural, like Tier A; Tier A is unaffected. The admitted list went 70 → 57 on the day. **Major, not minor**, by the precedent: v2's population change was major and v2.1's exclusion minor, and this is a population change first — it moves every composite that carries the dimension, by up to 20 points on the stored scans. The tier change rides on the digest through `domain_tiers`, which a test confirms. **Stored `v3` rows are not re-scored.** See `build-log.md`, 2026-09-27, sections 1 and 2. |
| 2026-09-27 | `v3` | **Citation Strength is measured again, from a two-tier citing-domain authority input.** `services/domain_authority.py`: Tier A is a curated structural list (gov/edu/mil suffixes and country forms, Wikipedia, Wikidata, AP, Reuters, BBB, Trustpilot, G2, Capterra); Tier B is a live aggregate over stored citations — cited across ≥ 3 `Client.industry` values and ≥ 5 clients, both thresholds first guesses. Values 100 / 50 / 0. A brand's citing domains are the third-party sources cited in answers that name it, less its own; the subject's sum is normalised against the best in the scan, as this spec always said and the v1–v2 stand-in never did. `NO_AUTHORITY_DATA` now fires only when no cited domain has a tier; `NO_COMPETITOR_SET` when there is no rival to normalise against. The tiers are in the digest. **Honest limit, in the report copy:** Tier B counts through an industry label whose confidence is uncalibrated (Finding 2, open), so it cannot screen out a mis-classified client. Major bump by v2's precedent: a dimension re-entering the weighting moves every composite with a classifiable citation. **Stored `v2.1` rows are not re-scored.** See the section above and `build-log.md`, 2026-09-27. |
| 2026-08-20 | `v1` | Initial weights from product-spec.md §6. Not yet data-tuned. |
| 2026-08-21 | `v1.1` | **No-competitor Share of Voice now excluded, not scored 100.** v1 awarded a full 25 points when competitor detection returned nothing, which hands a quarter of the composite to a *detection failure* — a technically-computable but meaningless number, and exactly the outcome this project refuses everywhere else (null classification, null score, `INSUFFICIENT_DATA`). It is now excluded and redistributed, the same treatment v1 already prescribed for sentiment with no population. Weights themselves are unchanged. See `build-log.md` Epic 5.2. |
| 2026-09-12 | `v2.2` | **A citation is a source the answer cited, on every engine.** The grounded Claude engine now calls search directly (`allowed_callers: ["direct"]`), which is the only path on which the answer's own `web_search_result_location` citations are returned, and records those; the results the search retrieved are the fallback only when the answer cited nothing inline — the rule `PerplexityAdapter` already applied. Until this date `claude_search` rows held **every result the search retrieved**, about twice the cited set (measured: 16.2 retrieved against 7.5 cited per answer), while Perplexity rows held the cited set — two definitions in one table, under copy that said "cited" throughout. **The 5,277 stored `claude_search` citation rows across 23 scans are the retrieved kind and are not rewritten**; a row's `engine_version` says which it is (`…/web_search_20260209/direct` is cited; without the suffix, retrieved). No stored score moves — Citation Strength has been excluded since v2.1. Measured on the way (build log, "what a citation is"): direct calls were 7.7% cheaper and had half the tail latency; the engine is not a default since the same day. See `build-log.md`. |
| 2026-09-08 | `v2.1` | **Citation Strength excluded under `NO_AUTHORITY_DATA`, weight redistributed.** The stand-in formula — the subject's own domain against every distinct third-party domain cited — could not exceed `1/N` for any brand; across all 33 stored scores the maximum was 3.70, every rival showed the identical figure, and the pitch beat promised about 20 of its recoverable points from it. Interim only: what the dimension should measure is a separate decision. Stored `v2` rows are not re-scored. Rivals' citation strength is `null` while the subject's is excluded, so a column means one thing. See the section above and `build-log.md`, second pilot dry run. |
| 2026-09-08 | `v2` | **Mention Rate and Share of Voice are scored on awareness prompts only.** The generator names the subject brand in comparison and bottom-funnel questions by instruction, and a mention is a text match, so those prompts registered a mention 255 times in 256 — measuring the question rather than the engine. Epic 0's first stated job is proving a stranger is *invisible*, which is an unprompted property. Weights are unchanged; the population is not. Sentiment and Citation Strength deliberately keep every answered result. **Stored `v1.1` scores are not re-scored** — rule 5 exists so before/after reporting compares like with like, and 14 of them describe what the old definition produced. See `build-log.md` for the measurement and the decision. |
| 2026-08-21 | `v1.1` | **Technical Foundation excluded pending Epic 6**, with reason `NOT_YET_MEASURED` — deliberately distinct from `NO_POPULATION`, so a report can say "not yet checked" rather than "nothing found". Scoring it 0 would depress every score by up to 10 points for a reason unrelated to the client. |
