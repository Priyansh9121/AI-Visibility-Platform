"""at most one open scan per client

Closes the double-spend race on POST /clients/{clientId}/scans.

`get_or_create_scan` reuses an open (queued/running) scan rather than starting
a second, but SELECT-then-INSERT is a check-then-act: two concurrent requests
can both look, both find nothing, and both insert — two scans, and two scans'
worth of paid model calls. Epic 9.5 narrowed the window from ~303s to
milliseconds by committing the row early. Only the database can close it.

Partial, because the invariant concerns OPEN scans only — a client accumulates
any number of finished ones. `scans.status` is VARCHAR-backed with a CHECK
constraint rather than a native enum (models/base.py, `native_enum=False`), so
the predicate is a plain string comparison and needs no cast.

See build-log Epic 9.6.

Revision ID: a41d9c7e5b02
Revises: bc32281a20c5
Created: 2026-08-25 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a41d9c7e5b02'
down_revision: str | None = 'bc32281a20c5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN = "status IN ('queued', 'running')"


def upgrade() -> None:
    # Resolve any pre-existing violation BEFORE creating the index, so this
    # migration cannot fail against real data.
    #
    # avp_dev was checked at authoring time and had none — zero clients with
    # more than one open scan. This runs anyway, because a migration has to be
    # safe against every database it will ever meet, not the one in front of
    # its author. Duplicates are reachable: every request that raced before
    # this index existed could produce a pair.
    #
    # The NEWEST open scan per client survives; older ones are failed. `id` is
    # a ULID, so ordering by it is creation order. They are failed rather than
    # deleted — a scan that consumed paid model calls is a record, and its
    # engine_results still reference it. EXECUTOR_SUPERSEDED says exactly what
    # happened, distinct from EXECUTOR_LOST (Epic 9.5's reaper: the process
    # went away) and ALL_ENGINE_CALLS_FAILED (the pipeline ran and failed).
    op.execute(
        f"""
        UPDATE scans SET
            status = 'failed',
            error_code = 'EXECUTOR_SUPERSEDED',
            error_detail = 'Superseded by a newer open scan for the same client.',
            finished_at = COALESCE(finished_at, now())
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY client_id ORDER BY id DESC
                ) AS rn
                FROM scans WHERE {OPEN}
            ) ranked WHERE ranked.rn > 1
        )
        """
    )

    op.create_index(
        'uq_scans_one_open_per_client',
        'scans',
        ['client_id'],
        unique=True,
        postgresql_where=sa.text(OPEN),
    )


def downgrade() -> None:
    # Only the index is reversible. The rows failed above are not restored:
    # re-opening them would recreate the very duplicates the index forbids, so
    # a downgrade-then-upgrade would fail. Losing the constraint is reversible;
    # inventing back a second open scan is not.
    op.drop_index('uq_scans_one_open_per_client', table_name='scans')
