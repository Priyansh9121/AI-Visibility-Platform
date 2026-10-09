"""Opening a report or a score is a read — item R1.

In production, after a scan finished, the log said `scoring.completed` again
(the same `inputs_digest`, `status=scored`) each time its report was opened: on
`GET /scans/{id}/report` and on `POST /reports/shared`. Nothing was being
written. What the reads WERE doing is scoring the whole scan again, to
get the two things a stored Score does not hold (the subject's average position
and the per-rival comparison), and the scoring function logged itself whether
or not it had persisted. A scoring line that a page view produces cannot tell
anyone that a scan was scored, which is the only thing it exists to say.

What these tests hold:

1. **Nothing is written.** Every read path, three times over, leaves every row
   of every table byte for byte as it was: every column, `computed_at` included,
   and Postgres's own `xmin` and `xmax`, which move on an UPDATE that writes
   the same value back and when a row is locked. No statement that writes or
   locks a row is issued either. A write here would be a score changing under a
   reader, which scoring-spec.md rule 5 forbids.
2. **Nothing is scored.** No read path reaches `score_scan`, `compute_score` or
   the inputs digest, and `compare_on_read` loads only what the comparison uses.
3. **The figures are the ones the recompute gave.** `comparison_from_facts` is
   held equal to `compute_score`'s own over randomised facts, `compare_on_read`
   equal to `score_scan(persist=False)` over stored rows in every shape of scan
   the product makes, and the report and score bodies equal to what they were
   when a read re-scored the scan.
4. **The log says what happened.** No read path logs `scoring.completed`; a scan
   that is scored still does.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from ulid import ULID

from avp_api import ids
from avp_api.models import (
    ActionItem,
    ActionItemSource,
    ActionItemStatus,
    AiCrawlerAccess,
    AuditStatus,
    Base,
    BrandMention,
    Citation,
    CitationType,
    ClassificationStatus,
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Effort,
    Engine,
    EngineResult,
    EngineResultStatus,
    Priority,
    Prompt,
    PromptIntent,
    PromptSet,
    Scan,
    ScanStatus,
    ScanTrigger,
    Score,
    ScoreStatus,
    Sentiment,
    TechnicalAudit,
    TechnicalAuditCheck,
)
from avp_api.models.technical_audit import CheckStatus
from avp_api.services import scoring, scoring_runner
from avp_api.services.ai_crawlers import AccessVerdict, AgentPurpose, RuleSource
from avp_api.services.domain_authority import CURATED_ONLY, DomainAuthority
from avp_api.services.scoring import (
    CompetitorFacts,
    ReadComparison,
    ResultFacts,
    ScoreResult,
    comparison_from_facts,
    compute_score,
)

BASE = "/api/v1"
SUBJECT = "Help Scout"
SUBJECT_DOMAIN = "helpscout.com"

# Tier B needs a domain cited across three industries and five clients. These
# two are cited by the main scan and by the five clients `_seed_platform_tier`
# adds, so they carry a tier only while those clients are on the platform.
TIER_B = ("tier-b-one.example", "tier-b-two.example")
PLAIN = tuple(f"plain-{i}.example" for i in range(6))
CURATED = ("g2.com", "wikipedia.org")

AW, CMP, BF = PromptIntent.AWARENESS, PromptIntent.COMPARISON, PromptIntent.BOTTOM_FUNNEL


# ---------------------------------------------------------------------------
# A scan, seeded in whatever shape the product can make one
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """One scan's shape. The defaults are the ordinary, well-formed one."""

    # The subject's own domain. Unique per agency, so a second scan on one agency moves it.
    domain: str = SUBJECT_DOMAIN
    rivals: tuple[str, ...] = ("Zendesk", "Freshdesk", "Intercom")
    # None means no competitor set row at all (detection never ran).
    set_status: DetectionStatus | None = DetectionStatus.OK
    # Rivals an operator struck: kept as tombstones, not part of the set.
    tombstones: tuple[str, ...] = ()
    manual: tuple[str, ...] = ()
    intents: tuple[PromptIntent, ...] = (AW, AW, AW, AW, CMP, CMP, BF, BF)
    engines: tuple[Engine, ...] = (Engine.CLAUDE_SEARCH, Engine.PERPLEXITY, Engine.CHATGPT)
    # False: every call failed, so nothing was answered.
    answered: bool = True
    # False: the awareness prompts' calls failed, the rest answered.
    awareness_answered: bool = True
    # False: no answer ever names the subject.
    subject_named: bool = True
    audit: bool = True
    # A Score row from an earlier formula, so `previousFormulaVersions` is real.
    earlier_version: str | None = "v4"
    seed: int = 7


@dataclass(frozen=True)
class Seeded:
    agency_id: str
    client_id: str
    scan_id: str


