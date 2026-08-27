"""public share token on scans

Adds the column behind `GET /api/v1/reports/{token}` — the first
unauthenticated read surface in this product (Epic 9.8, the Epic 9 send path).

Nullable, because a token is minted on demand rather than at scan creation.
Most scans are never shared, and a token that exists is a URL that works;
minting one for every scan would create a live public link for every scan ever
run. Absent by default is the safer state.

The unique index is PARTIAL on `share_token IS NOT NULL`. Postgres already
treats NULLs as distinct, so a plain unique index would permit many unshared
scans — but the partial form keeps the index off every row without a token,
which is most of them, and states the intent rather than relying on the
reader knowing that NULL semantics.

INDEPENDENT of `uq_scans_one_open_per_client` (Epic 9.6). That index constrains
`client_id` over open scans; this one constrains `share_token` over shared
scans. Different column, different predicate — they cannot interact. Verified
rather than assumed: both are present after `upgrade()` and a client can still
have exactly one open scan while any number of its finished scans are shared.

See build-log Epic 9.8.

Revision ID: c7e4a19f83d1
Revises: a41d9c7e5b02
Created: 2026-08-27 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e4a19f83d1'
down_revision: str | None = 'a41d9c7e5b02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("share_token", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_scans_share_token",
        "scans",
        ["share_token"],
        unique=True,
        postgresql_where=sa.text("share_token IS NOT NULL"),
    )


def downgrade() -> None:
    # Dropping the column destroys every live share link. That is the correct
    # behaviour for a downgrade — the endpoint that reads them is gone too —
    # but it is worth stating: this is not a reversible round trip for anyone
    # who has already sent a link to a prospect.
    op.drop_index("uq_scans_share_token", table_name="scans")
    op.drop_column("scans", "share_token")
