"""Launch checklist 3.116 (A9): nine reads of a client's scans name the agency as well.

Each of these selected a client's scans by the client's id alone. Each sits behind an
agency-scoped `get_client` (or a scan already loaded by agency), and the app never writes a scan
under another agency's id, so nothing leaked. The agency is now named in each as well (the shape
`competitors.latest_set_for_client` and `latest_scan_for_client` already have), so a scan row
that names the client under ANOTHER agency's id is never read:

  1. the history                      `client_history.build_history`
  2. the alerts feed's scan count     `routers/alerts.py`
  3. the alerts baseline              `alerts._baseline_for`, through `generate_for_scan`
  4. the client's scan list           `routers/scans.py`
  5. the crawler access screen        `crawler_access.build_crawler_access`
  6. the answer gaps' rival roll-up   `answer_gaps._rival_rollup`
  7. the answer gaps' scans           `answer_gaps.build_answer_gaps`
  8. the client's open scan           `competitors.open_scan`
  9. the automatic rescan             `auto_rescan.due_client_ids`: the client's last start, and
                                      whether a scan of it is in flight

That row is the one shape that tells the two lines apart, so every test makes it (`_stray_scan`)
and asks the read to leave it alone. A read behind a route is asked through the route, as the
client's own agency (1, 2, 4, 5, 6, 7). The others are asked of their service (3, 8, 9).

The open scan (8) is also put to the start path itself, with a stray open scan in the way:
`get_or_create_scan`, and the run button, which runs `start_scan`. The one-open-scan-per-client
index names the client alone, so the start meets it and raises, and the last test of that section
holds that nothing adopts the stray instead, and that the refusal is logged with this client's ids.

Where the right answer is a list, the client's OWN scan is in it, so a read that returned
nothing at all would not pass.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from avp_api import ids
from avp_api.models import (
    Agency,
    AiCrawlerAccess,
    AlertKind,
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
    Score,
    ScoreStatus,
    TechnicalAudit,
)
from avp_api.services import alerts as alerts_service
from avp_api.services import auto_rescan
from avp_api.services import competitors as detection
from avp_api.services.ai_crawlers import AccessVerdict, AgentPurpose, RuleSource

BASE = "/api/v1"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
# One second apart, so a scan made later has the larger id (ids sort by creation time).
_clock = itertools.count(int(time.time()) + 40_000)


class _TakesTheJobAndDoesNothing:
    """An executor that runs nothing: no scan in this file is to be run."""

    async def submit(self, job, *, settings) -> None:  # noqa: ANN001
        return None


@pytest.fixture
def scan_executor_factory() -> type[_TakesTheJobAndDoesNothing]:
    """So a start that is (wrongly) accepted here hands its job to nothing, not to the pipeline."""
    return _TakesTheJobAndDoesNothing


# --- the client, its own scans, and the row that does not belong ------------------------------


async def _client_row(client: AsyncClient, session: AsyncSession) -> Client:
    """The signed-in agency's client, made through the API."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Scan Reads Agency",
            "fullName": "Op",
            "email": "reads@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    made = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert made.status_code == 201, made.text
    row = await session.get(Client, made.json()["id"])
    assert row is not None
    return row


async def _agency(session: AsyncSession, slug: str = "another-agency") -> str:
    """An agency of its own, and its id: the id a stray scan row carries."""
    agency = Agency(id=ids.new_id(ids.AGENCY), name=f"Agency {slug}", slug=slug)
    session.add(agency)
    await session.commit()
    return agency.id


async def _new_scan(
    session: AsyncSession, row: Client, status: ScanStatus = ScanStatus.SUCCEEDED
) -> Scan:
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_timestamp(next(_clock))}",
        client_id=row.id, agency_id=row.agency_id, status=status,
    )
    session.add(scan)
    await session.commit()
    return scan


async def _stray_scan(
    session: AsyncSession, row: Client, other_agency_id: str,
    status: ScanStatus = ScanStatus.SUCCEEDED,
) -> Scan:
    """A scan row that names THIS client under ANOTHER agency's id; the app never writes one.

    The queries tested here read a client's scans by the agency's id as well as the client's, a
    second line behind the client's own id, and this is the one shape that tells the two apart.
    """
    stray = await _new_scan(session, row, status)
    stray.agency_id = other_agency_id
    await session.commit()
    return stray


