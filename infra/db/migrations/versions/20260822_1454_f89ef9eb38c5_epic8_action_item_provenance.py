"""epic8 action item provenance

Gives `action_items` an identity. The table shipped in the initial revision with
no unique constraint of any kind, so nothing prevented two rank-1 rows on one
scan and nothing keyed a regenerated fix back to the row it replaces.

`source` + `source_key` reproduce the business key the report client already
mints for every candidate fix (`gap:<dimension>` / `audit:<check_key>`), which
is what makes refresh-in-place possible. Both are NOT NULL rather than one
nullable `check_key`: Postgres treats NULLs as distinct inside a unique
constraint, so a key with a nullable member would silently enforce nothing on
exactly the rows it exists to protect.

`generated_by` records the authoring model, following prompt_sets.generated_by.

Revision ID: f89ef9eb38c5
Revises: 5e96862c19a1
Created: 2026-08-22 14:54:15.310532
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f89ef9eb38c5'
down_revision: str | None = '5e96862c19a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The two NOT NULL columns carry a temporary server_default so the ALTER
    # succeeds against a table that already has rows. The models declare no
    # default, so both are dropped below — leaving them would be drift, and
    # `alembic check` in test_migrations.py fails on it.
    op.add_column(
        'action_items',
        sa.Column(
            'source',
            sa.Enum(
                'gap', 'audit',
                name='action_item_source',
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
            server_default='gap',
        ),
    )
    op.add_column(
        'action_items',
        sa.Column('source_key', sa.String(length=80), nullable=False, server_default=''),
    )
    op.add_column('action_items', sa.Column('generated_by', sa.String(length=120), nullable=True))

    op.alter_column('action_items', 'source', server_default=None)
    op.alter_column('action_items', 'source_key', server_default=None)

    op.create_unique_constraint(
        'uq_action_items_scan_source_key',
        'action_items',
        ['scan_id', 'source', 'source_key'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_action_items_scan_source_key', 'action_items', type_='unique')
    op.drop_column('action_items', 'generated_by')
    op.drop_column('action_items', 'source_key')
    op.drop_column('action_items', 'source')
