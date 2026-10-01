# Closing the Loop: execute and reconcile

The founding rule stands: the advisor never edits source code. In `execute`, a separate executor agent edits code in an isolated git worktree; the advisor dispatches, reviews and renders a verdict. There is no GitHub issue publishing: never run `gh issue create`, and there is no `--issues` flag.

---

## `execute <plan>`: dispatch and review

`execute` needs the founder's explicit go-ahead each time; a plan in the repo is not authorisation. It is a founder decision because a fresh worktree has no `node_modules` or `.venv`, so the executor needs a locked install: `uv lock --check` first (run inside `apps/api` and inside `apps/workers`; there is no pyproject at the root), then `( cd apps/api && uv sync --locked --extra dev )` and the same in `apps/workers` (`dev` is an optional extra; CI uses `--frozen --extra dev`), and `corepack pnpm install --frozen-lockfile`. Never an unpinned `uv pip install`; plain `uv run` may re-lock, so use `uv run --locked`. Ask whether to allow it; if not, hand the plan over for manual execution. No new dependency is ever added by an executor. Only one API test-suite run at a time: `avp_test` and Redis db 15 are shared and the test conftest flushes db 15. That covers the executor, your own re-run, the `test-runner` agent and the founder's runs; tell the executor.

### Preconditions

- The repo is a git repository; the plan exists; its dependencies are DONE in the plans README.
- Run the plan's drift check yourself; refresh a drifted plan first.
- `git status --porcelain -- <in-scope paths>` is empty (a worktree built from HEAD misses uncommitted edits); otherwise stop and tell the founder.
- Record the base SHA (`git rev-parse HEAD`) at dispatch.
- The plan states "do not commit". Executor commits on the worktree branch default to NO; allowed only when the founder's go-ahead says so, and then with explicit-path staging only (never `git add -A` or `.`). Never on the founder's branch.

### Dispatch

Spawn one `general-purpose` subagent with `isolation: "worktree"`; model `sonnet` unless the founder names one (the orchestrator's `implementer` agent is the same tier). The prompt contains the full plan text inlined (an uncommitted plan is not visible in the worktree), this preamble, and the report format.

> You are the executor for the plan below. Follow it step by step. Run every
> verification command and confirm the expected result before moving on. Touch
> only the in-scope files. If any STOP condition occurs, stop and report. Do
> not improvise. Do not push, stash, reset, checkout or clean. Do not commit
> unless the dispatcher told you that committing on this worktree branch is
> allowed, and then stage explicit paths only. Never read `apps/api/.env`,
> `.mcp.json`, `.claude/settings.local.json`, `dump.rdb` or `.devdata/`. Check
> env vars by presence only: `[ -n "${VAR:+x}" ]`; never `echo $VAR`,
> `env | grep`, `printenv` or `set -x`. Only one API test-suite run at a time
> (`avp_test` and Redis db 15 are shared). Skip the plan's instruction to update the
> plans README; your reviewer maintains the index. Before reporting, check
> every claim against a tool result from this session; say plainly what failed
> or was skipped.

```
STATUS: COMPLETE | STOPPED
STEPS: per step, done/skipped + verification command result (exit code and count)
STOPPED BECAUSE: (only if STOPPED) which STOP condition, what was observed
FILES CHANGED: list
NOTES: deviations, surprises, judgment calls
```

### Review (never fix anything yourself)

1. Re-run every done criterion in the worktree, with exit codes read from files and counts checked against `infra/ci/floors.json`. Do not trust the report.
2. Scope: `git -C <worktree> diff --stat <base SHA>` and `git -C <worktree> status --porcelain --untracked-files=all` against the in-scope list (the first covers committed and uncommitted changes to tracked files, the second catches new files). Any file outside scope fails review.
3. Read the full diff against "Why this matters" and the repo conventions. Delegate a diff touching auth, sessions, tokens, billing, webhooks, tenancy or identifier logging to `security-reviewer`, and a user-visible `apps/web` or design-system diff to `ui-reviewer`.
4. Audit the new tests: do they assert something meaningful, and did a negative control fail on broken code?
5. Facts-only check: nothing stores or logs third-party raw text, secrets or emails.

### Verdict

Documented deviations are judged on merit; undocumented ones fail review.

| Verdict | When | Action |
|---|---|---|
| APPROVE | Criteria pass, scope clean, quality holds | Mark DONE in the index. Report diff summary, worktree path and branch, NOTES. Merging and committing are the founder's; never merge, push or commit to their branch. |
| REVISE | Fixable gaps | SendMessage to the same executor with specific feedback. Max 2 rounds, then BLOCK. |
| BLOCK | STOP condition, unrecoverable scope violation, or revisions exhausted | Mark BLOCKED with the reason; refine the plan; tell the founder. |

Verification inside the disposable worktree is fine. The no-mutation rule protects the founder's working tree.

---

## `reconcile`: keep the plans directory alive

Read the plans README and every plan, then per status:

- DONE: spot-check cheap done criteria on current HEAD; mark verified. Keep the file as the record.
- BLOCKED: investigate the obstacle; rewrite the plan (new number if the approach changed) or mark REJECTED with one line.
- IN PROGRESS (stale): flag to the founder; check for a leftover worktree.
- TODO: run the drift check; if drifted, re-verify the finding still exists, then refresh "Current state" and `Planned at`; if gone, mark REJECTED ("fixed independently").

Also compare against the end of `docs/build-log.md` for items fixed without a plan. Finish with a short report: verified done, refreshed, rejected, executable now.
