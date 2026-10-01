# Plan Template (AVP)

Every plan is written for an executor with zero context, possibly a smaller model: competent at explicit instructions, weak at filling gaps or knowing when to stop. Three properties make a plan executable: self-contained context; a verification gate (command plus expected result) on every step; hard boundaries with "STOP and report" escape hatches.

File naming: `NNN-short-slug.md` in the plans directory the founder named (proposal: `docs/plans/`, not yet created), numbered in execution order.

---

## Template

```markdown
# Plan NNN: <Imperative title: what will be true after this plan>

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before the next step.
> If any STOP condition occurs, stop and report; do not improvise.
> **Do not commit, push, stash, reset, checkout or clean.** Leave the changes
> in the working tree; the founder commits after the full bar passes.
> When done, update this plan's row in the plans README, unless a reviewer
> dispatched you and said they maintain the index.
>
> **Precondition for `execute`**: `git status --porcelain -- <in-scope paths>` must be empty before a worktree is built (a worktree from HEAD misses uncommitted edits).
>
> **Drift check (run first)**: `git diff --stat <planned-at SHA>..HEAD -- <in-scope paths>`
> If an in-scope file changed, compare "Current state" against the live code;
> on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 | P2 | P3
- **Effort**: S | M | L
- **Risk**: LOW | MED | HIGH
- **Depends on**: NNN-*.md (or "none")
- **Category**: bug | security | perf | tests | tech-debt | migration | dx | docs | direction
- **Planned at**: commit `<short SHA>`, <YYYY-MM-DD>
- **north-star §8 drift check**: layer(s) (§3) touched and lifecycle phase (§6) served, one line

## Why this matters

2-5 sentences: the problem, its concrete cost, what improves. Intent lets the
executor make a correct judgment call when a detail is off. Any number needs a
named source or the label "unverified heuristic".

## Current state

Inlined facts, never "as discussed":
- Relevant files, one line of role each, with `file:line` ranges.
- Short excerpts of today's code with `file:line` markers (from the advisor's own reads).
- Conventions to match, with one exemplar file and lines.
- Decided constraints quoted from `docs/` (`north-star.md`, `scoring-spec.md`, `design-system.md`): the executor has not read them.
- Facts-only: plans never quote AI answer text, page HTML or competitor copy; use ids, counts, names, URLs.
- Secrets: location and credential type only, never a value.

## The bar (every plan; trim only what the scope cannot touch)

Set `LOG` to an absolute scratch dir outside the repo. Run from the repo root or in a subshell. Never pipe a test, lint or typecheck command; redirect to a file, take the tool's own exit code, then read the file.

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
fails=0
# Each block is a subshell; a subshell's status is its last command's, so every step below stops on failure itself.
# apps/api (needs Postgres avp_test on 127.0.0.1:55433 and Redis db 15; if down, STOP and say so; do not start them)
( cd apps/api || exit 1
  uv lock --check || { echo "FAIL: lock drift"; exit 1; }       # first: a plain uv run may re-lock and fetch
  rc=0; uv run --locked --extra dev ruff check src tests > "$LOG/ruff.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL ruff rc=$rc"; exit "$rc"; }
  rc=0; uv run --locked --extra dev mypy src > "$LOG/mypy.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || [ "$rc" = 1 ] || { echo "FAIL mypy rc=$rc"; exit "$rc"; }   # 1 = errors found; the ceiling decides
  python3 ../../infra/ci/assert_count.py api-mypy-errors "$LOG/mypy.log" || exit 1
  rc=0; uv run --locked --extra dev pytest -q -p no:cacheprovider > "$LOG/api-pytest.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL pytest rc=$rc"; exit "$rc"; }      # assert_count's "N passed" also matches "2 failed, N passed"
  python3 ../../infra/ci/assert_count.py api-pytest "$LOG/api-pytest.log" || exit 1) || fails=1
# apps/workers
( cd apps/workers || exit 1
  uv lock --check || { echo "FAIL: lock drift"; exit 1; }
  rc=0; uv run --locked --extra dev pytest -q -p no:cacheprovider > "$LOG/workers-pytest.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL workers rc=$rc"; exit "$rc"; }
  python3 ../../infra/ci/assert_count.py workers-pytest "$LOG/workers-pytest.log" || exit 1) || fails=1
# JS: node --test and vitest, typechecks
( cd packages/shared-types || exit 1
  rc=0; corepack pnpm test > "$LOG/st.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL shared-types rc=$rc"; exit "$rc"; }
  python3 ../../infra/ci/assert_count.py shared-types-node-test "$LOG/st.log" || exit 1
  corepack pnpm typecheck || exit 1) || fails=1
( cd packages/design-system || exit 1
  rc=0; corepack pnpm test > "$LOG/ds.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL design-system rc=$rc"; exit "$rc"; }
  python3 ../../infra/ci/assert_count.py design-system-vitest "$LOG/ds.log" || exit 1
  corepack pnpm typecheck || exit 1) || fails=1
( cd apps/web || exit 1
  rc=0; corepack pnpm test > "$LOG/web.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { echo "FAIL web rc=$rc"; exit "$rc"; }
  python3 ../../infra/ci/assert_count.py web-vitest "$LOG/web.log" || exit 1
  corepack pnpm typecheck || exit 1) || fails=1
[ "${fails:-0}" = 0 ] || { echo "BAR FAILED"; exit 1; }
```

