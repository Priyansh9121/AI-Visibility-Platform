# Changes from the source

Paths are in the source folder `skills/constraint-driven-development/`.

- Description rewritten to trigger before "done" or a commit and to name what it is not for -> the source's broad triggers ("no quality bar", "set up constraints", "define our standards") (`SKILL.md:3`) would fire on unrelated work; AVP's bar already exists.
- Removed the interview (Steps 1-2, four questions, defaults) -> AVP's bar is fixed in `CLAUDE.md`, `infra/ci/floors.json`, `infra/ci/README.md`; nothing to ask (`SKILL.md:40-91`, `SKILL.md:36-38`).
- Removed `CONSTRAINTS.md` creation and the "add a line to `AGENTS.md` and `CLAUDE.md`" step -> replaced by reading the existing bar; a needed new constraint is proposed to the founder; `CLAUDE.md`/`AGENTS.md` are never edited without the founder (`SKILL.md:93-140`).
- Removed the tool table and every install (`pip install`, `pipx install`, `brew install`, `npm i -D`) and "picking a dimension means installing something" -> no installs; security/perf/a11y/architecture/mutation tools (semgrep, gitleaks, osv-scanner, Lighthouse, axe, size-limit, dependency-cruiser, Stryker) dropped; gitleaks noted as "founder decision, not installed" (`SKILL.md:142-184`).
- Replaced the coverage row "Changed lines >= 80% covered" and the coverage/mutation/LCP/CLS defaults with AVP's count gates: suite floors and the mypy ceiling via `infra/ci/assert_count.py` -> the source's percentages are unsourced defaults and AVP gates counts (`SKILL.md:117`, `SKILL.md:234-250`).
- Removed lifecycle wiring (`/build`, `/test`, `/review`, `/ship`, check:fast/task/full scripts, time budgets) -> those commands do not exist here (`SKILL.md:186-200`, `SKILL.md:172-182`).
- Removed the Escalation Path, exceptions table, `.constraintsignore` and stub/`TODO`/empty-`catch` detection -> only the floor was kept per the audit; exceptions go to the founder, not a table (`SKILL.md:133-138`, `SKILL.md:252-262`, `floor-guard.md:75`, `floor-guard.md:167`).
- Replaced the Node `floor-guard.mjs` (CONSTRAINTS.md threshold parser) with a bash/awk/python3 script over the diff: flags new suppressions and skips, deleted tests, removed tests/assertions, a lowered floor or raised ceiling in `floors.json` (with a `docs/build-log.md` reason check), removed lines in the five guard tests, piped or `|| true` test commands, `--no-verify` -> AVP's stack has no Node guard, and `floors.json` is the threshold file (`floor-guard.md:17-162`).
- Extended the suppression regex with AVP forms (`# noqa`, `# type: ignore`, `pytest.mark.skip`, `pytest.skip(`, `pytest.mark.xfail`, `it.skip`, `describe.skip`, `test.skip`, `.only(`, `@ts-expect-error`) and dropped `istanbul ignore`, `nosemgrep`, `gitleaks:allow`, `Stryker disable` (tools not present) (`floor-guard.md:73-77`, `SKILL.md:210`).
- Kept, verbatim in meaning: exit codes `0`/`1`/`2` and "Never let a `2` read as a `0`"; diff plus untracked files; tightening silent, loosening loud; the five moves; external/project/suite ranking; ratchet idea reduced to "measure, hold, propose to the founder" (`floor-guard.md:11`, `floor-guard.md:9`, `SKILL.md:206-224`, `SKILL.md:226-232`).
- Every check reads its own exit code, nothing piped (grep 0 = hit, 1 = none, 2 = error) -> matches `infra/ci/README.md` rule 1; the source's `catch` on git errors already treated null as not-clean, kept as `die` (exit 2) (`floor-guard.md:28-33`).
- Guard tests named and protected (`providers.test.ts`, `reportIsolation.test.ts`, `referrerPolicy.test.ts`, `test_facts_only.py`, `test_secrets_stay_out_of_logs.py`) -> AVP-specific; the source has no equivalent.
- Cross-references `interview-me`, `code-review-and-quality`, `ci-cd-and-automation`, `security-and-hardening`, `performance-optimization` replaced with `test-driven-development` (library), `verification-before-completion` (installed), built-in `code-review` -> siblings that do not exist here (`SKILL.md:304-311`).
- Removed the "Sane Defaults" table and the author/date line "Last reviewed: 2026-08-08 by @addy" (`SKILL.md:100`, `SKILL.md:238-248`) -> unsourced numbers and another person's example.
- Rationalizations and Red Flags rewritten for AVP (floors.json, mypy ceiling, guard tests); Verification checklist removed as it verified CONSTRAINTS.md setup (`SKILL.md:264-302`).
- Kept `references/floor-guard.md` only; no `allowed-tools` field (none needed).

Security-review fixes (2026-09-30):
- Suppression pattern now also catches whole-file forms `@ts-nocheck` (also as `// @ts-nocheck`), `# ruff: noqa`, `# mypy: ignore-errors`, and `pytest.xfail(` / `pytest.importorskip(` -> these hid whole files or tests from the floor. The source's `@ts-nocheck` (`floor-guard.md:73`) had been dropped in the first adaptation and is restored.
- Both diffs use `--no-renames` (the deleted-files query too) -> a `git mv` of a test file showed as a rename with no content lines and hid the deletion.
- The guard prints `file:line` plus the matched token only, never the whole matched line (suppressions, `--no-verify`, piped commands, removed guard-test lines; test counts are numbers) -> the source promised "never the matched secret value" and cut lines to 120 chars (`floor-guard.md:12`, `floor-guard.md:68`). Flattened diff rows now carry a line number for this.
- Untracked files are read with `git ls-files -z` and `read -d ''`; the guard exits 2 if `diff.err` is non-empty -> filenames with spaces or newlines, and silent git warnings, were missed.
- Diff format pinned (`--no-color --no-ext-diff --src-prefix=a/ --dst-prefix=b/`); exit 2 when `diff.txt` has content but `lines.tsv` is empty (a binary-only diff lands here by design) -> user git config could change the format and read as clean.
- Trailing tab git adds after `+++`/`---` paths with spaces is stripped; tabs in code text become spaces before the split -> mis-split rows.
- The floors check runs `python3 -I -` -> a `json.py` in the repo root cannot be imported.
- `--no-verify` is checked in all added lines including `.md` (`CLAUDE.md`, `.claude/agents/*.md`), while other patterns stay code-only -> `SKILL.md` said "anywhere" but `.md` was excluded.
- The scratch directory path is printed on exit (trap), `FLOOR_GUARD_LOG` documented, and the doc says it holds a diff copy and should be deleted after reading.
- `SKILL.md` lists the new patterns and the `--no-verify` scope.
- CHANGES citation for the removed defaults table and author line corrected from `SKILL.md:99` to `SKILL.md:100`.
