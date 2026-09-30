# Changes from upstream (`skills/systematic-debugging/`)

## SKILL.md
- Description rewritten to AVP surfaces and a narrow trigger; "any bug/any technical issue" removed -> stops over-triggering (SKILL.md:3-4, 24-30).
- Removed the "Performance problems / Build failures / Integration issues" catch-all list and the "Manager wants it fixed NOW" line -> scope and tone (SKILL.md:24-42).
- Replaced the multi-layer example `echo "IDENTITY: ${IDENTITY:+SET}${IDENTITY:-UNSET}"` -> it prints the value when set (SKILL.md:91).
- Replaced `env | grep IDENTITY || echo ...` -> prints the value (SKILL.md:95). Both now a presence-only check (`[ -n "${VAR:+x}" ] && echo "VAR: set" || echo "VAR: unset"`, which expands to `x`, never the value).
- Removed macOS keychain / `security find-identity` / `codesign` diagnostics -> keychain and credential access (SKILL.md:97-104).
- Added rule that diagnostics never print env values, tokens or emails and never read `apps/api/.env` -> house rule (CLAUDE.md, AVP-FACTS).
- Added never-pipe test rule with redirect-to-file commands, avp_test/Redis db 15 note, guard tests that grep source, `FORMULA_VERSION` rule -> AVP verification bar.
- `superpowers:test-driven-development` -> library `test-driven-development`; `superpowers:verification-before-completion` -> `verification-before-completion` (SKILL.md:177, 189).
- Added negative control and floors/`assert_count.py` step to Phase 4 verify -> AVP count gates (SKILL.md:185-189).
- "Discuss with your human partner" -> "the founder"; removed the "your human partner's Signals" section -> source-specific persona (SKILL.md:210, 233-242).
- Kept: four phases, iron law, 3-failed-fixes architecture stop, red flags, rationalizations (SKILL.md:44-255).
- "95% of no root cause cases are incomplete investigation" -> relabelled "(unverified heuristic)"; unsourced statistic (SKILL.md:275).
- Dropped the Quick Reference table and dot-style prose repetition -> shorter (SKILL.md:257-264).

## root-cause-tracing.md
- TypeScript/`npm test 2>&1 | grep` examples replaced with Python/pytest and a redirect-to-file capture -> pipes forbidden, stack (root-cause-tracing.md:41-95).
- Removed the `find-polluter.sh` invocation; replaced with a written manual bisection procedure using pytest/vitest commands -> script not copied, never pipe (root-cause-tracing.md:97-107).
- Removed the Lace-specific worked example and the "1847 tests passed" impact stats -> unsourced, not AVP (root-cause-tracing.md:109-128, 163-169).
- Removed graphviz digraphs -> shorter (root-cause-tracing.md:11-24, 132-152).
- Instrumentation example logs only booleans/ids, not env values (root-cause-tracing.md:74-79, `process.env.NODE_ENV`).

## defense-in-depth.md
- Node/TS `existsSync`, `NODE_ENV`, git-init examples -> Pydantic v2 / service / `RobotsGate` / test-DB guard examples (defense-in-depth.md:25-85).
- Removed the "1847 tests" session result and Lace example (defense-in-depth.md:96-112).
- Added note not to weaken guard tests or add persisted fields for evidence (facts only).

## condition-based-waiting.md
- TS `setTimeout` `waitFor` replaced with pytest/asyncio, Celery and vitest patterns (condition-based-waiting.md:36-82).
- Dropped `condition-based-waiting-example.ts` (Lace `ThreadManager` code, not applicable); an inline snippet is enough (condition-based-waiting.md:82).
- Removed "15 flaky tests, 60% -> 100%, 40% faster" claims -> unsourced (condition-based-waiting.md:109-115).

## Dropped files
- `find-polluter.sh` -> not copied per brief; runs `npm test` in a loop; replaced by prose bisection (find-polluter.sh:51).
- `CREATION-LOG.md`, `test-academic.md`, `test-pressure-*.md` -> pressure tests of the skill itself.
- `condition-based-waiting-example.ts` -> see above.

## Security-review follow-ups
- Boundary example rewritten to the real sources: API via pydantic-settings `model_fields_set` (exception prints only its type), worker via `os.environ` presence form; Render presence is the founder's check -> the old layers read the same shell and could not tell them apart (SKILL.md:87-95; `apps/api/src/avp_api/config.py:53-59,80`, `apps/workers/src/avp_workers/config.py:40`).
- Presence form changed to `${VAR:+x}`; ban list extended (`set -x`, `printenv`, bare `env`, `export -p`, `ps e`, `/proc/*/environ`, `docker inspect`, `print(os.environ)`, `print(settings)`/`.model_dump()`, pytest `-l`/`--showlocals`) -> value leaks under xtrace and locals dumps (SKILL.md:91,95).
- Added `import sys` to the instrumentation snippet (root-cause-tracing.md:69-83).
- Negative control now says how (copy, edit, run, copy back, `cmp`; no stash or checkout) (SKILL.md:185-189).
- Evidence limited to local avp_dev/avp_test; no DB URL on a command line; `LOG` defined with `${LOG:?...}`.
- Vitest bisection note: files run in parallel in its own order; bisect by file subsets (root-cause-tracing.md:97-107).
- Unlogged removals: "Violating the letter..." line (SKILL.md:12); red-flag lines "Pattern says X but I'll adapt it" and "Here are the main problems" (SKILL.md:223-224); the "Reference too long" rationalization (SKILL.md:253); the Quick Patterns table (condition-based-waiting.md:48-56); the "Key Insight" section (defense-in-depth.md:114-122).
