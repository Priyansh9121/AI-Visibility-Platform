---
name: alembic-migrations
description: "Use when writing, reviewing or deploying an Alembic revision for AVP's Postgres schema (`infra/db/migrations/versions/`, models in `apps/api/src/avp_api/models/`): adding or dropping columns, indexes or tables, backfilling data, or planning a rollback. Not for query tuning or index design (use supabase-postgres-best-practices), not for Django, Prisma or other tools, and not for running anything against a real database."
---

# Alembic migrations (AVP)

AVP's schema changes are Alembic revisions and nothing else: `infra/db/alembic.ini`,
`infra/db/migrations/versions/`. Models live in `apps/api/src/avp_api/models/`. For
Postgres specifics (index choice, locking, RLS, data types) read the installed
`supabase-postgres-best-practices` skill; this skill covers only how a change is
shaped, written, checked and shipped here.

## Facts about this repo (checked 2026-10-01)

- File names: `YYYYMMDD_HHMM_<rev>_<slug>.py` (`file_template` in `alembic.ini`), e.g.
  `20260930_1200_3e8a5b7c9d14_scans_submitted_at.py`.
- `env.py` drives migrations through asyncpg (no psycopg: LGPL, excluded). The URL
  comes from `-x db_url=...`, else the `DATABASE_URL` environment variable.
- The container runs `alembic upgrade head` in `apps/api/docker-entrypoint.sh` before
  uvicorn starts, on Render. A failed migration exits non-zero and the API never
  serves. Two replicas starting at once is unproven: `docker-entrypoint.sh` claims
  Alembic's version-table lock serialises them, but nothing in this repo verifies it.
- **Transaction boundary:** `env.py` (`begin_transaction` around `run_migrations`) does not
  set `transaction_per_migration`, so ALL pending revisions of one deploy run in ONE
  transaction. Two revisions shipped together share their locks and roll back together:
  an `ADD COLUMN` plus a plain `op.execute(UPDATE ...)` holds ACCESS EXCLUSIVE on the
  table for the whole UPDATE, and a failed backfill rolls the DDL back. (Alembic's
  `autocommit_block` docstring says it unconditionally commits the preceding transaction.)
  Setting `transaction_per_migration=True` would change that; it is an `env.py` change for
  the founder to decide and review, and this skill does not make it.
- Enums are `VARCHAR` plus a CHECK constraint (`native_enum=False, create_constraint=True`),
  so a new enum value is drop-and-recreate of the CHECK, ordinary transactional DDL.
  `env.py:include_object` hides those constraints, and autogenerate does not compare CHECK
  contents anyway: a new enum value yields an EMPTY migration and `alembic check` passes.
  Write the CHECK revision by hand (precedent: `20260907_1540_4e7d2c91ab05_truncated_and_paused_statuses.py`,
  marked HAND-WRITTEN). `apps/api/tests/test_enum_constraints.py` is the test that catches
  a missing one; run it after any enum change.
- Tests build their schema with `metadata.create_all`, so the model must be right on
  its own. `apps/api/tests/test_migrations.py` asserts `alembic check` is clean on a
  freshly migrated database.

## Rules

1. **Model and revision travel together**, in the same commit. Edit the model first,
   then `alembic revision --autogenerate -m "<slug>"`, then read and hand-edit the
   file. Run autogenerate as `env -u DATABASE_URL ../../apps/api/.venv/bin/alembic -x db_url=<scratch URL> revision --autogenerate ...`
   from `infra/db`: `env.py` falls back to `DATABASE_URL` (even after a mistyped `-x` key),
   so unset it. Autogenerate cannot see data migrations, enum CHECK changes, turns a rename into drop plus add
   (data loss), and knows nothing about lock strategy.
2. **Every revision has a real `downgrade()`.** Write it, and round-trip it on a
   scratch DB (below). If a step is truly irreversible (dropped data), say so in the
   docstring and raise from `downgrade()` rather than leaving it empty.
3. **Schema and data are separate revisions, and every backfill uses `autocommit_block`**
   (next sections) **or ships in a later deploy, and is always batched whichever deploy it ships in.** Separate files are not enough: all
   pending revisions share one transaction (see the facts above), so a plain `UPDATE`
   in its own file still runs inside the DDL's transaction and lock.
4. **Expand, then contract.** Never rename or drop in one step. Expand (add nullable
   column or table), deploy code that writes both, backfill in a LATER deploy than that
   code (rows the old container inserts in between stay NULL until the backfill runs
   after it is gone), deploy code that reads the new one, and only then drop the old in a later revision. Drop a column only after
   a deploy that no longer references it has shipped.
