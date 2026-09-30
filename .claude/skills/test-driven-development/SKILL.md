---
name: test-driven-development
description: Use when implementing a feature or bug fix in apps/api, apps/workers, apps/web or packages/* where a test can be written first (scoring formula changes, API pytest fixtures, vitest component and logic tests). Not for docs-only or config-only changes.
---

# Test-Driven Development (AVP)

Write the test first. Watch it fail. Write minimal code to pass. Then run the project's full bar.

**Core principle:** if you did not watch the test fail, you do not know it tests the right thing.

## When to use

- New features, bug fixes, behaviour changes and refactors in `apps/api`, `apps/workers`, `apps/web`, `packages/design-system`, `packages/shared-types`.
- Docs-only changes, config-only changes, generated code (for example the OpenAPI export and `packages/shared-types` output) and throwaway prototypes are outside this skill, but you do not grant that exception yourself: get the founder's OK. Config that changes behaviour (retry settings, security headers, rate limits, `Referrer-Policy`) is not "config-only"; it gets a test.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Wrote code before the test? Stop and ask the founder before discarding written code. Do not quietly keep it as "reference" and do not quietly delete it: say what exists, and let the founder choose between writing the test now against that code (then run a negative control, see "Negative control" below) or restarting from the test.

## Red-Green-Refactor

1. **RED**: write one minimal test for one behaviour, with a clear name and real code (mock only what is slow or external).
2. **Verify RED**: run only that test. It must fail (not error), with the expected message, because the feature is missing (not a typo).
3. **GREEN**: write the simplest code that passes. No extra features.
4. **Verify GREEN**: the test passes, then the wider suite passes (see below).
5. **REFACTOR**: after green only. Remove duplication, improve names. Add no behaviour. Stay green.

Repeat for the next behaviour.

### Commands (AVP)

Never pipe test output. Redirect to a file, capture the tool's own exit code, then read the file. `LOG` is any scratch directory outside the repo.

```bash
LOG="${LOG:?set LOG to a scratch dir outside the repo}"
# Run every block from the repo root; each block uses a subshell so the cwd never leaks.

# API (needs Postgres avp_test on 127.0.0.1:55433 and Redis db 15; if they are down, say so, do not start them)
( cd apps/api && rc=0; uv run pytest -q -p no:cacheprovider tests/test_x.py > "$LOG/t.log" 2>&1 || rc=$?; echo "rc=$rc" )
tail -5 "$LOG/t.log"

# Workers
( cd apps/workers && rc=0; uv run pytest -q -p no:cacheprovider tests/test_x.py > "$LOG/t.log" 2>&1 || rc=$?; echo "rc=$rc" )

# Web (vitest)
rc=0; corepack pnpm --filter @avp/web exec vitest run src/path/file.test.ts > "$LOG/t.log" 2>&1 || rc=$?; echo "rc=$rc"

# Design system (vitest): use its own filter, not @avp/web, or a missing file reads as a false RED
rc=0; corepack pnpm --filter @avp/design-system exec vitest run src/path/file.test.ts > "$LOG/t.log" 2>&1 || rc=$?; echo "rc=$rc"

# shared-types (node --test)
rc=0; corepack pnpm --filter @avp/shared-types test > "$LOG/t.log" 2>&1 || rc=$?; echo "rc=$rc"

# Count gate, from the repo root, with the log path
python3 infra/ci/assert_count.py api-pytest "$LOG/api-pytest.log"
```

Confirm on RED: exit code non-zero, failure text is the expected one. Confirm on GREEN: exit code 0, other tests still pass, output has no errors or warnings.

**Test fails? Fix the code, not the test. Other tests fail? Fix now.** Deleting, skipping (`skip`, `xfail`, `.only`) or loosening an existing test needs the founder's OK and a line in the report. A pinned test that fails after a deliberate change (for example `test_formula_version_is_recorded` after a `FORMULA_VERSION` bump) is updated by the person who made the decision, visibly, never deleted: the count gate is a floor, so a deleted test can go unnoticed.

Bare `npm test`, `npx` or `jest` are never used here.

### Negative control

Break the code on purpose, watch the test fail, restore exactly. Back up first: `cp path/to/file "$LOG/file.bak"`; break it; run the test; `cp "$LOG/file.bak" path/to/file`; `cmp "$LOG/file.bak" path/to/file` must print nothing. Never use `git checkout` or `git restore` to undo it: that wipes uncommitted work.

### "Other tests" means the whole suite

A green run of your own test file is not a green suite. Before calling the change done, run the full suite of every package you touched (the full command set and the lint, mypy and `uv lock --check` steps are in `.github/workflows/ci.yml` and `infra/ci/README.md`; delegate full runs to the test-runner agent where available). A task scope bounds the deliverable, not your verification. Any failure that run shows, including one you did not cause, goes in your report by name. Use the installed `verification-before-completion` skill before any completion claim.

### Count gates, not coverage

There is no coverage percentage target. Counts are asserted instead:

- After a full run: `python3 infra/ci/assert_count.py <key> "$LOG/file.log"` (keys such as `api-pytest`, `workers-pytest`, `web-vitest`, `design-system-vitest`, `shared-types-node-test`, `api-mypy-errors`).
- When you add tests, bump the matching floor in `infra/ci/floors.json` in the same change.
- Never lower a floor without a written reason in `docs/build-log.md`. mypy is a ceiling: never raise it without a reason.

## Scoring formula changes

Scoring lives in `apps/api/src/avp_api/services/scoring.py`; the rules are in `docs/scoring-spec.md`.

- A formula change bumps `FORMULA_VERSION`. Write the test first: with the new formula, a hand-computed fixture yields the hand-derived literal score, and the result carries the new `FORMULA_VERSION`.
- Scoring never re-scores history. Write a test that a score already stored under the old `FORMULA_VERSION` keeps its old value and version after the change (a stored row is read back, not recomputed). Follow existing patterns in `apps/api/tests/test_scoring.py` and `test_score_schema.py`.
- Expected scores are literals computed by hand from the spec, never by calling the code under test.
- A version bump or any user-visible number change is a product decision: ask the founder if the brief has not settled it.

## No commit step

TDD here ends at a green tree, not a commit. Commit only when the founder asks, and only after the full bar. Stage explicit paths.

## Good tests

| Quality | Good | Bad |
|---|---|---|
| Minimal | One thing. "and" in the name? Split it. | `validates email and domain and whitespace` |
| Clear | Name describes behaviour | `test1` |
| Shows intent | Demonstrates the desired API | Obscures what code should do |

When writing or changing any test, read [writing-good-tests.md](writing-good-tests.md): name the break, assert real behaviour not mock behaviour, keep test-only code out of production classes, understand side effects before mocking, and know which guard tests grep source on purpose.

## Common rationalizations

| Excuse | Reality |
|---|---|
| "Too simple to test" | Simple code breaks. The test takes a minute. |
| "I'll test after" | Tests written after pass immediately, which proves nothing. You never watched them fail. |
| "Already manually tested" | Ad hoc, unrecorded, cannot be re-run. |
| "Need to explore first" | Fine. Explore, then start from a test. Ask before keeping exploratory code. |
| "Test is hard, so skip" | Hard to test means hard to use. Simplify the design. |
| "Existing code has no tests" | You are improving it. Add tests for what you touch. |

## Red flags: stop and ask

Code before a test; a test that passes immediately; cannot explain why a test failed; "just this once"; "I already manually tested it". Each means: stop, say so, and ask the founder before discarding written code.

## Verification checklist

- [ ] Every new behaviour has a test, watched failing for the expected reason
- [ ] Minimal code written for each
- [ ] Targeted tests pass; exit codes read from a file, nothing piped
- [ ] Full suite for each touched package passes; counts asserted against `infra/ci/floors.json`, floor bumped if tests were added
- [ ] Negative control run where the test proves something (restored, `cmp` clean)
- [ ] No existing test deleted, skipped or loosened without the founder's OK
- [ ] Edge cases and error paths covered
- [ ] Scoring change: `FORMULA_VERSION` bumped and history-not-rescored test present
- [ ] No commit made

## When stuck

| Problem | Solution |
|---|---|
| Do not know how to test | Write the wished-for API, write the assertion first, ask the founder. |
| Must mock everything | Code too coupled. Use dependency injection. |
| Setup is huge | Extract helpers; still complex, simplify the design. |
| A test fails and the cause is unclear | Use the library skill `systematic-debugging`. |

## Bugs

Bug found? Write a failing test reproducing it, then follow the cycle. The test proves the fix and prevents regression. Use `systematic-debugging` to find the cause first when it is not obvious. Something else broken that is outside your brief goes under "Found, not fixed", not into this change.
