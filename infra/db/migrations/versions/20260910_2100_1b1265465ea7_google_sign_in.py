"""google sign in

`users.google_sub` — Google's stable account id, unique, nullable — and the
active-user credential CHECK widened from "has a password" to "has a password
OR a Google link". Epic 20.

WHY THE CONSTRAINT IS DROPPED AND RE-CREATED UNDER A NEW NAME
-------------------------------------------------------------
This environment's autogenerate compares CHECK constraints by name. Changing
the expression under the old name would have produced a migration that said
nothing had changed while the model and the database disagreed. A new name
makes the change a visible drop-and-create, here and in `alembic check`.

NULL FOR EVERY EXISTING ROW, AND THAT IS CORRECT: no account has signed in
with Google yet. The widened constraint admits every row the old one did.

DOWNGRADE WILL REFUSE if any active account has no password — an account
created through Google. That is the right failure: rolling back to a world
with one credential cannot invent a password for a person who never set one,
and silently deleting or suspending them is not a migration's decision.


Revision ID: 1b1265465ea7
Revises: 04652544a081
Created: 2026-09-10 21:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '1b1265465ea7'
down_revision: str | None = '04652544a081'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('google_sub', sa.String(length=255), nullable=True))
    op.create_unique_constraint(op.f('uq_users_google_sub'), 'users', ['google_sub'])
    op.drop_constraint(op.f('ck_users_active_user_has_password'), 'users', type_='check')
    op.create_check_constraint(op.f('ck_users_active_user_has_credential'), 'users', "(status = 'invited' AND password_hash IS NULL) OR password_hash IS NOT NULL OR google_sub IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_users_active_user_has_credential'), 'users', type_='check')
    op.create_check_constraint(op.f('ck_users_active_user_has_password'), 'users', "status::text = 'invited'::text AND password_hash IS NULL OR password_hash IS NOT NULL")
    op.drop_constraint(op.f('uq_users_google_sub'), 'users', type_='unique')
    op.drop_column('users', 'google_sub')
