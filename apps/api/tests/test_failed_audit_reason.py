"""A technical audit that failed has its own exclusion reason - launch checklist 3.87.

Until now a failed audit whose error code was not one of the three mapped ones
(`ROBOTS_DISALLOWED`, `ADDRESS_NOT_PUBLIC`, `ROBOTS_UNREACHABLE`) left Technical Foundation
excluded as `NOT_YET_MEASURED`, which a report words as "not yet checked", when an audit
exists and failed. It is now `AUDIT_FAILED`. What these tests hold:

1. **Every unmapped failure is AUDIT_FAILED**: each kind of code `technical_audit` and
   `crawl` produce (`TIMEOUT`, `BROWSER_BUSY`, `BROWSER_ERROR`, `FETCH_FAILED`, `HTTP_404`,
   `HTTP_503`) and a failed audit with no code at all.
2. **The rest are as they were**: the three mapped codes keep their reasons, and no audit
   row at all is still `NOT_YET_MEASURED`.
3. **Scoring did not move.** The reason is a label: the composite, every sub-score and the
   weights are the same whichever reason Technical Foundation is excluded under (and equal to
   the composite of a scan with no audit at all). Only `excluded_dimensions`, the flag a
   `NOT_YET_MEASURED` raises, and the digest (which has always recorded the reason) differ.
   `FORMULA_VERSION` stays `v5`.
4. **The lines say it.** `scoring.completed` carries the reason in `excluded`;
   `audit.completed` carries the audit's status, its error code and the reason Technical
   Foundation will be left out under, codes only.

Nothing here reaches a provider: rows are written by the test and the audit is faked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import (
    Agency,
    AuditStatus,
    BrandMention,
    Client,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptIntent,
    PromptSet,
    Scan,
    ScanStatus,
    ScanTrigger,
    Sentiment,
    TechnicalAudit,
)
from avp_api.models.client import ClassificationStatus
from avp_api.services import audit_runner, scoring_runner
from avp_api.services.crawl import ROBOTS_DISALLOWED, ROBOTS_UNREACHABLE
from avp_api.services.egress import ADDRESS_NOT_PUBLIC
from avp_api.services.scoring import (
    FORMULA_VERSION,
    TECHNICAL_FOUNDATION_EXCLUSIONS,
    compute_score,
)
from avp_api.services.technical_audit import AuditSignals

UNMAPPED = ["TIMEOUT", "BROWSER_BUSY", "BROWSER_ERROR", "FETCH_FAILED", "HTTP_404", "HTTP_503"]
MAPPED = [
    (ROBOTS_DISALLOWED, "CRAWL_DISALLOWED"),
    (ADDRESS_NOT_PUBLIC, "CRAWL_ADDRESS_NOT_PUBLIC"),
    (ROBOTS_UNREACHABLE, "CRAWL_POLICY_UNREADABLE"),
]


async def _scan(session: AsyncSession) -> Scan:
    agency_id = ids.new_id(ids.AGENCY)
    agency = Agency(
        id=agency_id, name="Failed Audit", slug=f"failed-audit-{agency_id[-8:].lower()}"
    )
    session.add(agency)
    await session.flush()
    client = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency.id, name="Subject Co",
        domain="subject.example", brand_name="Subject Co", industry="software",
        classification_status=ClassificationStatus.CLASSIFIED,
    )
    session.add(client)
    await session.flush()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency.id,
        status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL, formula_version="v5",
        prompt_count=1, engine_result_count=1,
    )
    session.add(scan)
    await session.flush()
    pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id)
    session.add(pset)
    await session.flush()
    prompt = Prompt(
        id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text="question",
        intent=PromptIntent.AWARENESS, position=1,
    )
    session.add(prompt)
    await session.flush()
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompt.id,
        engine=Engine.CLAUDE_SEARCH, status=EngineResultStatus.OK, mentioned=True, position=1,
        sentiment=Sentiment.POSITIVE, sentiment_confidence=Decimal("0.900"),
    )
    session.add(result)
    await session.flush()
    session.add(BrandMention(
        id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id,
        entity_name="Subject Co", entity_domain="subject.example", is_subject=True, position=1,
    ))
    await session.flush()
    return scan


def _failed_audit(scan: Scan, error_code: str | None) -> TechnicalAudit:
    return TechnicalAudit(
        id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=scan.id,
        url_audited="https://subject.example/", status=AuditStatus.FAILED,
        error_code=error_code, technical_foundation=None, pages_crawled=0,
        audited_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
    )


async def _score(session: AsyncSession, scan: Scan):  # noqa: ANN202
    with structlog.testing.capture_logs() as events:
        _row, computed, _cmp = await scoring_runner.score_scan(session, scan)
    [line] = [e for e in events if e["event"] == "scoring.completed"]
    return computed, line


class TestReason:
    @pytest.mark.parametrize("code", [*UNMAPPED, None])
    async def test_a_failed_audit_with_any_other_code_is_audit_failed(
        self, session: AsyncSession, code: str | None
    ) -> None:
        scan = await _scan(session)
        session.add(_failed_audit(scan, code))
        await session.flush()
        value, reason = await scoring_runner.load_technical_foundation(session, scan.id)
        assert value is None
        assert reason == "AUDIT_FAILED"

    @pytest.mark.parametrize(("code", "expected"), MAPPED)
    async def test_the_three_mapped_codes_are_unchanged(
        self, session: AsyncSession, code: str, expected: str
    ) -> None:
        scan = await _scan(session)
        session.add(_failed_audit(scan, code))
        await session.flush()
        assert await scoring_runner.load_technical_foundation(session, scan.id) == (None, expected)

    async def test_no_audit_row_is_still_not_yet_measured(self, session: AsyncSession) -> None:
        scan = await _scan(session)
        assert await scoring_runner.load_technical_foundation(session, scan.id) == (None, None)
        computed, line = await _score(session, scan)
        assert computed.excluded_dimensions["technical_foundation"] == "NOT_YET_MEASURED"
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" in computed.degradation_flags
        assert line["excluded"]["technical_foundation"] == "NOT_YET_MEASURED"

    def test_the_new_reason_is_a_recognised_one(self) -> None:
        assert "AUDIT_FAILED" in TECHNICAL_FOUNDATION_EXCLUSIONS


class TestTheWordsShown:
    def test_the_pdf_has_the_agreed_label_and_sentence_for_the_reason(self) -> None:
        from avp_api.services.report_narrative import EXCLUSION_DETAIL, EXCLUSION_REASON

        assert EXCLUSION_REASON["AUDIT_FAILED"] == "Technical check failed"
        assert EXCLUSION_DETAIL["AUDIT_FAILED"] == (
            "The technical check of the site could not be completed for this scan. Technical "
            "Foundation is left out of the score."
        )

    @pytest.mark.parametrize("code", [*UNMAPPED, None])
    async def test_every_unmapped_code_maps_to_the_reason_whose_words_those_are(
        self, session: AsyncSession, code: str | None
    ) -> None:
        from avp_api.services.report_narrative import EXCLUSION_DETAIL

        scan = await _scan(session)
        session.add(_failed_audit(scan, code))
        await session.flush()
        _value, reason = await scoring_runner.load_technical_foundation(session, scan.id)
        assert reason is not None
        assert reason == "AUDIT_FAILED"
        assert EXCLUSION_DETAIL[reason] == (
            "The technical check of the site could not be completed for this scan. Technical "
            "Foundation is left out of the score."
        )


class TestScoringDidNotMove:
    async def test_the_composite_is_the_same_for_a_failed_audit_and_for_none(
        self, session: AsyncSession
    ) -> None:
        none_scan = await _scan(session)
        failed_scan = await _scan(session)
        session.add(_failed_audit(failed_scan, "TIMEOUT"))
        await session.flush()

        none_result, _ = await _score(session, none_scan)
        failed_result, failed_line = await _score(session, failed_scan)

        assert failed_result.composite is not None
        assert failed_result.composite == none_result.composite
        assert failed_result.formula_version == none_result.formula_version == FORMULA_VERSION
        def shape(result):  # noqa: ANN001, ANN202
            return {d: (s.value, s.included, s.weight) for d, s in result.sub_scores.items()}

        assert shape(failed_result) == shape(none_result)
        assert none_result.excluded_dimensions["technical_foundation"] == "NOT_YET_MEASURED"
        assert failed_result.excluded_dimensions["technical_foundation"] == "AUDIT_FAILED"
        assert failed_line["excluded"]["technical_foundation"] == "AUDIT_FAILED"
        # No flag says "had not been audited": an audit exists, and it failed.
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in failed_result.degradation_flags

    def test_the_reason_changes_no_number_in_compute_score(self) -> None:
        results = [
            compute_score(
                [], [], competitor_set_status=None, technical_foundation=None,
                technical_foundation_exclusion=reason,
            )
            for reason in (None, "AUDIT_FAILED", "CRAWL_DISALLOWED")
        ]
        assert len({r.composite for r in results}) == 1
        assert len({r.formula_version for r in results}) == 1
        assert FORMULA_VERSION == "v5"


class TestTheAuditLine:
    @pytest.mark.parametrize("code", UNMAPPED)
    async def test_a_failed_audit_logs_its_status_code_and_reason(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, code: str
    ) -> None:
        scan = await _scan(session)
        client = await session.get(Client, scan.client_id)
        assert client is not None

        async def fake_audit(url: str, **kwargs: object) -> AuditSignals:  # noqa: ARG001
            return AuditSignals(url=url, domain=client.domain, ok=False, error_code=code)

        monkeypatch.setattr(audit_runner, "audit_site", fake_audit)
        with structlog.testing.capture_logs() as events:
            await audit_runner.run_audit(session, scan, client)
        [line] = [e for e in events if e["event"] == "audit.completed"]

        assert line["status"] == "failed"
        assert line["error_code"] == code
        assert line["technical_foundation_reason"] == "AUDIT_FAILED"
        assert "NOT_YET_MEASURED" not in str(line)
        # The fields this item added are codes: upper-case words, never a URL or text.
        for field in ("status", "error_code", "technical_foundation_reason"):
            assert "/" not in str(line[field]) and " " not in str(line[field])

    @pytest.mark.parametrize(("code", "expected"), MAPPED)
    async def test_a_mapped_failure_logs_its_own_reason(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, code: str, expected: str
    ) -> None:
        scan = await _scan(session)
        client = await session.get(Client, scan.client_id)
        assert client is not None

        async def fake_audit(url: str, **kwargs: object) -> AuditSignals:  # noqa: ARG001
            return AuditSignals(url=url, domain=client.domain, ok=False, error_code=code)

        monkeypatch.setattr(audit_runner, "audit_site", fake_audit)
        with structlog.testing.capture_logs() as events:
            await audit_runner.run_audit(session, scan, client)
        [line] = [e for e in events if e["event"] == "audit.completed"]
        assert line["technical_foundation_reason"] == expected

    async def test_a_good_audit_logs_no_reason(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scan = await _scan(session)
        client = await session.get(Client, scan.client_id)
        assert client is not None

        async def fake_audit(url: str, **kwargs: object) -> AuditSignals:  # noqa: ARG001
            return AuditSignals(
                url=url, domain=client.domain, http_status=200, ok=True,
                schema_types=["Organization"], has_organization_schema=True,
                has_title=True, has_meta_description=True, canonical_present=True,
                robots_txt_present=True, robots_allows_crawl=True, has_sitemap=True,
                is_indexable=True, h1_count=1, word_count=900, content_age_days=30,
                lcp_ms=1200, cls=Decimal("0.02"),
            )

        monkeypatch.setattr(audit_runner, "audit_site", fake_audit)
        with structlog.testing.capture_logs() as events:
            await audit_runner.run_audit(session, scan, client)
        [line] = [e for e in events if e["event"] == "audit.completed"]
        assert line["status"] in ("ok", "partial")
        assert line["technical_foundation_reason"] is None
