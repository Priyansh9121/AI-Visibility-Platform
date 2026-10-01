# SQLAlchemy 2 async on Postgres (asyncpg) - review rules for apps/api

Scope: session lifecycle, query shape, tenancy scoping, raw SQL, locks. Schema and migration procedure: `alembic-migrations` and `supabase-postgres-best-practices`. Facts below were checked against `apps/api/src/avp_api/` on 2026-10-01.

## Session: one per request, the router commits
- Severity: critical.
- `db.session_scope` yields one `AsyncSession` per request (`async_sessionmaker`, `expire_on_commit=False`, `autoflush=False`, `pool_pre_ping=True`) and rolls back on exception. Routers get it as `DbDep`. It does not commit; its docstring (`db.py:48`) saying commit is in the service layer is stale.
- Convention: routers hold the `db.commit()` calls; request-path services `add()`/`flush()` and never commit. Only a job that opens its own `get_sessionmaker()` session commits itself (`services/scan_executor.py:170-181`, `scan_runner.py`). Never pass `DbDep` into a background task.
- Why it matters: `scan_allowance.assert_can_start` takes the agency row lock and relies on the router's commit to release it (`routers/scans.py:212-224`), after `submitted_at` is stamped. A service that commits earlier releases the lock too soon and two requests at the cap can both start.
- Flag: `AsyncSession(engine)` built by hand; a router write path with no commit; a service committing the request session; reliance on autoflush (off, so a query after an unflushed `add` will not see it: `await session.flush()`).
- `expire_on_commit=False` keeps objects readable after commit but possibly stale; re-select with `.execution_options(populate_existing=True)` after taking a lock.

```python
# Bad: hand-built session, unscoped, no commit
session = AsyncSession(get_engine())
scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
scan.status = ScanStatus.CANCELLED

# Good: service scopes, mutates, flushes; the router commits once
async def cancel_scan(db: AsyncSession, agency_id: str, scan_id: str) -> Scan:
    scan = (await db.execute(
        select(Scan).where(Scan.id == scan_id, Scan.agency_id == agency_id)
    )).scalar_one_or_none()
    if scan is None:
        raise NotFound(detail="No scan with that identifier.")
    scan.status = ScanStatus.CANCELLED
    await db.flush()
    return scan

@router.post("/scans/{scanId}/cancel", response_model=ScanDetailOut)
async def cancel(principal: PrincipalDep, db: DbDep, scan_id: str = Path(alias="scanId")) -> Any:
    scan = await scans_service.cancel_scan(db, principal.agency_id, scan_id)
    await db.commit()
    return scan
```

(Illustrative shape only; `cancel_scan` and that route do not exist in the repo.)

## Tenancy on every query
- Severity: critical. Agency-owned reads and writes filter by `agency_id` from the principal, never from the request body. Missing row and other tenant's row are the same `NotFound` (404). `update`/`delete` statements carry the same predicate and check `rowcount`.
- Soft-deleted rows: `Client.deleted_at.is_(None)` belongs in the predicate where the table has it (`routers/clients.py`).

## No vendor I/O inside a transaction
- Severity: critical unless deliberate. Read what you need, close the window, call the vendor (httpx, Stripe, crawl), then write. The one deliberate exception is checkout: per-agency lock held across Stripe, `lock_timeout` set, `55P03` mapped to 409 (`services/billing.py`). Any new lock across I/O needs the same three things: per-row scope, a timeout, a mapped 409.
- A long-running scan must not hold a connection from the request pool for its duration.

## Locks and concurrency
- Severity: critical on contended write paths.
- Pessimistic: lock the parent row with `.with_for_update()` and re-select with `populate_existing=True`; the model is `services/billing.py:333-338` (`key_share=True`, i.e. FOR NO KEY UPDATE, when no key column is written so child inserts are not blocked). `seats.py:50` and `scan_allowance.py:440` lock without `populate_existing`.
- Open AVP item (not fixed here): `scan_allowance.py:440` re-selects the Agency row already loaded by `deps.py:132-136` without `populate_existing`, so the subscription status used around `:387` can be stale.
- Bound the wait: `await db.execute(text("SET LOCAL lock_timeout = '3s'"))` (SET cannot bind parameters, so only a module constant may be interpolated), catch `DBAPIError`, compare `getattr(exc.orig, "sqlstate", None) == "55P03"`, raise a problem type.
- Idempotency by constraint: `INSERT ... ON CONFLICT DO NOTHING` in the same transaction as the state change (insert at `services/billing.py:659`; ledger model `models/stripe_webhook_event.py`), so a failed handler rolls back its ledger row and the retry is processed.
- Always take locks in a consistent order (agency before its children) to avoid deadlocks.

## Query construction
- Prefer `select()/update()/delete()` expressions. `text()` only with bound params and only for Postgres-specific needs (`SET LOCAL`, advisory locks); string-formatted user input is critical.
- N+1: `selectinload(Parent.children)` for collections, `joinedload` for single many-to-one, or one aggregate query. `await` of a lazy relationship in async raises `MissingGreenlet`; that is a missing eager load, not a reason to lazy-load.
- Pagination: cursor on ULID ids (`WHERE id < cursor ORDER BY id DESC LIMIT n`); no offset on growing tables, no unbounded `.all()`.
- Postgres-native types are fine (JSONB, arrays, partial indexes). There is no SQLite portability requirement; a SQLite branch or fixture is itself a finding.

## Model property rule
- A model `@property` or `__init__` never opens a session or queries; it derives from loaded columns only. Cross-table data is fetched by the service with an explicit load.

## Schema drift
- A changed model without an Alembic revision fails `alembic check`. Tests build schema with `metadata.create_all`, so a model right on its own is necessary but not sufficient; the revision is what production runs.
