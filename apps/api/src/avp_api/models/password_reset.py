"""Password-reset tokens — Epic 9.13.

WHY THE DIGEST IS STORED AND NOT THE TOKEN
------------------------------------------
Epic 9.8's `Scan.share_token` is stored in the clear, and this table is
deliberately NOT modelled on it. That column is clear-text for one specific
reason recorded in `security.new_share_token`: an operator has to be able to
come back and copy the URL again, so a digest would make the link
unrecoverable.

A reset token has the opposite lifecycle. It is minted, put in exactly one
email, and never shown again — nobody ever needs it read back. So it follows
`SessionStore`'s rule instead: **only the SHA-256 digest is persisted**, and a
dump of this table hands an attacker nothing usable. That is the same reasoning
`security.token_digest` already states for session tokens.

Single use and short lived, both enforced in the service rather than implied:
`used_at` is stamped on redemption and `expires_at` is checked on every lookup.
A reset link that still works after the password has been changed is a second
key to the same door.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, fk_column, id_column

if TYPE_CHECKING:
    from .tenancy import User


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = fk_column("users.id")

    # SHA-256 hex of the token that was emailed. 64 characters, never the token.
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL until redeemed. Stamped rather than deleted so a support question
    # ("did that link get used?") is answerable, and so a replay attempt hits a
    # row that says why it was refused instead of one that is simply missing.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()

    __table_args__ = (
        # Unique because the digest IS the lookup key — a collision would mean
        # one link opening two accounts. Not partial: unlike a share token this
        # column is never NULL, so there is nothing to exclude.
        Index("uq_password_reset_tokens_digest", "token_digest", unique=True),
        # No explicit index on user_id: `fk_column` already sets index=True, so
        # declaring one here creates a SECOND index on the same column. The
        # migration drift test caught exactly that and it was removed rather
        # than kept — the "invalidate the others" sweep is served by the
        # foreign key's own index.
    )
