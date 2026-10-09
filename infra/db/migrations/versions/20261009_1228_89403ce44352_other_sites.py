"""other sites

M1 task 1.1: a brand's other sites. A domain with the brand's name under another public suffix
(rmit.edu.vn for rmit.edu.au) is PROPOSED as the brand's other site, and counts as the brand's
own only once the agency CONFIRMS it. Three lists, each a JSONB array of registrable domains in
the DNS FORM (`intake.dns_form`: lower case ASCII, an internationalised label IDNA-encoded, so
`bücher.at` is stored as `xn--bcher-kva.at`; no `www.`). One string per host, and ASCII, which a
JSONB column holds in a database of any encoding (the local development cluster is SQL_ASCII,
which refuses a non-ASCII character in JSONB). A cited domain keeps the spelling the engine
gave, so whatever reads the lists compares through `dns_form`:

- `clients.other_sites`: the domains the agency confirmed as the client's own other sites.
- `clients.dismissed_other_sites`: the domains the agency said are NOT any brand's own, for the
  client and its rivals alike (one list per client). A dismissed domain is never proposed again.
- `competitors.other_sites`: a rival's confirmed other sites, on its row. A scan's competitor set
  is a frozen copy of the client's list, so the row is where a rival's sites have to live: they
  are copied with it wherever a set is copied and cleared when the rival's host changes (the
  same host in another spelling is the same host).

All three are NOT NULL with the constant default '[]'. No backfill and no data revision: nothing
is confirmed or dismissed yet, so every row that exists is correct as an empty list. A constant
default adds a column without rewriting the table. The old instance that keeps serving while this
one migrates inserts clients and rivals without the columns, which the default allows. Nothing
here is third-party text (a list of domains), so the facts-only rule is untouched, and no score,
ratio or `formula_version` is read or rewritten.

`downgrade()` drops the three columns, and with them whatever the agency confirmed or dismissed
since. Nothing that scores or reads a citation reads them yet (task 1.2), so no stored figure
depends on them.

Revision ID: 89403ce44352
Revises: 5863629b56ab
Created: 2026-10-09 12:28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '89403ce44352'
down_revision: str | None = '5863629b56ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A long transaction on `clients` or `competitors` fails the deploy instead of queueing every request behind the ALTERs.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'clients',
        sa.Column('other_sites', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.add_column(
        'clients',
        sa.Column(
            'dismissed_other_sites', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False
        ),
    )
    op.add_column(
        'competitors',
        sa.Column('other_sites', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('competitors', 'other_sites')
    op.drop_column('clients', 'dismissed_other_sites')
    op.drop_column('clients', 'other_sites')
