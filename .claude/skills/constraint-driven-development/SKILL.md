---
name: constraint-driven-development
description: Use before claiming an AVP change is done, and before any commit the founder asked for, to check that the diff did not lower the test or lint floor: no new suppressions, no skipped or deleted tests, no lowered floor or raised mypy ceiling in infra/ci/floors.json, no weakened guard test, no piped or `|| true` test commands. Not for writing tests (library test-driven-development) or for proving the work is complete (installed verification-before-completion).
---

# Constraint-Driven Development (the floor)

## Overview

An agent that hits a red check takes the cheapest road to green: silence the checker, skip the test, lower the number. AVP's bar is already written down. This skill does not create a bar. It reads the bar and checks that **this diff did not lower it**.

Tightening the bar is silent. Loosening it is loud. Only lines this change adds or removes count; suppressions and skips that already exist on the base are not this change's finding.

## The bar (read it, do not restate it)

- `CLAUDE.md` (root): "The verification bar", "What must never ship".
- `infra/ci/floors.json`: floors for `api-pytest`, `workers-pytest`, `shared-types-node-test`, `design-system-vitest`, `web-vitest`; ceiling for `api-mypy-errors`. Read its `_why`.
- `infra/ci/README.md`: rule 1 (never pipe a test or lint command) and rule 2 (assert the count, not only the exit code).
- `infra/ci/assert_count.py <key> <log>`: the count gate.

If a new constraint seems needed (a new suite, a new ceiling, a new suppression allowance), **propose it to the founder** with the number you measured and where it would run. Measure first and hold that line ("must not fall" or "must not grow"); an invented target gets ignored. Never add it yourself, and never edit `CLAUDE.md` or `AGENTS.md` without the founder.

## When to use

- You are about to say a change is done, or about to commit (only when the founder asked for a commit).
- A check went red and you are tempted to silence, skip, delete or relax something.
- You touched `infra/ci/`, `.github/workflows/`, a test file, or a lint/type config.

Not for: writing tests (library `test-driven-development`), proving the change works end to end (installed `verification-before-completion`), or a code review (built-in `code-review`).

## The five moves to catch

1. **A threshold moved.** A floor lowered or a mypy ceiling raised in `infra/ci/floors.json`; a check dropped from `.github/workflows/ci.yml`; `assert_count.py` weakened. Lowering needs a written reason in `docs/build-log.md` naming which tests went and why. No reason: a violation.
2. **A test got easier.** `pytest.mark.skip`, `pytest.skip(`, `pytest.mark.xfail`, `pytest.xfail(`, `pytest.importorskip(`, `it.skip`, `describe.skip`, `test.skip`, `.only(` added; a test file deleted; test functions or assertions removed from a test file that stayed.
3. **A checker was silenced.** New `# noqa`, `# type: ignore`, `@ts-expect-error`, `@ts-ignore`, `eslint-disable`, and the whole-file forms `// @ts-nocheck`, `# ruff: noqa`, `# mypy: ignore-errors`. A `# type: ignore` hides an error from the mypy ceiling instead of fixing it.
4. **A guard test was weakened.** Five tests grep source on purpose and are never "fixed" away: `apps/web/src/components/legal/providers.test.ts`, `packages/design-system/src/tokens/reportIsolation.test.ts`, `apps/web/src/app/referrerPolicy.test.ts`, `apps/api/tests/test_facts_only.py`, `apps/api/tests/test_secrets_stay_out_of_logs.py`. Any removed line in them is a finding. Added lines (a new host classified, a new case) are fine.
5. **The way tests run was loosened.** `|| true` or a pipe (`| tail`, `| grep`, `| tee`) on a test, lint or typecheck command in CI or scripts; `--no-verify` in any added line, including `.md` files such as `CLAUDE.md` and `.claude/agents/*.md`.

## Process

1. **Get the diff** against the merge base with `origin/main`, including the working tree and untracked files. `git diff` alone misses new files.
2. **Run the guard** in [references/floor-guard.md](references/floor-guard.md). It exits `0` clean, `1` a violation, `2` it could not run. Never let a `2` read as a `0`.
3. **Report new vs pre-existing.** Only added lines count. A line that merely moved (same text removed and added in the same file) is not new; say so.
4. **Fix the code, or stop and ask the founder.** Do not add a suppression to get past the guard. Do not lower a floor to match a smaller suite. If a floor must drop (tests legitimately removed), that is a founder decision with a `docs/build-log.md` reason.
5. **A clean guard is not a green suite.** The guard reads the diff only. Run the count gates (the local form is in `CLAUDE.md` and `infra/ci/README.md`; the `test-runner` agent runs them): API and workers pytest, web and design-system vitest, shared-types `node --test`, each through `assert_count.py`; mypy through `assert_count.py api-mypy-errors` (mypy's own rc is 1 by design, the ceiling decides); `ruff check src tests`; typechecks; `uv lock --check` in both `apps/api` and `apps/workers`. Bump a floor in the same change that adds tests.

## Rank the checks

Ask of each check: can the agent make it pass by writing code that does not work?

- **External:** `infra/ci/floors.json` counts against a suite you did not write this session, `apps/api/scripts/license_audit.py`, the OpenAPI regeneration diff, `alembic check`.
- **Project:** ruff, mypy config, `.github/workflows/ci.yml`. A human owns them.
- **Suite:** the tests you just wrote. The only circular one, which is why the floor counts them.

## Red flags

- The diff edits `infra/ci/floors.json` and a feature in the same commit, and the number went down.
- A test count fell and the story is "consolidated".
- A suppression appears next to the failure it hides.
- Someone proposes relaxing a threshold instead of fixing the code.
- A test or lint command in CI gained a pipe.
- The guard printed exit 2 and the report says "clean".

## Rationalizations

| Excuse | Reality |
|--------|---------|
| "It is only one `# type: ignore`" | The mypy ceiling is 50. One hidden error is how it got there. |
| "The test was flaky, I skipped it" | A skip lowers the count the floor guards. Fix it or ask the founder. |
| "I will restore the floor next commit" | Floors move with the change that adds tests, and only down with a reason. |
| "The guard test is too strict" | Those five grep source on purpose. Fix the code they caught. |
| "Grep found nothing, so clean" | grep exits 1 on no match and 2 on error. Read the code, do not assume. |
