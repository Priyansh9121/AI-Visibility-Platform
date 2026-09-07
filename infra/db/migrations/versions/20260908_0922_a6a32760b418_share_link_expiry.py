"""share link expiry

One nullable column, `scans.share_expires_at`, and a CHECK that pairs it with
`share_token`.

WHY
---
`models/scan.py` carried this as a named gap rather than an oversight: "there
is NO EXPIRY and NO REVOCATION. Once minted, the link works until the row is
deleted... the first agency that shares a report with the wrong prospect has no
way to take it back." Revocation needs no column — it clears `share_token` —
so this migration is the expiry half.

The two do different jobs. Revocation is deliberate and immediate, for a link
sent to the wrong address. Expiry is automatic and bounds the link nobody
remembers sending, which is the larger population and the one no UI will ever
catch.

NULL IS NOT "NEVER EXPIRES"
---------------------------
`share.scan_for_share_token` requires a live `share_expires_at`, so a token
without one serves nothing. Fail-closed is the right default for a credential,
and the CHECK constraint turns the unset state from "quietly dead" into "cannot
be written": mint sets both columns, revocation clears both, and there is no
third shape. The constraint is added AFTER the backfill for that reason.

BACKFILL
--------
Rows that already carry a token get 30 days from now — the same TTL a fresh
mint gets. Those are live links somebody has already sent, so expiring them at
upgrade time would break a conversation in progress; giving them a full TTL
from the migration is the reading that neither breaks them nor leaves the gap
this migration exists to close. A shorter window would be arbitrary, and NULL
is not an option once the CHECK lands.

No index. The lookup is by `share_token`, which `uq_scans_share_token` already
covers; the expiry is a predicate on the row that index finds.

DOWNGRADE drops both. The previous revision's read path does not consult either.

Revision ID: a6a32760b418
Revises: 2754583da94a
Created: 2026-09-08 09:22:01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a6a32760b418'
down_revision: str | None = '2754583da94a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_scans_share_token_and_expiry_together"


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("share_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE scans SET share_expires_at = now() + interval '30 days'"
        " WHERE share_token IS NOT NULL"
    )
    op.create_check_constraint(
        op.f(CONSTRAINT),
        "scans",
        "(share_token IS NULL) = (share_expires_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "scans", type_="check")
    op.drop_column("scans", "share_expires_at")
