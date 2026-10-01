---
name: backend-code-review
description: Reviews Python changes under apps/api (FastAPI routers, services, models, schemas, tests) against AVP's own rules - agency tenancy, session and transaction scope, problem types, rate limits, N+1, facts-only persistence and log hygiene. Use for pending-change, snippet or file-focused review of apps/api code; not for apps/web, design-system, apps/workers-only changes, or general security audits (use built-in security-review).
---

# AVP backend code review (apps/api)

Review only what the user gave you (staged/working-tree diff, pasted snippet, or named files). Confirm a pattern in the repo before you cite it; if unsure, say "unverified" rather than inventing a file. Read-only: this skill reviews, it does not edit unless asked.

Layer (north-star section 3) and phase (section 6) are the orchestrator's drift check; you just review.

## Steps

1. Identify the mode (diff / snippet / files) and list the files in scope.
2. Run the checklist below on each file; open [references/sqlalchemy-async-postgres.md](references/sqlalchemy-async-postgres.md) whenever a query, session, lock or raw SQL is in scope.
3. Check the verification bar was met (last section).
4. Report in the output format. Every finding: severity, `path:line`, why it matters, a concrete fix.

## Checklist

### Tenancy (critical)
- Every route that takes an `agency_id` in the path calls `assert_own_agency(principal.agency_id, agency_id)` (`apps/api/src/avp_api/deps.py`) before reading any state.
- Every query on an agency-owned table filters by the principal's `agency_id` (for example `Scan.id == scan_id, Scan.agency_id == agency_id` in `routers/scans.py`). A lookup by id alone is an IDOR.
- Another tenant's id answers **404, never 403** (403 confirms the row exists). Check both the code and that a test asserts it: `tests/test_share_link.py:135`, `tests/test_seat_endpoints.py:600`, `tests/test_billing.py:872` assert 404-not-403 (`tests/test_tenant_isolation.py` proves the data is isolated but does not assert 404).
- New authenticated routes take `PrincipalDep` or `require_role(...)`; an unauthenticated route must be deliberate (token routes, health, webhook) and say why.
- Children of a scoped parent are loaded through the scoped parent, not by their own id.

### Session and transaction scope (critical)
- One session per request via `DbDep` (`deps.py`, backed by `db.session_scope`). No ad-hoc `AsyncSession(...)`, no second engine, no module-level session.
- **The router commits, once.** The `DbDep` wrapper only rolls back and closes (its docstring in `db.py:48`, "Commit is explicit in the service layer", is stale). Routers hold the `db.commit()` calls; request-path services add and `flush()` but do not commit. Only code that opens its own `get_sessionmaker()` session commits itself (`services/scan_executor.py`, `scan_runner.py`). Flag: a router write path with no commit; a service-level `commit()` on the request session (for example a commit inside `detection.get_or_create_scan`, called at `routers/scans.py:213`, would release the agency row lock before `submitted_at` is stamped, `routers/scans.py:212-224`, letting two requests at the cap both start); a `DbDep` session handed to a background task (the task opens its own session). `flush()` is for ids and constraint errors inside the transaction.
- No vendor call (httpx to AI engines, Stripe, crawl) inside a transaction unless the lock is deliberate and bounded. The billing checkout holds a per-agency lock across Stripe on purpose, with a timeout; anything else should read, release, call, then write.
- Contended read-modify-write (allowance counts, seats, checkout, share) uses `.with_for_update()` on the parent agency/scan row; any lock a user can wait on sets `SET LOCAL lock_timeout` and maps SQLSTATE `55P03` to a 409 problem (`services/billing.py`). Verify rowcount on conditional updates.
- No blocking calls (sync HTTP, `time.sleep`, CPU-heavy work) inside `async def` paths; use `asyncio.to_thread` or the Celery workers.