async def _asked(session: AsyncSession, scan: Scan, *, fingerprint: str | None = None) -> Prompt:
    """One question on the scan's prompt set: what makes it a grid for the answer gaps screen."""
    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id,
        generation_params={"fingerprint": fingerprint} if fingerprint else {}, prompts=[],
    )
    session.add(prompt_set)
    await session.flush()
    prompt = Prompt(
        id=ids.new_id(ids.PROMPT), prompt_set_id=prompt_set.id,
        text="Which help desk should a small team choose?",
        intent=PromptIntent.COMPARISON, position=1,
    )
    session.add(prompt)
    await session.commit()
    return prompt


async def _rivals_won(
    session: AsyncSession, scan: Scan, prompt: Prompt, *rivals: str
) -> None:
    """An answer on the scan that named these rivals and not the client."""
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompt.id,
        engine=Engine.CHATGPT, status=EngineResultStatus.ANSWERED_NO_MENTION,
        mentioned=False, brands_mentioned=len(rivals), citations=[], brand_mentions=[],
    )
    session.add(result)
    await session.flush()
    for rank, name in enumerate(rivals, start=1):
        session.add(BrandMention(
            id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id, entity_name=name,
            entity_domain=f"{name.lower()}.example", is_subject=False, position=rank,
        ))
    await session.commit()


async def _policy(session: AsyncSession, scan: Scan) -> None:
    """The scan's audit read the site's robots.txt: one AI crawler blocked by name."""
    audit = TechnicalAudit(
        id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=scan.id, url_audited="helpscout.com"
    )
    session.add(audit)
    await session.flush()
    session.add(AiCrawlerAccess(
        id=ids.new_id(ids.AI_CRAWLER_ACCESS), audit_id=audit.id, agent_token="GPTBot",
        vendor="OpenAI", purpose=AgentPurpose.TRAINING, verdict=AccessVerdict.BLOCKED,
        rule_source=RuleSource.EXPLICIT, matched_token="gptbot", disallow_rules=1,
    ))
    await session.commit()


# --- 1. the history ---------------------------------------------------------------------------


async def test_the_history_does_not_list_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    own = await _new_scan(session, row)
    await _stray_scan(session, row, other)  # a scan that would be plotted
    await _stray_scan(session, row, other, ScanStatus.FAILED)  # and one that would be counted

    resp = await client.get(f"{BASE}/clients/{row.id}/history")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [point["scanId"] for point in body["scans"]] == [own.id]
    assert body["scansWithoutData"] == 0


# --- 2. the alerts feed's count of the client's scans -----------------------------------------


async def test_the_alerts_feed_does_not_count_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    await _new_scan(session, row)
    await _stray_scan(session, row, other)

    resp = await client.get(f"{BASE}/clients/{row.id}/alerts")

    assert resp.status_code == 200, resp.text
    assert resp.json()["scansTotal"] == 1


# --- 3. the alerts baseline -------------------------------------------------------------------


async def test_the_alerts_baseline_is_never_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The stray is newer than the client's own baseline and old enough to be one.

    Picked, it would be compared against (59 to 60: a rise, no alert). The client's own baseline
    (80 to 60) is what the scan is compared with, so the drop is reported against that scan.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    same_questions = "f" * 64

    async def scored(scan: Scan, composite: str, hours_ago: int) -> Scan:
        scan.created_at = NOW - timedelta(hours=hours_ago)
        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=scan.id, status=ScoreStatus.SCORED,
            composite=Decimal(composite),
        ))
        await session.commit()
        await _asked(session, scan, fingerprint=same_questions)
        return scan

    baseline = await scored(await _new_scan(session, row), "80.0", hours_ago=72)
    await scored(await _stray_scan(session, row, other), "59.0", hours_ago=24)
    current = await scored(await _new_scan(session, row), "60.0", hours_ago=0)

    made = await alerts_service.generate_for_scan(session, current, row)

    assert [(a.kind, a.baseline_scan_id) for a in made] == [
        (AlertKind.VISIBILITY_DROP, baseline.id)
    ]


# --- 4. the client's scan list ----------------------------------------------------------------


