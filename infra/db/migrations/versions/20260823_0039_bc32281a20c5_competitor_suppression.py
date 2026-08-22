"""competitor suppression

Records that an operator STRUCK a competitor.

Epic 3 shipped `is_manual_override` and preserved operator additions across
re-detection correctly, but a removal left no row and therefore no record, so
the next detection run reinstated the struck rival. Half of "correcting a set"
silently reverted. Found by running the mechanism against real data rather than
by a test — see build-log Epic 3.6.

A suppressed row is a tombstone: excluded from every read path, kept only so
`persist_detection` knows not to offer that rival again.

Revision ID: bc32281a20c5
Revises: f89ef9eb38c5
Created: 2026-08-23 00:39:29.954188
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'bc32281a20c5'
down_revision: str | None = 'f89ef9eb38c5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default so the ALTER succeeds against existing rows; dropped
    # immediately after, because the model declares no server default and
    # `alembic check` in test_migrations.py fails on the drift.
    op.add_column(
        'competitors',
        sa.Column('is_suppressed', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('competitors', 'is_suppressed', server_default=None)


def downgrade() -> None:
    op.drop_column('competitors', 'is_suppressed')
