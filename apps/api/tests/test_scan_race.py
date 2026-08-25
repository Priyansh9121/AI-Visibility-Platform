"""Two requests, one scan — Epic 9.6.

`get_or_create_scan` has always meant to reuse an open scan rather than start a
second, but SELECT-then-INSERT is a check-then-act. Epic 9.5 narrowed the window
from ~303s to milliseconds by committing the scan early; only the database can
close it, because narrowing a race is not closing one.

`uq_scans_one_open_per_client` closes it. These tests cover all three layers:
the invariant the database now enforces, the recovery path the loser takes, and
the behaviour two genuinely concurrent HTTP requests see.

A losing scan is a scan that never happened — but a duplicated one costs a full
run of paid model calls, which is the actual thing being prevented.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Client, Scan, ScanStatus
from avp_api.services import competitors as detection

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "race@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Race Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _defer(client: AsyncClient) -> list:
    """An executor that records the job and never runs it."""
    jobs: list = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    return jobs


class TestTheDatabaseEnforcesIt:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (ScanStatus.QUEUED, ScanStatus.QUEUED),
            (ScanStatus.QUEUED, ScanStatus.RUNNING),
            (ScanStatus.RUNNING, ScanStatus.QUEUED),
            (ScanStatus.RUNNING, ScanStatus.RUNNING),
        ],
    )
    async def test_a_second_open_scan_is_rejected(
        self, client: AsyncClient, session, first: ScanStatus, second: ScanStatus
    ) -> None:  # noqa: ANN001
        """Every combination of open states, because the predicate spans both."""
        await _sign_up(client)
        cid = await _make_client(client)
        row = await session.get(Client, cid)

        session.add(Scan(id=ids.new_id(ids.SCAN), client_id=cid,
                         agency_id=row.agency_id, status=first))
        await session.commit()

        session.add(Scan(id=ids.new_id(ids.SCAN), client_id=cid,
                         agency_id=row.agency_id, status=second))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_finished_scans_are_not_constrained(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """The index is PARTIAL — a client accumulates any number of finished scans.

        A constraint that also covered terminal scans would let one client be
        scanned exactly once, ever.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        row = await session.get(Client, cid)

        for status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED,
                       ScanStatus.CANCELLED, ScanStatus.SUCCEEDED):
            session.add(Scan(id=ids.new_id(ids.SCAN), client_id=cid,
                             agency_id=row.agency_id, status=status))
        # ...plus one open scan alongside them.
        session.add(Scan(id=ids.new_id(ids.SCAN), client_id=cid,
                         agency_id=row.agency_id, status=ScanStatus.QUEUED))
        await session.commit()

        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 6


