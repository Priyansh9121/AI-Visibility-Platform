"""Dashboard behaviour, including the wire contract for scores."""

from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.models import (
    DEFAULT_WEIGHTS,
    Client,
    Scan,
    ScanStatus,
    Score,
    ScoreStatus,
)

BASE = "/api/v1"


async def _agency(client: AsyncClient, email: str = "dash@test.example") -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Dashboard Test",
            "fullName": "Operator",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _scan_with_score(
    engine,  # noqa: ANN001
    agency_id: str,
    composite: Decimal | None,
    status: ScoreStatus,
    domain: str = "northaven-dental.example",
) -> str:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        c = Client(
            id=ids.new_id(ids.CLIENT),
            agency_id=agency_id,
            name="Northaven Dental",
            domain=domain,
        )
        s.add(c)
        await s.flush()
        scan = Scan(
            id=ids.new_id(ids.SCAN),
            client_id=c.id,
            agency_id=agency_id,
            status=ScanStatus.SUCCEEDED,
        )
        s.add(scan)
        await s.flush()
        s.add(
            Score(
                id=ids.new_id(ids.SCORE),
                scan_id=scan.id,
                status=status,
                composite=composite,
                formula_version="v1",
                weights=dict(DEFAULT_WEIGHTS),
                reason_code=None if composite is not None else "NO_PROMPTS",
            )
        )
        await s.commit()
        return scan.id


async def test_empty_dashboard_is_explicitly_empty(client: AsyncClient) -> None:
    await _agency(client)
    body = (await client.get(f"{BASE}/dashboard")).json()
    assert body["isEmpty"] is True
    assert body["clientCount"] == 0
    assert body["scanCount"] == 0
    assert body["recentScans"] == []


