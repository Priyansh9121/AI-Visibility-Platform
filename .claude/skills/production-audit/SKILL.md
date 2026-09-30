---
name: production-audit
description: Launch-readiness audit of AVP (API, workers, web, Render, Stripe billing, legal pages) from local repo evidence, scored with blockers. Use when the founder asks "is AVP ready to launch / what breaks in prod / what blocks go-live"; not for routine code review (built-in `code-review`) or a diff security review (built-in `security-review`).
---

# Production audit (AVP launch readiness)

Answers "can AVP go live, and what blocks it?" from evidence in the checkout.
Engineering triage, not legal or compliance certification.

Drift check (north-star §8): touches all layers read-only; serves the launch phase.

## Hard limits
- Local evidence only. Do not run unpinned remote code, upload repo contents, or call remote scanners.
- No calls against production (Render, Stripe, Supabase) unless the founder asks for that specific call. Everything here is read-only.
- Secrets are checked by variable NAME and presence only: `[ -n "${VAR:+x}" ] && echo "VAR: set" || echo "VAR: unset"`. Never print a value; never open `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `dump.rdb`, `.devdata/`.
- No commit, push or edit. Report goes to chat; write a file only if the founder asks (then under `docs/`).
- `LOG` is a scratch dir outside the repo: `LOG="${LOG:?set LOG to a scratch dir outside the repo}"`.
- Commands: `uv run` in `apps/api` or `apps/workers`; `corepack pnpm` for JS; never pipe output (redirect to a log file outside the repo, read the tool's own exit code, then read the file).

## Order
Give every verdict per ref: `origin/main` and the current branch (e.g. `stripe_webhook_events` exists only on `launch-readiness`).

1. `git status --short --branch`, `git log --oneline -20`, `git diff --stat origin/main...HEAD`. Note that Render auto-deploys `main` before CI finishes; unpushed branch work is not live.
2. Read the end of `docs/build-log.md` ("Found, not fixed") and `docs/runbook.md` for open items.
3. Walk the checklist below; record file, verdict, evidence.
4. Run the verification bar (delegate to the `test-runner` agent).
5. Score, report.

## AVP evidence checklist
Payments
- Stripe webhook: `apps/api/src/avp_api/routers/billing.py` (`POST /billing/webhook`) verifies the signature before trusting the payload. Read `services/billing.py` `verify_webhook` (raw bytes, `stripe.Webhook.construct_event`, 503 with no fallback when the secret is unset), its call in the router, and the tests in `apps/api/tests/test_billing.py` (missing, garbage, wrong-body and replayed signatures refused). Idempotency and ordering via `stripe_webhook_events` (`apps/api/src/avp_api/models/stripe_webhook_event.py`): duplicate and stale events acknowledged, not re-applied. Look for a test of each.
- Test/live key separation: check by NAME presence only which Stripe variables the code reads (`apps/api/src/avp_api/config.py`) and that no key value is committed (tracked files only, filenames not values: `git grep -lE '(sk|rk)_(live|test)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}'`; never plain grep over the tree, it would print keys from untracked `apps/api/.env`). Live keys are the founder's to set on Render, never in the repo: list this as a founder item.

Data
- Alembic (`infra/db/alembic.ini`, `infra/db/migrations/versions/`): single head, every revision has a `downgrade()`, and migrations apply. `upgrade head` is a write: run it only against a throwaway local DB, and choose the target explicitly: `cd infra/db && ../../apps/api/.venv/bin/alembic -x db_url=postgresql://avp@127.0.0.1:55433/<scratch_db> upgrade head`, then the same with `check` (form from `infra/db/README.md`). `env.py` reads `DATABASE_URL` from the shell when `-x` is absent, so never run without `-x`, and never with a `DATABASE_URL` from the shell or `.env` (an exported Supabase URL would migrate production). Against `avp_dev` run only `heads` and `current`. Say what happens on Render (who runs migrations, when).
- Facts-only: `apps/api/tests/test_facts_only.py` passes; no model stores AI answer text, page HTML or competitor copy.

Operations
- Render health: `GET /api/v1/health` (liveness) and `GET /api/v1/ready` (Postgres + Redis) in `apps/api/src/avp_api/routers/health.py`; no `render.yaml` exists; if the repo does not name the health-check path, list it under Evidence missing (do not open the Render dashboard).
- Celery: `apps/workers/src/avp_workers/celery_app.py` reading config is not evidence. Require a task that actually retries (`autoretry_for` or `self.retry`); `task_max_retries` and `task_retry_backoff*` may be per-task options. Jobs must be safe to retry.
- Stranded work: a restart must not leave a scan QUEUED forever (see build-log).
- Logs: `apps/api/src/avp_api/logs.py` redacts; `apps/api/tests/test_secrets_stay_out_of_logs.py` passes. Note tokens in Render's request log if build-log says so.
- Runbook covers deploy, rollback, manual password reset (email may be off in production; the runbook says so).

