"""scan named domains

F2 (2026-10-09), the founder's decision: a domain that has the client's own restricted ending
(edu.au for rmit.edu.au) and that the scan's answers named a brand for ("Deakin University" for
deakin.edu.au) is a probable rival, not a source to "Get onto". Whether an answer named a label
can only be told while the answer text exists, and answer text is never stored, so the scan
records it as it persists its results.

One column on `scans`, `named_domains`, JSONB and nullable with no default: a map from a cited
domain to the number of answers that named its label (`{}` when the scan measured it and nothing
qualified; see `Scan.named_domains`). NULL means the scan did not record it, and that is every
row that exists when this runs, and any an older instance inserts while it is still serving
during the deploy. There is no backfill and no data revision: the fact cannot be recomputed from
what is stored, a guess would be a claim about a client's rivals, and a report reads NULL exactly
as it read before the column. Adding a nullable column with no default does not rewrite the
table. Nothing here is third-party text (a domain and a count each), so the facts-only rule is
untouched, and no score, ratio or `formula_version` is read or rewritten.

`downgrade()` drops the column and with it what scans recorded: the next report of such a scan
reads NULL, which is what it read before.

Revision ID: 5f08c7c74129
Revises: d5b2f84a6c13
Created: 2026-10-09 18:30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5f08c7c74129'
down_revision: str | None = 'd5b2f84a6c13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `scans` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'scans',
        sa.Column('named_domains', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('scans', 'named_domains')