async def _seed_platform_tier(session: AsyncSession, agency_id: str) -> None:
    """Five other clients in three industries, each citing both TIER_B domains.

    That is Tier B's threshold exactly (three industries, five clients), so the
    two domains carry a platform tier for as long as these clients are live.
    """
    industries = ("dental practice", "web analytics software", "agricultural machinery")
    for n in range(5):
        client = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=f"Other {n}",
            domain=f"other-{n}.example", industry=industries[n % 3],
            classification_status=ClassificationStatus.CLASSIFIED,
        )
        session.add(client)
        await session.flush()
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency_id,
            status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL, formula_version="v5",
        )
        session.add(scan)
        await session.flush()
        pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id)
        session.add(pset)
        await session.flush()
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text="best tool?",
            intent=AW, position=1,
        )
        session.add(prompt)
        await session.flush()
        result = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompt.id,
            engine=Engine.CLAUDE_SEARCH, status=EngineResultStatus.OK, mentioned=True, position=1,
        )
        session.add(result)
        await session.flush()
        for i, domain in enumerate(TIER_B, start=1):
            session.add(Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=result.id, source_domain=domain,
                source_url=f"https://{domain}/page", source_type=CitationType.OTHER,
                position=i, cites_subject=False,
            ))
    await session.commit()


async def _seed_scan(session: AsyncSession, agency_id: str, spec: Spec) -> Seeded:
    """The scan, its competitor set, prompts, answers, audit and fixes, then scored.

    Scored by `score_scan` itself, so the Score row is the one the product
    writes, with a real digest and `computed_at`.
    """
    rng = random.Random(spec.seed)
    client = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=SUBJECT, domain=spec.domain,
        brand_name=SUBJECT, industry="customer support software",
        classification_status=ClassificationStatus.CLASSIFIED,
    )
    session.add(client)
    await session.flush()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency_id,
        status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL, formula_version="v5",
        prompt_count=len(spec.intents), engine_result_count=len(spec.intents) * len(spec.engines),
        started_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 1, 9, 5, tzinfo=UTC),
    )
    session.add(scan)
    await session.flush()

    competitors: dict[str, Competitor] = {}
    if spec.set_status is not None:
        cset = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=spec.set_status,
            detection_confidence=Decimal("0.667"), serp_queries_run=2,
            co_citation_prompts_run=3, candidates_considered=6,
            detected_at=datetime(2026, 10, 1, 8, 55, tzinfo=UTC),
        )
        session.add(cset)
        await session.flush()
        for rank, name in enumerate([*spec.rivals, *spec.tombstones], start=1):
            competitors[name] = Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name,
                domain=f"{name.lower()}.com", rank=rank,
                detection_source=(
                    DetectionSource.MANUAL if name in spec.manual else DetectionSource.BOTH
                ),
                signal_count=2, serp_mentions=2, co_citation_mentions=2,
                corroborated=name not in spec.manual, score=Decimal("4.500"),
                is_manual_override=name in spec.manual, is_suppressed=name in spec.tombstones,
            )
            session.add(competitors[name])
        await session.flush()

    pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id, generated_by="test")
    session.add(pset)
    await session.flush()
    prompts = []
    for position, intent in enumerate(spec.intents, start=1):
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text=f"question {position}",
            intent=intent, position=position,
        )
        prompts.append(prompt)
        session.add(prompt)
    await session.flush()

    pool = [*CURATED, *TIER_B, *PLAIN]
    for prompt in prompts:
        for engine in spec.engines:
            failed = not spec.answered or (prompt.intent is AW and not spec.awareness_answered)
            names: list[str] = []
            if not failed:
                if spec.subject_named and rng.random() < (0.55 if prompt.intent is AW else 0.95):
                    names.append(SUBJECT)
                names += [r for r in spec.rivals if rng.random() < 0.5]
                if rng.random() < 0.3:
                    names.append("Unlisted Brand")
                rng.shuffle(names)
            mentioned = SUBJECT in names
            result = EngineResult(
                id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompt.id,
                engine=engine, engine_version="test",
                status=(
                    EngineResultStatus.ERROR if failed
                    else EngineResultStatus.OK if mentioned
                    else EngineResultStatus.ANSWERED_NO_MENTION
                ),
                mentioned=mentioned, position=names.index(SUBJECT) + 1 if mentioned else None,
                sentiment=rng.choice(list(Sentiment)) if mentioned else None,
                sentiment_confidence=Decimal("0.900") if mentioned else None,
                brands_mentioned=len(names), latency_ms=800,
            )
            session.add(result)
            await session.flush()
            for position, name in enumerate(names, start=1):
                rival = competitors.get(name)
                session.add(BrandMention(
                    id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id,
                    entity_name=name,
                    entity_domain=spec.domain if name == SUBJECT else (
                        rival.domain if rival else None
                    ),
                    is_subject=name == SUBJECT, competitor_id=rival.id if rival else None,
                    position=position,
                ))
            if not failed and (engine is not Engine.CHATGPT or rng.random() < 0.4):
                cited = rng.sample(pool, rng.randint(2, 6))
                if rng.random() < 0.2:
                    cited.append(spec.domain)
                cited += [
                    competitors[r].domain for r in spec.rivals
                    if r in names and rng.random() < 0.4
                ]
                for position, domain in enumerate(dict.fromkeys(cited), start=1):
                    rival = next((c for c in competitors.values() if c.domain == domain), None)
                    session.add(Citation(
                        id=ids.new_id(ids.CITATION), engine_result_id=result.id,
                        source_domain=domain, source_url=f"https://{domain}/p{position}",
                        source_type=CitationType.OTHER, position=position,
                        cites_subject=domain == spec.domain,
                        competitor_id=rival.id if rival else None,
                    ))
    await session.flush()

    if spec.audit:
        audit = TechnicalAudit(
            id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=scan.id,
            url_audited=f"https://{spec.domain}/", status=AuditStatus.OK,
            technical_foundation=Decimal("71.00"),
            audited_at=datetime(2026, 10, 1, 9, 2, tzinfo=UTC), pages_crawled=1,
            schema_types=["Organization"], has_organization_schema=True, is_indexable=True,
            robots_allows_crawl=True, has_sitemap=True, canonical_present=True,
        )
        session.add(audit)
        await session.flush()
        for key, status in (
            ("title", CheckStatus.PASS), ("canonical", CheckStatus.PASS),
            ("faq_schema", CheckStatus.WARN), ("product_schema", CheckStatus.FAIL),
        ):
            session.add(TechnicalAuditCheck(
                id=ids.new_id(ids.AUDIT_CHECK), audit_id=audit.id, check_key=key, status=status,
                detail_code=f"MISSING_{key.upper()}" if status is not CheckStatus.PASS else None,
            ))
        session.add(AiCrawlerAccess(
            id=ids.new_id(ids.AI_CRAWLER_ACCESS), audit_id=audit.id, agent_token="gptbot",
            vendor="OpenAI", purpose=AgentPurpose.TRAINING, verdict=AccessVerdict.ALLOWED,
            rule_source=RuleSource.EXPLICIT, matched_token="gptbot", disallow_rules=0,
        ))
    for rank, (source, key) in enumerate(
        [(ActionItemSource.GAP, "gap:mention_rate"), (ActionItemSource.AUDIT, "audit:faq_schema")],
        start=1,
    ):
        session.add(ActionItem(
            id=ids.new_id(ids.ACTION_ITEM), scan_id=scan.id, title=f"Fix {rank}",
            detail=f"Detail {rank}", priority=Priority.HIGH, effort=Effort.M,
            status=ActionItemStatus.OPEN, source=source, source_key=key, rank=rank,
            dimension_key="mention_rate", points_upside=Decimal("4.00"), generated_by="test",
        ))
    if spec.earlier_version is not None:
        # Dated before the scoring below, so the newest row, the one a report
        # shows, is still the current formula's.
        session.add(Score(
            id=f"{ids.SCORE}_{ULID.from_datetime(datetime(2026, 9, 1, tzinfo=UTC))}",
            scan_id=scan.id, status=ScoreStatus.SCORED, composite=Decimal("41.50"),
            formula_version=spec.earlier_version, weights={}, excluded_dimensions={},
            degradation_flags=[], inputs_digest="0" * 64,
            computed_at=datetime(2026, 9, 1, tzinfo=UTC),
        ))
    await session.commit()

    scan = await session.get(Scan, scan.id)
    assert scan is not None
    await scoring_runner.score_scan(session, scan)
    await session.commit()
    return Seeded(agency_id=agency_id, client_id=client.id, scan_id=scan.id)