Security and abuse
- Auth rate limits (login, sign-up, reset) per email and per IP: find the limiter under `apps/api/src/avp_api/` and its tests.
- Token routes send `Referrer-Policy: no-referrer`: `apps/web/next.config.mjs` and `apps/web/src/app/referrerPolicy.test.ts`.
- Sessions and Google sign-in: server-side enforcement on sensitive routes; tenancy scoped by agency.
- CORS allow-list (`apps/api/src/avp_api/main.py`, `config.py`): explicit origins, no wildcard with credentials.
- `avp_session` cookie flags (`config.py`, `sessions.py`): Secure, HttpOnly, SameSite; CSRF protection on state-changing routes.
- Per-IP rate limits (`services/rate_limit.py`): which proxy IP is trusted behind Render (a spoofable `X-Forwarded-For` defeats the limit), and whether the limiter fails open or closed when Redis is down.
- Prompt injection: crawled page content and engine answers reach the scan and fix-generator LLM calls (`services/fix_generator.py` takes facts only). Check that untrusted text cannot steer privileged actions or reach output unfiltered.

Honest crawling
- `apps/api/src/avp_api/services/crawl.py`: `RobotsGate` present, product token `AVPScanBot`, no spoofed UA or evasion; `/crawler` page (`apps/web/src/app/crawler/`) matches the code.

Dependencies and hosts
- Every outbound host and runtime dependency is classified in `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`; that test passes.
- Licences: `cd apps/api && uv run python scripts/license_audit.py > "$LOG/lic.log" 2>&1; echo "rc=$?"`; read the log. It covers the `apps/api` Python env only; check `apps/workers` and the JS packages by hand.

Legal (founder item)
- `/privacy` and `/terms` (`apps/web/src/app/`) are lawyer-pending drafts. Australian law applies (Privacy Act 1988 APPs, Spam Act 2003). Report status as an open founder decision; do not judge legal adequacy. Deletion handling: check build-log for what is still open.

## Verification bar (counts, not only exit codes)
Delegate to the `test-runner` agent (`.claude/agents/test-runner.md`). It must return counts vs `infra/ci/floors.json`, checked with `python3 infra/ci/assert_count.py <key> <log>`:
API pytest, workers pytest, web vitest, design-system vitest, shared-types `node --test`, `ruff check src tests`, mypy (ceiling, rc 1 is normal), all typechecks, `uv lock --check` in both `apps/api` and `apps/workers`, and the OpenAPI regeneration diff. API tests need Postgres `avp_test` on 55433 and Redis db 15; if down, report the evidence as missing, do not start them. Follow the `verification-before-completion` skill: no "passes" claim without this run's output.

## Scoring
Use scores to force prioritisation, not imply precision.

| Band | Score | Meaning |
| --- | --- | --- |
| Blocked | 0-49 | Do not ship until top risks are fixed |
| Risky | 50-69 | Small rollout or internal beta only |
| Launchable with caveats | 70-84 | Ship if the founder accepts the listed risks |
| Strong | 85-100 | No obvious blockers from available evidence |

Cap at 69 if: auth or tenancy missing on sensitive data; Stripe webhook not signature-verified or not idempotent; migrations cannot run forward or `alembic check` fails; a secret is committed, in a client bundle or in logs; no rollback path; the honest-crawler or facts-only guard tests fail.
Cap at 84 if: any suite is below its floor or CI is red; the launch-critical path (sign up, scan, pay, report) was not tested end to end; `/privacy` or `/terms` still lawyer-pending is a named caveat, not a cap.

## Output (in chat)
First line: `Production audit: NN/100, <band>, <the one or two risks that decide it>.`
Then: `Blockers`, `High-value fixes`, `Founder items` (live Stripe keys on Render, legal review, anything needing a product decision), `Evidence checked` (files, commands, counts), `Evidence missing`, `Next action` (one concrete step). Keep strengths short.

## Anti-patterns
- Running `npx <pkg>@latest` or any remote scanner; uploading code or data to an audit service.
- A score without named evidence; treating green CI as readiness.
- Touching production to "confirm" something without the founder asking.
- A generic "let me know what you want to do" ending.

## See also
`security-review` and `code-review` (built-in), `verification-before-completion`, `supabase-postgres-best-practices`, `handoff` and `git-guardrails` (library).
