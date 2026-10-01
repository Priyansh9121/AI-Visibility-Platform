---
name: receiving-code-review
description: Use when acting on review findings in AVP - output from the built-in code-review or security-review, the project's security-reviewer or ui-reviewer agents, or a human or PR review - before changing any code. Verifies each finding against the repo, pushes back with evidence, and fixes only what holds. Not for producing a review (use code-review or security-review) and not for feedback on the founder's product decisions.
---

# Receiving code review in AVP

Review output is a set of claims, not orders. Evaluate each against the code, then act.

**Core principle:** verify before implementing; technical correctness over agreement. Reviewers (including the AVP reviewer agents and built-in reviews) work from a diff and can miss context.

## Response pattern

1. READ all findings first, without acting.
2. UNDERSTAND: restate each in one line; if any is unclear, stop and ask about it before implementing anything (items can be related).
3. VERIFY against the repo, read-only: open the cited file and line, find callers (`grep`), read the test that covers it. Reproduce the claim where cheap.
4. EVALUATE: is it correct for THIS codebase? Would the fix break existing behaviour, a policy test, or an earlier founder decision?
5. RESPOND per finding: holds / does not hold (with evidence) / cannot verify (say what is needed) / needs the founder. For a SECURITY finding (defined below), "does not hold" needs evidence about the attack path: the specific check that blocks it, or a failed reproduction run locally (avp_test on 127.0.0.1:55433, Redis db 15, localhost:3000), never against Render, Stripe live or Supabase. A test that pins current behaviour shows intent, not safety. If the fix would break pinned behaviour, the founder decides; do not close the finding.
6. IMPLEMENT one finding at a time.

## What counts as a security finding

Anything raised by `security-reviewer` or the built-in `security-review`, or touching the `CLAUDE.md` list: auth, sessions, cookies, tokens (share, invite, reset), passwords, rate limits, billing or Stripe webhooks, tenancy (`agency_id` scoping), logging of identifiers, CORS. A `code-review` finding in those areas is a security finding; it cannot be relabelled non-security and parked.

## Verification checks for AVP

Before accepting a finding, check:
- Does the cited code exist as described (file, line, current branch)? Stale findings are common.
- Is the current behaviour deliberate? Look for a test that pins it and a `docs/build-log.md` entry. (Intent is not safety: see step 5 for security findings.) Policy tests grep source on purpose and are not to be "fixed" away: `apps/web/src/components/legal/providers.test.ts`, `packages/design-system/src/tokens/reportIsolation.test.ts`, `apps/web/src/app/referrerPolicy.test.ts`, `apps/api/tests/test_facts_only.py`, `apps/api/tests/test_secrets_stay_out_of_logs.py`.
- Does the suggested FIX break a house rule? `CLAUDE.md` rules: storing third-party raw text, a new runtime dependency or outbound host without classifying it, a secret or email in a log line, re-scoring history, a stealthy crawler. Test-enforced rules: stock Tailwind classes and raw hex (`packages/design-system/src/components/render.test.tsx`, `apps/web/src/components/report/ReportView.test.tsx`). Decline the suggested fix, not the defect: if the defect is real, fix it another way within the rules, or ask the founder.
- Is it a product decision (a price, a limit, retention, user-visible copy or behaviour)? Do not choose; put the question to the founder.
- Is it in scope? A valid non-security finding outside the current change is recorded, not fixed (see below). A security finding that holds is never parked by the agent (see below).
- YAGNI: if the reviewer asks for "proper" metrics, options or abstraction, grep for real callers first. No callers: propose removing or skipping it rather than building it.

## Pushing back

Push back when the suggestion breaks existing behaviour or a policy test, lacks context, is technically wrong for this stack (FastAPI, async SQLAlchemy, Celery, Next 15, Tailwind 3 preset), adds unused features, or conflicts with a founder decision. Use evidence: file:line, the check that blocks the path, the command output. A pinning test is context, not proof. Ask a specific question instead of arguing. If a finding conflicts with a prior founder decision, stop and ask.

If you cannot verify a finding without something you lack (production data, a live provider, a browser session), say what you need and ask how to proceed; do not guess. Findings you cannot test or verify (races, timing, production-only behaviour) go to the founder as open; never close them as "does not hold".

## Fixing what holds

- Reproduce first: write the failing test (see `test-driven-development`), watch it fail for the right reason, then fix. A finding with no reproducing test is untestable or unverified: report it to the founder, do not close it.
- One finding at a time; run the targeted test after each. Order: security and correctness blockers, then simple fixes, then refactors.
- Security, auth, tenancy, billing, webhook or logging findings: rerun the `security-reviewer` agent on the diff. User-visible web changes: `ui-reviewer`.
- Before claiming done, run the bar in `verification-before-completion`: each suite writes to a file, its own exit code is read, never piped; counts are asserted with `infra/ci/assert_count.py` against `infra/ci/floors.json` (bump a floor in the same change that adds tests).
- Endpoint change from a fix: regenerate the OpenAPI contract and shared types as `CLAUDE.md` says. Schema change: model plus Alembic revision.

## Recording the outcome

- Non-security findings that hold but are out of scope, or accepted as not worth fixing, go under "Found, not fixed" in the `docs/build-log.md` entry, with the finding, the evidence, and why it was deferred.
- A security finding that holds can be deferred only by the founder. List it in the reply with its severity and ask; do not park it under "Found, not fixed" on your own. Once the founder defers it, record it under "Found, not fixed" with severity and area only.
- The GitHub repo is public. Exploit details of UNFIXED findings stay out of the build-log, PR text and commit messages (public once pushed); describe them to the founder in chat only.
- Findings rejected go in the reply with the evidence. Before closing a rejected security finding, re-run `security-reviewer` with the counter-evidence; a non-security rejection can also be re-run that way.
- Do not commit or push; the founder decides. Never push to `main`.

## Wording

State the technical fact. Skip performative agreement and gratitude: write "Fixed: <what changed, where>" or "Not fixed: <evidence>". If you pushed back and were wrong, say so factually ("checked X, it does Y; fixing") and move on.

## Common mistakes

| Mistake | Fix |
|---|---|
| Implementing every finding because a reviewer said so | Verify each against the code first |
| Fixing a finding without a failing test | Reproduce, then fix |
| Batching fixes untested | One finding, one test run |
| "Fixing" a policy test that flagged a real change | Fix the change, not the guard |
| Silently dropping a valid out-of-scope finding | Record under "Found, not fixed" |
| Choosing on a product question | Return it to the founder |
| Proceeding on a finding you cannot verify | State the limit and ask |
| Closing a security finding because a test pins the behaviour | Show the blocking check or a failed reproduction; else the founder decides |
| Parking a security finding under "Found, not fixed" | Only the founder defers it |

## GitHub thread replies

Only when a PR exists and the founder has asked for replies. Reply inside the comment's own thread, not as a top-level PR comment:
Write the reply body to a file under `LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"`, then create the file with the Write tool or a quoted heredoc (`<<'EOF'`); an unquoted heredoc or `echo "..." >` still runs backticks, and replies quote `file:line` in backticks. Post it from the file, never inline in double quotes (where `$()` or backticks would run):
`gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies -F body=@"$LOG/reply.md"`
Keep tokens, emails and exploit details of unfixed findings out of reply text.
Never resolve, merge or push as part of replying (`gh pr merge` is denied in `.claude/settings.json`).
