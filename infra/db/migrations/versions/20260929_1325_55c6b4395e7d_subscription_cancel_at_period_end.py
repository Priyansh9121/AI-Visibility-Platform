"""subscription cancel at period end

`agencies.subscription_cancel_at_period_end` — whether a subscription that is
still `active` ends at `subscription_current_period_end` instead of renewing.
Stripe's customer portal cancels at the end of the billing period, so after
cancelling, a subscription stays `active` with `cancel_at_period_end: true`
until then, and Settings would otherwise say "Renews" for a plan that won't.
Written by the Stripe webhook only.

`server_default=false` backfills every existing row, since the column is NOT
NULL. A subscription already cancelled in the portal before this ran reads
false until Stripe's next update for it; resending that event from the Stripe
dashboard corrects it.

The default is KEPT, unlike `competitors.corroborated`'s. The container
migrates before it serves, so the old instance keeps serving during a deploy,
and its sign-ups insert agencies without this column. Without a default, those
inserts would break the NOT NULL. The model declares the same server default,
so `alembic check` (which compares server defaults) stays clean.

Revision ID: 55c6b4395e7d
Revises: 1b1265465ea7
Created: 2026-09-29 13:25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '55c6b4395e7d'
down_revision: str | None = '1b1265465ea7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'agencies',
        sa.Column(
            'subscription_cancel_at_period_end',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('agencies', 'subscription_cancel_at_period_end')
