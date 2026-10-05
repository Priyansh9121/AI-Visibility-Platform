"""The client's next list: where a saved or detected rivals list goes (launch checklist 3.78, 3.79).

A scan's competitor set never changes once the scan has started. A save (`PUT
/clients/{clientId}/competitors`) and a detection run from the button (`POST
.../competitors/detect`) write the client's NEXT LIST instead: a `CompetitorSet` that belongs to the
client and to no scan (`scan_id` NULL, `client_id` set), at most one per client. The next scan that
starts adopts it (`ensure_set_for_scan`), and from then on it is that scan's own set and is never
written again. These tests hold each half of that: the schema, what a save and a detection write,
what a scan takes, and that a save racing an adoption cannot reach the adopted set.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api.models import (
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Scan,
    ScanStatus,
)
from avp_api.services import competitors as detection
from avp_api.services import prompt_runs

BASE = "/api/v1"
NO_SET = (
    "No competitor set for this client yet. "
    "A scan runs detection for a client that has none."
)
RUNNING = "A scan of this client is running. Name the rivals after it finishes."


# --- building blocks ----------------------------------------------------------------------------


async def _sign_up(client: AsyncClient, email: str = "nextlist@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Next List Agency", "fullName": "Operator",
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
        id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=owner.agency_id, status=status
    )
    session.add(scan)
    await session.commit()
    return scan.id


async def _add_set(session: AsyncSession, scan_id: str, *names: str) -> str:
    """A scan's own set of detected rivals, as a finished scan has one."""
    cset = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan_id, status=DetectionStatus.OK,
        detection_confidence=1, serp_queries_run=6, candidates_considered=9,
        detected_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(cset)
    await session.flush()
    for rank, name in enumerate(names, start=1):
        session.add(
            Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name,
                domain=f"{name.lower().replace(' ', '')}.com", rank=rank,
                detection_source=DetectionSource.BOTH, signal_count=7, serp_mentions=4,
                co_citation_mentions=3, corroborated=True,
            )
        )
    await session.commit()
    return cset.id


async def _rows(session: AsyncSession, set_id: str) -> list[tuple[Any, ...]]:
    """Every row of a set (tombstones too) as plain values, so nothing is a stale object."""
    result = await session.execute(
        select(
            Competitor.id, Competitor.name, Competitor.domain, Competitor.rank,
            Competitor.detection_source, Competitor.is_manual_override, Competitor.is_suppressed,
        )
        .where(Competitor.competitor_set_id == set_id)
        .order_by(Competitor.rank, Competitor.name)
    )
    return [tuple(row) for row in result]


async def _active(session: AsyncSession, set_id: str) -> list[str]:
    return [r[1] for r in await _rows(session, set_id) if not r[6]]


async def _scan_set_ids(session: AsyncSession, client_id: str) -> list[str]:
    """The sets that belong to this client's scans."""
    return list(
        (
            await session.execute(
                select(CompetitorSet.id)
                .join(Scan, Scan.id == CompetitorSet.scan_id)
                .where(Scan.client_id == client_id)
                .order_by(CompetitorSet.id)
            )
        ).scalars()
    )


async def _next_list_ids(session: AsyncSession, client_id: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(CompetitorSet.id).where(
                    CompetitorSet.client_id == client_id, CompetitorSet.scan_id.is_(None)
                )
            )
        ).scalars()
    )


def _put(client: AsyncClient, cid: str, *names: str):  # noqa: ANN202
    return client.put(
        f"{BASE}/clients/{cid}/competitors",
        json={"competitors": [
            {"name": n, "domain": f"{n.lower().replace(' ', '')}.com"} for n in names
        ]},
    )