### Contracts and errors
- Request/response models derive from `ApiModel` (`schemas/common.py`: Pydantic v2, camelCase aliases, `from_attributes`). Every route requires `response_model=` (the repo pattern is `-> Any` with `response_model=`, `routers/scans.py:247-252`), so an ORM object cannot leak fields. No `RootModel`, no `...` defaults.
- Errors are `ProblemError` subclasses from `errors.py` (RFC 9457 `application/problem+json`, `type` under `/problems/`). Prefer `ProblemError` over `HTTPException`: one existing use in `routers/alerts.py`, and an `HTTPException` with a status missing from the slug table (for example 402 or 503) renders as `/problems/internal-error` (`errors.py:347-352`), no hand-built error JSON, no `detail` that leaks internals or another tenant's data. A new user-visible failure gets a new problem type and a test on its `type` and status.
- Services may raise `ProblemError` subclasses (eight service modules import from `errors.py`); they should not import FastAPI request/response types.
- Use `Annotated[..., Depends(...)]` aliases (`DbDep`, `PrincipalDep`, `SettingsDep`, `RedisDep`). Prefer the existing service module over inline queries in a router when one exists for the table.

### Abuse limits
- Per-IP rate-limit keys come from `rate_limit.client_ip(request, settings)` (`services/rate_limit.py:121`), never `request.client.host` or `X-Forwarded-For`.
- Anything expensive or abusable (auth, scans, reports, LLM or vendor calls) is rate-limited or allowance-checked **before** the expensive work; see `services/rate_limit.py` (`enforce`) and `services/scan_allowance.py`.
- Redis failing is a 503 `/problems/service-unavailable`, not a 500 and not a silent allow (the one documented fail-open is reset-request).
- New limits, quotas and retention choices are product decisions: flag, do not pick numbers.

### Outbound fetches (SSRF)
- SSRF applies here. `services/crawl.py:183` fetches an operator-supplied site's `robots.txt` with `follow_redirects=True`; the only guard is a hostname-shape check (`registrable_domain`, `crawl.py:309-323`), with no resolved-IP check. Flag any new code that fetches a user- or vendor-supplied URL, follows redirects, or accepts an IP/internal host.
- The real fix is a network-level egress guard; resolve-then-fetch is defeated by DNS rebinding and by Chromium's own resolution. See `avp-security-review` (library) for the full note. Recommend the security-reviewer agent for any such diff.

### Queries
- No N+1: loops that call `await db.execute(...)` per item; use `selectinload`/`joinedload` (see `routers/scans.py`) or one `IN`/join query.
- Lists are paginated (`Page` cursor envelope in `schemas/common.py`); no unbounded `.all()` on a growing table.
- No SQL built from strings: raw `text()` only with bound parameters. The one existing `SET LOCAL lock_timeout` f-string interpolates a module constant because `SET` cannot take bind parameters; a user-derived value there is a critical finding.
- Query columns that are filtered or ordered have an index in the same migration; no redundant leftmost-prefix indexes (see `supabase-postgres-best-practices`).

### Schema
- Schema changes only through a model change plus an Alembic revision in `infra/db/migrations/versions/` (never `create_all` in app code, never hand-edited DB). Postgres only: no SQLite branches, no `batch_alter_table`. Defer the procedure to `alembic-migrations` (library) and `supabase-postgres-best-practices`.
- Models use SQLAlchemy 2 `Mapped[...]`; no session work or queries inside model properties.

### Facts-only persistence (critical)
- Nothing third-party is stored or republished: no AI answer text, page HTML, competitor copy, or Stripe payload dumps. Columns hold booleans, counts, positions, URLs, names, ids. A new `Text`/`JSON` column holding vendor content fails review; `tests/test_facts_only.py` must stay green.
- Model-written reasoning about client sites stays out of logs and tables.

