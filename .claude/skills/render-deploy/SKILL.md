---
name: render-deploy
description: Builds the pre-merge checklist and the post-deploy check for a change that will reach AVP production on Render (API in apps/api, web in apps/web), for the founder to run. It does not edit the Dockerfile or CI, call Render's API, or change env vars; the founder sets those in the Render dashboard.
---

# Render deploy: before and after

AVP runs on Render. The agent prepares the checklist and reads the repo; the
founder pushes, sets env vars and watches the deploy. Never call Render's API,
hold its keys, or push to `main`.

## How deploys work here (confirmed in the repo)

- Render deploys the API and the web app through its own git hook on push to
  `main`, **before CI finishes** (`infra/deploy/README.md`;
  `.github/workflows/deploy.yml` header). A red suite does not stop it. So
  CI must be green on the branch before anything reaches `main`, and nobody
  pushes to `main` without the founder's explicit OK.
- Configuration lives in the Render dashboard. There is **no `render.yaml`**.
  `deploy.yml` deploys nothing today.
- The API image is `apps/api/Dockerfile` (built from the repo root). Its
  entrypoint `apps/api/docker-entrypoint.sh` checks the settings, runs
  `alembic upgrade head`, then uvicorn. A start the settings refuse ends before
  the migration; a failed migration exits non-zero; either way the container
  never serves.
- The web app has no Dockerfile; API and web deploy **separately**, so there is
  a skew window where one is new and the other old.
- The scan executor runs inside the API process (Dockerfile header), so a
  deploy kills it. A deploy fails in-flight scans; it does not resume them.
  `reap_stale_scans` (`services/scan_executor.py`) marks a RUNNING scan FAILED
  (`EXECUTOR_LOST`) once its 180 s lease has lapsed and logs `scan.reaped`; it
  runs only when the dashboard or scan list is loaded (`routers/dashboard.py`,
  `routers/scans.py`), so the failure appears late. How a failed scan counts
  against an allowance changes with the branch: read the current
  `scan_executor` and scan-allowance code, do not assume.
- Postgres is Supabase, Redis is Render Key Value. `/0`, `/1`, `/2` are
  sessions, Celery broker, Celery results (`infra/deploy/README.md`).
- Health: `GET /api/v1/health` is liveness and touches no dependency (the image
  `HEALTHCHECK` uses it). `GET /api/v1/ready` checks Postgres and Redis and
  answers 503 when either fails (`routers/health.py`). A host's deploy check
  should poll `/ready` (Dockerfile comment). Which path the Render service is
  configured to poll is dashboard state; the repo does not record it, so ask
  the founder rather than assume.

## Pre-merge checklist (produce it filled in, from the diff)

Run from the repo root; `LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"`.

1. **CI green.** CI runs only on pull requests into `main` and pushes to `main`
   (`ci.yml:40-44`): so the PR's checks, or the local bar via the test-runner
   agent. Every job in `.github/workflows/ci.yml`,
   including the `image` job (builds the image, refuses the placeholder
   secret, migrates, serves, checks `/health` and `/ready`). Counts meet
   `infra/ci/floors.json`: `python3 infra/ci/assert_count.py <key> <log>`.
   Never pipe test output; redirect, read the tool's own exit code, read the file.
2. **Migrations in the diff.** List them:
   `git diff --name-only main... -- infra/db/migrations/versions` (read-only).
   For each file, quote its `lock_timeout` line if it has one
   (`grep -n lock_timeout <file>`; `20260930_1000_7c1d4e9a2b53` and
   `20260930_1200_3e8a5b7c9d14` carry `SET LOCAL lock_timeout = '5s'`) and say
   which table it alters. A migration without one on a busy table is a finding,
   not a fix. Precedent: `20260929_1325_55c6b4395e7d` adds a NOT NULL DEFAULT
   false column with no `lock_timeout`; it is already applied, do not edit it. Also say whether the previous code still works against
   the new schema (old API running mid-deploy); if not, flag it.
   `upgrade head` is not a dry run: it changes the database. For a dry run use
   `--sql` (offline, prints the SQL; it still needs `-x db_url=<scratch_url>`
   because env.py requires a URL, but does not connect). To really apply, use only a throwaway
   scratch DB, never `avp_dev` or `avp_test`:
   `cd infra/db && ../../apps/api/.venv/bin/alembic -x db_url=postgresql://avp@127.0.0.1:55433/<scratch_db> upgrade head`.
