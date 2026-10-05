"""subscription trial end

`agencies.subscription_trial_end` — when the subscription's free trial ends, as Stripe
states it in the Subscription's `trial_end`. Written by the Stripe webhook from every
`customer.subscription.*` event, so it mirrors Stripe: it survives a trial that converted
and is NULL for a subscription that never had one. `GET /billing` shows it as `trialEndsAt`
only while `subscription_status` is `trialing`; Settings says "Free trial until <date>,
then <the plan's price> a month" from it. A free trial at checkout is the founder's
decision of 2026-10-04 (`STRIPE_TRIAL_DAYS`, 0 by default).

Nullable, no default, no backfill: every agency that exists has no trial, which is NULL,
and the old instance that keeps serving while this one migrates inserts agencies without
the column. It needs no data revision, and the column is a fact (a timestamp), so the
facts-only rule is untouched.

Revision ID: 7106cadcd0ed
Revises: f45983cfa5e3
Created: 2026-10-04 04:47
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7106cadcd0ed'
down_revision: str | None = 'f45983cfa5e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `agencies` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'agencies',
        sa.Column('subscription_trial_end', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('agencies', 'subscription_trial_end')
