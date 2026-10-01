# Changes from ECC `database-migrations`

Source: `skills/database-migrations/SKILL.md` (S:n = line n).

- Removed the Prisma, Drizzle, Kysely, Django and golang-migrate sections (S:128-390) and their
  names in the description (S:3) -> AVP uses Alembic only; other tools' commands (`npx prisma`,
  `npx drizzle-kit`, `kysely`, `manage.py`, `migrate`) would mislead and include npx/install-style steps.
- Replaced the backfill `DO $$ ... COMMIT; ...` block (S:106-125, `COMMIT` at S:123) with a
  Python loop in `op.get_context().autocommit_block()` -> a `COMMIT` inside Alembic's revision
  transaction fails or breaks it; batches now commit individually.
- Added an `IS NOT NULL` guard on the source expression and dropped `FOR UPDATE SKIP LOCKED` (S:118)
  -> without the guard a NULL-producing row loops forever; SKIP LOCKED can end the loop early while
  rows remain. Added a post-loop count check.
- Added `autocommit_block()` for `CREATE INDEX CONCURRENTLY` (S:63-66 only said "special handling") ->
  Alembic wraps revisions in a transaction, which CONCURRENTLY cannot run in. Flagged as unproven in
  this repo (no revision uses it yet).
- Added the `lock_timeout = '5s'` pattern and the Render deploy caution, copied from AVP revisions
  `7c1d4e9a2b53` and `3e8a5b7c9d14` -> turns lock queueing into a failed deploy; noted `SET LOCAL`
  is a no-op inside autocommit.
- Replaced "forward-only, rollbacks use new migrations" (S:23) with: a downgrade for every revision,
  round-tripped on a scratch DB, production undone by a new forward revision -> matches the brief and
  the AVP revisions.
- Kept "schema and data separate", expand-contract, nullable-first, immutability (S:22-37, 392-430),
  shortened; dropped the day-by-day timeline example (S:411-419) as filler.
- Dropped "tested against production-sized data" (S:25, 37) as a bare claim -> replaced by a scratch-DB
  round trip and an explicit "unverified heuristic" for backfill duration.
- Added scratch-DB commands using only `-x db_url=` (never `DATABASE_URL`), `$LOG` redirects and read
  exit codes, no pipes -> house verification rules.
- Added the AVP rules: model and revision in one change, `alembic check`, scoring never re-scored
  (`FORMULA_VERSION`, `scoring.py`), facts-only, migrations run by `docker-entrypoint.sh` before serving,
  enum CHECK constraints, asyncpg-only `env.py`.
- Added a pointer to `supabase-postgres-best-practices` for Postgres specifics.
- Removed "Setting up migration tooling for a new project" from the activation list (S:18) -> tooling exists.
- Removed `metadata.origin` frontmatter; description rewritten in third person with when-not.

## Security-review fixes (2026-10-01)

- M1: stated that `env.py` runs all pending revisions in one transaction; every backfill must use
  `autocommit_block` or ship in a later deploy; `transaction_per_migration=True` proposed as a founder-owned env.py change -> a plain UPDATE would hold ACCESS EXCLUSIVE and roll the DDL back.
- M2: enum values need a hand-written CHECK revision (precedent 4e7d2c91ab05); `test_enum_constraints.py` named -> autogenerate and `alembic check` miss them.
- L3: NOT VALID then VALIDATE for CHECK/NOT NULL -> lock_timeout bounds the wait, not the scan hold.
- L4: session-level `SET lock_timeout`/`RESET` in autocommit blocks; `pg_index.indisvalid` check before retry; snapshot-wait coverage marked unverified.
- L5: removed the "version-table lock serialises replicas" claim (copied from docker-entrypoint.sh:7-8); replica concurrency unproven; "old container keeps serving" marked Render behaviour.
- L6: restored deploy order (backfill in a later deploy than the dual-write code); this corrects the earlier shortening of source S:411-419 / S:394-409.
- L7: autogenerate via `env -u DATABASE_URL`; offline `--sql` cannot render the Python backfill loop (from reading, not run).

## Re-review nits (2026-10-01)

- Index example now SETs and RESETs `lock_timeout` inside the autocommit block -> SET LOCAL is a no-op there.
- NOT VALID and VALIDATE ship in separate deploys -> one deploy is one transaction.
- Invalid-index check before a Render retry is a founder step; revision can drop an `indisvalid = false` index itself -> agents never open a production session.
- Autocommit commit scope described as every earlier pending revision, not "each revision".
- `env -u DATABASE_URL` put directly in the four scratch-DB commands -> env.py falls back to DATABASE_URL.
- Rule 3: backfill always batched, whichever deploy it ships in.
