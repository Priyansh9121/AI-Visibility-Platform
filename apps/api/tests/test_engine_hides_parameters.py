"""Bound values stay out of a SQLAlchemy error's text (launch checklist 3.5).

`logger.exception` and `exc_info=True` render the exception, and SQLAlchemy appends
`[parameters: (...)]` unless the engine was built with `hide_parameters=True`.

The failing statement is a division by zero, so the error text is SQLAlchemy's own and the driver
does not quote the value. (A unique or foreign-key violation would, in the Postgres DETAIL line
`Key (email)=(...)`; that is the database's message, not a bound parameter, and this flag does not
touch it.)
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from avp_api import db
from avp_api.config import Settings

SECRET = "tok_1f9c-not-a-real-token@example.com"


async def _failing_text(engine) -> str:  # noqa: ANN001
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 / (length(CAST(:v AS text)) * 0)"), {"v": SECRET})
    except DBAPIError as exc:
        return str(exc)
    raise AssertionError("the statement was meant to fail")


@pytest.mark.asyncio
async def test_the_runtime_engine_leaves_bound_values_out_of_the_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "_engine", None)
    engine = db.get_engine(settings)
    try:
        message = await _failing_text(engine)
    finally:
        await engine.dispose()
    assert SECRET not in message
    assert "SELECT 1 / (length" in message  # the statement is still reported


@pytest.mark.asyncio
async def test_without_the_flag_the_value_does_appear(settings: Settings) -> None:
    # Control for the test above: the failing statement does render its parameters by default.
    engine = create_async_engine(settings.database_url)
    try:
        message = await _failing_text(engine)
    finally:
        await engine.dispose()
    assert SECRET in message
