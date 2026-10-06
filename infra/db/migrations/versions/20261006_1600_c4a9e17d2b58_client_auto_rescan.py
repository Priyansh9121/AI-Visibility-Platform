"""client auto rescan

F2 (2026-10-06), automatic monthly rescans. Four columns on `clients`; the model says what each is:

- `auto_rescan`, NOT NULL, default false: the per-client switch. Every client that exists is off.
- `auto_rescan_by_user_id`, nullable, an FK to `users` that SET NULLs, with the usual FK index:
  the user the scan runs as.
- `auto_rescan_attempted_at` and `auto_rescan_skip_reason`, nullable: the claim, and why the last
  attempt started no scan (our own code, never a vendor's words).

No backfill and no data revision: the default is off, the three others are NULL, and every row
that exists is correct as it stands. The old instance that keeps serving while this one migrates
inserts clients without the columns, which the default and the NULLs allow. Nothing here is third-party text,
so the facts-only rule is untouched.

Revision ID: c4a9e17d2b58
Revises: b3f1c8e2a9d4
Created: 2026-10-06 16:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4a9e17d2b58'
down_revision: str | None = 'b3f1c8e2a9d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `clients` fails the deploy instead of queueing every request behind the ALTERs.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'clients',
        sa.Column('auto_rescan', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'clients', sa.Column('auto_rescan_by_user_id', sa.String(length=40), nullable=True)
    )
    op.add_column(
        'clients',
        sa.Column('auto_rescan_attempted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'clients', sa.Column('auto_rescan_skip_reason', sa.String(length=32), nullable=True)
    )
    op.create_index(
        op.f('ix_clients_auto_rescan_by_user_id'),
        'clients',
        ['auto_rescan_by_user_id'],
        unique=False,
    )
    op.create_foreign_key(
        op.f('fk_clients_auto_rescan_by_user_id_users'),
        'clients', 'users', ['auto_rescan_by_user_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(
        op.f('fk_clients_auto_rescan_by_user_id_users'), 'clients', type_='foreignkey'
    )
    op.drop_index(op.f('ix_clients_auto_rescan_by_user_id'), table_name='clients')
    op.drop_column('clients', 'auto_rescan_skip_reason')
    op.drop_column('clients', 'auto_rescan_attempted_at')
    op.drop_column('clients', 'auto_rescan_by_user_id')
    op.drop_column('clients', 'auto_rescan')
