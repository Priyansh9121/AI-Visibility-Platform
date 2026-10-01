---
name: writing-plans
description: Guides writing a task-by-task implementation plan when the founder has a spec or brief for a multi-step AVP change (several tasks across apps/api, apps/workers, apps/web, packages/design-system or infra/db) and wants the plan before any code is touched. Not for single-file edits, bug hunts (use systematic-debugging) or architecture decisions (use architecture-decision-records).
---

# Writing Plans

Write the plan for an implementer who has not seen this codebase or this spec. They write idiomatic code once they know the exact interface and the exact test. What they cannot know is what you decided: files, names, signatures, spec values, and which test proves each task. Document those. Bite-sized tasks. DRY. YAGNI. Tests first.

**Announce at start:** "I'm using the writing-plans skill to create the implementation plan."

**Where plans live:** the founder says. There is no `docs/plans/` yet. Propose a path (for example `docs/plans/YYYY-MM-DD-<feature>.md`) and ask; do not create the folder or file until the founder agrees. A plan is a document, not a commit: the founder commits when asked.

## Drift check comes first

Before scoping, the plan's first line under its title answers `docs/north-star.md` section 8 in one line:

> Drift check: touches layer(s) <section 3 layer names>; serves lifecycle phase <section 6 phase>.

If the brief touches several layers or phases without a clear reason, stop and propose splitting it into separate plans (north-star 8.1: "three briefs wearing one coat"). Each plan must produce working, testable software on its own. If the brief needs a product decision (a price, a limit, retention, user-visible behaviour), list it as an open question for the founder; do not decide it in the plan.

## File structure

Before tasks, map which files are created or modified and what each is responsible for.

- One responsibility per file; files that change together live together.
- Follow the existing patterns. Do not restructure unilaterally; a split of an unwieldy file is fine if the plan says why.
- Check every path exists (read-only) before naming it. If unsure, leave it out or mark it "to be created".

## Task right-sizing

A task is the smallest unit that carries its own test cycle and is worth a reviewer's gate. Fold setup, config and doc steps into the task whose deliverable needs them. Split only where a reviewer could reject one task while approving its neighbour. Each task ends with an independently testable deliverable.

## Plan header

```markdown
# [Feature Name] Implementation Plan

Drift check: touches layer(s) ...; serves lifecycle phase ...

> **For agentic workers:** Work task by task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** one sentence.
**Architecture:** 2-3 sentences.
**Stack:** AVP's (FastAPI + async SQLAlchemy 2 / asyncpg, Celery, Postgres, Alembic, Next 15 App Router, `@avp/design-system`), plus anything new.
**Spec:** path to the spec or brief; executors read both.

## Global constraints
Project-wide requirements from the spec, one line each, exact values verbatim.
Always include AVP's house rules that apply (CLAUDE.md): facts-only storage; design-system tokens only; no new runtime dependency or outbound host without classifying it in `apps/web/src/components/legal/providers.test.ts` and naming it on `/privacy`; no secret, token or email in a log; scoring never re-scores history (formula change bumps `FORMULA_VERSION`); honest crawling.

## Review focus
Up to five input classes or failure modes the spec implies but no task's tests exercise, most likely first, one line each. Add the test that pins each to the task that owns the code. If pinning a behaviour means choosing something the spec does not settle (user-visible behaviour, a limit), route that choice to "Open questions for the founder" instead of deciding it.

## Open questions for the founder
Product decisions the plan must not make.

---
```

## Task structure

````markdown
### Task N: [Component]

**Files:**
- Create: `exact/path`
- Modify: `exact/path:123-145`
- Test: `exact/test/path`
- (a task that changes a `pyproject.toml` also lists both `uv.lock` files here, so they are staged with it)

**Interfaces:**
- Consumes: exact signatures from earlier tasks
- Produces: exact names and types later tasks rely on (an implementer sees only their own task)

**Agent:** which tier does it (see Delegation)

