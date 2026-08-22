"""add claude_search engine

Revision ID: 2b58b50e048d
Revises: 672109d81730
Created: 2026-08-21 12:13:29.141791
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2b58b50e048d'
down_revision: str | None = '672109d81730'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # HAND-WRITTEN. Alembic autogenerate produced an EMPTY migration here.
    #
    # The Engine enum is mapped as VARCHAR + CHECK (native_enum=False), and
    # autogenerate does not diff the *contents* of a CHECK constraint — it sees
    # a VARCHAR column that has not changed. Adding an enum member in Python
    # therefore produces no migration, and the first insert of the new value
    # fails at runtime with a constraint violation.
    #
    # `alembic check` does not catch this either. tests/test_enum_constraints.py
    # closes the gap by asserting every Python enum member is accepted by the
    # database.
    #
    # op.f() is required on the constraint name: the metadata naming convention
    # prefixes `ck_%(table_name)s_`, so passing an already-qualified name
    # produces `ck_engine_results_ck_engine_results_engine` and the DROP fails.
    op.drop_constraint(
        op.f("ck_engine_results_engine"), "engine_results", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_engine_results_engine"),
        "engine_results",
        "engine IN ('chatgpt', 'perplexity', 'google_ai_overview', 'gemini', 'claude', 'claude_search', 'copilot')",
    )


def downgrade() -> None:
    # Rows using the removed value would violate the narrowed constraint, so
    # clear them rather than letting the downgrade fail half-applied.
    op.execute("DELETE FROM engine_results WHERE engine = 'claude_search'")
    op.drop_constraint(
        op.f("ck_engine_results_engine"), "engine_results", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_engine_results_engine"),
        "engine_results",
        "engine IN ('chatgpt', 'perplexity', 'google_ai_overview', 'gemini', 'claude', 'copilot')",
    )
