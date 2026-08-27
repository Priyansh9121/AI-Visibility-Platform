"""password reset tokens

The table behind `POST /auth/reset-password/request` and `/confirm` — Epic 9.13,
the first authentication surface added since Epic 1.3.

Only the SHA-256 DIGEST of the emailed token is stored, never the token. This
deliberately follows `SessionStore`'s rule rather than Epic 9.8's share token:
`Scan.share_token` is clear-text because an operator must be able to copy that
URL again, whereas a reset token is minted, emailed once, and never shown back —
so a dump of this table should hand an attacker nothing usable.

`expires_at` and `used_at` make the two refusals explicit rather than implied.
A reset link that still works after the password has changed is a second key to
the same door, so redemption stamps `used_at` rather than deleting the row —
which also keeps "was that link ever used?" answerable.

The digest index is UNIQUE and not partial: the digest is the lookup key, a
collision would mean one link opening two accounts, and unlike a share token
this column is never NULL so there is nothing to exclude.

See build-log Epic 9.13.

Revision ID: d8f1b2a45c93
Revises: c7e4a19f83d1
Created: 2026-08-27 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd8f1b2a45c93'
down_revision: str | None = 'c7e4a19f83d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
    )
    op.create_index(
        "uq_password_reset_tokens_digest",
        "password_reset_tokens",
        ["token_digest"],
        unique=True,
    )
    # `fk_column` sets index=True, so this is the index the model declares —
    # named as SQLAlchemy names it, not invented here. Declaring a second one
    # by another name is what the drift test rejected.
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    # Dropping the table invalidates every outstanding reset link. That is
    # correct — the endpoints that read them are gone too — but it is worth
    # stating: anyone mid-reset when this runs has to start again.
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_index("uq_password_reset_tokens_digest", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