5. **Nullable first.** Add columns as `nullable=True` with no default, as
   `scans.submitted_at` was. Tighten to NOT NULL in a later revision after the
   backfill is verified. `NOT NULL DEFAULT <constant>` is fine on current Postgres
   (no rewrite); a volatile default or a NOT NULL without a default is not.
   `SET NOT NULL` and re-adding a CHECK scan the whole table under ACCESS EXCLUSIVE;
   `lock_timeout` bounds the wait, not the hold. Add the CHECK as `NOT VALID`, then
   `VALIDATE CONSTRAINT` (weaker lock) in a later deploy (both in one deploy share one transaction under this `env.py`); for NOT NULL, validate a
   `CHECK (col IS NOT NULL)` first, then `SET NOT NULL`. Details: `supabase-postgres-best-practices`.
6. **Once a revision has run anywhere shared, never edit it.** Write a new one.
7. **Scoring never re-scores history.** No migration rewrites a stored score, ratio or
   `formula_version`. A formula change bumps `FORMULA_VERSION` in
   `apps/api/src/avp_api/services/scoring.py` (see `docs/scoring-spec.md`), and old
   rows keep the version they were scored under. A backfill may fill a new fact
   column; it must not recompute an old score.
8. **Facts only.** No column stores third-party raw text (AI answers, page HTML,
   competitor copy). `apps/api/tests/test_facts_only.py` enforces the schema side.
9. **Production is forward-only in practice.** A written `downgrade()` is for dev and
   scratch DBs. To undo something that shipped, add a new forward revision.

## The lock-timeout pattern

Every DDL revision starts with a short lock budget, as the recent revisions do:

```python
def upgrade() -> None:
    # A long transaction on `scans` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('scans', sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('scans', 'submitted_at')
```

**Deploy caution.** An `ALTER` waits for the lock and, while it waits, every other
query on that table queues behind it. `lock_timeout` turns that pile-up into a failed
statement. Because the container runs migrations before serving, the failure is a
failed deploy on Render, not an outage of the running version: the new container
exits and the old one keeps serving. Retry the deploy when the long transaction is
gone. "The old container keeps serving" is Render's behaviour; it is not verifiable from
this repo. `SET LOCAL` lasts only for the current transaction, so it does nothing inside
an autocommit block (next section).

When one revision touches two tables, take the stronger lock first (see the
ACCESS EXCLUSIVE comment in `20260930_1000_7c1d4e9a2b53_stripe_webhook_events.py`).

## Indexes on existing tables: `CREATE INDEX CONCURRENTLY`

`CONCURRENTLY` cannot run inside a transaction, and Alembic runs every pending revision of a deploy in one. Use `autocommit_block()`, in a revision that contains only the index:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.create_index(
            op.f('ix_scans_submitted_at'), 'scans', ['submitted_at'],
            postgresql_concurrently=True, if_not_exists=True,
        )
        op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            op.f('ix_scans_submitted_at'), table_name='scans',
            postgresql_concurrently=True, if_exists=True,
        )
```

- The block commits everything earlier in the deploy (every preceding pending revision, not just this one), so a revision that mixes
  DDL and a concurrent index can half-apply. Keep the index alone.
- A killed or failed `CONCURRENTLY` build leaves an INVALID index. `if_not_exists` would skip
  it and Alembic would stamp the revision as done. Checking `pg_index.indisvalid` before a
  Render retry is a founder step: agents never open a production session. Safer: have the
  revision itself drop any `indisvalid = false` index of that name (concurrently, in the
  same autocommit block) before creating it.
- Inside the block `SET LOCAL` does nothing. Use a session-level `SET lock_timeout = '5s'`
  as the first statement in the block and `RESET lock_timeout` at the end. Whether
  `lock_timeout` covers the wait for older snapshots during `CONCURRENTLY` is unverified.
- The index name in the model (`Index(...)` or `index=True`) must match the revision,
  or `alembic check` reports drift.
- Indexes on a table created in the same revision need none of this.
- No existing AVP revision uses `autocommit_block()` yet; prove the first one on a
  scratch DB before relying on it.

## Backfills: batched, outside the DDL transaction

One `UPDATE` over a large table holds row locks for the whole statement and
produces one huge transaction. Do not use `COMMIT` inside a `DO $$` block or a
plain `op.execute` in a revision: the revision's transaction is owned by Alembic (and is shared with
every other pending revision) and the statement fails or breaks it. Batch from Python inside an autocommit block,
so each batch commits on its own:

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        while True:
            done = conn.execute(sa.text(
                "UPDATE scans SET new_col = <fact expression> "
                "WHERE id IN (SELECT id FROM scans WHERE new_col IS NULL "
                "AND <fact expression> IS NOT NULL ORDER BY id LIMIT 5000)"
            )).rowcount
            if done == 0:
                break
```

