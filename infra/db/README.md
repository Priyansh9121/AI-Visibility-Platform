# infra/db

Postgres schema and migrations (product-spec.md §5.2).

Migrations are Alembic revisions in `migrations/versions/`. The SQLAlchemy
models they reflect live in `apps/api/src/avp_api/models/` — `migrations/env.py`
puts `apps/api/src` on `sys.path` to bridge the split that §5.2 mandates.

## Running migrations

`DATABASE_URL` is read from the environment; it is never stored in
`alembic.ini`, so a connection string cannot land in version control.

```bash
cd infra/db
export DATABASE_URL="postgresql+asyncpg://avp:avp@127.0.0.1:5432/avp"

alembic upgrade head            # apply everything
alembic downgrade -1            # undo the last revision
alembic current                 # what is applied
alembic history --verbose       # what exists
alembic check                   # do models and migrations agree?
```

Use `-x db_url=...` to override for a one-off run without touching the
environment:

```bash
alembic -x db_url=postgresql://avp@127.0.0.1:55433/scratch upgrade head
```

## Creating a migration

```bash
cd infra/db
alembic revision --autogenerate -m "add citation authority score"
```

**Always read the generated file before committing it.** Autogenerate is a
first draft, not an answer. In particular it cannot see:

- data migrations (it only diffs schema)
- column renames — it emits a drop plus an add, which destroys the data
- anything requiring a specific lock strategy on a large table

## Two things worth knowing

**Everything runs through asyncpg.** Alembic's default template uses a
synchronous driver, which in practice means psycopg2 or psycopg3 — and both are
LGPL-3.0, which the dependency-licensing rule puts on the stop-and-ask list. `env.py`
drives migrations through asyncpg (Apache-2.0) instead, so the project needs no
licence exception for a dependency used only by migrations.

**Enum CHECK constraints are filtered out of autogenerate.** Models use
`Enum(..., native_enum=False, create_constraint=True)`, which emits a CHECK
constraint at DDL time without exposing one in the metadata. Autogenerate
therefore sees each constraint in the database, finds no counterpart in the
models, and proposes dropping it — on every run, for all seventeen of them.
`env.py:include_object` filters exactly those names, so drift detection stays
trustworthy for everything else. `tests/test_migrations.py` in `apps/api`
asserts `alembic check` is clean against a freshly migrated database.

## Why VARCHAR + CHECK instead of native Postgres enums

Native enums validate just as well, but adding a value means `ALTER TYPE`,
which interacts badly with migration tooling and long-running transactions.
Dropping and recreating a CHECK constraint is ordinary, transactional DDL.
