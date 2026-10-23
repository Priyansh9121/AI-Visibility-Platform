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

A9b is the same rule for the other rows a client's id selects, and for the client a scan names.
It is defence in depth in the same way, and each site is put to the row the app never writes:

  10. the alerts list                 `routers/alerts.py`: an alert under another agency's id,
                                      and an alert of ours that names another agency's scan
  11. the prompt-run history          `prompt_runs.history`: a run under another agency's id

and, for a scan under THIS agency's id that names ANOTHER agency's client (`_scan_naming`, the
reverse of `_stray_scan`), the sites that load a scan's client:

  12. the report                      `report.build_report`: by the signed-in route (a 500), and
                                      by a share link, whose lookup (`share.scan_for_share_token`)
                                      answers the dead-link 404 first
  13. the audit and fixes routes      `routers/audits.py` and `routers/action_items.py`
  14. the executor                    `scan_executor.execute_scan`, also for a job that names a
                                      different client of the same agency
  15. the competitor set of a scan    `competitors.persist_detection`
  16. the dashboard's recent scans    `routers/dashboard.py`
  17. adopting the next list          `competitors.adopt_next_list`: a stray scan takes nothing
"""

from __future__ import annotations

import itertools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import structlog
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from avp_api import ids
from avp_api.config import Settings
from avp_api.models import (
    Agency,
    AiCrawlerAccess,
    Alert,
    AlertKind,
    BrandMention,
    Client,
    DetectionStatus,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptIntent,
    PromptRun,
    PromptRunStatus,
    PromptSet,
    Scan,
    ScanStatus,
    ScanTrigger,
    Score,
    ScoreStatus,
    TechnicalAudit,
)
from avp_api.services import alerts as alerts_service
from avp_api.services import audit_runner, auto_rescan, fix_runner
from avp_api.services import competitors as detection
from avp_api.services import scan_executor as executor_module
from avp_api.services.ai_crawlers import AccessVerdict, AgentPurpose, RuleSource
from avp_api.services.competitors import Candidate, DetectionOutcome
from avp_api.services.engines import DEFAULT_ENGINES
from avp_api.services.fix_generator import FixOutcome
from avp_api.services.scan_executor import ScanJob, execute_scan

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


# ===========================================================================================
# A9b: the other rows a client's id selects, and the client a scan names
# ===========================================================================================

# Another agency's client: a name, a domain and a confirmed other site that nothing of the
# first agency's says, so they can be looked for in a body or a log and found nowhere.
THEIR_NAME = "Bravo Rival Company"
THEIR_DOMAIN = "bravo-rival-company.example"
THEIR_OTHER_SITE = "bravo-rival-shop.example"


async def _their_client(session: AsyncSession, agency_id: str) -> Client:
    """A client of ANOTHER agency (`THEIR_NAME`, `THEIR_DOMAIN`, one confirmed other site)."""
    row = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=THEIR_NAME, domain=THEIR_DOMAIN,
        other_sites=[THEIR_OTHER_SITE],
    )
    session.add(row)
    await session.commit()
    return row


async def _scan_naming(
    session: AsyncSession, agency_id: str, theirs: Client,
    status: ScanStatus = ScanStatus.SUCCEEDED,
) -> Scan:
    """A scan under `agency_id` that names ANOTHER agency's client; the app never writes one.

    The reverse of `_stray_scan`: that one carries another agency's id and names our client, this
    one carries our agency's id and names their client.
    """
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_timestamp(next(_clock))}",
        client_id=theirs.id, agency_id=agency_id, status=status,
    )
    session.add(scan)
    await session.commit()
    return scan


async def _alert(
    session: AsyncSession, row: Client, *, scan: Scan, baseline: Scan,
    agency_id: str | None = None,
) -> Alert:
    """An alert of the client's, in the client's agency unless `agency_id` says another."""
    alert = Alert(
        id=ids.new_id(ids.ALERT), client_id=row.id, agency_id=agency_id or row.agency_id,
        kind=AlertKind.OWNED_CITATION_LOST, scan_id=scan.id, baseline_scan_id=baseline.id,
        detail="No answer cited the client this scan.",
    )
    session.add(alert)
    await session.commit()
    return alert


async def _prompt_run(
    session: AsyncSession, row: Client, text: str, *, agency_id: str | None = None
) -> PromptRun:
    """A prompt run of the client's, in the client's agency unless `agency_id` says another."""
    run = PromptRun(
        id=ids.new_id(ids.PROMPT_RUN), client_id=row.id, agency_id=agency_id or row.agency_id,
        prompt_text=text, status=PromptRunStatus.OK, subject_name="Help Scout",
        subject_domain=row.domain, results=[],
    )
    session.add(run)
    await session.commit()
    return run


async def _shared(session: AsyncSession, scan: Scan) -> Scan:
    """Give the scan a share link that opens (a token and an end a week away)."""
    scan.share_token = f"token-for-{scan.id}"
    scan.share_expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.commit()
    return scan


@asynccontextmanager
async def _seen_as_responses(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The same app and the same sign-in, where an unhandled error comes back as its 500."""
    transport = ASGITransport(app=client._transport.app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", cookies=client.cookies,
        headers={"Origin": client.headers["Origin"]},
    ) as seen:
        yield seen


