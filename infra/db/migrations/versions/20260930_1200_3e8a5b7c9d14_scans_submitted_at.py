"""scans submitted_at

`scans.submitted_at` — when the scan was handed to an executor. Nullable, no
default, no backfill: a NULL row is one only competitor detection has opened,
or one that predates this column, and both behave exactly as before (counted
from `created_at`, never reaped while QUEUED).

Revision ID: 3e8a5b7c9d14
Revises: 7c1d4e9a2b53
Created: 2026-09-30 12:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '3e8a5b7c9d14'
down_revision: str | None = '7c1d4e9a2b53'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `scans` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('scans', sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('scans', 'submitted_at')
