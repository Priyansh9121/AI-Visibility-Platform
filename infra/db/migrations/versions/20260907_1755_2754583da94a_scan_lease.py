"""scan lease

One nullable column, `scans.lease_expires_at`: the moment after which the
executor that claimed a scan is presumed gone, unless it has since said
otherwise.

WHY
---
The executor treated duration and identity as one signal. Nothing told
"still genuinely working" from "should be treated as dead" except elapsed time,
so a constant — STALE_AFTER, 900 seconds off `started_at` — stood in for a
signal the system did not have. The API key discipline audit did the
arithmetic: a maximal scan's engine phase alone can legitimately run 976
seconds, so the constant reaped scans that were still running and still
billing, and the next POST started a second full run beside the first.

A lease separates the two. The claim stamps `lease_expires_at`, the running
executor renews it while it works, and the reaper keys off THIS column rather
than off `started_at`. A renewal that matches zero rows tells the executor it
no longer owns the scan, which is the half the reaper never had.

NULL means "not held under a lease": every QUEUED row, every finished row, and
a scan driven directly through `scan_runner.run_scan` (scripts/verify_e2e.py,
scripts/verify_scoring.py), which sets RUNNING itself and holds no lease. The
reaper's predicate is strict — `lease_expires_at < now()` — so NULL never
matches and those direct runs are never reaped mid-flight.

BACKFILL
--------
RUNNING rows at upgrade time get `lease_expires_at = now()`. Their executors
were in-process BackgroundTasks and did not survive the deploy that applied
this migration, so an already-expired lease is the truth about them; it makes
them reapable on the next read, which is what STALE_AFTER used to do for them
fifteen minutes later. Without the backfill the strict predicate would leave
them RUNNING forever.

No index: the reaper filters on `status = 'running'` first, which
`ix_scans_status` covers, and RUNNING rows are a handful at any moment.

DOWNGRADE drops the column. The previous revision's reaper keys off
`started_at`, which this migration does not touch.

Revision ID: 2754583da94a
Revises: 4e7d2c91ab05
Created: 2026-09-07 17:55:53
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2754583da94a'
down_revision: str | None = '4e7d2c91ab05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE scans SET lease_expires_at = now() WHERE status = 'running'")


def downgrade() -> None:
    op.drop_column("scans", "lease_expires_at")