Count gates: every suite must meet its floor and mypy must stay at or under its ceiling in `infra/ci/floors.json`. Read current values from that file; do not copy numbers into the plan. A plan that adds tests bumps the floor in the same change; lowering a floor or raising the mypy ceiling needs a written reason in `docs/build-log.md` and is a founder decision.

Add when the scope requires it:
- Endpoint added, changed or removed: `( cd apps/api && uv run --locked --extra dev python scripts/export_openapi.py )` (check its exit code is 0), then `corepack pnpm --filter @avp/shared-types generate` from the repo root, then `git diff --exit-code -- packages/shared-types/openapi.json`: exit 1 is expected when the intended change is present (read the diff, confirm it is only that change); exit 0 means the endpoint change did not reach the contract. List the endpoint changes in the report; `docs/api-contracts.md` is the scribe's.
- Schema change: model plus an Alembic revision in `infra/db/migrations/versions/`. Run Alembic only against a scratch database: `( cd infra/db && ../../apps/api/.venv/bin/alembic -x db_url=postgresql://avp@127.0.0.1:55433/<scratch_db> upgrade head )` then `( cd infra/db && ../../apps/api/.venv/bin/alembic -x db_url=postgresql://avp@127.0.0.1:55433/<scratch_db> check )` with the same `-x db_url=` form. `avp_dev` and `avp_test` are off-limits as scratch databases (`avp_test` is the shared test DB). Never point Alembic at a database chosen by an environment variable, and never at production (Render, Supabase). Follow `supabase-postgres-best-practices`.
- `pyproject.toml` change: re-lock both `apps/api` and `apps/workers` (`uv lock`), then `uv lock --check` in each (run it before any `uv run`). A new runtime dependency or outbound host must be classified in `apps/web/src/components/legal/providers.test.ts`, named on `/privacy`, and pass `apps/api/scripts/license_audit.py`; say so in the report.
- UI change: only `@avp/design-system` tokens and components; no raw hex, inline colour, stock Tailwind colour or off-scale spacing; the report stays paper in both themes.
- Scoring change: bump `FORMULA_VERSION`; never re-score history.
- Finish with a `docs/build-log.md` entry ending in a `## Verified` block (counts before -> after, negative control, the rest of the bar); the scribe writes it. Say what must be set on Render.
- Negative control where a test proves something: break the code, watch the new test fail, restore the file byte-for-byte (`cp` to `$LOG` before, `cp` back and `cmp` after; never `git checkout`, `git restore` or `git stash`).