async def _sign_up(client: AsyncClient) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Reads Agency", "fullName": "Op", "email": "reads@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["agency"]["id"])


def _stranger(client: AsyncClient) -> AsyncClient:
    """Somebody holding a link: the same app, no cookie."""
    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    return AsyncClient(
        transport=transport, base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    )


async def _share(client: AsyncClient, scan_id: str) -> str:
    """Mint the share link: a deliberate write, made before anything is snapshotted."""
    resp = await client.post(f"{BASE}/scans/{scan_id}/share")
    assert resp.status_code == 200, resp.text
    return str(resp.json()["token"])


@dataclass
class Reads:
    report: dict[str, Any]
    score: dict[str, Any]
    public: dict[str, Any]
    history: dict[str, Any] | None


async def _read_every_path(
    client: AsyncClient,
    stranger: AsyncClient,
    seeded: Seeded,
    token: str,
    *,
    history: bool = True,
    pdf: bool = True,
) -> Reads:
    """The signed-in report and the public one (JSON and PDF each), the score, the history, and
    the agency's rival suggestions (F2)."""
    sid = seeded.scan_id
    report = await client.get(f"{BASE}/scans/{sid}/report")
    assert report.status_code == 200, report.text
    public = await stranger.post(f"{BASE}/reports/shared", json={"token": token})
    assert public.status_code == 200, public.text
    if pdf:
        signed_in = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert signed_in.status_code == 200
        assert signed_in.headers["content-type"] == "application/pdf"
        shared = await stranger.post(f"{BASE}/reports/shared/pdf", json={"token": token})
        assert shared.status_code == 200 and shared.content.startswith(b"%PDF")
    score = await client.get(f"{BASE}/scans/{sid}/score")
    assert score.status_code == 200, score.text
    hist: dict[str, Any] | None = None
    if history:
        resp = await client.get(f"{BASE}/clients/{seeded.client_id}/history")
        assert resp.status_code == 200, resp.text
        hist = resp.json()
    suggestions = await client.get(f"{BASE}/clients/{seeded.client_id}/rival-suggestions")
    assert suggestions.status_code == 200, suggestions.text
    return Reads(report.json(), score.json(), public.json(), hist)