- [ ] **Step 1: Write the failing test** (name and assertions as code, with the spec's exact values)
- [ ] **Step 2: Run it, expect FAIL** (targeted command, `uv run --locked pytest <file>` for Python; output redirected to a file, read the tool's own exit code)
- [ ] **Step 3: Implement `name(args) -> Type` in `path`** (one line on approach; a body only for an algorithm the signature and tests do not determine)
- [ ] **Step 4: Run it, expect PASS**
- [ ] **Step 5: Full bar (counts vs floors), then the founder commits when asked**
  Run the whole bar the task touches (below). Do not commit; do not stage.
````

### The full bar, per task

Name the suites the task touches and the count gate for each, using `infra/ci/floors.json` keys (`api-pytest`, `workers-pytest`, `web-vitest`, `design-system-vitest`, `shared-types-node-test`, `api-mypy-errors`). Commands follow `infra/ci/README.md`.

**Preflight, before any `uv run`** (the plan's first step, ahead of even the red/green runs): check both lockfiles, each on its own exit code. A plain `uv run` silently re-locks a stale `uv.lock`, so a check run after it can never fail, and the image installs the lock with `uv sync --frozen` (`apps/api/Dockerfile`); CI's `uv lock --check` exists because a stale workers lock once shipped (comment above "uv.lock is current" in `.github/workflows/ci.yml`).

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
rc_api=0; ( cd apps/api || exit 1; uv lock --check > "$LOG/lock-api.log" 2>&1 ) || rc_api=$?
rc_wrk=0; ( cd apps/workers || exit 1; uv lock --check > "$LOG/lock-workers.log" 2>&1 ) || rc_wrk=$?
echo "uv lock --check exit: api=$rc_api workers=$rc_wrk"
```

Either non-zero: stop, read that log, and run no `uv run` until the lock is current (after a deliberate `pyproject.toml` change: `uv lock` in both projects, then check again). Every `uv run` in the plan is `uv run --locked ...`, so a stale lock fails the command instead of being rewritten.

Full-suite count gate (run from the repo root, full suite, no file argument):

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( cd apps/api || exit 1; rc=0; uv run --locked pytest -q -p no:cacheprovider > "$LOG/api-pytest.log" 2>&1 || rc=$?; echo "pytest exit: $rc"; exit "$rc" ) && python3 infra/ci/assert_count.py api-pytest "$LOG/api-pytest.log"
```

Targeted single-file runs are for the red/green steps only and are never count-gated (one file cannot meet a whole-suite floor).

**Final whole-bar step, before the founder commits:** `uv run --locked ruff check src tests` (apps/api); `uv run --locked mypy src` via `assert_count.py api-mypy-errors` (mypy's rc is 1 by design; the ceiling decides); every suite with its floor (api, workers, web, design-system, shared-types); all typechecks; the OpenAPI regeneration check, on every change as CI does (snippet below). The lock checks already ran in the preflight; re-run them only if a `pyproject.toml` changed since. Delegate the full run to the test-runner.

OpenAPI check (repeat the `LOG` line first; agent Bash calls do not keep env vars). Copy the contract aside before exporting, so the comparison does not depend on git state:

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
cp packages/shared-types/openapi.json "$LOG/openapi.before.json"
rc=0; ( cd apps/api || exit 1; uv run --locked python scripts/export_openapi.py > "$LOG/openapi.log" 2>&1 ) || rc=$?; echo "export exit: $rc"
cmp "$LOG/openapi.before.json" packages/shared-types/openapi.json; echo "cmp exit: $?"
```

No endpoint change: `cmp` must exit 0 (identical). Endpoint change: it is expected to differ; then run `corepack pnpm --filter @avp/shared-types generate` and list `packages/shared-types/openapi.json` and `packages/shared-types/src/api.gen.ts` in the task's Files, plus the endpoint change for the scribe.

- Never pipe a test, lint or typecheck command. Redirect to a file, read the tool's own exit code, then read the file. Count, not only exit code.
- A task that adds tests also bumps the floor in `infra/ci/floors.json` in the same change; lowering a floor needs a written reason in `docs/build-log.md`.
- Endpoint change: regenerate the OpenAPI contract and shared types, and list it for the scribe (`docs/api-contracts.md`). Schema change: model plus Alembic revision, then, against a scratch DB only: `cd infra/db && ../../apps/api/.venv/bin/alembic -x db_url=postgresql://avp@127.0.0.1:55433/<scratch_db> upgrade head`, then the same with `check` (env.py falls back to `DATABASE_URL` otherwise, so never run it without `-x`). `pyproject.toml` change: `uv lock` in both `apps/api` and `apps/workers`, then the preflight check; both `uv.lock` files are part of the change.
- Every change ends with a `docs/build-log.md` entry (scribe) with a Verified block: counts before and after, negative control, rest of the bar.

## Delegation (CLAUDE.md agent tiers)

Assign each task an agent:

- **explorer**: "where / how / what calls" questions and mapping the change before building.
- **implementer**: one scoped edit with its tests. Never commits.
- **test-runner**: suites, lint, typecheck and count gates; returns counts and failures.
- **security-reviewer**: any task touching auth, sessions, tokens, passwords, rate limits, billing, webhooks, tenancy or logging of identifiers.
- **ui-reviewer**: any user-visible `apps/web` or design-system task.
- **scribe**: build-log entries, `docs/api-contracts.md`, runbook steps.

Pick the cheapest that fits.

## What a step contains

A step is done when the implementer can write exactly one reasonable thing from it.

- **Test step:** the test's name and assertions, as code.
- **Code step:** exact signature, file, and the values the spec pins. The implementer writes the body.
- **Verification step:** the command and the output that means it passed.
- **Reference to another task:** point to its Interfaces block; do not repeat its code.

Lines that decide nothing ("TBD", "handle edge cases", "add validation", "write tests for the above") are a failure, as is a plan that writes the code itself.

## Self-review

After writing the plan, check it yourself (no subagent):

1. **Spec coverage:** each requirement maps to a task; list gaps.
2. **Step scan:** no step decides nothing; no step carries a body the signature and tests already determine.
3. **Type consistency:** names and signatures match across tasks.
4. **Review focus:** each line has a test in the owning task.
5. **Proportion:** a plan several times longer than its spec is a transcript. Replace bodies with signatures and assertions.
6. **Drift check** is the first line under the title, and the plan spans one layer/phase or says why not.
7. **Lock discipline:** the preflight lock check is the first step, before any `uv run`, and every `uv run` is `uv run --locked`.
8. **Facts-only and house rules:** no task stores third-party raw text, adds an unclassified dependency, or decides a product question.

Fix issues inline.

## Handoff

Link the plan for the founder to read (after they confirm the location). Ask them to review it and confirm it captures what they want, and to say how to execute: tasks run one by one by the implementer agent with the test-runner gate after each, or another method they name. Wait for their review before implementation. Do not commit the plan unless asked.
