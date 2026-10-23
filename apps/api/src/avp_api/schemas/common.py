"""Shared schema base and pagination envelope."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from ..services.links import http_url_or_none

# A URL field that is exposed only as an http or https URL, else null (`links.http_url_or_none`).
HttpUrlOrNone = Annotated[str | None, BeforeValidator(http_url_or_none)]

# Up to this many keys past a model's own are still named one by one; past it the body is a flood.
EXTRA_KEYS_MARGIN = 10


class ApiModel(BaseModel):
    """Base for every response model, and for every request model through `RequestModel`.

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


class RequestModel(ApiModel):
    """Base for every model a request body is read into, and every model nested inside one.

    A key the model does not declare is refused (`422`, `extra_forbidden`) instead of dropped
    unread. Dropping it is how a client learns nothing when it names something the API will not
    take, and how a field added to a model later starts to be accepted from callers that were
    already sending it. The aliases are unchanged: the camelCase alias and the snake_case name are
    both accepted for a field the model declares.

    **A flood of keys is refused whole.** Pydantic builds one `extra_forbidden` error per unknown
    key, at every depth, and a body is validated before any rate limit on the route runs. A body of
    1,000,000 unknown keys would make a million errors and a response of tens of megabytes on the
    one worker. So a dict with more than `EXTRA_KEYS_MARGIN` keys beyond the model's declared fields
    is refused before pydantic walks it: ONE error of type `too_many_fields`, at the model's own
    place in the body. Nested request models inherit this, since each is one.

    Responses stay on `ApiModel`: a response that gains a field must never break a client.
    `tests/test_request_schemas_forbid_extra.py` walks the live app's routes, so a new body model
    that is not on this base fails there.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _refuse_a_flood_of_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and len(data) > len(cls.model_fields) + EXTRA_KEYS_MARGIN:
            raise PydanticCustomError(
                "too_many_fields", "This request has more fields than it takes."
            )
        return data


class Page[T](ApiModel):
    """Cursor-paginated envelope: `{ data: [], nextCursor: string | null }`.

    Cursor rather than offset because IDs are ULIDs — monotonic and
    time-sortable — so `WHERE id < cursor ORDER BY id DESC` is a stable, index-
    backed window. Offset pagination skips or repeats rows whenever something
    is inserted mid-scroll, which for a scan list is the common case.
    """

    data: list[T]
    next_cursor: str | None = None