class TestTheLoserAdoptsTheWinner:
    async def test_a_lost_race_returns_the_winners_scan_rather_than_raising(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:  # noqa: ANN001
        """The recovery branch, driven deterministically.

        A real race needs the winner to commit between the loser's SELECT and
        its INSERT — a window of microseconds that a test cannot reliably hit.
        Instead the loser's first lookup is forced to miss, which is exactly
        what a stale snapshot looks like from inside the function, and the
        INSERT then meets a row that is genuinely already committed.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        row = await session.get(Client, cid)

        winner = Scan(id=ids.new_id(ids.SCAN), client_id=cid,
                      agency_id=row.agency_id, status=ScanStatus.QUEUED)
        session.add(winner)
        await session.commit()

        real = detection.open_scan
        calls = {"n": 0}

        async def blind_once(sess, client_id):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the stale snapshot
            return await real(sess, client_id)

        monkeypatch.setattr(detection, "open_scan", blind_once)

        adopted = await detection.get_or_create_scan(session, row)

        assert adopted.id == winner.id, "the loser started a second scan"
        assert calls["n"] == 2, "the recovery lookup did not happen"
        # The surrounding transaction is still usable — the failed INSERT was
        # rolled back to a SAVEPOINT, not the whole transaction.
        await session.commit()
        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 1

    async def test_a_genuine_constraint_failure_still_raises(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:  # noqa: ANN001
        """Recovery is for the race, not a blanket swallow of IntegrityError."""
        await _sign_up(client)
        cid = await _make_client(client)
        real = await session.get(Client, cid)

        async def never(sess, client_id):  # noqa: ANN001, ANN202, ARG001
            return None

        monkeypatch.setattr(detection, "open_scan", never)

        # A transient client that was never persisted. The scan it produces
        # violates the FOREIGN KEY, not the partial index, and no open scan
        # exists to adopt — so the recovery branch must re-raise rather than
        # quietly returning something.
        phantom = Client(
            id=ids.new_id(ids.CLIENT),
            agency_id=real.agency_id,
            name="Phantom",
            domain="phantom.example",
        )

        with pytest.raises(IntegrityError):
            await detection.get_or_create_scan(session, phantom)
        await session.rollback()


class TestTwoConcurrentRequests:
    async def test_they_resolve_to_one_scan(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """Genuinely concurrent, not sequential — 9.5 already covered sequential.

        Both requests are in flight at once, on separate sessions. Whichever way
        they interleave, the invariant is the same: one scan, two 202s, both
        naming it.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        _defer(client)

        first, second = await asyncio.gather(
            client.post(f"{BASE}/clients/{cid}/scans", json={}),
            client.post(f"{BASE}/clients/{cid}/scans", json={}),
        )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        # Step 9's contract: the loser is not an error and is not a second
        # scan — it is answered with the winner's scan, in its current state.
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["status"] == second.json()["status"] == "queued"

        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 1, "two concurrent requests bought two scans"

    async def test_five_at_once_still_resolve_to_one_scan(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """Widening the race widens the chance of hitting the INSERT window."""
        await _sign_up(client)
        cid = await _make_client(client)
        _defer(client)

        responses = await asyncio.gather(
            *(client.post(f"{BASE}/clients/{cid}/scans", json={}) for _ in range(5))
        )

        assert {r.status_code for r in responses} == {202}
        assert len({r.json()["id"] for r in responses}) == 1

        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 1


class TestTheDetectionPlaceholder:
    """Detection opens a scan. Epic 9.6 makes sure that scan stays usable.

    `POST /clients/{clientId}/competitors/detect` calls `get_or_create_scan` because
    §5.3 nests CompetitorSet under Scan. If no scan is run afterwards, the row
    it opened stays QUEUED indefinitely. That is by design — it exists so a
    later scan reuses it — but it must not be mistaken for work in progress.
    """

    async def test_detection_alone_leaves_exactly_one_open_scan(
        self, client: AsyncClient, session, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["zendesk.com"], cocit_brands=[("Zendesk", "zendesk.com")])

        detect = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert detect.status_code == 201, detect.text

        rows = list((await session.execute(select(Scan).where(Scan.client_id == cid))).scalars())
        assert len(rows) == 1
        assert rows[0].status is ScanStatus.QUEUED
        # Nothing has claimed it. RUNNING means claimed; this is not that, and
        # the Epic 9.5 reaper deliberately leaves it alone.
        assert rows[0].started_at is None

    async def test_running_detection_twice_does_not_open_a_second_scan(
        self, client: AsyncClient, session, stub_discovery
    ) -> None:  # noqa: ANN001
        """Epic 3's intent, now enforced by the index rather than only hoped for."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["zendesk.com"], cocit_brands=[("Zendesk", "zendesk.com")])

        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 1

    async def test_a_later_scan_reuses_the_placeholder_rather_than_starting_over(
        self, client: AsyncClient, session, stub_discovery
    ) -> None:  # noqa: ANN001
        """The flow the placeholder exists for, and the one the UI used to block.

        Before Epic 9.6 the dashboard treated that QUEUED row as work in
        progress and disabled re-run for the client indefinitely — so the only
        way to reach this path was to never look at the dashboard.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["zendesk.com"], cocit_brands=[("Zendesk", "zendesk.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        placeholder = (await session.execute(
            select(Scan).where(Scan.client_id == cid)
        )).scalar_one()

        _defer(client)
        queued = await client.post(f"{BASE}/clients/{cid}/scans", json={})

        assert queued.status_code == 202, queued.text
        assert queued.json()["id"] == placeholder.id, "the placeholder was abandoned"
        total = (await session.execute(
            select(func.count()).select_from(Scan).where(Scan.client_id == cid)
        )).scalar_one()
        assert total == 1
