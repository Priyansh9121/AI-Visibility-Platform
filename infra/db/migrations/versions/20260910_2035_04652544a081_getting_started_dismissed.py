"""getting started dismissed

One nullable timestamp on `agencies` — Epic 19, the getting-started checklist.

THE ONLY STORED FACT ABOUT THE CHECKLIST
----------------------------------------
The dashboard's checklist says whether an agency has a client, a scan, a
score, a teammate and a shared report. All five are DERIVED on read from rows
that already exist; nothing here records a step. What cannot be derived is
that an operator chose to close the card — that is a decision, not a state the
data is in — so it is the one thing written down.

A timestamp rather than a boolean, matching `invitations.accepted_at` and
`revoked_at`: "when" is one column and "whether" comes free. Per agency and
not per user, because the checklist describes the agency's account and the
tenancy model already makes the agency the unit everything else hangs off.

NULL FOR EVERY EXISTING ROW, AND THAT IS CORRECT. No agency has dismissed a
card that did not exist. An agency that has already done all five steps will
see the checklist once, in its completed state, and close it; that is the
milestone being acknowledged rather than a backfill being skipped.


Revision ID: 04652544a081
Revises: 59c47a7a44f6
Created: 2026-09-10 20:35
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '04652544a081'
down_revision: str | None = '59c47a7a44f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agencies', sa.Column('getting_started_dismissed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('agencies', 'getting_started_dismissed_at')
