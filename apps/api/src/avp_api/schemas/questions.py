"""The client's questions flag (F6): a boolean in, the same boolean out. Nothing else."""

from __future__ import annotations

from pydantic import StrictBool

from .common import ApiModel


class NewQuestionsRequest(ApiModel):
    new_questions_next_scan: StrictBool


class NewQuestionsOut(ApiModel):
    new_questions_next_scan: bool
