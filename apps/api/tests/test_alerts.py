"""Alerts — Epic E.

The assertions that matter are the ones that fail silently if wrong: an alert
fired against a re-run of the same scan, a percentage computed off a base of
two, and a threshold quietly lowered until it fires on noise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from avp_api.models.alert import Alert, AlertKind
from avp_api.models.engine_result import Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.models.scan import Scan
from avp_api.models.score import Score
from avp_api.services import alerts as alerts_service
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"


class TestTheThresholdsAreArgued:
    """The numbers are asserted as RELATIONSHIPS, not as literals.

    Epic 9.24's prompt-run throttle set this pattern: a test that pins a
    constant to its own value proves only that nobody typed a different one. A
    test that pins it to the reason it was chosen forces the argument to be
    made again when it changes.
    """

    def test_the_baseline_guard_clears_the_observed_rerun_gap(self) -> None:
        # The whole point of the guard. Plausible's scans sit 43 minutes and
        # 2h11m apart; comparing across those measures engine nondeterminism.
        assert (
            alerts_service.MIN_BASELINE_HOURS > alerts_service.OBSERVED_RERUN_GAP_HOURS
        )

    def test_the_baseline_guard_still_admits_a_daily_cadence(self) -> None:
        # And the ceiling: raised past 24h, a client scanning daily would never
        # produce an alert at all, which is a worse failure than a noisy one
        # because it is silent.
        assert alerts_service.MIN_BASELINE_HOURS < alerts_service.DAILY_CADENCE_HOURS

    def test_the_sentiment_threshold_sits_above_measured_rerun_movement(self) -> None:
        # Worst observed re-run movement in net tone is -17% (18 -> 15). The
        # threshold must be clear of it or every re-run becomes an alert.
        assert Decimal("0.17") < alerts_service.SENTIMENT_DECLINE_FRACTION

    def test_a_percentage_needs_a_base_worth_taking_one_of(self) -> None:
        # 2 -> 1 is a 50% decline and means nothing.
        assert alerts_service.SENTIMENT_MIN_BASE >= 5


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """Stop after the engine phase; these tests drive generation directly."""
    return engines_only_executor


async def _sign_up(client: AsyncClient, email: str = "alerts@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Alerts Test Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Engines whose tone and owned-citation count the test chooses."""

    def _install(
        *,
        tone: Sentiment = Sentiment.POSITIVE,
        cite_own: bool = True,
        prompts: int = 8,
    ):
        generated = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text="Help Scout is a good option. Zendesk is another.",
                    citations=(
                        [
                            CitedSource(
                                url="https://helpscout.com/a",
                                domain="helpscout.com",
                                position=1,
                            )
                        ]
                        if cite_own
                        else [CitedSource(url="https://g2.com/a", domain="g2.com", position=1)]
                    ),
                    latency_ms=5,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return tone, Decimal("0.9")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(
            scan_runner.extraction_service, "classify_sentiment", fake_sentiment
        )

    return _install


