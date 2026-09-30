"""stripe webhook events

`stripe_webhook_events` — one row per handled Stripe event id, inserted with
ON CONFLICT DO NOTHING in the same transaction as the state change, so a
retried or concurrent delivery applies once. Facts only: id, type, Stripe's
`created`, the agency it resolved to, and an outcome code.

`agencies.stripe_event_at` — `event.created` of the newest event applied to the
agency's billing state, so an older event arriving late is ignored. Nullable,
no backfill.

Revision ID: 7c1d4e9a2b53
Revises: 55c6b4395e7d
Created: 2026-09-30 10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7c1d4e9a2b53'
down_revision: str | None = '55c6b4395e7d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `agencies` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # ACCESS EXCLUSIVE on `agencies` once, up front: creating the table's FK
    # first would take SHARE ROW EXCLUSIVE and then have to upgrade the lock.
    op.add_column('agencies', sa.Column('stripe_event_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('stripe_webhook_events',
    sa.Column('id', sa.String(length=255), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('stripe_created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('agency_id', sa.String(length=40), nullable=True),
    sa.Column('outcome', sa.Enum('applied', 'stale', name='stripe_webhook_outcome', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agency_id'], ['agencies.id'], name=op.f('fk_stripe_webhook_events_agency_id_agencies'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stripe_webhook_events'))
    )
    op.create_index(op.f('ix_stripe_webhook_events_agency_id'), 'stripe_webhook_events', ['agency_id'], unique=False)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('agencies', 'stripe_event_at')
    op.drop_index(op.f('ix_stripe_webhook_events_agency_id'), table_name='stripe_webhook_events')
    op.drop_table('stripe_webhook_events')
