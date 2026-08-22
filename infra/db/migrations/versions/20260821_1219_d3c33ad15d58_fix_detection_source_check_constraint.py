"""fix detection_source check constraint

Revision ID: d3c33ad15d58
Revises: 2b58b50e048d
Created: 2026-08-21 12:19:47.259072
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd3c33ad15d58'
down_revision: str | None = '2b58b50e048d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add 'both' to competitors.detection_source.

    Epic 3 introduced `DetectionSource.BOTH` — the value marking a competitor
    that SERP and co-citation surfaced independently, which is the strongest
    signal in the whole ranking — but the CHECK constraint was never widened.
    Autogenerate could not see it (VARCHAR + CHECK; contents are not diffed),
    and the test suite could not see it either, because conftest builds the test
    schema with `create_all()`, which regenerates constraints from the current
    Python enum and therefore always agrees with it.

    The result: every Epic 3 test passed while a migrated database would have
    rejected the insert with a constraint violation on the first corroborated
    competitor. Found by tests/test_enum_constraints.py once it was repointed at
    a genuinely migrated database.
    """
    op.drop_constraint(
        op.f("ck_competitors_detection_source"), "competitors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_competitors_detection_source"),
        "competitors",
        "detection_source IN ('serp', 'co_citation', 'both', 'manual')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM competitors WHERE detection_source = 'both'")
    op.drop_constraint(
        op.f("ck_competitors_detection_source"), "competitors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_competitors_detection_source"),
        "competitors",
        "detection_source IN ('serp', 'co_citation', 'manual')",
    )
