"""scan matcher version

Checklist 3.97 (2026-10-08), the founder's decision: an explicit re-score of a scan whose brand
matches were made before FORMULA_VERSION v7 is refused. v7 changed BRAND MATCHING
(`extraction.find_brand` began to find a 2-4 character short form in capitals), and answer text is
never stored, so such a scan can never be matched again; `POST /scans/{scanId}/score` over its
stored matches wrote a row labelled v7 from matches v7 did not make. Nothing recorded which rule
made a scan's matches, so this records it.

One column on `scans`, NOT NULL, default 'pre-v7' ("not recorded"). The code stamps the current
matcher when a scan starts running (`scan_runner.run_scan`) and the ORM defaults a new row to it;
the database's default is only for a row written without the column. That is every row that exists
when this runs, and any an older instance inserts while it is still serving during the deploy.
'pre-v7' for those is the safe direction: the worst case is a refused re-score, and a new scan
answers it.

THE BACKFILL, and why it is not "every existing row is pre-v7". Production already holds scans that
can be RUN after v7 deployed (the founder's brief of 2026-10-08 describes RMIT University's re-run,
made after it), and marking them
'pre-v7' would refuse a re-score that is fine. `BACKFILL_V7_MATCHED` marks a scan 'v7' when it has at
least one score and every score it has is v7: a scan scored by v7 at the end of its own run. A scan
matched before v7 and explicitly re-scored after the deploy has its older row as well (v5), so it
stays 'pre-v7' as it should; one with only an older row, or none, stays 'pre-v7'. The statement
changes only `scans.matcher_version`, from 'pre-v7' to 'v7'. It reads `scores` and writes none of it,
and no score, ratio or `formula_version` is rewritten (scoring-spec.md rule 5). Run again it changes
nothing. Nothing here is third-party text, so the facts-only rule is untouched.

ONE REVISION, AND THE LOCK. The column and its backfill share this revision, where the
alembic-migrations skill prefers a batched data revision of its own. They run in one transaction
either way (`env.py` runs every pending revision of a deploy in one), so the ALTER's lock on `scans`
is held for the UPDATE. The UPDATE is a single pass over `scans` with an indexed lookup in `scores`
(`ix_scores_scan`) per scan, and `scans` has one row per scan run. Measured on a scratch database of
20,004 scans and 20,004 scores, `alembic upgrade head` over this one revision took 0.63 s wall, process
start included. Production's row count has not been measured, which no agent opens. `SET LOCAL
lock_timeout` bounds the wait for the lock, not the hold.

Revision ID: 5863629b56ab
Revises: c4a9e17d2b58
Created: 2026-10-08 22:15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5863629b56ab'
down_revision: str | None = 'c4a9e17d2b58'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 'v7' is written out, not imported: a revision says what it did on the day it ran, whatever the
# application's constants become. Only a row still 'pre-v7' is considered, so a second run leaves a
# row a scan's run has stamped as it is. Both subqueries are on `scores.scan_id`, which is indexed.
BACKFILL_V7_MATCHED = """
UPDATE scans
SET matcher_version = 'v7'
WHERE matcher_version = 'pre-v7'
  AND EXISTS (
      SELECT 1 FROM scores WHERE scores.scan_id = scans.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM scores
      WHERE scores.scan_id = scans.id AND scores.formula_version IS DISTINCT FROM 'v7'
  )
"""


def upgrade() -> None:
    # A long transaction on `scans` fails the deploy instead of queueing every request behind the ALTER.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        'scans',
        sa.Column('matcher_version', sa.String(length=40), server_default='pre-v7', nullable=False),
    )
    op.execute(BACKFILL_V7_MATCHED)


def downgrade() -> None:
    # The column and what it says go together; no score is touched, so nothing else is undone.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('scans', 'matcher_version')
