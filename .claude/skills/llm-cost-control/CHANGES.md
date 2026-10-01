# Changes from cost-aware-llm-pipeline (source: ECC-main/skills/cost-aware-llm-pipeline/SKILL.md)

- Removed "Model Routing by Task Complexity" and `select_model` (SKILL.md:21-43, 169) -> routing scan queries to a cheaper model changes what is measured and breaks comparability. Replaced by hard rules 1-2: never reroute; model per task is a fixed named constant.
- Removed the Pricing Reference table (SKILL.md:155-165, e.g. :161 "Sonnet 5 | $2.00 | $10.00") -> unsourced, undated, perishable prices. Replaced by rule 4: prices only with a vendor URL and read-date; prefer counts.
- Removed `CostRecord`/`CostTracker` with `cost_usd: float` (SKILL.md:49-78) -> money as float and prices baked into code. Replaced by counting `EngineResult` rows per scan, and `Decimal` if money is ever shown.
- Removed the budget-limit default of 1.00 and `BudgetExceededError` flow (SKILL.md:61, 138-140) -> an invented budget. Replaced by rule 5: budgets are founder decisions.
- Replaced the `anthropic`-only blocking retry (SKILL.md:85-104; `time.sleep(2 ** attempt)` at :102) with an async checklist: `await asyncio.sleep`, jitter, a cap, `Retry-After`, retry inside the call ceiling, same vendor and model, tests with fake transports -> a blocking sleep stalls the event loop for every slot in a scan; retry without a cap or jitter amplifies rate limits. Also adds httpx (OpenAI, Perplexity, Gemini are called over httpx; only the anthropic SDK is allowed).
- Added "How AVP bounds a call today" (engines.py `DEFAULT_TIMEOUT`/`MAX_RETRIES`/`ENGINE_CALL_CEILING`, `call_bounds.CallBound`, `scan_executor.MAX_SCAN_DURATION`/`LEASE`, `PROMPT_CONCURRENCY`, `_ask_gated`, Perplexity `Retry-After` retry) -> the source assumed a stack-agnostic loop; AVP's bounds already exist and are pinned by tests. Each item was confirmed in the repo on 2026-10-01.
- Reworded the retryable-error list to status codes plus the AVP timeout-ordering trap (SKILL.md:80-104, 173) -> the source's SDK exception tuple does not cover httpx engines and misses that the outer `TimeoutError` is not an `anthropic.APIError`.
- Reworked prompt caching (SKILL.md:106-127, 172) into a measured, optional note limited to long fixed helper prompts -> scan prompts are short single-turn questions; caching must not alter model input. The 1024-token figure was dropped as unsourced here; it points to current vendor docs.
- Added facts-only and no-secret/log rules (rules 3, 7) -> CLAUDE.md; source stored nothing of the kind but had no guard.
- Dropped generic "When to Activate"/"When to Use"/Anti-Patterns lists (SKILL.md:12-17, 175-188) -> filler; "Start with the cheapest model" (:169) deleted as it contradicts rule 1.
- Dropped `metadata.origin: ECC` and rewrote the description (SKILL.md:3) to a third-person, AVP-specific trigger with explicit exclusions.
- Added a verification block that redirects output to files, reads exit codes, never pipes, and uses `assert_count.py` instead of any coverage target; negative control restored with `cp`/`cmp`.
- No install steps, hooks, telemetry, `!`-backtick injection, or `allowed-tools` existed in the source; none added.

## Security-review fixes (2026-10-01)
- M1: retry checklist item 5 now says re-enter the pacer only, never the vendor semaphore (held by `_ask_gated`) -> retries would deadlock to the ceiling.
- M2: billing claims scoped: timeouts/5xx may be billed, only connect errors and 429 with Retry-After presumed unbilled; Anthropic SDK already retries once, so change `max_retries`, no app loop; EngineResult count is labelled a lower bound.
- L3: dropped `LEASE` from the recomputed sum; only `MAX_SCAN_DURATION` is (and its 2x-worst-case check).
- L4: "unbilled" scoped to Perplexity 429s; SDK honours Retry-After up to 60 s noted.
- L5: 529 cited to `classify.py`; Gemini per-day-quota 429 excluded from retries.
- Nits: removed Celery workers from the description and reworded item 8 (no paid calls in workers tasks); fixed the TimeoutError-vs-connection-error sentence.
- AVP nits for the founder, not fixed here: stale comments at `call_bounds.py:20-24` and `extraction.py:78-82`; `engines.py:147-149` comment about SDK backoff is inaccurate (SDK honours Retry-After up to 60 s).
- Re-review: 5xx billing marked unverified (call_bounds docstring supports timeouts only); 529 cited to `classify.py:78` (line verified: the word 529 is on that line).
