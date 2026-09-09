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
slower and flakier than running natively, and there is no deployment target yet
that would justify maintaining service images.

**Docker is optional.** If you already run Postgres and Redis locally, ignore
this file and point `DATABASE_URL` / `REDIS_URL` at your own instances. Epic 1
was developed and verified against host-installed instances.

## What is deliberately not here yet

Nothing in this directory should be written before there is a real deployment
target, because infrastructure-as-code written against a hypothetical
environment is guaranteed to be wrong:

- Service container images (`Dockerfile` for api / workers / web)
- IaC for the hosting platform
- Managed Postgres and Redis provisioning
- Secret storage wiring — Epic 1 uses `.env` locally; a deployed environment
  must inject the variables in `apps/api/.env.example` from a real secret store
- CI pipeline definition

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
