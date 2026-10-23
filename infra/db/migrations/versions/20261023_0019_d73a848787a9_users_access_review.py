"""users google linked at, access review done at

A6 part 4 (launch checklist 3.123): the one-time "Check who has access" review that Settings > Team
shows a seat holder whose Google sign-in replaced a password. Two columns on `users`, both
TIMESTAMPTZ and nullable with no default:

* `google_linked_at`: when a Google link REPLACED a password on that row. It is written by
  `google_oauth._link` in the link's own transaction, and only then: a Google-first account and an
  invited seat that joined through Google had no password to replace, so they have nothing to
  review. The review lists the other seat holders whose rows were created before it, because they had
  access before this person first signed in with Google.
* `access_review_done_at`: when that holder pressed "Keep these people". The review is shown while it
  is NULL and `google_linked_at` is not.

NULL means no review, and that is every user when this runs, and any user the old instance of the
deploy inserts while it is still serving (it does not know the columns). There is no backfill and no
data revision: an account linked before this deploys has no `google_linked_at`, because the time of
that link was never recorded, so it gets no review. Adding nullable columns with no default does not
rewrite the table. The values are timestamps, so the facts-only rule is untouched, and no score,
ratio or `formula_version` is read or rewritten.

`downgrade()` drops both columns and with them every review written. It is for a development or
scratch database: production is forward-only. Once this revision has run on a database, an image from
before it cannot be redeployed, because its `alembic upgrade head` does not know the revision the
database is at (Alembic cannot locate it) and exits, so the deploy fails while the running version
keeps serving. The way back from this change is a forward revert that keeps this revision and the
model columns: code that stops writing or reading them simply shows no review.

Revision ID: d73a848787a9
Revises: a3480a6d70d1
Created: 2026-10-23 00:19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd73a848787a9'
down_revision: str | None = 'a3480a6d70d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `users` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('users', sa.Column('google_linked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'users', sa.Column('access_review_done_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('users', 'access_review_done_at')
    op.drop_column('users', 'google_linked_at')
