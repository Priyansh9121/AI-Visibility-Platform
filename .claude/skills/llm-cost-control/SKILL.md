---
name: llm-cost-control
description: Reviews or changes how AVP bounds, retries and counts paid AI calls (scan-runner engine adapters in `apps/api/src/avp_api/services/engines.py`, the `CallBound` sites such as the fix generator), when someone asks to cut spend, add a retry, raise a timeout or concurrency, or add a budget. Not for choosing a cheaper model for scan answers (forbidden), and not for pricing or plan limits (founder decisions).
---

# LLM cost control for AVP

Every engine call is paid, and in a scan it is also a **measurement**. Cost control here means: bound each call, count the calls, stop runaway spend. It never means changing what is measured.

## Hard rules

1. **Never reroute, downgrade or fall back to another model or provider for a scan answer.** The engine, model, effort and search settings are part of what a scan measures; `EngineResult.engine_version` exists so a score change can be attributed (`models/engine_result.py`). A cheaper model changes the data and breaks comparability with history. The adapters pin model names and effort on purpose (`ANSWER_MODEL`, `OPENAI_ANSWER_MODEL`, `PERPLEXITY_ANSWER_MODEL`, `GEMINI_ANSWER_MODEL`, and the `*_EFFORT` / `*_THINKING_LEVEL` constants in `engines.py`). A model change is a founder decision and is recorded, not a runtime branch.
2. **Model choice per task is fixed in a named constant, never selected by input size.** AVP's own helper calls (sentiment uses `SENTIMENT_MODEL`, a Haiku model, in `extraction.py`; classification, co-citation, prompt generation and fixes use their own constants) are chosen once by the founder. Do not add a `select_model(text_length, ...)` router.
3. **Facts only.** Cost and usage records store counts, statuses, latencies, engine ids and model names. Never store answer text, page HTML, prompts echoed back, or vendor error bodies (`test_facts_only.py`; `error_code` is "our own diagnostic code, NOT a vendor error body").
4. **No invented prices.** Do not write a $/token table from memory. A price appears only with a vendor URL and the date it was read (as the comments in `engines.py` do, for example "read 2026-09-11"), and is labelled as perishable. Prefer counts: engine calls per scan, statuses, latency.
5. **Budgets are founder decisions.** A dollar cap, a per-plan call limit, a retry count that changes failure rates: propose it with the numbers and stop. Do not pick one.
6. **Only the `anthropic` SDK** is allowed among vendor LLM SDKs (`CLAUDE.md`). OpenAI, Perplexity and Gemini are called over `httpx`. A new vendor or host needs classifying in `apps/web/src/components/legal/providers.test.ts` and naming on `/privacy`; say so, do not add it silently.
7. **No secret, token or email in a log line** (`logs.py`, `test_secrets_stay_out_of_logs.py`). Keys come from `settings.provider_key(...)`; never print them, never put them in a URL (Gemini uses the `x-goog-api-key` header, not `?key=`).

## How AVP bounds a call today (confirm before relying)

Read the code; these were checked 2026-10-01 and will drift.

- **One call = attempts x per-attempt timeout + backoff allowance, enforced by an outer `asyncio.timeout`.** The SDK `timeout` bounds one HTTP attempt and the SDK retries timeouts, so a "60 s timeout" can be minutes of billing. `engines.py` (`DEFAULT_TIMEOUT`, `MAX_RETRIES`, `ENGINE_CALL_CEILING`) and `services/call_bounds.py` (`CallBound`, used by `fix_generator.py`, `prompts.py`, `cocitation.py`, `extraction.py`, `classify.py`) both state the retry count explicitly and never inherit the SDK default. A new paid call declares a `CallBound`; `tests/test_call_bounds.py` and `tests/test_engine_timeout.py` pin the arithmetic.
- **Timeout branch order matters.** `httpx.TimeoutException` and `anthropic.APITimeoutError` subclass the connection/transport error, so map them first or every timeout reads as "provider unreachable". The outer deadline raises the builtin `TimeoutError`, which is not an SDK exception at all: catch it explicitly (`_map_error`, `_map_transport_error`).
- **Per-scan deadline.** `scan_executor.py` has `MAX_SCAN_DURATION`, an outer ceiling on the whole chain, recomputed from the call ceilings in `test_scan_executor.py`, which fails if a ceiling rises past it and also if the ceiling is more than twice the worst case. (`LEASE` is a heartbeat clock, not a sum of ceilings.)
- **Concurrency.** `scan_runner.PROMPT_CONCURRENCY` bounds prompt slots; `engines._ask_gated` adds a per-vendor in-flight semaphore (`max_in_flight`) and a start-rate pacer (`min_start_interval`, used for Perplexity, whose bucket was measured at one start per second). Fan-out is chosen from measurement, and the comments say which numbers are measured and which are predictions.
- **Retries today are narrow and deliberate.** Anthropic calls: the SDK retries once (`max_retries=1`). The httpx engines do not retry; `httpx.AsyncClient` does not. Perplexity alone retries once on a 429 that carries a readable `Retry-After`, only if the wait fits the ceiling (`PERPLEXITY_RETRY_AFTER_MAX`, `_retry_after_seconds`). A 429 elsewhere is recorded as `RATE_LIMITED` / `PROVIDER_RATE_LIMITED`. Perplexity 429s were observed unbilled (Epic 21.1, `engines.py` comments); do not assume that of other vendors. Timeouts can be billed in the worst case (`call_bounds.py` docstring); 5xx billing is unverified. The anthropic SDK (0.125.0) already retries 408/409/429/>=500 once and honours `Retry-After` up to 60 s, so for Anthropic calls change `max_retries`; never add an application retry loop on top.