def _no_detection(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make detection fail the test, and count what reaches it."""
    calls: list[int] = []

    async def never(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        calls.append(1)
        raise AssertionError("detection must not run for a client that has a list")

    monkeypatch.setattr(detection, "detect_for_client", never)
    return calls


async def _owner(session: AsyncSession, client_id: str) -> Client:
    return (await session.execute(select(Client).where(Client.id == client_id))).scalar_one()


async def _ad_hoc_names(session: AsyncSession, client_id: str) -> list[str]:
    """The rivals an ad hoc prompt run would look for."""
    owner = await _owner(session, client_id)
    return [name for name, _ in await prompt_runs.subject_competitors(session, owner)]


async def _scan(session: AsyncSession, scan_id: str) -> Scan:
    return (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()


# --- the schema ---------------------------------------------------------------------------------


class TestTheSchemaHoldsTheShape:
    async def test_a_client_has_at_most_one_next_list(self, client: AsyncClient, session) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        session.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=cid))
        await session.commit()

        session.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=cid))
        with pytest.raises(IntegrityError, match="uq_competitor_sets_next_list_per_client"):
            await session.commit()
        await session.rollback()

    async def test_a_set_belongs_to_a_scan_or_a_client_never_both(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)

        session.add(
            CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan_id, client_id=cid)
        )
        with pytest.raises(IntegrityError, match="ck_competitor_sets_scan_or_client"):
            await session.commit()
        await session.rollback()

    async def test_a_set_belongs_to_a_scan_or_a_client_never_neither(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        session.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET)))
        with pytest.raises(IntegrityError, match="ck_competitor_sets_scan_or_client"):
            await session.commit()
        await session.rollback()

    async def test_each_client_may_hold_a_next_list_beside_the_sets_of_its_scans(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """The positive control: the two constraints refuse the wrong shapes and only those."""
        await _sign_up(client)
        one = await _make_client(client, "helpscout.com")
        two = await _make_client(client, "zendesk.com")
        await _add_set(session, await _add_scan(session, one), "Alpha")
        await _add_set(session, await _add_scan(session, two), "Bravo")
        session.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=one))
        session.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=two))
        await session.commit()

        assert len(await _next_list_ids(session, one)) == 1
        assert len(await _next_list_ids(session, two)) == 1


# --- a save --------------------------------------------------------------------------------------


class TestASaveWritesTheNextList:
    async def test_the_first_save_copies_the_scans_list_and_leaves_the_scan_alone(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, "Zendesk", "Front")
        before = await _rows(session, own)

        resp = await _put(client, cid, "Front", "Intercom")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scanId"] is None
        assert [c["name"] for c in body["competitors"]] == ["Front", "Intercom"]
        assert all(c["isManualOverride"] for c in body["competitors"])
        assert body["detectionConfidence"] is None
        # The scan's own set is exactly as it was: same rows, same ids, same flags.
        assert await _rows(session, own) == before
        # The save is one next list, the copy with the override applied: the rival it did not
        # send back is a struck row, so a later detection cannot reinstate it.
        [next_list] = await _next_list_ids(session, cid)
        assert next_list != own and next_list == body["id"]
        rows = {r[1]: r for r in await _rows(session, next_list)}
        assert set(rows) == {"Front", "Intercom", "Zendesk"}
        assert rows["Zendesk"][6] is True and rows["Front"][6] is False
        assert await _scan_set_ids(session, cid) == [own]

    async def test_a_second_save_edits_the_same_next_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        own = await _add_set(session, await _add_scan(session, cid), "Zendesk", "Front")

        first = await _put(client, cid, "Front", "Intercom")
        [next_list] = await _next_list_ids(session, cid)
        front_row = next(r[0] for r in await _rows(session, next_list) if r[1] == "Front")
        second = await _put(client, cid, "Front", "Freshdesk")

        assert first.status_code == second.status_code == 200
        assert second.json()["id"] == first.json()["id"] == next_list
        assert await _next_list_ids(session, cid) == [next_list]
        assert [c["name"] for c in second.json()["competitors"]] == ["Front", "Freshdesk"]
        # The kept rival's row is reused, not recreated: its id is what the row is known by.
        assert next(r[0] for r in await _rows(session, next_list) if r[1] == "Front") == front_row
        assert await _active(session, own) == ["Zendesk", "Front"]

    async def test_a_client_with_a_next_list_and_no_scan_set_is_edited_in_place(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """No set on any scan, a next list already there (named earlier): the save edits it."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        first = await _put(client, cid, "Zendesk")
        second = await _put(client, cid, "Zendesk", "Front")

        assert (first.status_code, second.status_code) == (200, 200)
        assert second.json()["id"] == first.json()["id"]
        assert [c["name"] for c in second.json()["competitors"]] == ["Zendesk", "Front"]

    async def test_an_empty_save_clears_the_next_list_and_the_scans_list_stays(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        own = await _add_set(session, await _add_scan(session, cid), "Zendesk")

        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": []})

        assert resp.status_code == 200, resp.text
        assert resp.json()["competitors"] == [] and resp.json()["status"] == "no_signal"
        assert await _active(session, own) == ["Zendesk"]

    async def test_a_save_after_a_scan_adopted_the_list_starts_a_new_one(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk")
        _no_detection(monkeypatch)
        await _put(client, cid, "Front")

        scan_id = await _add_scan(session, cid, ScanStatus.QUEUED)
        adopted, how = await detection.ensure_set_for_scan(
            session, await _scan(session, scan_id), await _owner(session, cid)
        )
        assert how == "adopted" and adopted is not None
        await session.commit()
        adopted_rows = await _rows(session, adopted.id)
        assert await _next_list_ids(session, cid) == []

        resp = await _put(client, cid, "Freshdesk")

        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] != adopted.id and resp.json()["scanId"] is None
        assert await _rows(session, adopted.id) == adopted_rows
        assert await _active(session, adopted.id) == ["Front"]


class TestWhatAnotherAgencyCannotDo:
    async def test_another_agencys_save_and_read_touch_nothing(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client, "owner@next.example")
        cid = await _make_client(client)
        own = await _add_set(session, await _add_scan(session, cid), "Zendesk")
        await _put(client, cid, "Front")
        before = await _rows(session, (await _next_list_ids(session, cid))[0])
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "intruder@next.example")
            put = await other.put(
                f"{BASE}/clients/{cid}/competitors",
                json={"competitors": [{"name": "Planted"}]},
            )
            got = await other.get(f"{BASE}/clients/{cid}/competitors")
            assert put.status_code == got.status_code == 404
            assert put.json()["detail"] != NO_SET

        [next_list] = await _next_list_ids(session, cid)
        assert await _rows(session, next_list) == before
        assert await _active(session, own) == ["Zendesk"]

    async def test_a_clients_next_list_is_not_another_clients_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        one = await _make_client(client, "helpscout.com")
        two = await _make_client(client, "zendesk.com")
        await _add_set(session, await _add_scan(session, one), "Zendesk")
        await _put(client, one, "Front")
        await _add_scan(session, two)

        assert (await client.get(f"{BASE}/clients/{two}/competitors")).status_code == 404
        # And the other client's save does not find this one's list to edit.
        made = await _put(client, two, "Intercom")
        assert made.status_code == 200
        [one_list] = await _next_list_ids(session, one)
        [two_list] = await _next_list_ids(session, two)
        assert one_list != two_list
        assert await _active(session, one_list) == ["Front"]
        assert await _active(session, two_list) == ["Intercom"]


class TestTheCurrentListIsWhatGetReturns:
    async def test_the_next_list_when_there_is_one_and_the_latest_set_when_there_is_not(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, "Zendesk", "Front")

        before = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert before.status_code == 200
        assert before.json()["id"] == own and before.json()["scanId"] == scan_id

        await _put(client, cid, "Intercom")
        after = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert after.json()["scanId"] is None
        assert [c["name"] for c in after.json()["competitors"]] == ["Intercom"]
        assert after.json()["id"] != own

    async def test_a_client_with_neither_is_a_404_in_the_same_words(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        resp = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert resp.status_code == 404 and resp.json()["detail"] == NO_SET


# --- a scan --------------------------------------------------------------------------------------


class TestAScanTakesTheClientsList:
    async def test_it_adopts_the_next_list_without_detecting_and_the_list_stops_being_a_next_list(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk")
        await _put(client, cid, "Front", "Intercom")
        [next_list] = await _next_list_ids(session, cid)
        calls = _no_detection(monkeypatch)

        scan_id = await _add_scan(session, cid, ScanStatus.QUEUED)
        found, how = await detection.ensure_set_for_scan(
            session, await _scan(session, scan_id), await _owner(session, cid)
        )
        await session.commit()

        assert how == "adopted" and calls == []
        assert found is not None and found.id == next_list
        # It is now that scan's set, and no longer the client's next list.
        row = (
            await session.execute(
                select(CompetitorSet.scan_id, CompetitorSet.client_id).where(
                    CompetitorSet.id == next_list
                )
            )
        ).one()
        assert tuple(row) == (scan_id, None)
        assert await _next_list_ids(session, cid) == []
        assert await _active(session, next_list) == ["Front", "Intercom"]

    async def test_a_scan_with_a_set_of_its_own_keeps_it_and_leaves_the_next_list_for_later(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid, ScanStatus.QUEUED)
        own = await _add_set(session, scan_id, "Zendesk")
        await _put(client, cid, "Front")
        [next_list] = await _next_list_ids(session, cid)
        _no_detection(monkeypatch)

        found, how = await detection.ensure_set_for_scan(
            session, await _scan(session, scan_id), await _owner(session, cid)
        )

        assert how == "existing" and found is not None and found.id == own
        assert await _next_list_ids(session, cid) == [next_list]
        await session.rollback()

    async def test_without_a_next_list_it_still_carries_the_latest_set_forward(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk", "Front")
        _no_detection(monkeypatch)
        scan_id = await _add_scan(session, cid, ScanStatus.QUEUED)

        found, how = await detection.ensure_set_for_scan(
            session, await _scan(session, scan_id), await _owner(session, cid)
        )

        assert how == "carried_forward" and found is not None
        assert [c.name for c in found.active_competitors] == ["Zendesk", "Front"]
        await session.rollback()

    async def test_a_scan_takes_only_its_own_clients_next_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        one = await _make_client(client, "helpscout.com")
        two = await _make_client(client, "zendesk.com")
        await _add_set(session, await _add_scan(session, one), "Zendesk")
        await _put(client, one, "Planted")
        [one_list] = await _next_list_ids(session, one)
        scan_id = await _add_scan(session, two, ScanStatus.QUEUED)

        # Client two has nothing to take, so it detects (the suite's autouse stubs stand in for
        # the network): what matters is that it does not take client one's list.
        found, how = await detection.ensure_set_for_scan(
            session, await _scan(session, scan_id), await _owner(session, two)
        )

        assert how == "detected" and found is not None
        assert found.id != one_list
        assert await _next_list_ids(session, one) == [one_list]
        await session.rollback()

    async def test_adopting_for_another_clients_scan_is_refused(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        one = await _make_client(client, "helpscout.com")
        two = await _make_client(client, "zendesk.com")
        await _add_set(session, await _add_scan(session, one), "Zendesk")
        await _put(client, one, "Planted")
        scan_id = await _add_scan(session, two, ScanStatus.QUEUED)

        with pytest.raises(ValueError):
            await detection.adopt_next_list(
                session, await _scan(session, scan_id), await _owner(session, one)
            )
        await session.rollback()
        assert len(await _next_list_ids(session, one)) == 1

    async def test_a_scan_queued_before_the_save_adopts_it_when_it_runs(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        """The placeholder a detection leaves, or any queued scan: named after, used at run."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk")
        queued = await _add_scan(session, cid, ScanStatus.QUEUED)

        named = await _put(client, cid, "Front", "Intercom")
        assert named.status_code == 200, named.text

        _no_detection(monkeypatch)
        found, how = await detection.ensure_set_for_scan(
            session, await _scan(session, queued), await _owner(session, cid)
        )
        await session.commit()

        assert how == "adopted" and found is not None
        assert await _active(session, found.id) == ["Front", "Intercom"]
        assert (await _scan_set_ids(session, cid)).count(found.id) == 1


# --- two writers ---------------------------------------------------------------------------------


async def _a_session_is_waiting_on_a_lock(
    engine: AsyncEngine, saving: asyncio.Task[Any], seconds: float = 10.0
) -> None:
    """Wait until the save is blocked on a row lock, or say why it is not.

    Each look is its own connection and transaction on purpose: `pg_stat_activity` is a snapshot
    taken the first time it is read in a transaction and kept until that transaction ends, so
    polling it from a session whose transaction is already open would show the moment of the
    first look for ever, and miss a wait that began after it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if saving.done():
            raise AssertionError(
                f"the save finished instead of waiting: {saving.result().status_code} "
                f"{saving.result().text[:300]}"
            )
        async with engine.connect() as conn:
            waiting = (
                await conn.execute(
                    text(
                        "select count(*) from pg_stat_activity "
                        "where datname = current_database() and wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("nothing ever waited on the adoption's lock")


class TestTwoWritersAtOnce:
    async def test_a_save_waiting_on_an_adoption_cannot_edit_the_adopted_list(
        self, client: AsyncClient, session, engine  # noqa: ANN001
    ) -> None:
        """The heart of "a scan's set never changes once the scan has started".

        The save has found the next list and is waiting for its row, because a scan is adopting
        it. When the adoption commits the row is the scan's, so the save must not write to it:
        it makes a new next list from what is now the latest set, and the adopted list is as it
        was.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Alpha")
        await _put(client, cid, "Bravo")
        queued = await _add_scan(session, cid, ScanStatus.QUEUED)

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as adopter:
            scan = (await adopter.execute(select(Scan).where(Scan.id == queued))).scalar_one()
            owner = (await adopter.execute(select(Client).where(Client.id == cid))).scalar_one()
            adopted, how = await detection.ensure_set_for_scan(adopter, scan, owner)
            assert how == "adopted" and adopted is not None
            adopted_id = adopted.id

            saving = asyncio.create_task(_put(client, cid, "Charlie"))
            await _a_session_is_waiting_on_a_lock(engine, saving)
            assert not saving.done(), "the save did not wait for the adoption"
            await adopter.commit()
            resp = await asyncio.wait_for(saving, timeout=15)

        assert resp.status_code == 200, resp.text
        assert resp.json()["scanId"] is None
        assert [c["name"] for c in resp.json()["competitors"]] == ["Charlie"]
        # The scan's set is the list that was adopted, as it was adopted.
        assert await _active(session, adopted_id) == ["Bravo"]
        assert adopted_id in await _scan_set_ids(session, cid)
        # The save is the client's one next list, and it is not the adopted one.
        assert await _next_list_ids(session, cid) == [resp.json()["id"]]
        assert resp.json()["id"] != adopted_id

    async def test_a_next_list_that_appears_first_is_adopted_not_a_500(
        self,
        client: AsyncClient,
        session,  # noqa: ANN001
        settings,  # noqa: ANN001
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two first saves: between this one's look and its insert another commits the list."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)
        real = detection.create_next_list

        async def look_then_lose_the_race(db, who, **kwargs):  # noqa: ANN001, ANN003
            other = create_async_engine(settings.database_url, poolclass=NullPool)
            try:
                async with async_sessionmaker(other, expire_on_commit=False)() as s:
                    s.add(CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=who.id))
                    await s.commit()
            finally:
                await other.dispose()
            return await real(db, who, **kwargs)

        monkeypatch.setattr(detection, "create_next_list", look_then_lose_the_race)

        resp = await _put(client, cid, "Zendesk", "Intercom")

        assert resp.status_code == 200, resp.text
        assert len(await _next_list_ids(session, cid)) == 1
        assert [c["name"] for c in resp.json()["competitors"]] == ["Zendesk", "Intercom"]

    async def test_two_first_saves_at_once_leave_one_next_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)

        first, second = await asyncio.gather(_put(client, cid, "Alpha"), _put(client, cid, "Bravo"))

        assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
        [next_list] = await _next_list_ids(session, cid)
        assert await _active(session, next_list) in (["Alpha"], ["Bravo"])


# --- which set is the latest ----------------------------------------------------------------------


async def _finish(session: AsyncSession, scan_id: str) -> None:
    scan = await _scan(session, scan_id)
    scan.status = ScanStatus.SUCCEEDED
    await session.commit()


class TestTheLatestSetBelongsToTheLatestScan:
    """"The latest set" follows the scans, not when a set was minted.

    A next list keeps the id it was minted with when a scan adopts it. If an earlier scan's own
    set is minted after that (its detection finishing late), ordering by set id would put the
    earlier scan's raw detection ahead of the later scan's adopted list, and the operator's names
    and strikes would silently be gone from every reader of "the latest set".
    """

    async def test_an_adopted_list_minted_before_an_earlier_scans_set_is_still_the_latest(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        s1 = await _add_scan(session, cid)  # ran; its own set is not written yet
        await asyncio.sleep(0.01)
        assert (await _put(client, cid, "Operator Pick")).status_code == 200
        [listed] = await _next_list_ids(session, cid)  # minted now: the next list
        await asyncio.sleep(0.01)
        raw = await _add_set(session, s1, "RawDetection")  # S1's own set, minted AFTER the list
        await asyncio.sleep(0.01)
        s2 = await _add_scan(session, cid, ScanStatus.QUEUED)
        adopted, how = await detection.ensure_set_for_scan(
            session, await _scan(session, s2), await _owner(session, cid)
        )
        assert how == "adopted" and adopted is not None and adopted.id == listed
        await session.commit()
        await _finish(session, s2)
        assert listed < raw, "the case needs the set ids in the opposite order to the scans"

        # GET, with no next list left, is the later scan's adopted list.
        got = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert got.json()["id"] == listed
        assert [c["name"] for c in got.json()["competitors"]] == ["Operator Pick"]

        # Carry-forward copies it, not S1's raw detection.
        await asyncio.sleep(0.01)
        s3 = await _add_scan(session, cid, ScanStatus.QUEUED)
        carried, how = await detection.ensure_set_for_scan(
            session, await _scan(session, s3), await _owner(session, cid)
        )
        assert how == "carried_forward" and carried is not None
        assert [c.name for c in carried.active_competitors] == ["Operator Pick"]
        await session.commit()

        # An ad hoc run looks for it.
        names = await _ad_hoc_names(session, cid)
        assert names == ["Operator Pick"]


class TestTheListQueriesAreScopedToTheAgency:
    async def test_another_agencys_client_finds_nothing(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk")
        await _put(client, cid, "Front")
        owner = await _owner(session, cid)
        stranger = SimpleNamespace(id=cid, agency_id="agcy_someone_else")

        for who, found in ((owner, True), (stranger, False)):
            for query in (
                detection.next_list_for_client,
                detection.latest_set_for_client,
                detection.current_list_for_client,
            ):
                assert (await query(session, who) is not None) is found, (who, query.__name__)


class TestALostRaceThenAnAdoption:
    @pytest.mark.parametrize("route", ["put", "detect"])
    async def test_the_loser_starts_again_from_the_adopted_set_instead_of_failing(
        self,
        client: AsyncClient,
        session,  # noqa: ANN001
        engine,  # noqa: ANN001
        stub_discovery,  # noqa: ANN001
        monkeypatch: pytest.MonkeyPatch,
        route: str,
    ) -> None:
        """A rival writer commits the first next list, and a scan adopts it before the loser looks.

        The loser's insert is refused by the one-next-list-per-client index; its locked re-read then
        finds no next list, because the winner's is now a scan's set. That used to be re-raised (a
        500, and on detect the paid outcome thrown away). It now copies the adopted set instead.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Alpha")
        queued = await _add_scan(session, cid, ScanStatus.QUEUED)  # the scan that will adopt
        stub_discovery(serp_domains=["kayako.com"], cocit_brands=[("Kayako", "kayako.com")])
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        state = {"raced": False, "adopted": False}
        real_create, real_next = detection.create_next_list, detection.next_list_for_client
        winner_id = ids.new_id(ids.COMPETITOR_SET)

        async def a_rival_writes_first(db, who, **kwargs):  # noqa: ANN001, ANN003
            async with factory() as s:
                s.add(CompetitorSet(id=winner_id, client_id=cid))
                await s.flush()
                s.add(
                    Competitor(
                        id=ids.new_id(ids.COMPETITOR), competitor_set_id=winner_id, name="Winner",
                        domain="winner.com", rank=1, detection_source=DetectionSource.MANUAL,
                        is_manual_override=True,
                    )
                )
                await s.commit()
            state["raced"] = True
            return await real_create(db, who, **kwargs)

        async def a_scan_adopts_before_the_reread(db, who, *, lock=False):  # noqa: ANN001
            if lock and state["raced"] and not state["adopted"]:
                state["adopted"] = True
                async with factory() as s:
                    scan = (await s.execute(select(Scan).where(Scan.id == queued))).scalar_one()
                    owner = (await s.execute(select(Client).where(Client.id == cid))).scalar_one()
                    assert await detection.adopt_next_list(s, scan, owner) is not None
                    await s.commit()
            return await real_next(db, who, lock=lock)

        monkeypatch.setattr(detection, "create_next_list", a_rival_writes_first)
        monkeypatch.setattr(detection, "next_list_for_client", a_scan_adopts_before_the_reread)

        if route == "put":
            resp = await _put(client, cid, "Zendesk", "Intercom")
            assert resp.status_code == 200, resp.text
            assert [c["name"] for c in resp.json()["competitors"]] == ["Zendesk", "Intercom"]
        else:
            resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
            assert resp.status_code == 201, resp.text
            # The winner's hand-named rival came through the copy of the adopted set, and detection
            # found Kayako beside it: the paid outcome was kept, not thrown away.
            assert [c["name"] for c in resp.json()["competitors"]] == ["Winner", "Kayako"]

        assert state == {"raced": True, "adopted": True}
        # The winner's list is the scan's set now, untouched; the loser's write is the next list.
        [next_list] = await _next_list_ids(session, cid)
        assert next_list == resp.json()["id"] != winner_id
        assert await _active(session, winner_id) == ["Winner"]
        scan_of_winner = (
            await session.execute(
                select(CompetitorSet.scan_id).where(CompetitorSet.id == winner_id)
            )
        ).scalar_one()
        assert scan_of_winner == queued


# --- detection from the button -------------------------------------------------------------------


class TestDetectionWritesTheNextList:
    async def test_it_leaves_a_finished_scans_set_alone_and_writes_the_next_list(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        own = await _add_set(session, await _add_scan(session, cid), "Zendesk", "Front")
        before = await _rows(session, own)
        stub_discovery(
            serp_domains=["kayako.com", "freshdesk.com"],
            cocit_brands=[("Kayako", "kayako.com"), ("Freshdesk", "freshdesk.com")],
        )

        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        assert resp.json()["scanId"] is None
        assert {c["name"] for c in resp.json()["competitors"]} == {"Kayako", "Freshdesk"}
        assert await _rows(session, own) == before
        [next_list] = await _next_list_ids(session, cid)
        assert next_list == resp.json()["id"]
        assert await _scan_set_ids(session, cid) == [own]

    async def test_it_keeps_what_an_operator_named_and_struck_in_the_list_it_starts_from(
        self, client: AsyncClient, session, stub_discovery, a_day_passes  # noqa: ANN001
    ) -> None:
        """Detection now starts from the current list, so the hand edits in it survive."""
        await _sign_up(client)
        cid = await _make_client(client)
        own = await _add_set(session, await _add_scan(session, cid), "Zendesk", "Front")
        await _put(client, cid, "Operator Pick")
        stub_discovery(
            serp_domains=["zendesk.com", "kayako.com"],
            cocit_brands=[("Zendesk", "zendesk.com"), ("Kayako", "kayako.com")],
        )
        await a_day_passes()

        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        names = [c["name"] for c in resp.json()["competitors"]]
        assert names[0] == "Operator Pick" and "Kayako" in names
        assert "Zendesk" not in names, "a rival the operator struck was reinstated"
        assert await _active(session, own) == ["Zendesk", "Front"]

    async def test_it_writes_the_next_list_even_while_a_scan_is_running(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """It used to write the running scan's own set, which that scan had already read."""
        await _sign_up(client)
        cid = await _make_client(client)
        running = await _add_scan(session, cid, ScanStatus.RUNNING)
        own = await _add_set(session, running, "Zendesk")
        before = await _rows(session, own)
        stub_discovery(serp_domains=["kayako.com"], cocit_brands=[("Kayako", "kayako.com")])

        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        assert resp.json()["scanId"] is None
        assert await _rows(session, own) == before
        assert len(await _next_list_ids(session, cid)) == 1

    async def test_it_still_opens_the_placeholder_scan_a_later_scan_reuses(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The open scan is unchanged; what moved is where the rivals are written."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["kayako.com"], cocit_brands=[("Kayako", "kayako.com")])

        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        scans = list((await session.execute(select(Scan).where(Scan.client_id == cid))).scalars())
        assert [s.status for s in scans] == [ScanStatus.QUEUED]
        # The placeholder has no set of its own: its scan will adopt the next list.
        assert await _scan_set_ids(session, cid) == []
        assert await _next_list_ids(session, cid) == [resp.json()["id"]]


# --- an ad hoc prompt run ------------------------------------------------------------------------


class TestAnAdHocRunLooksForTheCurrentList:
    """A run is a new measurement, not a view of an old scan (founder, 2026-10-05): it looks for
    the client's current list (next list, else latest scan's set), not for struck rivals."""

    async def test_a_run_after_a_save_on_a_finished_scan_looks_for_the_saved_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk", "Front")
        assert await _ad_hoc_names(session, cid) == [
            "Zendesk", "Front",
        ]

        await _put(client, cid, "Intercom", "Front")

        names = await _ad_hoc_names(session, cid)
        assert names == ["Intercom", "Front"]

    async def test_a_struck_rival_is_not_looked_for(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, "Zendesk", "Front")
        await session.execute(
            Competitor.__table__.update()
            .where(Competitor.competitor_set_id == own, Competitor.name == "Zendesk")
            .values(is_suppressed=True)
        )
        await session.commit()
        # A struck row in the latest scan's set (no next list) ...
        assert await _ad_hoc_names(session, cid) == ["Front"]
        # ... and in a next list, where a strike is a tombstone beside the kept rival.
        await _put(client, cid, "Front", "Intercom")
        [next_list] = await _next_list_ids(session, cid)
        assert "Zendesk" in [r[1] for r in await _rows(session, next_list)]
        assert await _ad_hoc_names(session, cid) == [
            "Front", "Intercom",
        ]

    async def test_it_reads_only_this_clients_list(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        one = await _make_client(client, "helpscout.com")
        two = await _make_client(client, "zendesk.com")
        await _add_set(session, await _add_scan(session, one), "Zendesk")
        await _put(client, one, "Planted")
        await _add_scan(session, two)

        assert await _ad_hoc_names(session, two) == []
        assert await _ad_hoc_names(session, one) == ["Planted"]


# --- a running scan -----------------------------------------------------------------------------


class TestARunningScanStillRefusesAnEdit:
    """The 409 is unchanged (the other refusals are in `test_competitor_set_missing.py`)."""

    async def test_an_existing_next_list_is_left_as_it_was(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), "Zendesk")
        await _put(client, cid, "Front")
        [next_list] = await _next_list_ids(session, cid)
        before = await _rows(session, next_list)
        await _add_scan(session, cid, ScanStatus.RUNNING)

        resp = await _put(client, cid, "Other")

        assert resp.status_code == 409 and resp.json()["detail"] == RUNNING
        assert await _rows(session, next_list) == before
        assert await _next_list_ids(session, cid) == [next_list]