# ---------------------------------------------------------------------------
# What a read may and may not do to the database
# ---------------------------------------------------------------------------

_WRITES = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY|ALTER|DROP|CREATE|LOCK)\b"
    r"|\bFOR\s+(NO\s+KEY\s+)?(UPDATE|SHARE)\b|\bFOR\s+KEY\s+SHARE\b",
    re.IGNORECASE,
)


class _Trace:
    """Every statement the engine runs while it is open."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine.sync_engine
        self.statements: list[str] = []

    def __enter__(self) -> _Trace:
        event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._engine, "before_cursor_execute", self._record)

    def _record(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.statements.append(" ".join(statement.split()))

    @property
    def writes(self) -> list[str]:
        """Statements that write a row or lock one."""
        return [s for s in self.statements if _WRITES.search(s)]

    def touching(self, table: str) -> list[str]:
        return [s for s in self.statements if re.search(rf"\b{table}\b", s)]


Snapshot = dict[str, list[dict[str, Any]]]


async def _snapshot(session: AsyncSession) -> Snapshot:
    """Every row of every table, every column, with the row's own xmin and xmax."""
    await session.rollback()
    out: Snapshot = {}
    for table in Base.metadata.sorted_tables:
        order = ", ".join(f'"{c.name}"' for c in table.primary_key.columns)
        rows = (
            await session.execute(
                text(
                    f'SELECT xmin::text AS "_xmin", xmax::text AS "_xmax", * '
                    f'FROM "{table.name}" ORDER BY {order}'
                )
            )
        ).mappings().all()
        out[table.name] = [dict(r) for r in rows]
    await session.rollback()
    return out


def _diff(before: Snapshot, after: Snapshot) -> list[str]:
    """Where two snapshots differ, as `table row column: before -> after`."""
    out: list[str] = []
    for table, rows in before.items():
        later = after[table]
        if len(rows) != len(later):
            out.append(f"{table}: {len(rows)} rows -> {len(later)}")
            continue
        for old, new in zip(rows, later, strict=True):
            out += [
                f"{table} {old.get('id')} {column}: {old[column]!r} -> {new.get(column)!r}"
                for column in old
                if old[column] != new.get(column)
            ]
    return out