# --- 10. the alerts list ----------------------------------------------------------------------


async def test_the_alerts_list_leaves_out_an_alert_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Neither listed nor counted: `scansCompared` counts the listed alerts, and the scan count
    next to it is the agency's, so a stray alert would make the two disagree."""
    row = await _client_row(client, session)
    other = await _agency(session)
    first = await _new_scan(session, row)
    second = await _new_scan(session, row)
    own = await _alert(session, row, scan=second, baseline=first)
    stray_scan = await _stray_scan(session, row, other)
    await _alert(session, row, scan=stray_scan, baseline=first, agency_id=other)

    resp = await client.get(f"{BASE}/clients/{row.id}/alerts")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["id"] for a in body["alerts"]] == [own.id]
    assert body["unacknowledged"] == 1
    assert (body["scansCompared"], body["scansTotal"]) == (1, 2)


@pytest.mark.parametrize("foreign", ["the_triggering_scan", "the_baseline_scan"])
async def test_the_alerts_list_leaves_out_an_alert_that_names_another_agencys_scan(
    client: AsyncClient, session: AsyncSession, foreign: str
) -> None:
    """An alert of this agency whose scan, or whose baseline, is under another agency's id is not
    listed, so that scan's id and times never show here; the client's own alert still is."""
    row = await _client_row(client, session)
    other = await _agency(session)
    first = await _new_scan(session, row)
    second = await _new_scan(session, row)
    own = await _alert(session, row, scan=second, baseline=first)
    theirs = await _stray_scan(session, row, other)
    if foreign == "the_triggering_scan":
        await _alert(session, row, scan=theirs, baseline=first)
    else:
        await _alert(session, row, scan=second, baseline=theirs)

    resp = await client.get(f"{BASE}/clients/{row.id}/alerts")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["id"] for a in body["alerts"]] == [own.id]
    assert body["unacknowledged"] == 1
    assert theirs.id not in resp.text


# --- 11. the prompt-run history ---------------------------------------------------------------


