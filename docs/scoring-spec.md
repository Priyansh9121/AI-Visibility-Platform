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
| 3 | **Citation Strength** | 20% | Number + authority of domains citing the brand — **excluded since v2.1** (`NO_AUTHORITY_DATA`), weight redistributed, until an authority source exists |
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

**Sentiment and Citation Strength keep every answered result. Decided, not
pending.** Sentiment asks how an answer PORTRAYS the brand, which is a real
signal whether the buyer named it or not — unlike Mention Rate and Share of
Voice, which answer *"was this discovered"*, a claim a brand-named question
cannot support. The two dimensions were scoped because the tautology made them
measure the question instead of the engine; sentiment has no such defect,
because the thing it measures does not depend on who raised the subject.

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

**No awareness prompts in a set excludes Mention Rate and Share of Voice and
redistributes their weight**, with `NO_AWARENESS_POPULATION`. Scoring the
absence 0 would punish a client for the shape of a prompt set they did not
choose — the same reasoning v1.1 applied to Share of Voice with no competitor
set. Unreachable through the generator (45% awareness quota) and through
`fallback_prompts` (five awareness shapes).

Full numbers and method: `build-log.md`, *"Mention Rate may be measuring the
prompt, not the engine — sized, not fixed"*.

### Citation Strength — excluded until there is an authority source (v2.1)

**Citation Strength is excluded from the composite with reason
`NO_AUTHORITY_DATA`, and its 20 points redistributed across the other
dimensions.** The table above asks for "number **and** authority" of citing
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

**This is an interim, not a redesign.** What Citation Strength should measure
— citation share against the best-cited *single* brand, the rate of answers
that cite the subject at all, or something an authority feed makes possible —
is its own decision with its own brief. Until then a stand-in that cannot be
earned is left out, the same treatment v1.1 gave Technical Foundation before
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
| Citations present but zero authority data | Fall back to raw domain count, normalised against the competitor max in the same scan. Record the degradation on the score row. |
| Sub-score > 100 from an input bug | Clamp to 100 **and** raise — a silent clamp hides pipeline defects. |

## Tuning changelog

| Date | Version | Change |
|---|---|---|
| 2026-08-20 | `v1` | Initial weights from product-spec.md §6. Not yet data-tuned. |
| 2026-08-21 | `v1.1` | **No-competitor Share of Voice now excluded, not scored 100.** v1 awarded a full 25 points when competitor detection returned nothing, which hands a quarter of the composite to a *detection failure* — a technically-computable but meaningless number, and exactly the outcome this project refuses everywhere else (null classification, null score, `INSUFFICIENT_DATA`). It is now excluded and redistributed, the same treatment v1 already prescribed for sentiment with no population. Weights themselves are unchanged. See `build-log.md` Epic 5.2. |
| 2026-09-08 | `v2.1` | **Citation Strength excluded under `NO_AUTHORITY_DATA`, weight redistributed.** The stand-in formula — the subject's own domain against every distinct third-party domain cited — could not exceed `1/N` for any brand; across all 33 stored scores the maximum was 3.70, every rival showed the identical figure, and the pitch beat promised about 20 of its recoverable points from it. Interim only: what the dimension should measure is a separate decision. Stored `v2` rows are not re-scored. Rivals' citation strength is `null` while the subject's is excluded, so a column means one thing. See the section above and `build-log.md`, second pilot dry run. |
| 2026-09-08 | `v2` | **Mention Rate and Share of Voice are scored on awareness prompts only.** The generator names the subject brand in comparison and bottom-funnel questions by instruction, and a mention is a text match, so those prompts registered a mention 255 times in 256 — measuring the question rather than the engine. Epic 0's first stated job is proving a stranger is *invisible*, which is an unprompted property. Weights are unchanged; the population is not. Sentiment and Citation Strength deliberately keep every answered result. **Stored `v1.1` scores are not re-scored** — rule 5 exists so before/after reporting compares like with like, and 14 of them describe what the old definition produced. See `build-log.md` for the measurement and the decision. |
| 2026-08-21 | `v1.1` | **Technical Foundation excluded pending Epic 6**, with reason `NOT_YET_MEASURED` — deliberately distinct from `NO_POPULATION`, so a report can say "not yet checked" rather than "nothing found". Scoring it 0 would depress every score by up to 10 points for a reason unrelated to the client. |
