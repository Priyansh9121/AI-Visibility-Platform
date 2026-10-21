"""users sessions valid after

A3 part 3 (launch checklist 3.125): the credential version. One column on `users`,
`sessions_valid_after`, TIMESTAMPTZ and nullable with no default: a session whose sign-in time is
before it is refused at every request (`deps.current_principal`), whatever Redis still holds. It is
written by the places that end a person's sessions (sign out everywhere, a password change, a reset,
a Google link, the operator's reset script, a seat's removal and the invitation that revives a removed
seat), in the transaction of the change, so that ending them no longer depends on Redis alone: the list
`revoke_all_for_user` reads can be evicted, a revoke can fail, and a session can be minted between the
two revokes of a change.

NULL means no cutoff, and that is every user when this runs, and any user the old instance of the
deploy inserts while it is still serving (it does not know the column). There is no backfill and no
data revision: no session is ended by the deploy, and a cutoff has no value until one of those places
writes it. Adding a nullable column with no default does not rewrite the table. The value is a
timestamp, so the facts-only rule is untouched, and no score, ratio or `formula_version` is read or
rewritten.

`downgrade()` drops the column and with it every cutoff written. It is for a development or scratch
database: production is forward-only. Once this revision has run on a database, an image from before it
cannot be redeployed, because its `alembic upgrade head` does not know the revision the database is at
(Alembic cannot locate it) and exits, so the deploy fails while the running version keeps serving. The
way back from this change is a forward revert that keeps this revision and the model column. Code that
stops checking or writing the cutoff ends sessions by Redis alone, as it did, so a session that a cutoff
had ended and Redis still holds would work again: that is the reason the Redis revokes stay in every one
of those places.

Revision ID: a3480a6d70d1
Revises: 5f08c7c74129
Created: 2026-10-21 22:54
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3480a6d70d1'
down_revision: str | None = '5f08c7c74129'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `users` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'users', sa.Column('sessions_valid_after', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('users', 'sessions_valid_after')