class TestReadingWritesNothing:
    async def test_every_row_is_as_it_was_after_every_read_path_ran_three_times(
        self, client: AsyncClient, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        """The signed-in report and PDF, the public report and PDF, the score, the history.

        Compared before and after, all rows in all tables: the scan's Score rows
        (the earlier formula's and the current one's, `computed_at` and the
        timestamps with them), its competitor set and competitors, its audit,
        checks and crawler verdicts, its prompts, results, brand mentions and
        citations, its fixes, the scan itself, the client and the agency. And
        `xmin`/`xmax` with them, so a row written with the value it already had,
        or locked, is a difference too.
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())
        token = await _share(client, seeded.scan_id)

        before = await _snapshot(session)
        # Not vacuous: the scan owns what the paths read.
        assert len(before["scores"]) == 2
        assert len(before["engine_results"]) == 8 * 3 + 5
        assert len(before["competitors"]) == 3
        assert len(before["technical_audits"]) == 1
        assert before["scans"] and before["engine_result_citations"]

        async with _stranger(client) as stranger:
            with _Trace(engine) as trace:
                for _ in range(3):
                    await _read_every_path(client, stranger, seeded, token)
        after = await _snapshot(session)

        assert len(trace.statements) > 3 * 6, "the trace saw the reads"
        assert trace.writes == []
        assert _diff(before, after) == []
        assert [r["computed_at"] for r in after["scores"]] == [
            r["computed_at"] for r in before["scores"]
        ]

    async def test_the_snapshot_sees_a_row_rewritten_with_the_value_it_had(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The control for the test above: it can fail.

        An UPDATE that changes nothing leaves every column as it was and still
        moves `xmin`; one that changes `computed_at` moves that column. Both are
        seen, so a read that did either would not pass.
        """
        agency_id = await _sign_up(client)
        seeded = await _seed_scan(session, agency_id, Spec())
        before = await _snapshot(session)

        await session.execute(
            text("UPDATE scores SET composite = composite WHERE scan_id = :s"),
            {"s": seeded.scan_id},
        )
        await session.commit()
        same_value = _diff(before, await _snapshot(session))
        assert same_value and all("_xmin" in line for line in same_value), same_value

        await session.execute(
            text("UPDATE scores SET computed_at = now() WHERE scan_id = :s"), {"s": seeded.scan_id}
        )
        await session.commit()
        assert any("computed_at" in line for line in _diff(before, await _snapshot(session)))

    async def test_a_write_trace_flags_a_statement_that_locks_a_row(
        self, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """The control for the trace: `SELECT ... FOR UPDATE` is a write of the lock kind."""
        agency_id = await _sign_up(client)
        seeded = await _seed_scan(session, agency_id, Spec())
        with _Trace(engine) as trace:
            await session.execute(
                text("SELECT id FROM scans WHERE id = :s FOR UPDATE"), {"s": seeded.scan_id}
            )
            await session.rollback()
        assert len(trace.writes) == 1


class TestRivalSuggestionsAreARead:
    """`GET /clients/{clientId}/rival-suggestions` (F2): the agency's suggestion of a probable
    rival is read from what a scan recorded and writes nothing, whichever way it is asked."""

    async def test_asking_three_times_leaves_every_row_as_it_was_and_locks_none(
        self, client: AsyncClient, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        agency_id = await _sign_up(client)
        seeded = await _seed_scan(session, agency_id, Spec(domain="rmit.edu.au"))
        # The scan names Deakin in four answers and cited it: a suggestion to be read, so the
        # route does its whole work (the scan, its citations, the lists) and not the empty path.
        await session.execute(
            text("UPDATE scans SET named_domains = CAST(:v AS jsonb) WHERE id = :s"),
            {"v": '{"deakin.edu.au": 4, "qilt.edu.au": 0}', "s": seeded.scan_id},
        )
        first = (await session.execute(
            text("SELECT id FROM engine_results WHERE scan_id = :s ORDER BY id LIMIT 1"),
            {"s": seeded.scan_id},
        )).scalar_one()
        for n, domain in enumerate(("deakin.edu.au", "qilt.edu.au"), start=90):
            session.add(Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=first, source_domain=domain,
                source_url=f"https://{domain}/p", source_type=CitationType.OTHER, position=n,
                cites_subject=False,
            ))
        await session.commit()
        before = await _snapshot(session)
        url = f"{BASE}/clients/{seeded.client_id}/rival-suggestions"

        with _Trace(engine) as trace:
            answers = [await client.get(url) for _ in range(3)]
        after = await _snapshot(session)

        assert [a.status_code for a in answers] == [200, 200, 200]
        body = answers[0].json()
        assert [s["domain"] for s in body["suggestions"]] == ["deakin.edu.au"], "not vacuous"
        (only,) = body["suggestions"]
        assert only["citations"] == 1 and only["name"] == "Deakin"
        assert all(a.json() == body for a in answers)
        assert trace.touching("scans") and trace.touching("engine_result_citations")
        assert trace.writes == []
        assert _diff(before, after) == []


# ---------------------------------------------------------------------------
# Nothing is scored on a read
# ---------------------------------------------------------------------------


class TestReadsDoNotScore:
    async def test_no_read_path_reaches_scoring(
        self,
        client: AsyncClient,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each of the scoring functions is made to raise, and every read still answers.

        `score_scan`, `compute_score` (by its name in both modules) and the
        inputs digest. The history is left out on purpose: it still calls
        `score_scan(persist=False)`, for the subject's recomputed share of voice.
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())
        token = await _share(client, seeded.scan_id)

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("a read path scored the scan")

        monkeypatch.setattr(scoring_runner, "score_scan", refuse)
        monkeypatch.setattr(scoring_runner, "compute_score", refuse)
        monkeypatch.setattr(scoring, "compute_score", refuse)
        monkeypatch.setattr(scoring, "compute_inputs_digest", refuse)

        async with _stranger(client) as stranger:
            reads = await _read_every_path(client, stranger, seeded, token, history=False)
        assert reads.report["competitorSet"]["competitors"]
        assert reads.score["competitors"]

    async def test_the_comparison_reads_neither_the_audit_nor_the_scores(
        self, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Technical Foundation is a sub-score input and the comparison has none.

        The row it is given carries what it needs, so it does not read `scores`
        either; the report reads the row once, for itself.
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as fresh:
            scan = await fresh.get(Scan, seeded.scan_id)
            row = await scoring_runner.latest_score(fresh, seeded.scan_id)
            assert scan is not None and row is not None
            with _Trace(engine) as trace:
                comparison = await scoring_runner.compare_on_read(fresh, scan, row)

        assert comparison.competitors
        assert trace.statements, "it did read something"
        for table in ("technical_audits", "technical_audit_checks", "scores", "action_items"):
            assert trace.touching(table) == [], table
        assert trace.writes == []

    async def test_the_platform_tier_is_read_once_when_there_are_rivals_and_never_when_not(
        self, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Tiers are read to score a rival's Citation Strength, so no rival, no tier read."""
        agency_id = await _sign_up(client)
        with_rivals = await _seed_scan(session, agency_id, Spec())

        # A second client: the agency's domains are unique.
        other = await _seed_scan(
            session, agency_id, Spec(set_status=None, rivals=(), domain="second.example")
        )

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        aggregate = "GROUP BY engine_result_citations.source_domain"

        async def aggregates(seeded: Seeded) -> int:
            async with factory() as fresh:
                scan = await fresh.get(Scan, seeded.scan_id)
                row = await scoring_runner.latest_score(fresh, seeded.scan_id)
                assert scan is not None and row is not None
                with _Trace(engine) as trace:
                    await scoring_runner.compare_on_read(fresh, scan, row)
            return sum(aggregate in s for s in trace.statements)

        assert await aggregates(with_rivals) == 1
        assert await aggregates(other) == 0

    async def test_a_stored_insufficient_data_row_has_no_comparison_and_loads_nothing(
        self, engine: AsyncEngine
    ) -> None:
        """`compute_score` answered such a scan with none; the stored status is that answer."""
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        row = Score(
            id=ids.new_id(ids.SCORE), scan_id="scan_never_loaded",
            status=ScoreStatus.INSUFFICIENT_DATA, composite=None,
        )
        scan = Scan(id="scan_never_loaded")
        async with factory() as fresh:
            with _Trace(engine) as trace:
                comparison = await scoring_runner.compare_on_read(fresh, scan, row)
        assert comparison == ReadComparison()
        assert trace.statements == []


# ---------------------------------------------------------------------------
# The figures are the ones the recompute gave
# ---------------------------------------------------------------------------


def _figures(read: ReadComparison | ScoreResult) -> tuple[Any, ...]:
    """The three derived figures, as strings, so `50.0` and `50.00` are not equal."""
    return (
        None if read.average_position is None else str(read.average_position),
        read.position_count,
        [
            (
                c.competitor_id, c.name, str(c.mention_rate), str(c.share_of_voice),
                None if c.citation_strength is None else str(c.citation_strength),
                None if c.average_position is None else str(c.average_position),
                c.position_count,
            )
            for c in read.competitors
        ],
    )


_CITE_POOL = [
    "g2.com", "wikipedia.org", "nih.gov", *TIER_B, *PLAIN, "rival0.example", SUBJECT_DOMAIN,
]


def _random_case(
    rng: random.Random,
) -> tuple[list[ResultFacts], list[CompetitorFacts], DetectionStatus | None, DomainAuthority]:
    competitors = [
        CompetitorFacts(
            competitor_id=f"comp_{i}", name=f"Rival {i}",
            domain=f"rival{i}.example" if rng.random() < 0.8 else None,
        )
        for i in range(rng.choice([0, 0, 1, 2, 3, 5]))
    ]
    status = rng.choice([
        None, DetectionStatus.OK, DetectionStatus.OK, DetectionStatus.WEAK_SIGNAL,
        DetectionStatus.NO_SIGNAL,
    ])
    authority = DomainAuthority(
        platform_domains=frozenset(rng.sample(list(PLAIN), rng.choice([0, 0, 2, 4])))
    )
    results: list[ResultFacts] = []
    for i in range(rng.choice([0, 1, 3, 8, 14])):
        answered = rng.random() < 0.85
        names: list[str] = []
        if answered:
            if rng.random() < 0.6:
                names.append(SUBJECT)
            names += [c.name for c in competitors if rng.random() < 0.5]
            if rng.random() < 0.3:
                names.append("Unlisted Brand")
            rng.shuffle(names)
        mentioned = SUBJECT in names
        cited = rng.sample(_CITE_POOL, rng.randint(0, 5)) if answered else []
        results.append(ResultFacts(
            result_id=f"eres_{i:03d}", prompt_id=f"prmt_{i}",
            engine=rng.choice(["claude_search", "chatgpt"]),
            status=(
                (EngineResultStatus.OK if mentioned else EngineResultStatus.ANSWERED_NO_MENTION)
                if answered else rng.choice([EngineResultStatus.ERROR, EngineResultStatus.TIMEOUT])
            ),
            mentioned=mentioned,
            sentiment=rng.choice(list(Sentiment)) if mentioned else None,
            intent=rng.choice([AW, AW, CMP, BF, None]),
            brands=tuple(sorted((n, n == SUBJECT) for n in names)),
            citations=tuple(sorted((d, d == SUBJECT_DOMAIN) for d in cited)),
            position=names.index(SUBJECT) + 1 if mentioned else None,
            brand_positions=tuple(sorted((n, names.index(n) + 1) for n in names)),
        ))
    return results, competitors, status, authority


class TestTheComparisonIsTheSameAsBefore:
    def test_comparison_from_facts_equals_what_compute_score_derives(self) -> None:
        """Eight hundred random fact sets: whenever a scan is scored, the three figures agree.

        The comparison is what `compute_score` put in `average_position`,
        `position_count` and `competitors`; `comparison_from_facts` makes the
        same three without the rest. Held here over facts that vary in who is
        named, where, what is cited and by whom, which prompts are awareness,
        which calls failed, whether there is a competitor set and how it came
        out, and which domains carry a platform tier. The tallies make sure each
        way the rivals' Citation Strength can be in or out was actually reached.
        """
        rng = random.Random(20261003)
        tally: Counter[str] = Counter()
        for _ in range(800):
            results, competitors, status, authority = _random_case(rng)
            technical_foundation = rng.choice([None, Decimal("70.00")])
            computed = compute_score(
                results, competitors, competitor_set_status=status,
                technical_foundation=technical_foundation, authority=authority,
            )
            read = comparison_from_facts(
                results, competitors, competitor_set_status=status, authority=authority
            )
            if computed.status != "scored":
                # The premise of the stored-status short-circuit in `compare_on_read`.
                assert computed.competitors == []
                assert (computed.average_position, computed.position_count) == (None, 0)
                tally["insufficient"] += 1
                continue
            assert _figures(read) == _figures(computed), (status, competitors, results)
            tally["scored"] += 1
            if competitors:
                reason = computed.excluded_dimensions.get("citation_strength", "included")
                tally[f"rivals, citation strength {reason}"] += 1
            if computed.average_position is not None:
                tally["subject position"] += 1

        assert tally["insufficient"] >= 10
        for reached in (
            "scored", "subject position", "rivals, citation strength included",
            "rivals, citation strength NO_AUTHORITY_DATA",
            "rivals, citation strength NO_AWARENESS_POPULATION",
            "rivals, citation strength NO_COMPETITOR_SET",
        ):
            assert tally[reached] >= 10, (reached, dict(tally))

    SCENARIOS: dict[str, tuple[Spec, bool]] = {
        "rivals, and the platform's tiers": (Spec(), True),
        "rivals, curated tier only": (Spec(), False),
        "no competitor set": (Spec(set_status=None, rivals=()), True),
        "a no-signal set with one manual rival": (
            Spec(set_status=DetectionStatus.NO_SIGNAL, rivals=("Zendesk",), manual=("Zendesk",)),
            True,
        ),
        "a weak-signal set": (Spec(set_status=DetectionStatus.WEAK_SIGNAL), True),
        "a struck rival is left out": (Spec(tombstones=("Front",)), True),
        "answered, but no awareness prompt answered": (Spec(awareness_answered=False), True),
        "subject never named": (Spec(subject_named=False), True),
        "every dimension excluded though answers exist": (
            Spec(awareness_answered=False, subject_named=False, audit=False), True
        ),
        "nothing answered": (Spec(answered=False), True),
    }

    @pytest.mark.parametrize("name", list(SCENARIOS))
    async def test_compare_on_read_equals_score_scan_without_persist(
        self, name: str, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Over stored rows, in each shape of scan: the old call and the new one give one answer."""
        spec, platform = self.SCENARIOS[name]
        agency_id = await _sign_up(client)
        if platform:
            await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, spec)

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as fresh:
            scan = await fresh.get(Scan, seeded.scan_id)
            stored = await scoring_runner.latest_score(fresh, seeded.scan_id)
            assert scan is not None and stored is not None
            new = await scoring_runner.compare_on_read(fresh, scan, stored)
            _row, old, _comparisons = await scoring_runner.score_scan(fresh, scan, persist=False)

        assert _figures(new) == _figures(old), name
        # Each scenario is the shape it says it is.
        active = len(spec.rivals) if spec.set_status is not None else 0
        if stored.status is ScoreStatus.INSUFFICIENT_DATA:
            assert new == ReadComparison()
        else:
            assert len(new.competitors) == active
        if name == "every dimension excluded though answers exist":
            assert stored.status is ScoreStatus.INSUFFICIENT_DATA and not new.competitors
            assert old.status == "insufficient_data"
        if name == "answered, but no awareness prompt answered":
            assert stored.status is ScoreStatus.SCORED
            assert [c.mention_rate for c in new.competitors] == [Decimal("0")] * 3
            assert all(c.citation_strength is None for c in new.competitors)
        if name == "a no-signal set with one manual rival":
            assert [c.citation_strength for c in new.competitors] == [None]
        if name == "rivals, and the platform's tiers":
            assert any(c.citation_strength is not None for c in new.competitors)

    async def test_the_platform_tier_reaches_the_rivals_figures(
        self, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """The control for the equality above: tiers move these figures.

        Read with the curated tier alone, the same facts give other Citation
        Strengths. So a `compare_on_read` that forgot the platform tier would
        not equal `score_scan`'s, and this shows it is a difference the scenario
        can see.
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as fresh:
            scan = await fresh.get(Scan, seeded.scan_id)
            stored = await scoring_runner.latest_score(fresh, seeded.scan_id)
            assert scan is not None and stored is not None
            with_platform = await scoring_runner.compare_on_read(fresh, scan, stored)
            results = await scoring_runner.load_result_facts(fresh, seeded.scan_id)
            competitors, cset = await scoring_runner.load_competitor_facts(fresh, seeded.scan_id)
        assert cset is not None
        curated_only = comparison_from_facts(
            results, competitors, competitor_set_status=cset.status, authority=CURATED_ONLY
        )
        assert _figures(curated_only) != _figures(with_platform)

    async def test_the_report_and_score_bodies_are_those_a_rescoring_read_gave(
        self,
        client: AsyncClient,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole of each body, the same for the same stored rows.

        Read once with `compare_on_read`, once with it replaced by the call a
        read used to make, a full `score_scan(persist=False)`. The signed-in
        report, the public report and `GET /scans/{id}/score`, `generatedAt`
        aside (it is the time of the read).
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())
        token = await _share(client, seeded.scan_id)

        def bodies(reads: Reads) -> dict[str, Any]:
            report, public = dict(reads.report), dict(reads.public)
            report.pop("generatedAt")
            public.pop("generatedAt")
            return {"report": report, "public": public, "score": reads.score}

        async with _stranger(client) as stranger:
            new = bodies(
                await _read_every_path(client, stranger, seeded, token, history=False, pdf=False)
            )

            real_score_scan = scoring_runner.score_scan

            async def rescoring_read(
                session_: AsyncSession, scan: Scan, score: Score
            ) -> ReadComparison:
                _row, computed, _comparisons = await real_score_scan(session_, scan, persist=False)
                return ReadComparison(
                    computed.average_position, computed.position_count, computed.competitors
                )

            monkeypatch.setattr(scoring_runner, "compare_on_read", rescoring_read)
            old = bodies(
                await _read_every_path(client, stranger, seeded, token, history=False, pdf=False)
            )

        assert new == old
        # Not vacuous: the figures are there, the earlier formula is named, and
        # all three bodies carry the comparison.
        assert new["score"]["previousFormulaVersions"] == ["v4"]
        assert new["score"]["averagePosition"] is not None
        for body in (new["score"], new["report"]["score"]):
            assert len(body["competitors"]) == 3
        rivals = new["report"]["competitorSet"]["competitors"]
        assert all(c["mentionRate"] is not None for c in rivals)
        assert new["public"]["scanErrorCode"] is None

    async def test_a_rivals_citation_strength_follows_todays_tiers_while_the_stored_one_stays(
        self, engine: AsyncEngine, session: AsyncSession, client: AsyncClient
    ) -> None:
        """Characterisation of what is, not of what should be — R1's honesty finding.

        The subject's Citation Strength is the one stored when the scan was
        scored, with the tiers of that day. Each rival's is derived on read, with
        the tiers of the day it is read. When the platform's tiers move between
        the two, the report puts a figure from one day beside figures from
        another: here the five clients that made two domains Tier B are removed,
        and the rivals' Citation Strengths change while the row does not.

        What the report should do about it is an open decision (store the
        comparison when scoring, show rivals' figures only beside a
        current-formula row, say so in the report's copy, or leave it). When it
        is made, this is the test to change.
        """
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async def read() -> tuple[Score, ReadComparison]:
            async with factory() as fresh:
                scan = await fresh.get(Scan, seeded.scan_id)
                stored = await scoring_runner.latest_score(fresh, seeded.scan_id)
                assert scan is not None and stored is not None
                return stored, await scoring_runner.compare_on_read(fresh, scan, stored)

        stored_then, rivals_then = await read()
        await session.execute(
            text("UPDATE clients SET deleted_at = now() WHERE domain LIKE 'other-%.example'")
        )
        await session.commit()
        stored_now, rivals_now = await read()

        assert stored_now.citation_strength == stored_then.citation_strength
        assert stored_now.inputs_digest == stored_then.inputs_digest
        assert [c.citation_strength for c in rivals_now.competitors] != [
            c.citation_strength for c in rivals_then.competitors
        ]


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------


def _scorings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == "scoring.completed"]


class TestTheLogSaysWhatHappened:
    async def test_no_read_path_logs_a_scoring(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The report and the public report (JSON and PDF each), the score and the history."""
        agency_id = await _sign_up(client)
        await _seed_platform_tier(session, agency_id)
        seeded = await _seed_scan(session, agency_id, Spec())
        token = await _share(client, seeded.scan_id)

        async with _stranger(client) as stranger:
            with structlog.testing.capture_logs() as events:
                for _ in range(2):
                    await _read_every_path(client, stranger, seeded, token)

        assert _scorings(events) == []

    async def test_a_scan_that_is_scored_still_logs_it_once_with_what_it_always_did(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """`POST /scans/{id}/score`, the route that writes, and `score_scan` directly."""
        agency_id = await _sign_up(client)
        seeded = await _seed_scan(session, agency_id, Spec(earlier_version=None))

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/scans/{seeded.scan_id}/score")
        assert resp.status_code == 201, resp.text
        [line] = _scorings(events)
        assert line["scan_id"] == seeded.scan_id
        assert line["status"] == "scored"
        assert line["results"] == 24 and line["competitors"] == 3
        assert line["inputs_digest"] == resp.json()["inputsDigest"][:16]
        assert line["log_level"] == "info"

        scan = await session.get(Scan, seeded.scan_id)
        assert scan is not None
        with structlog.testing.capture_logs() as events:
            await scoring_runner.score_scan(session, scan)
        assert len(_scorings(events)) == 1
        await session.rollback()

    async def test_score_scan_without_persist_logs_no_scoring(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """What `client_history` and the `scripts/` still call: it wrote nothing, says nothing."""
        agency_id = await _sign_up(client)
        seeded = await _seed_scan(session, agency_id, Spec())
        scan = await session.get(Scan, seeded.scan_id)
        assert scan is not None

        with structlog.testing.capture_logs() as events:
            row, computed, _comparisons = await scoring_runner.score_scan(
                session, scan, persist=False
            )
        assert row is None and computed.status == "scored"
        assert _scorings(events) == []