async def _client_id(client: AsyncClient, url: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": url, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _run_scan(client: AsyncClient, cid: str) -> str:
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


async def _age_scan(session, scan_id: str, hours: float) -> None:
    """Backdate a scan, so a baseline can exist inside a test."""
    scan = await session.get(Scan, scan_id)
    scan.created_at = datetime.now(UTC) - timedelta(hours=hours)
    await session.commit()


async def _set_composite(session, scan_id: str, value: str) -> None:
    """Give a scan a stored composite.

    Written directly rather than by running the scoring phase: this suite uses
    the engines-only executor, which stops before phase 7, and the alert logic
    reads the STORED composite. Driving real scoring here would make every test
    depend on the scoring formula's current output, which is a different
    module's business — `test_scoring.py` owns that.
    """
    from avp_api.ids import SCORE, new_id
    from avp_api.models.score import ScoreStatus

    row = (
        await session.execute(
            select(Score).where(Score.scan_id == scan_id).order_by(Score.created_at.desc())
        )
    ).scalars().first()
    if row is None:
        row = Score(id=new_id(SCORE), scan_id=scan_id, status=ScoreStatus.SCORED)
        session.add(row)
    row.composite = Decimal(value)
    await session.commit()


class TestTheBaselineGuard:
    """The correction the whole epic turns on."""

    async def test_a_scan_with_only_a_recent_predecessor_produces_nothing(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """A re-run is not a time series.

        Two scans an hour apart measure the same reality twice. Any difference
        between them is the engines answering nondeterministically, and
        reporting that as a change is the failure this guard exists to prevent.
        """
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=1)  # inside the guard
        second = await _run_scan(client, cid)

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert made == []

    async def test_a_scan_with_an_older_predecessor_can_produce_alerts(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "60.0")

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert any(a.kind is AlertKind.VISIBILITY_DROP for a in made)

    async def test_a_first_scan_produces_nothing(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        only = await _run_scan(client, cid)

        scan = await session.get(Scan, only)
        cl = await _load_client(session, cid)
        assert await alerts_service.generate_for_scan(session, scan, cl) == []


async def _load_client(session, cid: str):  # noqa: ANN001, ANN202
    from avp_api.models import Client

    return await session.get(Client, cid)


async def _set_version(session, scan_id: str, version: str) -> None:  # noqa: ANN001
    """Mark a scan's stored score as computed under some other formula."""
    row = (
        await session.execute(
            select(Score).where(Score.scan_id == scan_id).order_by(Score.created_at.desc())
        )
    ).scalars().first()
    assert row is not None
    row.formula_version = version
    await session.commit()


class TestAFormulaChangeIsNotABusinessEvent:
    """v2.1 bumped the formula; the guard here is what keeps that from
    reading as every client's visibility moving on the same day.

    Rule 5 stores `formula_version` so two composites can be told apart. An
    alert that compared across the bump would report the definition changing
    as the business changing — the third form of the failure this module's
    docstring already names twice (a re-run, and a recomputation).
    """

    async def test_a_drop_across_formula_versions_is_not_a_visibility_drop(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        await _set_version(session, first, "v2")  # the baseline, under the old definition
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "60.0")  # this one under the current

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert not any(a.kind is AlertKind.VISIBILITY_DROP for a in made)

    async def test_the_same_drop_under_one_formula_still_fires(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """The guard declines to compare across versions and nothing else."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        await _set_version(session, first, "v2")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "60.0")
        await _set_version(session, second, "v2")

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert any(a.kind is AlertKind.VISIBILITY_DROP for a in made)


class TestWhatEachKindActuallyDetects:
    async def test_a_tone_decline_fires_without_the_sign_ever_changing(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """The correction: net tone has never once been negative in real data.

        The minimum ever recorded is +4, so a rule watching for a sign change
        fires zero times. This asserts the DECLINE rule catches a fall that
        stays positive throughout — which is the shape the real Notion event
        had (13 -> 6, 12 -> 5, 10 -> 4).
        """
        await _sign_up(client)
        stub_engines(tone=Sentiment.POSITIVE)
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)

        # Second scan: most answers neutral, so net falls hard but stays >= 0.
        stub_engines(tone=Sentiment.NEUTRAL)
        second = await _run_scan(client, cid)

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        tone_alerts = [a for a in made if a.kind is AlertKind.SENTIMENT_DECLINE]
        assert tone_alerts, "a steep decline that stays positive must still fire"
        # Every one names its engine — tone is measured per engine.
        assert all(a.engine is not None for a in tone_alerts)
        # And nothing claims the sign changed, because it did not.
        assert all("negative" not in a.detail for a in tone_alerts)

    async def test_an_owned_citation_falling_to_zero_fires(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines(cite_own=True)
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)

        stub_engines(cite_own=False)
        second = await _run_scan(client, cid)

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert any(a.kind is AlertKind.OWNED_CITATION_LOST for a in made)

    async def test_a_client_that_never_had_a_citation_cannot_lose_one(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """Notion's real case: zero owned citations in both scans.

        Reporting a loss where there was never anything to lose would be the
        citation equivalent of Epic B's no-brands prompt counted as a gap.
        """
        await _sign_up(client)
        stub_engines(cite_own=False)
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        second = await _run_scan(client, cid)

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert not any(a.kind is AlertKind.OWNED_CITATION_LOST for a in made)

    async def test_a_rise_is_never_an_alert(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "40.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "90.0")

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        made = await alerts_service.generate_for_scan(session, scan, cl)
        assert not any(a.kind is AlertKind.VISIBILITY_DROP for a in made)


class TestGenerationIsIdempotent:
    async def test_regenerating_does_not_accumulate_duplicates(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "60.0")

        scan = await session.get(Scan, second)
        cl = await _load_client(session, cid)
        await alerts_service.generate_for_scan(session, scan, cl)
        await session.commit()
        await alerts_service.generate_for_scan(session, scan, cl)
        await session.commit()

        rows = (
            await session.execute(select(Alert).where(Alert.scan_id == second))
        ).scalars().all()
        assert len(rows) == len({(r.kind, r.engine) for r in rows})


class TestTheFeed:
    async def test_an_empty_feed_says_whether_anything_was_comparable(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """"No alerts" and "nothing could be compared" are different answers.

        Most clients have one scan and can never produce an alert. A feed that
        reported only "0" would read as an all-clear the data cannot support.
        """
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/alerts")).json()
        assert body["alerts"] == []
        assert body["scansTotal"] == 1
        assert body["scansCompared"] == 0
        assert body["minBaselineHours"] == alerts_service.MIN_BASELINE_HOURS

    async def test_alerts_are_newest_first_because_this_is_a_log(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "50.0")
        scan = await session.get(Scan, second)
        await alerts_service.generate_for_scan(session, scan, await _load_client(session, cid))
        await session.commit()

        body = (await client.get(f"{BASE}/clients/{cid}/alerts")).json()
        assert body["alerts"], "the drop must be reported"
        ids = [a["id"] for a in body["alerts"]]
        assert ids == sorted(ids, reverse=True)
        # Each row carries both scans and both timestamps, so the screen needs
        # no second request to say what it is comparing against.
        row = body["alerts"][0]
        assert row["scanId"] == second
        assert row["baselineScanId"] == first
        assert row["scannedAt"] and row["baselineScannedAt"]


class TestTheTrendCanActuallyFindThesePoints:
    """The stamp an alert reports must be the stamp the trend plots.

    A chart marks a scan by matching the alert's `scannedAt` against the trend
    point's `stamp`, and that stamp is `HistoryScanOut.scanned_at`. The first
    draft of the feed selected `Scan.created_at` while history selects
    `finished_at or created_at`; the two differ by the scan's duration, so no
    key ever matched and EVERY marker silently failed to draw. No error, no log
    line, and invisible to any test whose fixture stamps agree by construction.

    Asserted across the two endpoints for that reason: the bug lives in the gap
    between them, so neither one alone can catch it.
    """

    async def test_an_alert_stamp_matches_a_history_point_exactly(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "50.0")
        scan = await session.get(Scan, second)
        await alerts_service.generate_for_scan(
            session, scan, await _load_client(session, cid)
        )
        await session.commit()

        alerts = (await client.get(f"{BASE}/clients/{cid}/alerts")).json()["alerts"]
        history = (await client.get(f"{BASE}/clients/{cid}/history")).json()["scans"]
        assert alerts, "the drop must be reported"

        stamps = {s["scannedAt"] for s in history}
        for alert in alerts:
            assert alert["scannedAt"] in stamps, (
                "an alert points at a scan the trend cannot find: "
                f"{alert['scannedAt']!r} not in {sorted(stamps)!r}"
            )
            assert alert["baselineScannedAt"] in stamps


class TestAcknowledgement:
    async def _one_alert(self, client: AsyncClient, session, cid: str) -> str:
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "50.0")
        scan = await session.get(Scan, second)
        made = await alerts_service.generate_for_scan(
            session, scan, await _load_client(session, cid)
        )
        await session.commit()
        return made[0].id

    async def test_acknowledging_stamps_it_once_and_is_idempotent(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        aid = await self._one_alert(client, session, cid)

        first = (await client.post(f"{BASE}/alerts/{aid}/acknowledge")).json()
        assert first["acknowledgedAt"] is not None
        second = (await client.post(f"{BASE}/alerts/{aid}/acknowledge")).json()
        # A double click must not rewrite when the operator first looked.
        assert second["acknowledgedAt"] == first["acknowledgedAt"]

    async def test_acknowledging_drops_it_out_of_the_outstanding_count(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        aid = await self._one_alert(client, session, cid)

        before = (await client.get(f"{BASE}/clients/{cid}/alerts")).json()
        assert before["unacknowledged"] >= 1
        await client.post(f"{BASE}/alerts/{aid}/acknowledge")
        after = (await client.get(f"{BASE}/clients/{cid}/alerts")).json()
        assert after["unacknowledged"] == before["unacknowledged"] - 1
        # Still present in the feed — acknowledged, not deleted.
        assert len(after["alerts"]) == len(before["alerts"])

    async def test_another_agency_cannot_acknowledge(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "owner@test.example")
        stub_engines()
        cid = await _client_id(client)
        aid = await self._one_alert(client, session, cid)

        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, "stranger@test.example")
        resp = await client.post(f"{BASE}/alerts/{aid}/acknowledge")
        # 404, not 403 — the id must not be confirmed to exist elsewhere.
        assert resp.status_code == 404


class TestIpSafety:
    async def test_no_alert_carries_engine_text(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """ip-safety.md #7. `detail` is OUR sentence about OUR numbers."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        await _age_scan(session, first, hours=alerts_service.MIN_BASELINE_HOURS + 2)
        await _set_composite(session, first, "80.0")
        second = await _run_scan(client, cid)
        await _set_composite(session, second, "50.0")
        scan = await session.get(Scan, second)
        await alerts_service.generate_for_scan(session, scan, await _load_client(session, cid))
        await session.commit()

        raw = (await client.get(f"{BASE}/clients/{cid}/alerts")).text
        for phrase in ("is a good option", "Zendesk is another"):
            assert phrase not in raw, f"answer text leaked: {phrase!r}"


class TestScoringRanBeforeAlerts:
    async def test_the_chain_orders_alerts_after_scoring(self) -> None:
        """A visibility alert reads the STORED composite, which phase 7 writes.

        Ordered before scoring, every visibility alert would compare against
        `None` and silently produce nothing — a failure with no error and no
        log line, which is why the order is asserted rather than trusted.
        """
        from pathlib import Path

        src = Path(alerts_service.__file__).parent / "scan_executor.py"
        text = src.read_text()
        assert text.index('"scoring"') < text.index('"alerts"')
