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
