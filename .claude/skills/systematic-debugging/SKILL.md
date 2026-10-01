---
name: systematic-debugging
description: Use when a bug, failing test or unexpected behaviour in AVP (scan pipeline, Celery workers, crawler and `crawl.RobotsGate`, API, web) needs its root cause found before any fix is proposed. Not for planning features or designing new work.
---

# Systematic Debugging

**Core principle:** find the root cause before attempting a fix. A symptom fix is a failure.

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If Phase 1 is not complete, you may not propose a fix. Use this especially under time pressure, when a fix "looks obvious", or when a previous fix did not work.

## AVP ground rules for every diagnostic
- Diagnostics never print env values, tokens, passwords or email addresses, and never read `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `dump.rdb` or `.devdata/`. Check a variable by presence only, with a form that expands to `x` and never to the value (safe under xtrace and nounset):
  `[ -n "${VAR:+x}" ] && echo "VAR: set" || echo "VAR: unset"`.
  Never use `echo $VAR`, `env | grep`, `${VAR:-default}` inside an echo, `set -x` / `bash -x`, `printenv`, bare `env`, `export -p`, `ps e`, `/proc/*/environ`, `docker inspect`, `print(os.environ)`, `print(settings)` or `.model_dump()`, or pytest `-l` / `--showlocals` (they dump locals that may hold secrets).
- Set `LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"` before any command below that uses `$LOG`.
- Evidence comes from local `avp_dev` / `avp_test` only. Render, Supabase and Stripe state is the founder's to read in their dashboards; never put a database URL on a command line.
- Never pipe test, lint or typecheck output. Redirect to a file outside the repo, read the tool's own exit code, then read the file:
  `cd apps/api && uv run pytest -q -p no:cacheprovider tests/test_x.py > "$LOG" 2>&1; echo "rc=$?"` then read `$LOG`.
  Web: `corepack pnpm --filter @avp/web exec vitest run src/path/file.test.ts > "$LOG" 2>&1; echo "rc=$?"`.
- API tests need Postgres `avp_test` on 127.0.0.1:55433 and Redis db 15 (defaults in `apps/api/tests/conftest.py`). The dev DB `avp_dev` is also on 55433, not the `.env` port 5432. If they are down, say so; do not start or reconfigure them.
- Diagnostic logging must not add a secret, token or email to a log line (`apps/api/src/avp_api/logs.py` redacts; `tests/test_secrets_stay_out_of_logs.py` checks). Log ids, counts and names, not payloads. Facts only: never store or log third-party raw text (AI answers, page HTML, competitor copy).
- Some tests grep source on purpose (`apps/web/src/components/legal/providers.test.ts`, `packages/design-system/src/tokens/reportIsolation.test.ts`, `apps/web/src/app/referrerPolicy.test.ts`, `apps/api/tests/test_facts_only.py`, `apps/api/tests/test_secrets_stay_out_of_logs.py`). When one fails, the guarded code is usually what is wrong. Never weaken or delete the guard to get green.
- If the bug or its fix touches scoring: a formula change bumps `FORMULA_VERSION` and never re-scores history (`docs/scoring-spec.md`, rule 5).
- Product decisions (a price, a limit, retention, user-visible behaviour) are the founder's: stop and ask.

## The Four Phases
Complete each phase before the next.

### Phase 1: Root cause investigation
1. **Read the error completely.** Full stack trace, file paths, line numbers, status codes. Do not skip warnings.
2. **Reproduce consistently.** Exact steps, every time? If not reproducible, gather more data; do not guess.
3. **Check recent changes.** `git diff`, `git log`, new dependencies, config, migrations, environment differences (test DB vs `avp_dev` vs Render).
4. **Gather evidence at each component boundary** when the path crosses several (web -> API -> Celery task -> Postgres/Redis -> crawler/provider). Before proposing a fix, run once and record what enters and leaves each boundary, and whether config propagated, to see where it breaks. Presence-only example:
   ```bash
   # Layer 1: API. It reads apps/api/.env through pydantic-settings, so ask the settings object
   # whether the field was explicitly set (never print values; an exception prints only its type,
   # because a pydantic ValidationError can echo input values)
   cd apps/api && uv run python -c "
   try:
       from avp_api.config import get_settings as g
       print('redis_url explicitly set:', 'redis_url' in g().model_fields_set)
   except Exception as e:
       print('settings load failed:', type(e).__name__)"
   # Layer 2: worker. It reads os.environ only (apps/workers/src/avp_workers/config.py), so use the
   # presence form in the worker's own environment
   [ -n "${REDIS_URL:+x}" ] && echo "REDIS_URL: set" || echo "REDIS_URL: unset"
   # (a missing REDIS_URL silently falls back to a default; presence on Render is the founder's dashboard check)
   # Layer 3: what state did the row/queue reach? query ids, status, counts, timestamps only
   ```
   This shows which layer fails (API set, worker unset, and so on). Then investigate that component.
5. **Trace the data flow backward** when the error is deep in the stack: see `root-cause-tracing.md`. Fix at the source, not the symptom.

### Phase 2: Pattern analysis
1. Find similar working code in the repo (another Celery task, another router, another crawl path).
2. Read any reference implementation completely, not skimmed.
3. List every difference between working and broken, however small.
4. Note dependencies: settings, config, ordering, shared state, the RobotsGate/crawl policy, provider rate limits.

### Phase 3: Hypothesis and minimal test
1. State one hypothesis in writing: "I think X is the root cause because Y."
2. Make the smallest possible change to test it, one variable at a time.
3. Worked? Go to Phase 4. Did not? Form a new hypothesis; do not stack more changes on top.
4. If you do not understand something, say so and research or ask.

### Phase 4: Implementation
1. **Failing test first.** Write the simplest automated reproduction and watch it fail for the right reason. Use the library `test-driven-development` skill. This is mandatory before the fix.
2. **One fix** for the identified root cause. No "while I'm here" changes, no bundled refactor.
3. **Verify.** The new test passes, the neighbouring tests pass, the original symptom is gone. Run the negative control: revert the fix, confirm the new test fails, restore the file byte-for-byte. Do it by copying the file to `$LOG`'s directory, undoing the fix by editing, running the test, copying the file back and confirming with `cmp`; never `git stash`, `git stash pop` or `git checkout -- <file>`. Use the installed `verification-before-completion` skill before claiming success. Run the suite counts against `infra/ci/floors.json` with `python3 infra/ci/assert_count.py <key> <log>`; a new test means bumping the floor in the same change.
4. **If the fix does not work: STOP and count.** Fewer than 3 fixes tried: return to Phase 1 with the new information. **3 or more failed fixes: stop and question the architecture** (step 5). Do not attempt fix #4 without discussing it with the founder.
5. **Three failed fixes signal an architecture problem** when each fix reveals new shared state or coupling elsewhere, fixes need large refactors, or each fix creates new symptoms. Ask whether the pattern is sound and whether you are continuing out of inertia. Take it to the founder before more fixes. This is not a failed hypothesis; it is a wrong structure.

## Red flags: stop, return to Phase 1
- "Quick fix now, investigate later" / "just try changing X"
- Several changes at once, then run tests
- "Skip the test, I'll check by hand"
- "It's probably X" / "I don't fully understand but this might work"
- Proposing solutions before tracing the data flow
- "One more attempt" after 2 failures; each fix exposes a new problem elsewhere
- Deleting or loosening a guard test to make it pass

## Common rationalizations
| Excuse | Reality |
|--------|---------|
| "Simple issue, no process" | Simple issues have root causes too; the process is fast for them. |
| "Emergency, no time" | Guess-and-check thrashing is slower. |
| "Try this first, then investigate" | The first fix sets the pattern. |
| "I'll test after the fix works" | Untested fixes do not stick. |
| "Several fixes at once saves time" | You cannot isolate what worked. |
| "I see the problem" | Seeing a symptom is not understanding the cause. |
| "One more attempt" (after 2) | 3 failures = question the architecture. |

## When there is truly no root cause
If investigation shows the cause is environmental, timing-dependent or external (a provider outage, a slow site), you have completed the process: document what you checked, add appropriate handling (retry, timeout, clear error), and add logging (ids and counts only) for next time. Most "no root cause" cases are incomplete investigation (unverified heuristic).

## Supporting techniques
- `root-cause-tracing.md`: trace backward through the call chain, and a bisection procedure to find which test pollutes shared state.
- `defense-in-depth.md`: after the root cause is fixed, validate at every layer the bad data passed through.
- `condition-based-waiting.md`: replace arbitrary sleeps in flaky tests with waiting on the real condition (pytest/asyncio, Celery, vitest).