- The `IS NOT NULL` guard on the source expression matters: rows that stay NULL would
  loop forever.
- The backfill must be idempotent (safe to re-run) and use only stored facts.
  Never recompute a score (rule 7) and never fetch anything.
- After the loop, verify on the scratch DB with a `SELECT count(*) ... WHERE new_col IS NULL`.
- `downgrade()` for a pure backfill is usually a no-op with a comment saying why; the
  column's own revision drops the data.
- Migrations run before the API serves, so a long backfill delays the deploy. Unverified
  heuristic: if a backfill would take more than a few minutes, run it as a one-off
  script after the DDL revision ships instead, and say so in the build log.

## Checklist before handing back

- [ ] Model changed and revision added, same change.
- [ ] `upgrade()` and `downgrade()` both written; `lock_timeout` on DDL.
- [ ] New columns nullable (or constant default); NOT NULL is a later revision.
- [ ] Index on an existing table is concurrent, in its own revision.
- [ ] Backfill is its own revision, batched, idempotent, no score rewritten.
- [ ] Scratch-DB run below passes.
- [ ] Endpoint shape changed too? Regenerate the OpenAPI contract (`CLAUDE.md`).
- [ ] `docs/build-log.md` entry says what must be set on Render (usually nothing).

## Verifying (scratch DB only)

Never migrate a DB chosen by `DATABASE_URL`, never touch production (Render, Supabase).
Use a scratch database on the local Postgres at port 55433, selected only with
`-x db_url=`. Names below are placeholders; `avp_dev` and `avp_test` are the
developer's own databases, so use a separate scratch one.

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
SCRATCH=avp_migration_scratch
URL="postgresql://avp@127.0.0.1:55433/$SCRATCH"
# Create $SCRATCH first (empty database); ask the founder if you cannot.
cd infra/db
rc=0; env -u DATABASE_URL ../../apps/api/.venv/bin/alembic -x db_url="$URL" upgrade head > "$LOG/up.log" 2>&1 || rc=$?; echo "upgrade rc=$rc"
rc=0; env -u DATABASE_URL ../../apps/api/.venv/bin/alembic -x db_url="$URL" check > "$LOG/check.log" 2>&1 || rc=$?; echo "check rc=$rc"
rc=0; env -u DATABASE_URL ../../apps/api/.venv/bin/alembic -x db_url="$URL" downgrade -1 > "$LOG/down.log" 2>&1 || rc=$?; echo "downgrade rc=$rc"
rc=0; env -u DATABASE_URL ../../apps/api/.venv/bin/alembic -x db_url="$URL" upgrade head > "$LOG/up2.log" 2>&1 || rc=$?; echo "re-upgrade rc=$rc"
```

Read each log file; all four return codes must be 0. `alembic check` failing means
the model and revision disagree. For a reviewable SQL diff without a database, use
offline mode (`alembic -x db_url=... upgrade <from>:<to> --sql`); from reading, not run:
offline mode cannot render the Python backfill loop, so review that by reading. Run
`env -u DATABASE_URL` in front of every alembic call above if the variable is set.

Then run the API schema tests the normal way (`apps/api/tests/test_migrations.py`,
Postgres `avp_test` on 55433), redirected to a file with the exit code read from the
command itself, and the count gates in `infra/ci/floors.json` via
`infra/ci/assert_count.py`. Never pipe test output. Delegate full runs to the
test-runner agent.

The developer's own dev DB is upgraded separately with its URL passed the same way
(`-x db_url=postgresql://avp@127.0.0.1:55433/avp_dev`); note the `.env` URL points at
port 5432, which is not where the dev DB lives.

## Related

- `supabase-postgres-best-practices` (installed): index types, locking, schema design.
- `architecture-decision-records` (installed): record a contract or retention decision.
- `verification-before-completion` (installed): evidence before any "done".
- Product decisions (retention, limits, what a column means to users) are the
  founder's: stop and ask rather than encode a choice in a revision.
