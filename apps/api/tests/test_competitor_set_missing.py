"""A client with no competitor set can have one named by hand (launch checklist 3.78).

`PUT /clients/{clientId}/competitors` used to be a 404 for a client with no set on any scan, which
left the report's editor nothing to save to. Naming rivals now makes the client's NEXT LIST: a set
that belongs to the client and to no scan, recorded as every hand edit is, which the next scan that
starts adopts (`test_competitor_next_list.py` holds that design). No scan's own set is written, so
no finished scan, and no scan that is running, is scored against rivals nobody measured.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from avp_api import ids
from avp_api.models import Client, CompetitorSet, Scan, ScanStatus
from avp_api.services import competitors as detection

BASE = "/api/v1"
NO_SET = (
    "No competitor set for this client yet. "
    "A scan runs detection for a client that has none."
)
RIVALS = {"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}, {"name": "Intercom"}]}
RUNNING_SENTENCE = "A scan of this client is running. Name the rivals after it finishes."


async def _sign_up(client: AsyncClient, email: str = "missing@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Missing Set Agency", "fullName": "Operator",
            "email": email, "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_scan(
    session: AsyncSession, client_id: str, status: ScanStatus = ScanStatus.SUCCEEDED
) -> str:
    owner = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=owner.agency_id,
        status=status,
    )
    session.add(scan)
    await session.commit()
    return scan.id


async def _sets(session: AsyncSession, client_id: str) -> list[CompetitorSet]:
    """The sets that belong to this client's scans."""
    return list(
        (
            await session.execute(
                select(CompetitorSet)
                .join(Scan, Scan.id == CompetitorSet.scan_id)
                .where(Scan.client_id == client_id)
                .options(selectinload(CompetitorSet.competitors))
            )
        ).scalars()
    )


async def _next_lists(session: AsyncSession, client_id: str) -> list[CompetitorSet]:
    """The client's next list: a set with no scan, held for the client."""
    return list(
        (
            await session.execute(
                select(CompetitorSet)
                .where(CompetitorSet.client_id == client_id, CompetitorSet.scan_id.is_(None))
                .options(selectinload(CompetitorSet.competitors))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


def _never_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise AssertionError("detection must not run for a client that has a list")

    monkeypatch.setattr(detection, "detect_for_client", never)


async def _queued_scan(session: AsyncSession, client_id: str) -> Scan:
    scan_id = await _add_scan(session, client_id, ScanStatus.QUEUED)
    return (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()


async def _owner(session: AsyncSession, client_id: str) -> Client:
    return (await session.execute(select(Client).where(Client.id == client_id))).scalar_one()


class TestNamingRivalsForAClientWithNoSet:
    async def test_put_creates_the_next_list_as_a_hand_edit_and_no_scan_gets_a_set(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        await _add_scan(session, cid)

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # It belongs to the client, not to a scan: the next scan to start takes it.
        assert body["scanId"] is None
        assert body["status"] == "ok"
        assert body["detectionConfidence"] is None
        assert [(c["name"], c["isManualOverride"]) for c in body["competitors"]] == [
            ("Zendesk", True), ("Intercom", True),
        ]
        assert {c["detectionSource"] for c in body["competitors"]} == {"manual"}
        got = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert got.status_code == 200 and got.json()["id"] == body["id"]
        assert got.json()["scanId"] is None
        # Neither scan was given a set: no score or report of either can be moved by a save.
        assert await _sets(session, cid) == []
        assert [s.id for s in await _next_lists(session, cid)] == [body["id"]]

    async def test_an_empty_list_with_no_set_is_still_404_and_writes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": []})

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == NO_SET
        assert await _sets(session, cid) == []
        assert await _next_lists(session, cid) == []

    async def test_a_client_with_no_scan_is_still_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == NO_SET

    async def test_another_agencys_client_is_404_and_gets_no_set(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client, "owner@iso.example")
        cid = await _make_client(client)
        await _add_scan(session, cid)
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "intruder@iso.example")
            resp = await other.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
            assert resp.status_code == 404, resp.text
            assert resp.json()["detail"] != NO_SET
        assert await _sets(session, cid) == []
        assert await _next_lists(session, cid) == []

    async def test_the_next_scan_adopts_the_hand_named_list_without_detecting(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        named = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
        assert named.status_code == 200, named.text

        _never_detect(monkeypatch)
        fresh = await _queued_scan(session, cid)

        adopted, how = await detection.ensure_set_for_scan(
            session, fresh, await _owner(session, cid)
        )

        assert how == "adopted"
        assert adopted is not None and adopted.id == named.json()["id"]
        assert [c.name for c in adopted.active_competitors] == ["Zendesk", "Intercom"]
        assert all(c.is_manual_override for c in adopted.active_competitors)
        await session.rollback()


class TestTheLatestScansStatus:
    """A save never reaches a scan's own set, so no status of the latest scan can score it against
    rivals nobody measured. A scan that is RUNNING still refuses a save (409): whether it would
    take the list depends on a phase the person cannot see."""

    async def test_running_refuses_creating_the_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid, ScanStatus.RUNNING)

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == RUNNING_SENTENCE
        assert await _sets(session, cid) == []
        assert await _next_lists(session, cid) == []

    async def test_running_refuses_editing_an_existing_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        assert (
            await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
        ).status_code == 200
        await _add_scan(session, cid, ScanStatus.RUNNING)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors", json={"competitors": [{"name": "Other"}]}
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == RUNNING_SENTENCE
        [held] = await _next_lists(session, cid)
        assert sorted(c.name for c in held.active_competitors) == ["Intercom", "Zendesk"]

    async def test_a_queued_scan_adopts_the_list_when_it_runs_and_skips_detection(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        queued = await _add_scan(session, cid, ScanStatus.QUEUED)

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
        assert resp.status_code == 200, resp.text
        # Not the queued scan's set yet: it has not started, so it takes the list when it does.
        assert resp.json()["scanId"] is None
        assert await _sets(session, cid) == []

        _never_detect(monkeypatch)
        scan = (await session.execute(select(Scan).where(Scan.id == queued))).scalar_one()
        found, how = await detection.ensure_set_for_scan(
            session, scan, await _owner(session, cid)
        )
        assert how == "adopted"
        assert found is not None and len(found.active_competitors) == 2
        await session.rollback()

    async def test_a_failed_scan_is_no_reason_to_refuse_and_the_next_scan_adopts_the_list(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid, ScanStatus.FAILED)

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=RIVALS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["scanId"] is None

        _never_detect(monkeypatch)
        fresh = await _queued_scan(session, cid)
        adopted, how = await detection.ensure_set_for_scan(
            session, fresh, await _owner(session, cid)
        )
        assert how == "adopted"
        assert adopted is not None
        assert [c.name for c in adopted.active_competitors] == ["Zendesk", "Intercom"]
        await session.rollback()
