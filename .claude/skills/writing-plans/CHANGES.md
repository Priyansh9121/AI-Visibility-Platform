# Changes from upstream (superpowers writing-plans)

Line refs are to skills/writing-plans/SKILL.md upstream.

- Per-task "Commit" step (:132-137, `git commit -m`, `git add <files>`) replaced by "Full bar (counts vs floors), then the founder commits when asked" -> removes commits after only one task's test; CLAUDE.md: commit only on a green tree, only when asked. "Frequent commits" (:10) dropped and "Commit" removed from step list (:50).
- Added a first-line drift check (north-star section 8.1) and split-if-several rule -> CLAUDE.md requires it before scoping.
- Save location (:16 `docs/superpowers/plans/...`) -> founder decides; propose a path, create nothing (no `docs/plans/` exists).
- Removed superpowers sub-skill references (:14, :59, :201, :204: using-git-worktrees, subagent-driven-development, executing-plans) and the subagent/native execution menu (:187-198) -> those skills do not exist here; replaced by CLAUDE.md agent tiers (explorer, implementer, test-runner, security-reviewer, ui-reviewer, scribe) and a simple handoff.
- Added per-task Agent field and Delegation section -> CLAUDE.md delegation rules.
- Added full-bar section: no-pipe redirect pattern, `LOG` guard, count gates via `infra/ci/assert_count.py` and `floors.json`, floor bumps, OpenAPI/Alembic/uv lock rules -> AVP verification bar; upstream had only per-test runs (:118, :129).
- Added AVP house rules to Global Constraints and self-review (facts-only, design system, dependency classification, logs, FORMULA_VERSION) and "Open questions for the founder" section -> product decisions are the founder's.
- Added "check every path exists" rule -> avoids invented AVP paths.
- Stack line (:65) set to AVP's stack.
- Kept: bite-sized tasks, Interfaces blocks, right-sizing, step contents, review focus, self-review, proportion check (:34-177). Shortened prose throughout.
- Removed the upstream Step Granularity section (:43-50); its content is folded into the task template and "What a step contains".
- Review fix M1: bar snippet now runs the full suite, propagates pytest's exit code, then count-gates; single-file runs are red/green only -> removes a snippet that ignored rc and gated one file against a suite-wide floor.
- Review fix L2: added a final whole-bar step before the founder commits (ruff, mypy ceiling, all suites with floors, typechecks, uv lock --check in both projects, OpenAPI diff).
- Review fix L3: exact Alembic `-x db_url=` scratch-DB form -> env.py falls back to DATABASE_URL otherwise.
- Review fix L4: uncovered-behaviour choices route to "Open questions for the founder".
- Review fix L5: drift check is "first line under the title" in the skill text and template -> consistent wording.
- Review fix L6/L7: corrected line citation above; description rewritten in third person.
- Review fix (re-review M): both `uv lock --check` runs (apps/api and apps/workers, each on its own exit code, logged to files) moved to a preflight "before any `uv run`" at the front of the bar, and every `uv run` in the skill is now `uv run --locked` (task template Step 2, full-suite gate, ruff, mypy, OpenAPI export) -> removes the ordering where a plain `uv run` silently re-locks a stale `uv.lock` so a later check can never fail, and the stale lock ships (image uses `uv sync --frozen`, apps/api/Dockerfile:34,40; `.github/workflows/ci.yml` comment above "uv.lock is current"). A task that changes a `pyproject.toml` now lists both `uv.lock` files in its Files so they are staged with it; added a self-review item for lock discipline.
- Review fix (re-review nit): the OpenAPI regeneration diff runs on every change, not only after an endpoint change (as CI and CLAUDE.md do).
- Review nits: the full-suite snippet repeats the `LOG="${LOG:?...}"` guard -> agent Bash calls do not keep env vars, and an unset LOG would write to `/api-pytest.log` and look like a pytest failure. The OpenAPI check now copies `packages/shared-types/openapi.json` to `$LOG` before exporting and `cmp`s after (identical expected when no endpoint changed; a difference expected when one did, then regenerate shared types and list `openapi.json` and `src/api.gen.ts` in the task's Files) -> a check that does not depend on git index state.