## Suggested executor toolkit

(Optional.) Skills that plausibly exist and what for: `fastapi` and `supabase-postgres-best-practices` for `apps/api`, `test-driven-development`, `systematic-debugging`, `verification-before-completion` before any "done" claim.

## Scope

**In scope** (the only files you may modify):
- `path/to/file`
- `path/to/file.test` (create)

**Out of scope** (do NOT touch, even though related): list each with a reason. Always out of scope unless named above: `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `.claude/settings.json`, `CLAUDE.md`, `docs/api-contracts.md`, legal pages (`/privacy`, `/terms`, `/crawler`), anything in production.

## Git workflow

- **Do not commit.** The founder commits after the full bar passes, one logical item per commit, explicit paths, message via `git commit -F <file>`.
- Do not push, open a PR, stash, reset, checkout or clean. Never push to `main`: Render auto-deploys it before CI finishes.
- Exception: an `execute` run in a disposable worktree commits on its own worktree branch only if the founder's go-ahead says so (default: no). If it does, stage explicit paths only, never `git add -A` or `.`; the done criterion "nothing committed" then means nothing on the founder's branch.

## Steps

### Step 1: <imperative title>

What to do, precisely, with exact files and symbols and the target shape when load-bearing.

**Verify**: `<command>` -> <expected result>

### Step 2: ...

(Each step small enough to verify alone; never leave the tree broken between steps when avoidable.)

## Test plan

- New tests: which file, which cases (happy path, the regression, named edge cases).
- Structural pattern: "model after `apps/api/tests/test_x.py`".
- Verification: the targeted test command (exit code from a file) -> all pass, N new tests; then the full bar above.

## Done criteria

Machine-checkable; ALL must hold:
- [ ] ruff, mypy (via `assert_count.py`), pytest and vitest in scope exit as expected, with counts at or above `infra/ci/floors.json`
- [ ] `uv lock --check` passes in `apps/api` and `apps/workers` (and after any `pyproject.toml` change, both were re-locked)
- [ ] New tests for <X> exist and pass; negative control shows them failing on the broken code
- [ ] Only in-scope files changed (`git status --porcelain --untracked-files=all`, read-only); nothing committed (on an `execute` worktree branch, only the commits the founder allowed)
- [ ] Plans README status row updated

## STOP conditions

Stop and report (do not improvise) if:
- "Current state" does not match the live code (drift).
- A step's verification fails twice after a reasonable fix attempt.
- The fix needs an out-of-scope file, a new dependency or outbound host, a product decision, or a change to a guard test's intent.
- Postgres `avp_test` (55433) or Redis db 15 is down.
- You would have to print, log or store a secret, token, email, or third-party raw text.
- The key assumption "<assumption>" is false.

## Maintenance notes

What future changes will interact with this; what a reviewer should scrutinise; follow-ups deferred and why.
```

---

## Index file: `README.md` in the plans directory

```markdown
# Implementation Plans

Generated by the improve skill on <date> at commit <short SHA>. Execute in the
order below unless dependencies say otherwise. Read a plan fully before starting,
honour its STOP conditions, update your row when done. Nothing is committed by
an executor; the founder commits after the bar passes.

## Execution order and status

| Plan | Title | Priority | Effort | Depends on | Status |
|------|-------|----------|--------|------------|--------|

Status: TODO | IN PROGRESS | DONE | BLOCKED (one-line reason) | REJECTED (one-line rationale)

## Dependency notes
## Findings considered and rejected
```

## Quality bar (check before finishing each plan)

- Could a model that has never seen AVP execute this from the plan and the repo alone?
- Every verification a command with an expected result, exit codes read from files, none piped?
- Exact files and symbols, not "the relevant module"; every AVP path checked to exist.
- STOP conditions specific to this plan's risks.
- "Why this matters" plus "Done criteria" are enough for a reviewer to approve.
- No secret values, no third-party raw text, no invented numbers.
- "Planned at" SHA filled; drift-check paths match Scope.
