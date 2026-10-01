# AVP — house rules for Claude

AI Visibility & Competitive Intelligence Platform: a paid SaaS for agencies,
deployed on Render. Each rule names the file it comes from. Where a rule and a
brief disagree, stop and ask.

## Before any brief
- `docs/north-star.md` is **normative** (`docs/north-star.md:7`). Read it first.
- Every brief answers the §8 drift check in one line before scoping: *which
  layer(s) (§3) does this touch, and which lifecycle phase (§6) does it serve?*
  If it spans several without a reason, split it (`docs/north-star.md:902-927`).
- Product and core loop: `docs/product-spec.md`. Scoring: `docs/scoring-spec.md`.
- Current state and open items: the end of `docs/build-log.md` ("Found, not
  fixed" sections) and `docs/runbook.md`.

## What must never ship
- **Facts only.** No third-party raw text is stored or republished: no AI answer
  text, page HTML or competitor copy; only booleans, counts, positions, URLs,
  names (`apps/api/tests/test_facts_only.py:1-11`). Marketing and UI copy never
  invents numbers, testimonials, logos or customers.
- **Design-system only.** UI uses `packages/design-system` tokens and
  components. The preset replaces Tailwind's theme, so stock colours and
  off-scale spacing do not compile (`packages/design-system/src/tailwind-preset.ts:1-17`).
  The report stays paper in both themes (`packages/design-system/src/tokens/reportIsolation.test.ts`).
- **Dependency licences.** No copyleft, source-available or undeclared
  licence. Python deps (API and workers environments) are checked by
  `apps/api/scripts/license_audit.py`, JS deps by
  `apps/api/scripts/js_license_audit.py` over `pnpm licenses list`; both run in
  CI on one policy (`ALLOWED`/`BLOCKING` in `license_audit.py`). Open licence
  questions live in `infra/ci/licence-pending-decisions.json` until the founder
  decides; it is not an allow-list. psycopg, vendor LLM SDKs other than
  anthropic, and PDF libraries are deliberately absent.
- **Every new outbound host or runtime dependency** is classified in
  `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`.
- **No secret, token or email in a log.** `apps/api/src/avp_api/logs.py`
  redacts; `apps/api/tests/test_secrets_stay_out_of_logs.py` checks.
- **Scoring never re-scores history**; a formula change bumps
  `FORMULA_VERSION` (`docs/scoring-spec.md`, rule 5).
- **Honest crawling.** AVPScanBot identifies itself and obeys robots.txt
  (`apps/api/src/avp_api/services/crawl.py:142`, `/crawler` page). No stealth,
  spoofed user agents or bot-detection evasion, ever.
- Token routes (`[token]` pages) send `Referrer-Policy: no-referrer`
  (`apps/web/src/app/referrerPolicy.test.ts`).

## The verification bar (every change)
- **Never pipe test, lint or typecheck output.** Redirect to a file, read the
  command's own exit code, then read the file (`infra/ci/README.md`, rule 1;
  north-star §4.3).
- **Counts, not only exit codes.** Every suite must meet its floor and mypy must
  stay at or under its ceiling in `infra/ci/floors.json`, checked with
  `python3 infra/ci/assert_count.py <key> <log>`. Bump a floor in the commit
  that adds tests; lowering one needs a reason in `build-log.md`.
- The bar: API pytest, workers pytest, web vitest, design-system vitest,
  shared-types `node --test`, `ruff check src tests`, mypy (ceiling), all
  typechecks, `uv lock --check` in **both** `apps/api` and `apps/workers`, and
  an OpenAPI regeneration diff (`.github/workflows/ci.yml`).
- API tests need Postgres `avp_test` on **55433** and Redis db 15
  (`apps/api/tests/conftest.py:7-8`). The dev DB is `avp_dev` on 55433, not the
  `.env` port 5432.
- `corepack pnpm` only. After any `pyproject.toml` change, re-lock both uv
  projects.

## Every change also needs
- **Endpoint added, changed or removed:** update `docs/api-contracts.md`, then
  `cd apps/api && uv run python scripts/export_openapi.py` and
  `corepack pnpm --filter @avp/shared-types generate` (`docs/api-contracts.md:3-11`).
- **Schema change:** model + Alembic revision in `infra/db/migrations/versions/`,
  `alembic upgrade head` and `alembic check` pass.
- **A `docs/build-log.md` entry** ending in a `## Verified` block with counts
  before → after, the negative control, and the rest of the bar
  (e.g. `docs/build-log.md:19708-19757`). Say what must be set on Render.

## Git and deploys
- **Render auto-deploys on push to `main`, before CI finishes**
  (`docs/build-log.md:18304`). **Never push to `main` without the founder's
  explicit OK.** Never push anything unless asked.
- Commit only when asked, and only a green tree. One logical item per commit.
  Stage explicit paths; never `git add -A` or `git add .`.
- Never `git stash pop`, `reset --hard` or `clean` without asking.
- Commit messages are plain sentences describing the change (see `git log`).

## Secrets
- Never print a secret value. Do not `cat`, `echo` or dump `apps/api/.env`,
  `.claude/settings.local.json`, `.mcp.json`, `dump.rdb` or `.devdata/`. Read
  them by variable name only, or masked.
- `apps/api/.env` holds live provider keys and the Stripe test key. Stripe live
  keys are the founder's to set on Render, never in the repo.

## Delegation (cost tiers, `.claude/agents/`)
The Opus orchestrator plans, routes and decides. It does not read large files
or run suites itself when an agent can.
- **explorer** (haiku): any "where / how / what calls" question, and mapping a
  change before it is built.
- **implementer** (sonnet): one scoped edit with its tests. Never commits.
- **test-runner** (haiku): suites, lint, typecheck and count gates; returns
  counts and failures only.
- **security-reviewer** (opus): any diff touching auth, sessions, tokens,
  passwords, rate limits, billing, webhooks, tenancy or logging of identifiers.
- **ui-reviewer** (sonnet): any `apps/web` or design-system diff a user sees.
- **scribe** (haiku): build-log entries, `api-contracts.md`, runbook steps,
  from facts it is given.
Pick the cheapest agent that fits. Stop and ask the founder when an item needs
a product decision (a price, a limit, retention, user-visible behaviour).
