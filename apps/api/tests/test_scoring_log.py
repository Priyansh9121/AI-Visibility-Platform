"""The scoring line says why a dimension was left out — launch checklist 3.58.

`scoring.completed` is the one line a scan leaves in production about its score. It carried
`excluded=['citation_strength']`: the names of the dimensions left out and nothing about
why, although `compute_score` records a reason code for every one. When C1 (3114aff) looked
at two production scans with no Citation Strength, the only way to learn that the reason was
`NO_AUTHORITY_DATA` was to rule the others out against the scoring code and the stored
facts. The line now carries each reason beside its dimension:

    excluded={'citation_strength': 'NO_AUTHORITY_DATA'}

What these tests hold, each against rows scored by `score_scan` (the function that logs),
not against a stubbed score:

1. **One reason per case.** NO_AWARENESS_POPULATION, NO_AUTHORITY_DATA, NO_COMPETITOR_SET,
   each of Technical Foundation's four reasons, and the two the first three do not reach:
   NO_POPULATION (Sentiment, when no answer names the client) and NO_ANSWERED_RESULTS (a scan
   no engine answered, which leaves out all five).
2. **Nothing left out is an empty mapping,** a `{}` and not a missing field or an empty list.
3. **The line is what was stored.** Each mapping equals the Score row's `excluded_dimensions`,
   read back from the database, so a reason `compute_score` gains later reaches the log with
   no second edit.
4. **Dimension order.** The mapping comes sorted by dimension name, as the list it replaces
   did, not in the order `compute_score` decided them in.
5. **Facts only, and nothing else moved.** The reasons are codes (a dimension's name and an
   upper-case code, never text from an answer, a page or a name). Every other field is the
   one it was, and no field is added.

Nothing here reaches a provider: the rows are written by the test, and scoring is pure
arithmetic over them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import (
    Agency,
    AuditStatus,
    BrandMention,
    Citation,
    CitationType,
    ClassificationStatus,
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptIntent,
    PromptSet,
    Scan,
    ScanStatus,
    ScanTrigger,
    Score,
    Sentiment,
    TechnicalAudit,
)
from avp_api.services import scoring_runner
from avp_api.services.crawl import ROBOTS_DISALLOWED, ROBOTS_UNREACHABLE
from avp_api.services.egress import ADDRESS_NOT_PUBLIC
from avp_api.services.scoring import TECHNICAL_FOUNDATION_EXCLUSIONS, Dimension, ScoreResult

AW, CMP, BF = PromptIntent.AWARENESS, PromptIntent.COMPARISON, PromptIntent.BOTTOM_FUNNEL

SUBJECT = "Subject Co"
RIVAL = "Rival Co"
# On the curated list (Tier A), which needs no row behind it: citing it gives Citation
# Strength a tier. Cited by nobody else here, so no other domain can reach Tier B.
TIERED = "g2.com"
UNTIERED = "plain.example"

# The audit a scan carries: a good one, or the code of a failed one. None is no audit at all.
OK_AUDIT = "ok"

# What each way a Technical Foundation can be missing records: the failed audit's error code
# (None: no audit row) and the reason `compute_score` stores for it.
TECHNICAL_FOUNDATION_CASES: list[tuple[str | None, str]] = [
    (None, "NOT_YET_MEASURED"),
    (ROBOTS_DISALLOWED, "CRAWL_DISALLOWED"),
    (ADDRESS_NOT_PUBLIC, "CRAWL_ADDRESS_NOT_PUBLIC"),
    (ROBOTS_UNREACHABLE, "CRAWL_POLICY_UNREADABLE"),
    ("TIMEOUT", "AUDIT_FAILED"),
]

# The line's fields as they were before this item, plus nothing: `event` and `log_level` are
# structlog's own.
LINE_FIELDS = {
    "event", "log_level", "scan_id", "status", "composite", "excluded", "flags",
    "inputs_digest", "results", "competitors",
}


async def _seed(
    session: AsyncSession,
    *,
    intents: tuple[PromptIntent, ...] = (AW, AW),
    cited: tuple[str, ...] = (TIERED,),
    rival: bool = True,
    subject_named: bool = True,
    answered: bool = True,
    audit: str | None = OK_AUDIT,
) -> Scan:
    """One scan with a prompt for each intent and one engine's answer to each.

    The defaults leave nothing out: awareness prompts, an answer that names the client with
    a sentiment and cites a tiered source, one rival in a detected set, a good audit. Each
    argument takes exactly one of those away.

    `rival=False` writes no competitor set at all (detection never ran). `answered=False`
    makes every engine call an error. `audit` is OK_AUDIT, the error code of a failed
    audit, or None for no audit row.
    """
    agency = Agency(id=ids.new_id(ids.AGENCY), name="Scoring Log", slug="scoring-log")
    session.add(agency)
    await session.flush()
    client = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency.id, name=SUBJECT, domain="subject.example",
        brand_name=SUBJECT, industry="customer support software",
        classification_status=ClassificationStatus.CLASSIFIED,
    )
    session.add(client)
    await session.flush()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency.id,
        status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL, formula_version="v5",
        prompt_count=len(intents), engine_result_count=len(intents),
    )
    session.add(scan)
    await session.flush()

    competitor: Competitor | None = None
    if rival:
        cset = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=DetectionStatus.OK
        )
        session.add(cset)
        await session.flush()
        competitor = Competitor(
            id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=RIVAL,
            domain="rival.example", rank=1, detection_source=DetectionSource.BOTH,
        )
        session.add(competitor)
        await session.flush()

    pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id)
    session.add(pset)
    await session.flush()
    named = answered and subject_named
    for position, intent in enumerate(intents, start=1):
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text=f"question {position}",
            intent=intent, position=position,
        )
        session.add(prompt)
        await session.flush()
        result = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompt.id,
            engine=Engine.CLAUDE_SEARCH,
            status=(
                EngineResultStatus.ERROR if not answered
                else EngineResultStatus.OK if named
                else EngineResultStatus.ANSWERED_NO_MENTION
            ),
            mentioned=named, position=1 if named else None,
            sentiment=Sentiment.POSITIVE if named else None,
            sentiment_confidence=Decimal("0.900") if named else None,
        )
        session.add(result)
        await session.flush()
        if not answered:
            continue
        if named:
            session.add(BrandMention(
                id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id,
                entity_name=SUBJECT, entity_domain="subject.example", is_subject=True,
                position=1,
            ))
        if competitor is not None:
            session.add(BrandMention(
                id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id,
                entity_name=RIVAL, entity_domain=competitor.domain, is_subject=False,
                competitor_id=competitor.id, position=2 if named else 1,
            ))
        for rank, domain in enumerate(cited, start=1):
            session.add(Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=result.id, source_domain=domain,
                source_url=f"https://{domain}/page", source_type=CitationType.OTHER,
                position=rank, cites_subject=False,
            ))
    await session.flush()

    if audit is not None:
        good = audit == OK_AUDIT
        session.add(TechnicalAudit(
            id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=scan.id,
            url_audited="https://subject.example/",
            status=AuditStatus.OK if good else AuditStatus.FAILED,
            error_code=None if good else audit,
            technical_foundation=Decimal("70.00") if good else None,
            audited_at=datetime(2026, 10, 4, 9, 0, tzinfo=UTC), pages_crawled=1 if good else 0,
        ))
    await session.commit()
    return scan


async def _score(session: AsyncSession, scan: Scan) -> tuple[dict[str, Any], Score, ScoreResult]:
    """Score the scan the way the product does and hand back the one line it logged.

    The row is committed and read back, so what it holds is what the database stored and not
    the Python object `score_scan` assigned it from.
    """
    with structlog.testing.capture_logs() as events:
        row, computed, _comparison = await scoring_runner.score_scan(session, scan)
    assert row is not None
    [line] = [e for e in events if e["event"] == "scoring.completed"]
    await session.commit()
    await session.refresh(row)
    return line, row, computed


# What each way of leaving a dimension out logs: the scan's shape (what `_seed` takes away from
# the default) and the mapping its line must carry. `excluded` is compared whole, so a dimension
# that is left out and not listed here fails the case, and so does one listed and not left out.
CASES = [
    pytest.param({}, {}, id="nothing_left_out"),
    pytest.param(
        {"cited": (UNTIERED,)}, {"citation_strength": "NO_AUTHORITY_DATA"},
        id="no_authority_data",
    ),
    pytest.param(
        {"intents": (CMP, BF)},
        {
            "citation_strength": "NO_AWARENESS_POPULATION",
            "mention_rate": "NO_AWARENESS_POPULATION",
            "share_of_voice": "NO_AWARENESS_POPULATION",
        },
        id="no_awareness_population",
    ),
    pytest.param(
        {"rival": False},
        {"citation_strength": "NO_COMPETITOR_SET", "share_of_voice": "NO_COMPETITOR_SET"},
        id="no_competitor_set",
    ),
    pytest.param({"subject_named": False}, {"sentiment": "NO_POPULATION"}, id="no_population"),
    pytest.param(
        {"answered": False}, {d.value: "NO_ANSWERED_RESULTS" for d in Dimension},
        id="no_answered_results",
    ),
    *[
        pytest.param(
            {"audit": audit}, {"technical_foundation": reason},
            id=f"technical_foundation_{reason.lower()}",
        )
        for audit, reason in TECHNICAL_FOUNDATION_CASES
    ],
]


class TestTheLineSaysWhy:
    @pytest.mark.parametrize(("shape", "expected"), CASES)
    async def test_each_dimension_left_out_carries_its_reason(
        self, session: AsyncSession, shape: dict[str, Any], expected: dict[str, str]
    ) -> None:
        scan = await _seed(session, **shape)
        line, row, _computed = await _score(session, scan)

        assert line["excluded"] == expected
        # The line says what the database holds, so a report and the log cannot disagree.
        assert row.excluded_dimensions == expected

    async def test_nothing_left_out_is_an_empty_mapping_and_the_scan_is_scored(
        self, session: AsyncSession
    ) -> None:
        """A `{}`: not a missing field, and not the `[]` the list it replaces was."""
        scan = await _seed(session)
        line, _row, computed = await _score(session, scan)

        assert computed.status == "scored" and line["status"] == "scored"
        assert "excluded" in line
        assert isinstance(line["excluded"], dict) and not line["excluded"]

    def test_the_cases_cover_every_reason_technical_foundation_can_record(self) -> None:
        """A fifth reason added to `scoring.py` fails here until its case is written."""
        assert {reason for _, reason in TECHNICAL_FOUNDATION_CASES} == set(
            TECHNICAL_FOUNDATION_EXCLUSIONS
        )

    async def test_several_reasons_at_once_come_in_dimension_order(
        self, session: AsyncSession
    ) -> None:
        """Sorted by dimension, as the list it replaces was, whatever order they were decided in.

        `compute_score` decides Citation Strength, then Share of Voice, then Sentiment, then
        Technical Foundation; that is not alphabetical, so a mapping logged as it came would
        read differently from one sorted, and the same scan's line would depend on code order.
        """
        scan = await _seed(
            session, cited=(UNTIERED,), rival=False, subject_named=False,
            audit=ROBOTS_DISALLOWED,
        )
        line, row, computed = await _score(session, scan)

        assert list(computed.excluded_dimensions) == [
            "citation_strength", "share_of_voice", "sentiment", "technical_foundation",
        ]
        assert list(line["excluded"].items()) == [
            ("citation_strength", "NO_AUTHORITY_DATA"),
            ("sentiment", "NO_POPULATION"),
            ("share_of_voice", "NO_COMPETITOR_SET"),
            ("technical_foundation", "CRAWL_DISALLOWED"),
        ]
        assert row.excluded_dimensions == dict(line["excluded"])


class TestEveryOtherFieldIsAsItWas:
    async def test_the_line_has_the_fields_it_had_and_no_more(self, session: AsyncSession) -> None:
        """Facts only: a field added to this line would be one more thing a log search shows.

        A scan with three dimensions left out and a flag raised, so that `excluded` and `flags`
        are both non-empty and a swap between them would show.
        """
        scan = await _seed(
            session, cited=(UNTIERED,), subject_named=False, audit=ROBOTS_DISALLOWED
        )
        line, _row, computed = await _score(session, scan)

        assert set(line) == LINE_FIELDS
        assert line["log_level"] == "info"
        assert line["scan_id"] == scan.id
        assert line["status"] == computed.status == "scored"
        assert line["composite"] == str(computed.composite) != "None"
        assert line["flags"] == computed.degradation_flags == ["NO_SENTIMENT_POPULATION"]
        assert line["inputs_digest"] == computed.inputs_digest[:16]
        assert line["results"] == 2 and line["competitors"] == 1
        assert set(line["excluded"]) == {"citation_strength", "sentiment", "technical_foundation"}

    async def test_a_scan_no_engine_answered_still_logs_its_status_and_flag(
        self, session: AsyncSession
    ) -> None:
        scan = await _seed(session, answered=False)
        line, _row, _computed = await _score(session, scan)

        assert set(line) == LINE_FIELDS
        assert line["status"] == "insufficient_data"
        assert line["composite"] == "None"
        assert line["flags"] == ["NO_ANSWERED_RESULTS"]
        assert line["results"] == 2 and line["competitors"] == 1
