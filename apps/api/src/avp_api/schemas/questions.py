"""The client's questions (F6): the flag in, and the flag plus the set the client keeps out.

Counts, dates and names only. The questions' own text is not here: the Prompts tab shows how the
set came to be, and the questions themselves are on each report.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import StrictBool

from .common import ApiModel


class NewQuestionsRequest(ApiModel):
    new_questions_next_scan: StrictBool


class CurrentQuestionSetOut(ApiModel):
    """The set a rescan of this client would copy, and where it came from.

    `source_scan_id` and `written_at` are the scan that wrote it; `reused_since` is the date of the
    first scan that copied it (null while none has). `written_with_rivals` is the rival names the
    questions were written with (null for a set written before they were recorded) and
    `rivals_changed` is true when the client's current active rivals differ from them (false when
    they are not known).
    """

    source_scan_id: str
    written_at: datetime | None
    question_count: int
    reused_since: datetime | None
    written_with_rivals: list[str] | None
    rivals_changed: bool


class ClientQuestionsOut(ApiModel):
    new_questions_next_scan: bool
    # Null until the client has a set a rescan would copy (no scan yet, or only template-filled).
    current_set: CurrentQuestionSetOut | None