## Adding or changing a retry (checklist)

A retry spends more of the same measurement; it does not change it. It is still a product-visible change (more answers, fewer failed cells, more spend), so get the founder's yes on the count and cap first.

1. Retry only transient failures: connect errors, 429, 500, 502, 503, 504, and 529 for Anthropic (`classify.py:78` mentions 529). Only connect errors and 429s with `Retry-After` are presumed unbilled; a retried timeout may bill twice and 5xx billing is unverified. Exclude Gemini's per-day-quota 429 (`_map_gemini_error`), which no wait fixes. Never retry 400, 401, 402, 403, 404, `insufficient_quota`, or a refusal.
2. Async only: `await asyncio.sleep(...)`, never `time.sleep` (it blocks the event loop and every other slot in the scan).
3. Backoff is exponential with jitter and a hard cap, for example `min(cap, base * 2**attempt) * random.uniform(0.5, 1.0)` (an unverified heuristic; choose `base`, `cap` and attempts from measured latencies as `engines.py` does). Honour `Retry-After` when present, capped.
4. The retry and its sleeps must fit **inside** the call's ceiling (the outer `asyncio.timeout`). If you add attempts, recompute `ENGINE_CALL_CEILING` / the `CallBound`, then `MAX_SCAN_DURATION`, and let the existing tests prove it.
5. Re-enter the pacer only, as Perplexity does (`engines.py` around the `pacer.admit()` after the sleep). Never re-acquire the vendor semaphore inside `ask`: `_ask_gated` already holds it, and retries waiting for a second permit deadlock until the ceiling.
6. Retries go to the same vendor and the same model. No fallback chain.
7. Test with a fake `httpx` transport or monkeypatched client: a 429 then 200, a 429 forever (stops at the cap), a 401 (no retry), a timeout mapped before connection error. Never call a real paid API in a test.
8. Paid calls run in the API process today (`apps/workers` tasks hold only a health task). If a task ever wraps a paid call, its Celery retry settings (`celery_app.py`) multiply with call retries: compute the worst-case call count first.

## Cost visibility from what is already recorded

- `EngineResult` rows are one per (prompt x engine) per scan, with `status`, `latency_ms`, `error_code`, `engine_version`. **Engine calls per scan is a count of those rows**, split by engine and status. That is a lower bound on spend, not the total: one row can hide two attempts, and a grounded Claude call can make up to `SEARCH_MAX_USES` searches.
- Sentiment calls happen only where the subject is named; prompt generation, co-citation and fix generation are one or a few calls per scan. Count them from the code paths, not from a guess.
- Turn counts into money only with a dated vendor price source, stated as "at vendor price read on DATE", and keep money in `Decimal`, not float. Otherwise report counts.
- To reduce spend without touching measurements: lower the number of scans that run (allowance, dedupe, reuse of a fresh scan), avoid calls whose result is unused, fix wasted calls (timeouts that burn a full ceiling, truncated answers that were billed). Cutting prompts per scan or engines per scan changes the product; it is a founder decision.
- A spend alert or cap is a budget: propose the design (count per agency per period, where it is checked, what the user sees) and stop for the founder's decision. Any new column needs a model plus an Alembic revision (`alembic-migrations` in the library).

## Prompt caching

AVP scan prompts are short, single-turn questions with no shared system prompt, so caching has little to save there, and caching must never change what the model sees. Only consider it for a long fixed system prompt in a helper call (for example `fix_generator.py`) after measuring that the prompt is long enough to matter against the vendor's documented minimum (check the current Anthropic docs), and verify the output schema tests still pass.

## Verification

Run targeted tests, redirecting output to a file and reading the tool's own exit code, then the count gate:

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( cd apps/api && rc=0; uv run pytest -q -p no:cacheprovider tests/test_call_bounds.py tests/test_engine_timeout.py tests/test_scan_executor.py > "$LOG/cost.log" 2>&1 || rc=$?; echo "pytest exit: $rc" )
( cd apps/api && uv run ruff check src tests > "$LOG/ruff.log" 2>&1; echo "ruff exit: $?" )
```

Then read the logs, run the full API suite for the floor in `infra/ci/floors.json` via `python3 infra/ci/assert_count.py api-pytest <log>`, and write a negative control (break the retry cap or the ceiling, watch the new test fail, restore byte-for-byte with `cp` and `cmp`). Hand the test-runner agent the full suites.

Related: `production-audit`, `fact-check`, `avp-security-review`, `writing-plans` (library); `verification-before-completion`, `fastapi` (installed).
