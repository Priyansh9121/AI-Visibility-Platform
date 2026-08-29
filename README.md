# AI Visibility & Competitive Intelligence Platform

A prospecting and client-retention tool for SEO / digital marketing agencies.
It measures how visible a brand is inside AI answer engines, compares that to
competitors, and turns the gap into a report an agency can put in front of a
client.

---

## Repo layout

| Path | What lives here | Status |
|---|---|---|
| `apps/web` | Next.js + React frontend — intake screen, narrative report | ✅ **Epic 7 complete** |
| `apps/api` | **FastAPI (Python)** — HTTP API, auth, models | ✅ **Epic 1 complete** |
| `apps/workers` | **Celery (Python)** — background scan workers | ✅ **Epic 1 complete** |
| `packages/design-system` | Proprietary design system: tokens + components | ✅ **Epic 0 complete** |
| `packages/shared-types` | Domain types shared across all apps | Epic 1 |
| `infra/db` | Schema, migrations, local docker-compose | Epic 1 |
| `infra/deploy` | Deploy manifests, CI/CD | Epic 1 |
| `docs` | Spec, build log, API contracts, scoring spec, IP safety | live |

## Docs you should read first

- **`docs/ip-safety.md`** — standing design/IP constraints. Normative. Read before writing UI code.
- **`docs/north-star.md`** — competitive goal, the seven-layer product architecture,
  deployment and commercial architecture. **Normative.** Read alongside `ip-safety.md`
  at the start of every brief; §8 states the drift-check rule every brief must answer.
- `docs/product-spec.md` — source of truth for the product (§3 core loop, §5 architecture, §6 scoring, §7 epics).
- `docs/design-system.md` — token and component reference, with the reasoning behind each decision.
- `docs/build-log.md` — what was built, why, and what was traded off.
- `docs/api-contracts.md` — every endpoint.
- `docs/scoring-spec.md` — the AI Visibility Score formula.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Node.js | >= 20.11 | frontend + shared types |
| pnpm | 9 | `corepack enable && corepack prepare pnpm@9.12.0 --activate` |
| Python | 3.12 | installed automatically by uv — see below |
| uv | >= 0.5 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| PostgreSQL | 17+ | Docker, Homebrew, or any existing instance |
| Redis | 7+ | same |

If `corepack enable` fails with `EACCES` (it symlinks into `/usr/local/bin`),
skip it and prefix commands with `corepack` instead — `corepack pnpm install`.
No sudo required.

You do **not** need to install Python 3.12 yourself. `uv` reads
`.python-version` in each service and downloads the right interpreter. The
system Python is deliberately not used — 3.14 is too new for reliable Celery and
asyncpg wheels.

## Setup

### 1. Datastores

Either use Docker:

```bash
docker compose -f infra/deploy/docker-compose.yml up -d
```

…or point at instances you already run, by editing `DATABASE_URL` and
`REDIS_URL` in step 3.

### 2. Install dependencies

```bash
pnpm install                              # Node workspaces (web, packages/*)
cd apps/api     && uv sync --extra dev    # FastAPI service
cd ../workers   && uv sync --extra dev    # Celery workers
```

`apps/api` and `apps/workers` are **Python** (product-spec.md §5.1) and are
deliberately outside the pnpm workspace.

### 3. Configure

```bash
cp apps/api/.env.example apps/api/.env
```

Then edit `apps/api/.env`. Only `DATABASE_URL` and `REDIS_URL` matter to run
Epic 1 — every third-party API key is optional until Epic 2. `.env` is
git-ignored; `.env.example` is the committed template and holds no real values.

### 4. Migrate

```bash
cd infra/db
export DATABASE_URL="postgresql+asyncpg://avp:avp@127.0.0.1:5432/avp"
../../apps/api/.venv/bin/alembic upgrade head
```

## Run

### API

```bash
cd apps/api
uv run uvicorn avp_api.main:app --reload --port 8000
```

- API base: http://localhost:8000/api/v1
- Interactive docs: http://localhost:8000/api/v1/docs
- Health: http://localhost:8000/api/v1/health
- Readiness (checks Postgres + Redis): http://localhost:8000/api/v1/ready

Prove the Epic 1 acceptance path from a terminal:

```bash
curl -sX POST http://localhost:8000/api/v1/auth/sign-up \
  -H 'Content-Type: application/json' -c /tmp/avp.jar \
  -d '{"agencyName":"Acme SEO","fullName":"Alex Reed",
       "email":"alex@acme.example","password":"correct-horse-battery-staple"}'

curl -s http://localhost:8000/api/v1/dashboard -b /tmp/avp.jar
```

### Workers

```bash
cd apps/workers
uv run celery -A avp_workers.celery_app:celery_app worker \
  --loglevel=info --queues=scans,engines,audits --concurrency=4
```

