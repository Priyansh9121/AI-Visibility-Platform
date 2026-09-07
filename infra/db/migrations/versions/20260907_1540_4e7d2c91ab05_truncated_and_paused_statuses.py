"""truncated and paused engine result statuses

Two members on `EngineResultStatus`, and therefore two CHECK constraints
widened: `engine_results.status` and `prompt_run_results.status` share the
Python enum, and each column carries its own constraint.

WHY
---
Both engine adapters read the vendor's stop reason for exactly one value —
"refusal" — and treated everything else as a finished answer. A response cut
off by the token budget therefore reached fact extraction as OK; the brand was
absent from the truncated prefix, and the row was recorded
`answered_no_mention`, which scoring counts against the mention rate. A billed
call producing a fabricated absence, on a scan that still read `succeeded`.

`truncated` is the engine running out of room (Claude `max_tokens` /
`model_context_window_exceeded`, OpenAI `length`). `paused` is the engine
handing control back with nothing to resume it (Claude `pause_turn` /
`tool_use`, OpenAI `tool_calls` / `function_call`). Two values rather than
one, because `paused` has a possible fix — resuming the call — and `truncated`
does not. The `tool_use`, `tool_calls` and `function_call` triggers are
unreachable until a client-side tool is added to an adapter's request — on the
Claude side an entry in `kwargs["tools"]` that is not a server tool, on the
OpenAI side a `tools` or `functions` entry in the ChatGPT payload — and the
status is kept for that day rather than treated as dead.

HAND-WRITTEN, for the reason 2b58b50e048d recorded when Engine.CLAUDE_SEARCH
was added: autogenerate does not diff the contents of a CHECK constraint, so
adding an enum member produces an empty migration and `alembic check` reports
no drift. tests/test_enum_constraints.py is what catches it.

DOWNGRADE MAPS RATHER THAN DELETES. 2b58b50e048d deleted the rows that used
the removed value because a `claude_search` row has no meaning without its
engine. A truncated or paused row does: it is a billed call that produced no
answer, and `error` says exactly that in the narrower vocabulary. `error_code`
keeps ANSWER_TRUNCATED / ANSWER_PAUSED, so the distinction survives a
downgrade even though the status does not.

Revision ID: 4e7d2c91ab05
Revises: 33b576c694d0
Created: 2026-09-07 15:40:07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '4e7d2c91ab05'
down_revision: str | None = '33b576c694d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES_BEFORE = ("ok", "answered_no_mention", "rate_limited", "error", "timeout")
STATUSES_AFTER = (*STATUSES_BEFORE, "truncated", "paused")

# (table, the Enum's `name`) — the naming convention makes the constraint
# `ck_<table>_<enum name>`, and op.f() must wrap it so the convention is not
# applied a second time (see 2b58b50e048d).
TABLES = (
    ("engine_results", "engine_result_status"),
    ("prompt_run_results", "prompt_run_result_status"),
)


def _status_in(values: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _replace(table: str, enum_name: str, values: tuple[str, ...]) -> None:
    name = op.f(f"ck_{table}_{enum_name}")
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, _status_in(values))


def upgrade() -> None:
    for table, enum_name in TABLES:
        _replace(table, enum_name, STATUSES_AFTER)


def downgrade() -> None:
    for table, enum_name in TABLES:
        op.execute(
            f"UPDATE {table} SET status = 'error' WHERE status IN ('truncated', 'paused')"
        )
        _replace(table, enum_name, STATUSES_BEFORE)
