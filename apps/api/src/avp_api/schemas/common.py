"""Shared schema base and pagination envelope."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel

from ..services.links import http_url_or_none

# A URL field that is exposed only as an http or https URL, else null (`links.http_url_or_none`).
HttpUrlOrNone = Annotated[str | None, BeforeValidator(http_url_or_none)]


class ApiModel(BaseModel):
    """Base for every request and response model.

    `alias_generator=to_camel` with `populate_by_name=True` means Python code
    keeps snake_case while the wire format is camelCase, as api-contracts.md
    requires — enforced in one place rather than per-field.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class Page[T](ApiModel):
    """Cursor-paginated envelope: `{ data: [], nextCursor: string | null }`.

    Cursor rather than offset because IDs are ULIDs — monotonic and
    time-sortable — so `WHERE id < cursor ORDER BY id DESC` is a stable, index-
    backed window. Offset pagination skips or repeats rows whenever something
    is inserted mid-scroll, which for a scan list is the common case.
    """

    data: list[T]
    next_cursor: str | None = None
