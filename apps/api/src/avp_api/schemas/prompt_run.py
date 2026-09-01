"""Ad-hoc prompt run schemas — Epic 9.24.

ip-safety.md #7: every field below is a boolean, an ordinal, a count, a status
enum, an entity NAME, a cited URL/domain, a duration or a hash. There is
deliberately no field capable of carrying an engine's answer — `PromptRunResult`
has no such column to serve one from, and this layer adds none.

`promptText` is the exception the model states and for the same reason
`prompts.text` is: the operator typed it. It is our side of the exchange.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models.engine_result import CitationType, Engine, EngineResultStatus
from ..models.prompt_run import PromptRunStatus
from ..services.prompt_runs import MAX_PROMPT_CHARS
from .common import ApiModel


class PromptRunIn(ApiModel):
    """The question. Bounded here as well as in the service.

    Two checks on purpose: this one rejects an oversized body at the edge before
    it is read, and `normalise_prompt` rejects one that only becomes oversized
    after whitespace is collapsed. Neither subsumes the other.
    """

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS * 4)


class PromptRunBrandOut(ApiModel):
    """A brand this answer named, and where it appeared."""

    name: str
    domain: str | None = None
    is_subject: bool
    position: int


class PromptRunCitationOut(ApiModel):
    """A source this answer cited. Location, never content."""

    url: str
    domain: str
    source_type: CitationType
    position: int
    cites_subject: bool


class PromptRunResultOut(ApiModel):
    """What one engine did with the prompt."""

    id: str
    engine: Engine
    engine_version: str | None = None
    status: EngineResultStatus
    error_code: str | None = None
    latency_ms: int | None = None

    mentioned: bool
    # NULL when the subject was not named — never 0, which would sort as
    # "first" and read as a position nobody measured.
    position: int | None = None
    prominence: Decimal | None = None
    brands_mentioned: int

    brands: list[PromptRunBrandOut] = []
    citations: list[PromptRunCitationOut] = []


class PromptRunOut(ApiModel):
    id: str
    client_id: str
    prompt_text: str
    status: PromptRunStatus
    subject_name: str
    subject_domain: str
    created_at: datetime
    results: list[PromptRunResultOut] = []


class PromptRunHistoryOut(ApiModel):
    """A client's runs, newest first, with what the throttle has left.

    `runsRemaining` is served rather than left to the browser to infer. The
    ceiling is a server-side decision and a client that guessed it would either
    block a legal run or offer one that 429s — both worse than being told.
    """

    data: list[PromptRunOut] = []
    runs_remaining: int
    runs_per_hour: int
    max_prompt_chars: int