### Logging
- `structlog.get_logger(__name__)` with event names and key/value fields (`logger.warning("auth.rate_limited", action=action)`). No `print`; no stdlib `logging` except the existing setup in `main.py:35` and `logs.py:99`.
- `logs.py` redacts only uvicorn access-log paths, not structlog fields. The outright ban on logging emails, tokens and secrets is the protection; do not assume redaction will catch it.
- No email, token, password, session id, reset/share/ticket value, API key or raw request body in a log line or exception message. Log ids and outcomes. New routes that carry a secret in the URL need a `RedactSecretPaths` entry (`logs.py`); prefer POST bodies.
- Check `tests/test_secrets_stay_out_of_logs.py` covers new logging on auth-adjacent paths.

### Tests
- New behaviour has tests in `apps/api/tests/` using httpx `ASGITransport` against the real app, Postgres `avp_test` (port 55433) and Redis db 15 (`tests/conftest.py`). **Never SQLite**, never a mocked session for query logic.
- Tests cover the adversarial cases: other tenant gets 404, wrong role gets the right problem type, invalid input 422, Redis down 503, duplicate/concurrent request, boundary values. Assertions check status **and** problem `type`.
- Tests come from the requirement, not from what the code does. A failing test means question the code first.
- Mock only true external boundaries (vendors, Stripe, Google); never the database.
- Counts are gated, not percentages: no "80% coverage" targets. The API pytest count must meet its floor in `infra/ci/floors.json`, and a floor is bumped in the commit that adds tests.

### Endpoint changes
- Endpoint added, changed or removed: `cd apps/api && uv run python scripts/export_openapi.py`, then `corepack pnpm --filter @avp/shared-types generate`, and `docs/api-contracts.md` updated (scribe). CI fails on an OpenAPI diff.
- Scoring: a formula change bumps `FORMULA_VERSION`; history is never re-scored.

### Code quality (keep short; only flag what hurts)
- Ruff (`ruff check src tests`) and mypy under the ceiling in `floors.json`. `X | Y` unions, `TYPE_CHECKING` for type-only imports.
- Functions doing two jobs, deep nesting, magic numbers, bare `except`/swallowed errors, commented-out code, behaviour-switching boolean params.
- A new runtime dependency or outbound host needs classification in `apps/web/src/components/legal/providers.test.ts`, a mention on `/privacy`, and a pass of `apps/api/scripts/license_audit.py`; re-lock both `apps/api` and `apps/workers` (`uv lock`).

## Verification bar (for pending-change reviews)
Ask for evidence, do not assume. Commands never piped; each writes to a file and its own exit code is read.

`tests/conftest.py:86-87` drops and recreates every table in whatever database `AVP_TEST_DATABASE_URL` names. If it is set, run tests with `env -u AVP_TEST_DATABASE_URL ...` so conftest falls back to `avp_test` on 55433; never read the URL.

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( cd apps/api && uv run ruff check src tests > "$LOG/ruff.log" 2>&1; echo "ruff exit: $?" )
( cd apps/api && rc=0; uv run pytest -q -p no:cacheprovider tests/test_x.py > "$LOG/pytest.log" 2>&1 || rc=$?; echo "pytest exit: $rc" )
# only for a FULL-suite log; a single-file run is not gated by the floor
python3 infra/ci/assert_count.py api-pytest "$LOG/pytest.log"
```

The test-runner agent (`.claude/agents/test-runner.md`) can run full suites and return counts. For auth, sessions, tokens, billing, webhooks, tenancy, logging of identifiers, or fetching a user-supplied URL, recommend the security-reviewer agent as well.

## Output format

```markdown
# Code Review Summary

## Critical (must fix)
### 1. <issue>
FilePath: <path> line <n>
<snippet or pointer>
#### Explanation
<why; cite the AVP rule/file>
#### Suggested fix
<steps, optional code>

## Suggestions
(same shape)

## Nits
(same shape, fix as bullets)

## What's good
- <patterns done right>
```

- Omit empty sections. If nothing is wrong: `## Code Review Summary` / `No issues found.`
- More than 10 items in a section: say "10+" and list the first 10.
- If fixes are needed, end by asking whether to apply them.
- Do not quote secrets, emails or tokens from the code under review; refer to them by file and line.