3. **Env vars to set or change.** Diff `apps/api/.env.example` and
   `apps/api/src/avp_api/config.py` against the branch point; list each new or
   renamed name for the founder to set in the Render dashboard. Check presence
   only, never values: `[ -n "${VAR:+x}" ] && echo "VAR: set" || echo "VAR: unset"`.
   Never read `apps/api/.env`. Boot rules that fail a deploy: `APP_SECRET` must not
   be the dev placeholder and must be at least 32 bytes in staging/production
   (the founder checks its length with the runbook's "Check that `APP_SECRET` is
   long enough", which prints only True or False); `CORS_ALLOW_ORIGINS` must
   name real origins (`infra/deploy/README.md`).
4. **New outbound host or runtime dependency?** Classified in
   `apps/web/src/components/legal/providers.test.ts`, named on `/privacy`, and
   `apps/api/scripts/license_audit.py` passes. After a `pyproject.toml` change,
   `uv lock --check` in both `apps/api` and `apps/workers`.
5. **Endpoint change?** OpenAPI and shared-types regenerated and committed
   (CI diff check).
6. **Deploy-window notes.** State which of API or web should go first and what
   a user sees if only one is live (old web with new API, or the reverse).
   Always say: running scans fail on deploy (see above).
   Precedent: the Google sign-up ticket change, where either order showed
   "expired" and the user restarted sign-up (`docs/build-log.md`, Found, not
   fixed). Where the skew is harmful, say the two should be deployed together.
7. **What goes live on push.** Note features gated on Render vars (the build
   log's "What goes live on push" sections); say which vars they depend on.
8. **Rollback.** A deploy that contains a migration cannot be rolled back by
   redeploying an older build in the dashboard: the older image's entrypoint
   (`docker-entrypoint.sh`, `set -e`) runs `alembic upgrade head`, Alembic stops
   with "Can't locate revision identified by ..." because the database is at a
   revision that image lacks, and the container never serves. The way back is a
   forward fix (a new commit), founder-approved. A build can be
   redeployed from the dashboard (a founder action) only if no migration was
   applied after the build you roll back to; say which case this is,
   and whether the new schema is safe under the old code.

## Post-deploy check (the founder runs it)

1. `GET https://<api-host>/api/v1/health` answers 200 with `status: ok`.
2. `GET https://<api-host>/api/v1/ready` answers 200 with `status: ready`;
   503 `degraded` names the failing store (`postgres` or `redis`).
3. Render → API service → Logs: the start shows `entrypoint: checking settings`,
   `entrypoint: alembic upgrade head`, then `entrypoint: serving on :<port>`. If it
   ends after `checking settings` with a settings error (an `APP_SECRET`, a
   `CORS_ALLOW_ORIGINS`), nothing was migrated and the previous deploy is still
   serving: fix the setting and deploy again. If the last line is a migration error,
   the container is down and the previous deploy is still serving. If the log
   stops at `entrypoint: alembic upgrade head` with no error, the migration is
   probably waiting on a lock (or the database is unreachable).
4. Search the logs for known warning events and read what each means:
   `redis.unavailable` and `auth.rate_limit.unavailable` (Redis trouble; the
   runbook does not cover them, so check the Redis instance),
   `auth.rate_limit.ip_header_missing` (runbook: "Confirm how Render passes the
   client IP"), `stripe.webhook.second_subscription` (runbook: "If the logs show
   a second Stripe subscription"), `stripe.webhook.rejected`,
   `stripe.webhook.unmatched`, `scan.reaped`, `scan.reaped_never_started`,
   `scan.execute.failed`, `scan.execute.lease_lost` (none in the runbook). A few
   `scan.reaped` lines after a deploy are expected, once the dashboard or scan
   list is loaded; a stream of the rest is not.
5. Load the web app's `/` and one signed-in page; confirm it reaches the API.
6. Anything that failed: report it with the log event name and time. Do not
   paste tokens, cookies or emails from the logs.

## Rules

- Numbers only from the repo or a measured run; no latency or capacity
  figures from memory.
- Never push, merge or trigger a deploy; hand the founder the checklist.
- Not legal or user-facing copy: do not rewrite `/privacy`, `/terms`, `/crawler`.
