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
| 1 | **Mention Rate** | 30% | % of tracked prompts where the brand appears at all |
| 2 | **Share of Voice** | 25% | Brand mentions ÷ total mentions (brand + competitors) |
| 3 | **Citation Strength** | 20% | Number + authority of domains citing the brand |
| 4 | **Sentiment** | 15% | Weighted positive/neutral/negative across all mentions |
| 5 | **Technical Foundation** | 10% | Schema presence, structured data, content freshness |

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
| 2026-08-21 | `v1.1` | **Technical Foundation excluded pending Epic 6**, with reason `NOT_YET_MEASURED` — deliberately distinct from `NO_POPULATION`, so a report can say "not yet checked" rather than "nothing found". Scoring it 0 would depress every score by up to 10 points for a reason unrelated to the client. |
