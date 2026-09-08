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
| 3 | **Citation Strength** | 20% | Number + authority of domains citing the brand |
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

**Sentiment and Citation Strength keep every answered result, deliberately.**
Sentiment asks how an answer PORTRAYS the brand, which is a real signal whether
the buyer named it or not. Scoping it would also move scores UP — measured at
+1 to +26 points awareness-only — so it is a separate decision with separate
evidence, not a consistency fix. Recorded as open.

**No awareness prompts in a set excludes Mention Rate and Share of Voice and
redistributes their weight**, with `NO_AWARENESS_POPULATION`. Scoring the
absence 0 would punish a client for the shape of a prompt set they did not
choose — the same reasoning v1.1 applied to Share of Voice with no competitor
set. Unreachable through the generator (45% awareness quota) and through
`fallback_prompts` (five awareness shapes).

Full numbers and method: `build-log.md`, *"Mention Rate may be measuring the
prompt, not the engine — sized, not fixed"*.

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
| 2026-09-08 | `v2` | **Mention Rate and Share of Voice are scored on awareness prompts only.** The generator names the subject brand in comparison and bottom-funnel questions by instruction, and a mention is a text match, so those prompts registered a mention 255 times in 256 — measuring the question rather than the engine. Epic 0's first stated job is proving a stranger is *invisible*, which is an unprompted property. Weights are unchanged; the population is not. Sentiment and Citation Strength deliberately keep every answered result. **Stored `v1.1` scores are not re-scored** — rule 5 exists so before/after reporting compares like with like, and 14 of them describe what the old definition produced. See `build-log.md` for the measurement and the decision. |
| 2026-08-21 | `v1.1` | **Technical Foundation excluded pending Epic 6**, with reason `NOT_YET_MEASURED` — deliberately distinct from `NO_POPULATION`, so a report can say "not yet checked" rather than "nothing found". Scoring it 0 would depress every score by up to 10 points for a reason unrelated to the client. |