async def test_the_prompt_history_leaves_out_a_run_under_another_agencys_id(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    own = await _prompt_run(session, row, "Which help desk is simplest to set up?")
    await _prompt_run(session, row, "Which help desk do rivals prefer?", agency_id=other)

    resp = await client.get(f"{BASE}/clients/{row.id}/prompt-runs")

    assert resp.status_code == 200, resp.text
    assert [run["id"] for run in resp.json()["data"]] == [own.id]
    assert "rivals prefer" not in resp.text


# --- 12. the report: by the signed-in route, and by a share link ------------------------------


async def _report(seen: AsyncClient, via: str, scan: Scan) -> Response:
    if via == "the_report_route":
        return await seen.get(f"{BASE}/scans/{scan.id}/report")
    return await seen.post(f"{BASE}/reports/shared", json={"token": scan.share_token})


@pytest.mark.parametrize("via", ["the_report_route", "a_share_link"])
async def test_a_report_never_returns_the_client_of_another_agency(
    client: AsyncClient, session: AsyncSession, via: str
) -> None:
    """The report is not built: a 500, which says nothing about either agency.

    The client's own scan is read the same way first, and its report is a 200 that names the
    client, so the body can be searched and a missing name means something.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    own = await _shared(session, await _new_scan(session, row))
    stray = await _shared(session, await _scan_naming(session, row.agency_id, theirs))

    async with _seen_as_responses(client) as seen:
        mine = await _report(seen, via, own)
        asked = await _report(seen, via, stray)
        dead = await seen.post(f"{BASE}/reports/shared", json={"token": "never-issued"})

    assert mine.status_code == 200, mine.text
    assert row.domain in mine.text
    assert THEIR_NAME not in asked.text and THEIR_DOMAIN not in asked.text
    if via == "a_share_link":
        # The share lookup finds no scan whose client is in its own agency: the answer any dead
        # link gets, on the one path every rejection takes (`share.scan_for_share_token`).
        assert asked.status_code == 404, asked.text
        assert {k: v for k, v in asked.json().items() if k != "instance"} == {
            k: v for k, v in dead.json().items() if k != "instance"
        }
    else:
        assert asked.status_code == 500


# --- 13. the audit and fixes routes: no crawl and no model call -------------------------------


async def test_the_audit_route_crawls_nothing_for_a_scan_that_names_another_agencys_client(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    own = await _new_scan(session, row)
    stray = await _scan_naming(session, row.agency_id, theirs)
    crawled: list[str] = []
    stand_in = audit_runner.audit_site  # the suite's stand-in for the browser

    async def recording(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        crawled.append(url)
        return await stand_in(url, **kwargs)

    monkeypatch.setattr(audit_runner, "audit_site", recording)

    audited = await client.post(f"{BASE}/scans/{own.id}/audit")
    assert audited.status_code == 201, audited.text
    assert crawled == [f"https://{row.domain}"]  # the recording is wired

    resp = await client.post(f"{BASE}/scans/{stray.id}/audit")

    assert resp.status_code == 404, resp.text
    assert crawled == [f"https://{row.domain}"]  # and nothing was crawled for the stray


async def test_the_fixes_route_calls_no_model_for_a_scan_that_names_another_agencys_client(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    own = await _new_scan(session, row)
    stray = await _scan_naming(session, row.agency_id, theirs)
    written_for: list[str] = []

    async def candidates(session, scan):  # noqa: ANN001, ANN202, ARG001
        # Something to write about, so a start that was wrongly accepted would call the model.
        return ["a gap worth a fix"]

    async def generate(session, scan, client, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        written_for.append(client.id)
        return [], FixOutcome(status="failed", reason_code="TEST")

    monkeypatch.setattr(fix_runner, "build_candidates_for", candidates)
    monkeypatch.setattr(fix_runner, "generate_for_scan", generate)

    written = await client.post(f"{BASE}/scans/{own.id}/fixes")
    assert written.status_code == 201, written.text
    assert written_for == [row.id]  # the fake is wired

    resp = await client.post(f"{BASE}/scans/{stray.id}/fixes")

    assert resp.status_code == 404, resp.text
    assert written_for == [row.id]  # and the model was not asked about their client


# --- 14. the executor takes the vanished-client branch ----------------------------------------


async def test_the_executor_runs_nothing_for_a_scan_that_names_another_agencys_client(
    client: AsyncClient, session: AsyncSession, settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    own = await _new_scan(session, row, ScanStatus.QUEUED)
    stray = await _scan_naming(session, row.agency_id, theirs, ScanStatus.QUEUED)
    chains: list[str] = []

    async def chain(session, scan, client, job, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        chains.append(scan.id)

    monkeypatch.setattr(executor_module, "_run_chain", chain)

    await execute_scan(
        ScanJob(scan_id=own.id, client_id=row.id, engines=DEFAULT_ENGINES), settings=settings
    )
    assert chains == [own.id]  # the fake chain is wired

    with structlog.testing.capture_logs() as events:
        await execute_scan(
            ScanJob(scan_id=stray.id, client_id=theirs.id, engines=DEFAULT_ENGINES),
            settings=settings,
        )

    assert [e for e in events if e["event"] == "scan.execute.vanished"] == [
        {
            "event": "scan.execute.vanished", "log_level": "warning",
            "scan_id": stray.id, "client_id": theirs.id,
        }
    ]
    assert chains == [own.id]
    left = (
        await session.execute(
            select(Scan.status, Scan.started_at, Scan.lease_expires_at).where(Scan.id == stray.id)
        )
    ).one()
    assert tuple(left) == (ScanStatus.QUEUED, None, None)


async def test_the_executor_runs_nothing_for_a_job_that_names_another_client_of_the_agency(
    client: AsyncClient, session: AsyncSession, settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job's client must be the scan's own, not only in the same agency: a job that names a
    different client of this agency takes the vanished-client branch and runs nothing."""
    row = await _client_row(client, session)
    second = Client(
        id=ids.new_id(ids.CLIENT), agency_id=row.agency_id, name="second.example",
        domain="second.example",
    )
    session.add(second)
    await session.commit()
    own = await _new_scan(session, row, ScanStatus.QUEUED)
    chains: list[str] = []

    async def chain(session, scan, client, job, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        chains.append(scan.id)

    monkeypatch.setattr(executor_module, "_run_chain", chain)

    with structlog.testing.capture_logs() as events:
        await execute_scan(
            ScanJob(scan_id=own.id, client_id=second.id, engines=DEFAULT_ENGINES),
            settings=settings,
        )

    assert [e for e in events if e["event"] == "scan.execute.vanished"] == [
        {
            "event": "scan.execute.vanished", "log_level": "warning",
            "scan_id": own.id, "client_id": second.id,
        }
    ]
    assert chains == []
    left = (
        await session.execute(
            select(Scan.status, Scan.started_at, Scan.lease_expires_at).where(Scan.id == own.id)
        )
    ).one()
    assert tuple(left) == (ScanStatus.QUEUED, None, None)


# --- 15. the competitor set of a scan ---------------------------------------------------------


async def test_detection_for_a_scan_ignores_the_client_of_another_agency(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Their confirmed other site is not left out of our scan's set.

    Detection leaves out a domain the agency confirmed as another site of the client. The same
    domain, found for our own client with the site confirmed, is left out, which shows the
    mechanism is at work; for a scan that names their client it is written.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    row.other_sites = [THEIR_OTHER_SITE]
    await session.commit()
    own = await _new_scan(session, row)
    stray = await _scan_naming(session, row.agency_id, theirs)
    candidate = Candidate(domain=THEIR_OTHER_SITE, name="Bravo Shop", co_citation_positions=[1])
    outcome = DetectionOutcome(
        candidates=[candidate],
        status=DetectionStatus.OK, detection_confidence=Decimal("0.5"), serp_queries_run=0,
        co_citation_prompts_run=1, candidates_considered=1, used_industry_seed=False,
    )

    ours = await detection.persist_detection(session, own, outcome)
    assert ours.competitors == []

    with structlog.testing.capture_logs() as events:
        made = await detection.persist_detection(session, stray, outcome)

    assert [c.domain for c in made.competitors] == [THEIR_OTHER_SITE]
    assert [e for e in events if e["event"] == "competitors.skipped"] == []


# --- 16. the dashboard's recent scans ---------------------------------------------------------


async def test_the_dashboard_does_not_show_the_client_of_another_agency(
    client: AsyncClient, session: AsyncSession
) -> None:
    row = await _client_row(client, session)
    other = await _agency(session)
    theirs = await _their_client(session, other)
    own = await _new_scan(session, row)
    await _scan_naming(session, row.agency_id, theirs)

    resp = await client.get(f"{BASE}/dashboard")

    assert resp.status_code == 200, resp.text
    assert [scan["id"] for scan in resp.json()["recentScans"]] == [own.id]
    assert THEIR_NAME not in resp.text and THEIR_DOMAIN not in resp.text


# --- 17. a scan adopting the client's next list -----------------------------------------------


async def test_a_scan_under_another_agencys_id_cannot_adopt_the_clients_next_list(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The guard on adopting a next list names the agency as well as the client.

    A stray QUEUED scan names this client under another agency's id. Adopting is refused before
    anything is read or written, as it is for another client's scan
    (`test_competitor_next_list.py`), so the client's list can never become that row's set.
    """
    row = await _client_row(client, session)
    other = await _agency(session)
    stray = await _stray_scan(session, row, other, ScanStatus.QUEUED)

    with pytest.raises(ValueError, match="own client's next list"):
        await detection.adopt_next_list(session, stray, row)
