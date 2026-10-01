# Changes from upstream

Paths are in `langflow-main/.agents/skills/backend-code-review/`.

- Retargeted scope from `src/backend/` to `apps/api` (SKILL.md:3,10-20) -> wrong paths would never trigger or would review the wrong tree.
- Rewrote `references/sqlalchemy-rule.md` as `references/sqlalchemy-async-postgres.md`: SQLModel + `session_scope()`/`injectable_session_scope` (sqlalchemy-rule.md:6-23) -> AVP's `DbDep` session with the router committing once and services flushing (`db.py`, `deps.py`; corrected in the review fixes below).
- Replaced `user_id` scoping (sqlalchemy-rule.md:54-73; SKILL.md:52) -> `agency_id` scoping with `assert_own_agency` and 404-not-403 (`deps.py:147`).
- Dropped SQLite/dialect portability and `batch_alter_table` rules (db-schema-rule.md:111-174, sqlalchemy-rule.md:78, 185) -> AVP is Postgres-only; SQLite branches are now a review finding. Schema procedure deferred to Alembic + supabase-postgres-best-practices.
- Kept and adapted optimistic/pessimistic locking and short-transaction rules (sqlalchemy-rule.md:102-222) -> `FOR UPDATE`, `key_share`, `SET LOCAL lock_timeout` + SQLSTATE 55P03 -> 409, `ON CONFLICT DO NOTHING` idempotency, vendor I/O outside transactions, from `services/billing.py`, `seats.py`, `scan_allowance.py`, `stripe_webhook_event.py`.
- Replaced `lfx.log.logger` async logger and the approved-identifier list (SKILL.md:125-131; architecture-rule.md:190-210) -> `structlog` events and `logs.py` redaction, no email/token/session value in logs (risk: the source's logger does not exist here).
- Replaced "Coverage target: 80% (minimum 75%)" (SKILL.md:105) -> AVP count floors in `infra/ci/floors.json` via `assert_count.py`; percentage targets removed.
- Replaced `make format_backend/lint/unit_tests` (SKILL.md:133-138) -> AVP bar commands with LOG redirect, own exit code, never piped; removed the unsourced "10x cheaper" claim (SKILL.md:137), the "statistically higher defect rates" claim (SKILL.md:91) and file-length/5-function limits (SKILL.md:88-95), which were unsourced Langflow conventions.
- Removed Langflow-only rules: ServiceFactory/ServiceManager (repositories-rule.md:50-60), components-as-leaf-nodes (architecture-rule.md:156-160), `helpers/` purity (architecture-rule.md:81-114), Google docstring enforcement (architecture-rule.md:162-188), Python 3.10-3.13 and `CurrentActiveUser`/`DbSession` aliases (SKILL.md:68,78) -> do not exist in AVP.
- Folded the route-thin / layer-direction rules (architecture-rule.md:14-154) into two bullets of SKILL.md; dropped `architecture-rule.md`, `db-schema-rule.md`, `repositories-rule.md` as separate files (their Langflow content no longer applies).
- Replaced SQLModel Base/Table/Create/Read/Update pattern (db-schema-rule.md:176-261) -> Pydantic v2 `ApiModel` (camelCase, `from_attributes`) and SQLAlchemy 2 `Mapped[...]`, require `response_model=` on routes (`routers/scans.py:247-252`).
- Added AVP-only checks not in upstream: problem types (`errors.py`), rate limits before expensive work (`services/rate_limit.py`), facts-only persistence (`test_facts_only.py`), OpenAPI/shared-types/api-contracts regeneration, ASGITransport tests on `avp_test` (never SQLite), dependency/host classification, `FORMULA_VERSION`.
- Removed the Langflow component-specific command-injection and pickle items (SKILL.md:47-49); SSRF was wrongly removed and is restored as an "Outbound fetches" check (see review fixes). General security audit is left to the built-in `security-review` and the security-reviewer agent.
- Output format: removed emoji headings; otherwise kept the upstream Template A/B structure.
- No hooks, installers, network calls, telemetry or settings edits existed in the source; none added.

## Security-review fixes (2026-10-01)
- Commit ownership corrected: router commits once, services flush, only session-owning jobs commit; stale `db.py:48` docstring noted; "Good" example rewritten (was a service-level commit and an invented `label` field) -> a service commit releases the agency lock before `submitted_at` is stamped (`routers/scans.py:212-224`).
- Added "Outbound fetches (SSRF)" and a security-reviewer trigger for fetching user-supplied URLs (`services/crawl.py:183,309-323`).
- Lock example now cites `services/billing.py:333-338` (has `populate_existing`); `seats.py`/`scan_allowance.py` no longer cited as the pattern; stale-agency open item recorded.
- `response_model=` required (`routers/scans.py:247-252`) instead of "never return an ORM object".
- 404-not-403 tests cited: `test_share_link.py:135`, `test_seat_endpoints.py:600`, `test_billing.py:872`.
- Rate-limit keys must come from `rate_limit.client_ip()` (`rate_limit.py:121`).
- Services may raise `ProblemError`; `HTTPException` with unmapped statuses renders as `/problems/internal-error` (`errors.py:347-352`).
- stdlib-logging ban exempts `main.py:35` and `logs.py:99`; `logs.py` redacts access-log paths only, so the ban is the protection.
- ON CONFLICT insert cited at `services/billing.py:659`.
- Single-file pytest runs are not gated by the full-suite floor; test DB check added (`conftest.py:86-87` drops and recreates tables).
