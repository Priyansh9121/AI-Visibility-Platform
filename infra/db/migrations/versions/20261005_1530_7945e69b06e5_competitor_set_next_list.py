"""competitor set next list

`competitor_sets` gains a second owner. A set belonged to a scan (`scan_id`, unique, NOT NULL);
it now belongs to a scan OR is a client's NEXT LIST (`client_id` set, `scan_id` NULL), the list
a save and the button's detection write and the next scan adopts. A scan's own set never changes
once the scan has started (launch checklist 3.78, 3.79; the founder's decision of 2026-10-05).

The changes (the model says why each is there):

- `client_id`, nullable, an FK to `clients` that cascades, with the usual FK index.
- `scan_id` becomes nullable. `uq_competitor_sets_scan_id` stays: Postgres lets many NULLs
  through a unique index, so any number of next lists coexist with it, and only the partial
  index below says there is at most one per client.
- `ck_competitor_sets_scan_or_client`: exactly one of the two is set. Every row that exists
  has a scan and no client, so it holds for each of them and nothing needs backfilling. And
  `uq_competitor_sets_next_list_per_client`, unique on `client_id` where `scan_id IS NULL`.

The old instance that keeps serving while this one migrates inserts sets with a scan and no
client, which both constraints allow, and its lookups join `scans`, which leaves a next list
out, so it never sees one.

`downgrade()` deletes the next lists: a next list belongs to no scan, so the old shape cannot
hold it. That loses what was saved for later scans and nothing a scan shows (every scan's own set
is kept), and the client's next scan carries its latest set forward as it did before.

Revision ID: 7945e69b06e5
Revises: 7106cadcd0ed
Created: 2026-10-05 15:30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7945e69b06e5'
down_revision: str | None = '7106cadcd0ed'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `competitor_sets` fails the deploy instead of queueing every request behind the ALTERs.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('competitor_sets', sa.Column('client_id', sa.String(length=40), nullable=True))
    op.create_index(
        op.f('ix_competitor_sets_client_id'), 'competitor_sets', ['client_id'], unique=False
    )
    op.create_foreign_key(
        op.f('fk_competitor_sets_client_id_clients'),
        'competitor_sets', 'clients', ['client_id'], ['id'], ondelete='CASCADE',
    )
    op.alter_column(
        'competitor_sets', 'scan_id', existing_type=sa.String(length=40), nullable=True
    )
    op.create_check_constraint(
        op.f('ck_competitor_sets_scan_or_client'),
        'competitor_sets',
        'num_nonnulls(scan_id, client_id) = 1',
    )
    op.create_index(
        'uq_competitor_sets_next_list_per_client',
        'competitor_sets',
        ['client_id'],
        unique=True,
        postgresql_where=sa.text('scan_id IS NULL'),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index(
        'uq_competitor_sets_next_list_per_client',
        table_name='competitor_sets',
        postgresql_where=sa.text('scan_id IS NULL'),
    )
    op.drop_constraint(op.f('ck_competitor_sets_scan_or_client'), 'competitor_sets', type_='check')
    # A next list has no scan, so the old NOT NULL cannot hold it. Its rivals go with it
    # (`competitors.competitor_set_id` cascades); every scan's own set is untouched.
    op.execute("DELETE FROM competitor_sets WHERE scan_id IS NULL")
    op.alter_column(
        'competitor_sets', 'scan_id', existing_type=sa.String(length=40), nullable=False
    )
    op.drop_constraint(
        op.f('fk_competitor_sets_client_id_clients'), 'competitor_sets', type_='foreignkey'
    )
    op.drop_index(op.f('ix_competitor_sets_client_id'), table_name='competitor_sets')
    op.drop_column('competitor_sets', 'client_id')
