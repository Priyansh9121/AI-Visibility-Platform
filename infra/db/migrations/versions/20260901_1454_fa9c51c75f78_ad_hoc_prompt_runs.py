"""ad hoc prompt runs

Four tables behind `POST/GET /clients/{clientId}/prompt-runs` — Epic 9.24.

ADDITIVE, AND NOTHING EXISTING IS TOUCHED. No column is altered, no constraint
is dropped, and no row anywhere is rewritten. Every agency in this database is
in exactly the state it was in before this ran; the feature simply has nowhere
to store anything until it does.

WHY THIS IS NOT MORE `engine_results`
--------------------------------------
An `EngineResult` hangs off a `Scan`, and a scan is a MEASUREMENT of a client —
it carries a score, appears in the dashboard's recent list, and plots a point on
that client's trends. An ad-hoc run is a question an operator asked once. Reusing
the scan tables would have meant either inventing a synthetic `Scan` row for
every run (which would then show up in the history, the trends and the figures as
if a measurement had been taken) or adding a nullable `scan_id` to
`engine_results` and a flag to filter on — a filter that every existing query
would have to learn about, and that exactly one of them would eventually forget.

Separate tables cost four `CREATE TABLE`s and make the wrong reading impossible.

IP-SAFETY (ip-safety.md #7)
---------------------------
`prompt_runs.prompt_text` is the only TEXT column here, and it holds what the
OPERATOR typed — our side of the exchange, exactly as `prompts.text` is. The
three child tables carry booleans, ordinals, counts, a SHA-256 digest, entity
names and cited URLs, and no column capable of holding an engine's answer.
`test_ip_safety.py` lists all three in `FACTS_ONLY_MODELS`, so adding one later
fails CI rather than passing review.

Revision ID: fa9c51c75f78
Revises: e5a71c9d3b84
Created: 2026-09-01 14:54:34.083943
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'fa9c51c75f78'
down_revision: str | None = 'e5a71c9d3b84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('prompt_runs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('client_id', sa.String(length=40), nullable=False),
    sa.Column('agency_id', sa.String(length=40), nullable=False),
    sa.Column('asked_by_user_id', sa.String(length=40), nullable=True),
    sa.Column('prompt_text', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('ok', 'partial', 'failed', name='prompt_run_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('subject_name', sa.String(length=200), nullable=False),
    sa.Column('subject_domain', sa.String(length=253), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agency_id'], ['agencies.id'], name=op.f('fk_prompt_runs_agency_id_agencies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['asked_by_user_id'], ['users.id'], name=op.f('fk_prompt_runs_asked_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_prompt_runs_client_id_clients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_runs'))
    )
    op.create_index(op.f('ix_prompt_runs_agency_id'), 'prompt_runs', ['agency_id'], unique=False)
    op.create_index(op.f('ix_prompt_runs_asked_by_user_id'), 'prompt_runs', ['asked_by_user_id'], unique=False)
    op.create_index(op.f('ix_prompt_runs_client_id'), 'prompt_runs', ['client_id'], unique=False)
    op.create_index('ix_prompt_runs_client_id_id', 'prompt_runs', ['client_id', 'id'], unique=False)
    op.create_table('prompt_run_results',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('engine', sa.Enum('chatgpt', 'perplexity', 'google_ai_overview', 'gemini', 'claude', 'claude_search', 'copilot', name='prompt_run_engine', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('engine_version', sa.String(length=120), nullable=True),
    sa.Column('status', sa.Enum('ok', 'answered_no_mention', 'rate_limited', 'error', 'timeout', name='prompt_run_result_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('mentioned', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('prominence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('brands_mentioned', sa.Integer(), nullable=False),
    sa.Column('response_digest', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['prompt_runs.id'], name=op.f('fk_prompt_run_results_run_id_prompt_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_run_results')),
    sa.UniqueConstraint('run_id', 'engine', name='uq_prompt_run_results_run_id_engine')
    )
    op.create_index(op.f('ix_prompt_run_results_run_id'), 'prompt_run_results', ['run_id'], unique=False)
    op.create_table('prompt_run_brands',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('result_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('domain', sa.String(length=253), nullable=True),
    sa.Column('is_subject', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['result_id'], ['prompt_run_results.id'], name=op.f('fk_prompt_run_brands_result_id_prompt_run_results'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_run_brands'))
    )
    op.create_index(op.f('ix_prompt_run_brands_result_id'), 'prompt_run_brands', ['result_id'], unique=False)
    op.create_index('ix_prompt_run_brands_result_id_position', 'prompt_run_brands', ['result_id', 'position'], unique=False)
    op.create_table('prompt_run_citations',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('result_id', sa.String(length=40), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('domain', sa.String(length=253), nullable=False),
    sa.Column('source_type', sa.Enum('owned', 'competitor', 'directory', 'review', 'editorial', 'social', 'other', name='prompt_run_citation_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('cites_subject', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['result_id'], ['prompt_run_results.id'], name=op.f('fk_prompt_run_citations_result_id_prompt_run_results'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_run_citations'))
    )
    op.create_index(op.f('ix_prompt_run_citations_result_id'), 'prompt_run_citations', ['result_id'], unique=False)
    op.create_index('ix_prompt_run_citations_result_id_position', 'prompt_run_citations', ['result_id', 'position'], unique=False)


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_prompt_run_citations_result_id_position', table_name='prompt_run_citations')
    op.drop_index(op.f('ix_prompt_run_citations_result_id'), table_name='prompt_run_citations')
    op.drop_table('prompt_run_citations')
    op.drop_index('ix_prompt_run_brands_result_id_position', table_name='prompt_run_brands')
    op.drop_index(op.f('ix_prompt_run_brands_result_id'), table_name='prompt_run_brands')
    op.drop_table('prompt_run_brands')
    op.drop_index(op.f('ix_prompt_run_results_run_id'), table_name='prompt_run_results')
    op.drop_table('prompt_run_results')
    op.drop_index('ix_prompt_runs_client_id_id', table_name='prompt_runs')
    op.drop_index(op.f('ix_prompt_runs_client_id'), table_name='prompt_runs')
    op.drop_index(op.f('ix_prompt_runs_asked_by_user_id'), table_name='prompt_runs')
    op.drop_index(op.f('ix_prompt_runs_agency_id'), table_name='prompt_runs')
    op.drop_table('prompt_runs')
    # ### end Alembic commands ###
