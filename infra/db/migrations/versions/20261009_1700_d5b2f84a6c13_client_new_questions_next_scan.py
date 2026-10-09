"""client new questions next scan

F6 (2026-10-09). One column on `clients`: `new_questions_next_scan`, NOT NULL, default false. The
agency sets it to have the client's NEXT scan write new questions instead of copying the last
all-generated set; that scan clears it. Every client that exists is off, so there is no backfill
and no data revision. The old instance that keeps serving while this one migrates inserts clients
without the column, which the default allows. Nothing here is third-party text, so the facts-only
rule is untouched.

Revision ID: d5b2f84a6c13
Revises: 89403ce44352
Created: 2026-10-09 17:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd5b2f84a6c13'
down_revision: str | None = '89403ce44352'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `clients` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'clients',
        sa.Column(
            'new_questions_next_scan', sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('clients', 'new_questions_next_scan')