async def test_the_scan_list_does_not_include_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    first = await _new_scan(session, row)
    second = await _new_scan(session, row)
    await _stray_scan(session, row, other)  # the newest id, so it would head the list

    resp = await client.get(f"{BASE}/clients/{row.id}/scans")

    assert resp.status_code == 200, resp.text
    assert [scan["id"] for scan in resp.json()["data"]] == [second.id, first.id]


# --- 5. the crawler access screen -------------------------------------------------------------


async def test_a_policy_recorded_only_on_a_scan_under_another_agencys_id_is_not_shown(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    await _new_scan(session, row)  # the client's own scan recorded no policy
    await _policy(session, await _stray_scan(session, row, other))

    resp = await client.get(f"{BASE}/clients/{row.id}/ai-crawler-access")

    assert resp.status_code == 200, resp.text
    assert resp.json() is None


async def test_crawler_access_offers_only_the_clients_own_scans(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    own = await _new_scan(session, row)
    await _policy(session, own)
    stray = await _stray_scan(session, row, other)  # the newer scan, and it has a policy
    await _policy(session, stray)

    newest = (await client.get(f"{BASE}/clients/{row.id}/ai-crawler-access")).json()
    asked_for = (
        await client.get(f"{BASE}/clients/{row.id}/ai-crawler-access", params={"scanId": stray.id})
    ).json()

    assert newest["scanId"] == own.id
    assert newest["availableScanIds"] == [own.id]
    assert asked_for is None


# --- 6 and 7. the answer gaps: the scans it offers, and its roll-up of rivals -----------------


async def test_answer_gaps_offer_only_the_clients_own_scans(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    own = await _new_scan(session, row)
    await _asked(session, own)
    stray = await _stray_scan(session, row, other)  # the newer scan, and it has a grid
    await _asked(session, stray)

    newest = (await client.get(f"{BASE}/clients/{row.id}/answer-gaps")).json()
    asked_for = (
        await client.get(f"{BASE}/clients/{row.id}/answer-gaps", params={"scanId": stray.id})
    ).json()

    assert newest["scanId"] == own.id
    assert newest["availableScanIds"] == [own.id]
    assert asked_for is None


async def test_the_rival_roll_up_does_not_count_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    own = await _new_scan(session, row)
    await _rivals_won(session, own, await _asked(session, own), "Zendesk")
    stray = await _stray_scan(session, row, other)
    await _rivals_won(session, stray, await _asked(session, stray), "Zendesk", "Freshdesk")

    body = (await client.get(f"{BASE}/clients/{row.id}/answer-gaps")).json()

    assert [(r["name"], r["answersWon"], r["scansPresent"]) for r in body["rivals"]] == [
        ("Zendesk", 1, 1)
    ]


# --- 8. the client's open scan ----------------------------------------------------------------


async def test_the_open_scan_is_never_a_scan_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Asked as the client's agency, a stray RUNNING scan is not its open scan.

    One open scan per client is a unique index on the client alone, so this client has no open
    scan of its own beside the stray. The same row is found when it is asked for under the id it
    carries, which shows the answer above is the agency's doing.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    stray = await _stray_scan(session, row, other, ScanStatus.RUNNING)

    assert await detection.open_scan(session, row.id, agency_id=row.agency_id) is None
    found = await detection.open_scan(session, row.id, agency_id=other)
    assert found is not None and found.id == stray.id


Start = Callable[[AsyncClient, AsyncSession, Client], Awaitable[None]]


async def _by_get_or_create_scan(client: AsyncClient, session: AsyncSession, row: Client) -> None:
    await detection.get_or_create_scan(session, row)


async def _by_the_run_button(client: AsyncClient, session: AsyncSession, row: Client) -> None:
    """`POST /clients/{id}/scans`: `start_scan` asks for the open scan, then opens one."""
    await client.post(f"{BASE}/clients/{row.id}/scans", json={})


@pytest.mark.parametrize(
    ("start", "status"),
    [
        pytest.param(_by_get_or_create_scan, ScanStatus.QUEUED, id="get_or_create_scan-queued"),
        pytest.param(_by_the_run_button, ScanStatus.QUEUED, id="run_button-queued"),
        # `start_scan` returns a RUNNING scan it finds before it asks for anything else, so only
        # a RUNNING stray shows whether that lookup is scoped to the agency too.
        pytest.param(_by_the_run_button, ScanStatus.RUNNING, id="run_button-running"),
    ],
)
async def test_a_stray_open_scan_is_never_adopted_by_a_start(
    client: AsyncClient, session: AsyncSession, start: Start, status: ScanStatus
) -> None:
    """A stray QUEUED or RUNNING scan is not the client's open scan, so a start does not take it.

    The start finds no open scan, tries to open one, and meets `uq_scans_one_open_per_client`,
    which names the client alone. It raises, the refusal is logged once with this client's id and
    its own agency's (the other agency's is not named), and the stray is left as it was: not
    submitted, not relabelled, not moved to this agency, with no scan of the client's own beside it.
    A lookup by the client's id alone, added to `open_scan`, `get_or_create_scan` or `start_scan`,
    would hand the stray over instead, and none of this would hold.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    stray = await _stray_scan(session, row, other, status)

    with structlog.testing.capture_logs() as events, pytest.raises(IntegrityError):
        await start(client, session, row)

    assert [e for e in events if e["event"] == "scan.create.insert_refused"] == [
        {
            "event": "scan.create.insert_refused", "log_level": "warning",
            "client_id": row.id, "agency_id": row.agency_id,
            "constraint": "uq_scans_one_open_per_client",
        }
    ]
    assert other not in repr(events)

    scans = (
        await session.execute(
            select(
                Scan.id, Scan.agency_id, Scan.status, Scan.submitted_at, Scan.requested_by_user_id
            ).where(Scan.client_id == row.id)
        )
    ).all()
    assert [tuple(scan) for scan in scans] == [(stray.id, other, status, None, None)]


# --- 9. the automatic rescan: the last start, and what is in flight ---------------------------


async def _rescan_client(session: AsyncSession, agency_id: str) -> Client:
    """A client of this agency with the monthly rescan switched on."""
    row = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency_id, name="acme.example",
        domain="acme.example", auto_rescan=True,
    )
    session.add(row)
    await session.commit()
    return row


async def _started(
    session: AsyncSession, row: Client, *, agency_id: str, status: ScanStatus, days_ago: float
) -> Scan:
    """A scan of the client, submitted `days_ago` before NOW, carrying `agency_id`."""
    when = NOW - timedelta(days=days_ago)
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=row.id, agency_id=agency_id, status=status,
        trigger=ScanTrigger.MANUAL, formula_version="test", prompt_count=0,
        engine_result_count=0,
    )
    scan.created_at = when
    scan.submitted_at = when
    if status is ScanStatus.RUNNING:
        scan.lease_expires_at = NOW + timedelta(minutes=5)
    session.add(scan)
    await session.commit()
    return scan


async def test_a_running_scan_under_another_agencys_id_does_not_put_the_client_in_flight(
    session: AsyncSession,
) -> None:
    """The stray is older than the client's last start, so only the in-flight question sees it."""
    mine = await _agency(session, "mine")
    other = await _agency(session, "other")
    row = await _rescan_client(session, mine)
    await _started(session, row, agency_id=mine, status=ScanStatus.SUCCEEDED, days_ago=31)
    await _started(session, row, agency_id=other, status=ScanStatus.RUNNING, days_ago=40)

    assert await auto_rescan.due_client_ids(session, NOW) == [row.id]


async def test_a_newer_scan_under_another_agencys_id_is_not_the_clients_last_start(
    session: AsyncSession,
) -> None:
    mine = await _agency(session, "mine")
    other = await _agency(session, "other")
    row = await _rescan_client(session, mine)
    await _started(session, row, agency_id=mine, status=ScanStatus.SUCCEEDED, days_ago=31)
    await _started(session, row, agency_id=other, status=ScanStatus.SUCCEEDED, days_ago=5)

    assert await auto_rescan.due_client_ids(session, NOW) == [row.id]


async def test_a_client_whose_only_scan_is_under_another_agencys_id_has_never_been_scanned(
    session: AsyncSession,
) -> None:
    mine = await _agency(session, "mine")
    other = await _agency(session, "other")
    row = await _rescan_client(session, mine)
    await _started(session, row, agency_id=other, status=ScanStatus.SUCCEEDED, days_ago=60)

    assert await auto_rescan.due_client_ids(session, NOW) == []
