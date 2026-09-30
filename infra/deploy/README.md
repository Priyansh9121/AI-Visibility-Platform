# infra/deploy

Deployment and local-infrastructure configuration (product-spec.md §5.2).

## What is here now

`docker-compose.yml` — Postgres 17 and Redis 7 for local development.

```bash
docker compose -f infra/deploy/docker-compose.yml up -d
docker compose -f infra/deploy/docker-compose.yml down        # stop
docker compose -f infra/deploy/docker-compose.yml down -v     # stop and wipe data
```

It provides **datastores only**. The API, workers, and web app run on the host
via `uv run` and `pnpm dev` — a code-reload loop through a container mount is
slower and flakier than running natively.

**Docker is optional.** If you already run Postgres and Redis locally, ignore
this file and point `DATABASE_URL` / `REDIS_URL` at your own instances. Epic 1
was developed and verified against host-installed instances.

## Where deployment lives

The API has a `Dockerfile` (`apps/api/Dockerfile`; `apps/api/docker-entrypoint.sh`
runs `alembic upgrade head`, then uvicorn) and is deployed on Render, as is the
web app. Deploys happen through Render's own git hook on push to `main`, before
CI finishes. Configuration lives in the Render dashboard; there is no
`render.yaml`. Postgres is Supabase and Redis is Render Key Value.

`.github/workflows/deploy.yml` deploys nothing today; see the comment at its top.
The CI gate is `.github/workflows/ci.yml` with `infra/ci/`.

What is deliberately not here: infrastructure-as-code for Render, and a
`Dockerfile` for `apps/workers` or `apps/web`. Secrets are injected as the
variables in `apps/api/.env.example` from the Render dashboard, never committed.

## Deployment-time requirements already fixed

These are decided and must hold wherever this is deployed:

| Requirement | Why |
|---|---|
| `APP_SECRET` must not be the dev placeholder | The app refuses to boot in `staging`/`production` otherwise (`config.py`) |
| `SESSION_COOKIE_SECURE=true` | Forced automatically when `ENVIRONMENT` is `staging`/`production`. A session cookie over plaintext HTTP is a stolen session |
| `CORS_ALLOW_ORIGINS` must name real origins | Session cookies require explicit origins; browsers reject wildcard-with-credentials |
| Redis `/0`, `/1`, `/2` must be the same instance or three coordinated ones | Sessions, Celery broker, Celery results. Separated so flushing a wedged queue does not sign every user out |
| Migrations run before the API starts | `cd infra/db && alembic upgrade head` |
| No synchronous Postgres driver is available | `psycopg2`/`psycopg3` are LGPL-3.0 and excluded by the dependency-licensing rule. Everything uses asyncpg, including Alembic |
