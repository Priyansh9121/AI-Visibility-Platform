"""A client keeps its questions (F6, section 1).

The questions a scan asks are our own generated text. Writing them costs a model call and, more
to the point, changes the yardstick: two scans that asked different questions cannot be compared
honestly. So a rescan COPIES the client's set instead of writing a new one, and the agency asks
for new questions on purpose (`clients.new_questions_next_scan`).

The client's set is the prompt set of its most recent scan whose questions ALL came from the
generator, written fresh: `generated_by` is a model name (not "none", not "fallback", which is
what the first version called a set of nothing but templates, not a "+fallback" name, not
"reused"), no template question was counted in `generation_params.fromFallback`, and the scan was
not cut to a prompt limit (a cut set is a few of the questions, not the set). A set stored before
the counts existed has no `fromGenerator`, no `limited` and no way to tell a cut: it qualifies by
its name and by holding at least `MIN_PROMPTS` questions, which the first version's model-named
sets always did unless a `promptLimit` cut them. The fingerprint is a hash of the ordered (text,
intent) list: two scans "used the same questions" iff theirs are equal.

`is_all_generated` is that rule in Python and `_is_all_generated_in_sql` is the same rule as a
WHERE clause. `clients_set` asks the database for the few newest sets that pass it, so a client
rescanned thirty times does not load thirty sets and their questions to find the one it keeps,
and then confirms them with `is_all_generated`, which has the last word.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Prompt, PromptSet, Scan
from .prompts import MIN_PROMPTS

REUSED = "reused"
# The ending `prompts.generate_prompts` gives a model name when templates topped the set up.
FALLBACK_SUFFIX = "+fallback"
# A set is never the client's set if it was produced by one of these. "fallback" is the first
# version's name for a set of nothing but templates; sets stored under it are still in the database.
_NOT_A_GENERATED_NAME = frozenset({"", "none", "fallback", REUSED})
# How many candidate sets `clients_set` reads. The database has applied every test, so the newest
# candidate is the answer; the rest are slack in case Python refuses a row the database passed.
CANDIDATES = 3


def fingerprint(questions: Iterable[tuple[str, str]]) -> str:
    """A stable hash of the ordered (text, intent) list; JSON keeps any character in a text safe."""
    payload = json.dumps([[text, intent] for text, intent in questions], ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def is_all_generated(prompt_set: PromptSet) -> bool:
    """True when every question of the set was written fresh by the generator.

    A set stored without counts (no `fromGenerator`) is judged by its size as well, so this reads
    `prompt_set.prompts` for it, and the caller must have loaded them.
    """
    name = prompt_set.generated_by
    if name is None or name in _NOT_A_GENERATED_NAME or name.endswith(FALLBACK_SUFFIX):
        return False
    params = prompt_set.generation_params or {}
    return (
        params.get("fromFallback", 0) == 0
        and params.get("limited") is not True
        # Stored before the counts: fewer than MIN_PROMPTS questions means a `promptLimit` cut it.
        and ("fromGenerator" in params or len(prompt_set.prompts) >= MIN_PROMPTS)
    )


def _param_text(key: str) -> ColumnElement[str]:
    """`generation_params ->> key`: the value as text, NULL when the key is absent."""
    return func.jsonb_extract_path_text(PromptSet.generation_params, key)


def _has_param(key: str) -> ColumnElement[bool]:
    """`generation_params ? key`: the key is present, with any value (Python: `key in params`)."""
    return PromptSet.generation_params.op("?", is_comparison=True)(key)


def _prompt_count() -> ColumnElement[int]:
    """How many questions the set holds: a count correlated to the set's own row."""
    return (
        select(func.count(Prompt.id))
        .where(Prompt.prompt_set_id == PromptSet.id)
        .correlate(PromptSet)
        .scalar_subquery()
    )


def _is_all_generated_in_sql() -> ColumnElement[bool]:
    """`is_all_generated` as a WHERE clause, test for test, and "has questions" as well.

    It must never refuse a set Python would accept, or `clients_set` would answer differently from
    the version that loaded everything. The JSON values are the ones `build_prompt_set` writes: an
    integer `fromFallback`, the boolean `limited` and an integer `fromGenerator`, each absent from
    a set stored before it.
    """
    name = PromptSet.generated_by
    return and_(
        name.is_not(None),
        name.not_in(sorted(_NOT_A_GENERATED_NAME)),
        ~name.endswith(FALLBACK_SUFFIX, autoescape=True),
        func.coalesce(_param_text("fromFallback"), "0") == "0",
        _param_text("limited").is_distinct_from("true"),
        or_(_has_param("fromGenerator"), _prompt_count() >= MIN_PROMPTS),
        PromptSet.prompts.any(),
    )


async def clients_set(
    session: AsyncSession, *, client_id: str, agency_id: str, exclude_scan_id: str
) -> tuple[PromptSet, str] | None:
    """The client's most recent all-generated set and its scan's id, else None."""
    rows = await session.execute(
        select(PromptSet, PromptSet.scan_id)
        .join(Scan, Scan.id == PromptSet.scan_id)
        .where(
            Scan.client_id == client_id,
            Scan.agency_id == agency_id,
            Scan.id != exclude_scan_id,
            _is_all_generated_in_sql(),
        )
        .order_by(Scan.id.desc())
        .limit(CANDIDATES)
        .options(selectinload(PromptSet.prompts))
    )
    for prompt_set, scan_id in rows.all():
        if prompt_set.prompts and is_all_generated(prompt_set):
            return prompt_set, scan_id
    return None
