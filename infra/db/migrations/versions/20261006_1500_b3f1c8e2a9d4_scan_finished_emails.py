"""scan finished emails

`users.scan_emails` (NOT NULL, default true): whether the user is emailed when a scan they
started finishes. `scans.notified_at` (nullable, no backfill): when that email was claimed.
Both belong to F1 and do nothing unless `EMAIL_SCAN_NOTIFICATIONS` is on.

The default on `scan_emails` is KEPT: the old instance of a deploy inserts users without the
column, and the model declares the same server default so `alembic check` stays clean.

Revision ID: b3f1c8e2a9d4
Revises: 7945e69b06e5
Created: 2026-10-06 15:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b3f1c8e2a9d4'
down_revision: str | None = '7945e69b06e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on these tables fails the deploy instead of queueing every request behind the ALTERs.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'users',
        sa.Column('scan_emails', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column('scans', sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('scans', 'notified_at')
    op.drop_column('users', 'scan_emails')