Confirm the queue round-trips:

```bash
cd apps/workers
uv run python -c "
from avp_workers.orchestrator import CeleryOrchestrator
import time
o = CeleryOrchestrator(); j = o.enqueue('avp.health.ping')
time.sleep(1); print(o.status(j), o.result(j))"
```

### Design-system style guide

```bash
pnpm --filter @avp/design-system dev      # http://localhost:4100
```

### Web app

```bash
pnpm --filter @avp/web dev        # http://localhost:3000
```

Needs the API running on port 8000. Sign in with an account created via
`/auth/sign-up`, then submit a URL on the intake screen.

Classification requires `ANTHROPIC_API_KEY` in `apps/api/.env` **and a funded
Anthropic account** — without credit the API returns 400 and intake reports
`PROVIDER_QUOTA_EXHAUSTED` rather than a classification.

## Test

```bash
pnpm -r test                                   # Node workspaces
cd apps/api     && uv run pytest -q            # API
cd apps/workers && uv run pytest -q            # workers
```

1496 tests as of Epic 9.18 — api 822, workers 13, shared-types 53,
design-system 149, web 459.

Live verification of the Epic 2 acceptance criterion — real crawl, real model
call, no mocks:

```bash
cd apps/api
uv run python scripts/verify_intake.py              # needs API credit
uv run python scripts/verify_intake.py --crawl-only # fetch half only, free
```

Epic 3 competitor detection — real SerpApi searches and real model calls across
10 businesses. Costs money (~6 searches + ~4 model calls per URL):

```bash
uv run python scripts/verify_competitors.py             # full check
uv run python scripts/verify_competitors.py --serp-only # cheaper, diagnostic
```

Epic 4 scan execution — real prompt generation and real calls to both engines.
Costs money and time; the grounded engine has been measured at up to 216s for a
single prompt:

```bash
uv run python scripts/verify_scan.py                # 6 prompts x 2 engines
uv run python scripts/verify_scan.py --prompts 24   # a full-size scan
```

Epic 5 scoring — runs a real detection + scan, then scores the persisted rows
and re-scores five times to demonstrate determinism. Scoring itself makes no
provider calls; the cost is in producing genuine data to score:

```bash
DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
  uv run python scripts/verify_scoring.py --prompts 3
```

Epic 6 technical audit — real crawls of three sites, then the before/after proof
that a completed audit closes scoring's `NOT_YET_MEASURED` exclusion. Costs
nothing but bandwidth (no model or search calls):

```bash
DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
  uv run python scripts/verify_audit.py
```

Epic 7 report — assembles the narrative report from a real persisted scan,
proves the breakdown re-sums to the stored composite, checks the payload carries
facts only, and creates a genuinely unscoreable scan so the INSUFFICIENT_DATA
report on screen is a real row rather than a fixture. Costs nothing:

```bash
DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
  uv run python scripts/verify_report.py
```

It prints the two report URLs at the end. With the API and web app running, open
`/scans/{scanId}/report` to see them — rendered examples are checked in at
`docs/screenshots/`.

### Local datastores

One command brings up both, with data in gitignored `.devdata/`:

```bash
./infra/db/scripts/dev_cluster.sh up        # Postgres on 55433 + Redis on 6379
./infra/db/scripts/dev_cluster.sh status
./infra/db/scripts/dev_cluster.sh down      # stops Postgres, leaves Redis alone
```

Postgres runs on **55433** so it cannot collide with a system install on 5432.
Redis is only started if nothing already answers on 6379, so a Redis you manage
yourself is never displaced.

The API suite needs a Postgres and a Redis. It defaults to
`postgresql+asyncpg://avp@127.0.0.1:55433/avp_test` and Redis DB **15**
(flushed between tests — DB 15 deliberately, so a local dev session on DB 0 is
never signed out). Override with `AVP_TEST_DATABASE_URL` and
`AVP_TEST_REDIS_URL`.

## Regenerating shared types

The FastAPI schema is the source of truth for every request/response shape.
After changing any endpoint or schema:

```bash
cd apps/api && uv run python scripts/export_openapi.py
cd ../.. && pnpm --filter @avp/shared-types generate
```

## Licence audit

Every dependency must be MIT / Apache-2.0 / BSD per `docs/ip-safety.md` #6.

```bash
cd apps/api && uv run python scripts/license_audit.py
```

Exits non-zero on anything copyleft, source-available, or undeclared.

## Build process

Work proceeds epic by epic, in order. Epic 0 (design system) and Epic 1 (infra)
must both be complete before any screen work starts. Each epic ends with an
explicit pass/fail against its acceptance criteria, recorded in `docs/build-log.md`.