async def test_composite_score_is_a_string_encoded_decimal(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """docs/api-contracts.md documents this exact encoding.

    The value is NUMERIC(5,2) in Postgres. Serialising it as a JSON *number*
    would reintroduce binary-float error on the wire — 38.35 becomes
    38.349999999999994 in a double — which is precisely what scoring-spec.md
    determinism rule 3 exists to prevent. So it crosses as a string.
    """
    agency_id = await _agency(client)
    await _scan_with_score(engine, agency_id, Decimal("38.35"), ScoreStatus.SCORED)

    resp = await client.get(f"{BASE}/dashboard")
    assert '"compositeScore":"38.35"' in resp.text

    entry = resp.json()["recentScans"][0]
    assert isinstance(entry["compositeScore"], str)
    assert Decimal(entry["compositeScore"]) == Decimal("38.35")


async def test_insufficient_data_scan_reports_null_not_zero(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """An unrunnable scan must never reach a client as a score of 0."""
    agency_id = await _agency(client)
    await _scan_with_score(engine, agency_id, None, ScoreStatus.INSUFFICIENT_DATA)

    entry = (await client.get(f"{BASE}/dashboard")).json()["recentScans"][0]
    assert entry["compositeScore"] is None


async def test_unscored_scan_still_appears(client: AsyncClient, engine) -> None:  # noqa: ANN001
    """The join is a LEFT join — a queued scan must not vanish from the list."""
    agency_id = await _agency(client)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        c = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name="N", domain="queued.example"
        )
        s.add(c)
        await s.flush()
        s.add(
            Scan(
                id=ids.new_id(ids.SCAN),
                client_id=c.id,
                agency_id=agency_id,
                status=ScanStatus.QUEUED,
            )
        )
        await s.commit()

    body = (await client.get(f"{BASE}/dashboard")).json()
    assert body["scanCount"] == 1
    assert body["isEmpty"] is False
    assert body["recentScans"][0]["status"] == "queued"
    assert body["recentScans"][0]["compositeScore"] is None


async def test_recent_scans_are_newest_first(client: AsyncClient, engine) -> None:  # noqa: ANN001
    agency_id = await _agency(client)
    # Distinct domains: (agency_id, domain) is unique, since one agency tracks
    # a given domain once.
    created = [
        await _scan_with_score(
            engine, agency_id, Decimal("10.00"), ScoreStatus.SCORED, domain=f"c{i}.example"
        )
        for i in range(3)
    ]
    returned = [s["id"] for s in (await client.get(f"{BASE}/dashboard")).json()["recentScans"]]
    assert returned == list(reversed(created))


async def test_limit_is_bounded(client: AsyncClient) -> None:
    await _agency(client)
    assert (await client.get(f"{BASE}/dashboard?limit=0")).status_code == 422
    assert (await client.get(f"{BASE}/dashboard?limit=51")).status_code == 422
    assert (await client.get(f"{BASE}/dashboard?limit=50")).status_code == 200


# --- the getting-started facts — Epic 19 ------------------------------------
#
# Three fields the checklist derives its five steps from. `clientCount`,
# `scanCount` and `seats` already existed; these tests cover the two counts
# that had to be added because `recentScans` is a page and not a history, and
# the one stored fact — dismissal — which is a decision rather than a state.


async def test_a_new_agency_has_nothing_scored_nothing_shared_and_has_not_dismissed(
    client: AsyncClient,
) -> None:
    await _agency(client)
    body = (await client.get(f"{BASE}/dashboard")).json()
    assert body["scoredScanCount"] == 0
    assert body["sharedScanCount"] == 0
    assert body["gettingStartedDismissed"] is False


async def test_scored_scan_count_counts_scored_and_not_insufficient_data(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """INSUFFICIENT_DATA is a real outcome, and it is not a score.

    The checklist's "get a score" step must not light on a scan that ran and
    produced nothing — the same rule the list applies when it renders that
    scan's score as null rather than zero.
    """
    agency_id = await _agency(client)
    await _scan_with_score(engine, agency_id, None, ScoreStatus.INSUFFICIENT_DATA, "a.example")
    assert (await client.get(f"{BASE}/dashboard")).json()["scoredScanCount"] == 0

    await _scan_with_score(engine, agency_id, Decimal("41.20"), ScoreStatus.SCORED, "b.example")
    await _scan_with_score(engine, agency_id, Decimal("12.00"), ScoreStatus.SCORED, "c.example")
    assert (await client.get(f"{BASE}/dashboard")).json()["scoredScanCount"] == 2


async def test_scored_scan_count_is_the_whole_history_not_the_page(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The reason the count exists at all.

    `recentScans` is bounded by `limit`. With one scored scan and a page of
    unscored ones after it, the scored scan is not on the page — and the
    checklist must still know a score has been produced.
    """
    agency_id = await _agency(client)
    await _scan_with_score(engine, agency_id, Decimal("38.35"), ScoreStatus.SCORED, "old.example")
    # Three finished-but-unscored scans, one client each: `uq_scans_one_open_per_client`
    # allows a single OPEN scan per client, and CANCELLED is a closed state.
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        for i in range(3):
            c = Client(
                id=ids.new_id(ids.CLIENT),
                agency_id=agency_id,
                name=f"N{i}",
                domain=f"newer-{i}.example",
            )
            s.add(c)
            await s.flush()
            s.add(
                Scan(
                    id=ids.new_id(ids.SCAN),
                    client_id=c.id,
                    agency_id=agency_id,
                    status=ScanStatus.CANCELLED,
                )
            )
        await s.commit()

    body = (await client.get(f"{BASE}/dashboard?limit=2")).json()
    assert len(body["recentScans"]) == 2
    assert all(entry["compositeScore"] is None for entry in body["recentScans"])
    assert body["scoredScanCount"] == 1


async def test_shared_scan_count_follows_the_live_link(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """Minting a share link completes the step; revoking it un-does it.

    The count reads `share_token IS NOT NULL`, which is the column's own
    meaning — a live link exists — and not a diary of links that ever
    existed. The checklist describes the account as it is.
    """
    agency_id = await _agency(client)
    scan_id = await _scan_with_score(engine, agency_id, Decimal("38.35"), ScoreStatus.SCORED)

    assert (await client.get(f"{BASE}/dashboard")).json()["sharedScanCount"] == 0

    minted = await client.post(f"{BASE}/scans/{scan_id}/share")
    assert minted.status_code in (200, 201), minted.text
    assert (await client.get(f"{BASE}/dashboard")).json()["sharedScanCount"] == 1

    revoked = await client.delete(f"{BASE}/scans/{scan_id}/share")
    assert revoked.status_code == 204, revoked.text
    assert (await client.get(f"{BASE}/dashboard")).json()["sharedScanCount"] == 0


async def test_the_counts_are_scoped_to_the_callers_agency(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    other = await _agency(client, email="other@test.example")
    await _scan_with_score(engine, other, Decimal("55.00"), ScoreStatus.SCORED, "theirs.example")
    # Sign up a second agency on the same client; its cookie replaces the first.
    await _agency(client, email="mine@test.example")
    body = (await client.get(f"{BASE}/dashboard")).json()
    assert body["scoredScanCount"] == 0
    assert body["sharedScanCount"] == 0


async def test_dismissing_the_checklist_is_per_agency_and_idempotent(
    client: AsyncClient,
) -> None:
    await _agency(client)
    assert (await client.get(f"{BASE}/dashboard")).json()["gettingStartedDismissed"] is False

    first = await client.post(f"{BASE}/dashboard/getting-started/dismiss")
    assert first.status_code == 204, first.text
    assert first.content == b""
    assert (await client.get(f"{BASE}/dashboard")).json()["gettingStartedDismissed"] is True

    # A second click on a stale page is not a second decision, and not an error.
    second = await client.post(f"{BASE}/dashboard/getting-started/dismiss")
    assert second.status_code == 204

    # Another agency is untouched.
    await _agency(client, email="someone-else@test.example")
    assert (await client.get(f"{BASE}/dashboard")).json()["gettingStartedDismissed"] is False


async def test_dismissing_requires_a_session(client: AsyncClient) -> None:
    resp = await client.post(f"{BASE}/dashboard/getting-started/dismiss")
    assert resp.status_code == 401
