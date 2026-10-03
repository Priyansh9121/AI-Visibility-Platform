"""The dashboard's "Share a report" count is the links that would open — checklist 3.42.

Founder decision (final): "Dashboard 'Share a report' counts only live links. Count only
links that would open (not failed scans', not expired). Leave cancelled scans as the
checklist row says unless trivial."

`sharedScanCount` used to be `share_token IS NOT NULL`, which also counted a link past its
end and a failed scan's, so the card said "One live link." of a link that opened nothing.
It now asks the public lookup's own question: `share.link_would_open` is that rule written
once as SQL, and `scan_for_share_token` and `count_links_that_open` both put it in their
WHERE clause. What these pin:

* the cases the founder named (live, expired, withdrawn, a failed scan's, a cancelled
  scan's) in one agency, and the count is exactly the links that open;
* the edge of "live" to the microsecond, against the lookup and `link_is_live`;
* another agency's links, in both directions;
* every status, with a live link and an ended one: the lookup and the count agree;
* that both really read the one expression (it is swapped, and both go dark);
* a link's whole life through the real endpoints.

Cancelled scans stay counted. Nothing writes CANCELLED today, and the public lookup serves
a cancelled scan's link, so a count that left one out would call dead a link a stranger can
open. Two tests say that out loud rather than leave it to a reader.

Every scan here is written directly, so a case is exactly the row it names. The lifecycle
test is where the real mint and withdraw endpoints are used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import false
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.models import Client, Scan, ScanStatus
from avp_api.security import new_share_token
from avp_api.services import share as share_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"

MICROSECOND = timedelta(microseconds=1)


def _ten_days_on() -> datetime:
    return datetime.now(UTC) + timedelta(days=10)


def _yesterday() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


async def _agency(client: AsyncClient, email: str = "shared-count@test.example") -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Shared Count Test",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _scan(
    engine,
    agency_id: str,
    domain: str,
    *,
    status: ScanStatus = ScanStatus.SUCCEEDED,
    link_ends_at: datetime | None = None,
) -> tuple[str, str | None]:
    """A scan for a fresh client, written directly. Returns (scan id, its share token).

    With an end, the scan carries a link ending then, whatever its status: the row a mint
    leaves, or a link the scan outlived. Without one it was never shared (no token, no end:
    the CHECK on `scans` allows only both or neither). A QUEUED or RUNNING scan written this
    way holds no lease and was never submitted, so the dashboard's reaper leaves it alone.
    """
    token = new_share_token() if link_ends_at is not None else None
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        c = Client(id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=domain, domain=domain)
        s.add(c)
        await s.flush()
        scan = Scan(
            id=ids.new_id(ids.SCAN),
            client_id=c.id,
            agency_id=agency_id,
            status=status,
            share_token=token,
            share_expires_at=link_ends_at,
        )
        s.add(scan)
        await s.commit()
        return scan.id, token


async def _change(engine, scan_id: str, **values) -> None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        await s.execute(sa_update(Scan).where(Scan.id == scan_id).values(**values))
        await s.commit()


async def _token_on_row(engine, scan_id: str) -> str | None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        scan = await s.get(Scan, scan_id)
        assert scan is not None
        return scan.share_token


async def _shown(client: AsyncClient) -> int:
    """What the dashboard says, read as the signed-in caller does."""
    resp = await client.get(f"{BASE}/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()["sharedScanCount"]


async def test_the_count_is_exactly_the_links_that_would_open(
    client: AsyncClient, engine, session
) -> None:
    """A live link, an expired one, a withdrawn one, a failed scan's and a cancelled scan's.

    The count is the live one and the cancelled one: two. The cancelled scan's link opens
    (the lookup serves it), so it is counted. The test also asks the lookup which of the
    tokens open, so the number is checked against the scans it is made of and not alone.
    """
    agency_id = await _agency(client)
    _, live = await _scan(engine, agency_id, "live.example", link_ends_at=_ten_days_on())
    _, expired = await _scan(engine, agency_id, "expired.example", link_ends_at=_yesterday())
    withdrawn_id, withdrawn = await _scan(
        engine, agency_id, "withdrawn.example", link_ends_at=_ten_days_on()
    )
    _, failed = await _scan(
        engine,
        agency_id,
        "failed.example",
        status=ScanStatus.FAILED,
        link_ends_at=_ten_days_on(),
    )
    _, cancelled = await _scan(
        engine,
        agency_id,
        "cancelled.example",
        status=ScanStatus.CANCELLED,
        link_ends_at=_ten_days_on(),
    )
    await _scan(engine, agency_id, "never-shared.example")

    # The withdrawn one goes through the real endpoint, so it is exactly what a withdrawal
    # leaves behind: no token on the row, and the old address dead.
    assert (await client.delete(f"{BASE}/scans/{withdrawn_id}/share")).status_code == 204
    assert await _token_on_row(engine, withdrawn_id) is None

    tokens = {
        "live": live,
        "expired": expired,
        "withdrawn": withdrawn,
        "failed": failed,
        "cancelled": cancelled,
    }
    assert None not in tokens.values()
    opens = {
        name
        for name, token in tokens.items()
        if await share_service.scan_for_share_token(session, token) is not None
    }
    assert opens == {"live", "cancelled"}

    # Not vacuous: counting the column alone, as this did before, gives four here (the live
    # link, the expired one, the failed scan's and the cancelled scan's).
    assert await _shown(client) == 2


async def test_a_cancelled_scans_live_link_is_counted_because_the_lookup_serves_it(
    client: AsyncClient, engine, session
) -> None:
    """The founder left cancelled scans as the checklist row says, and this is what that is.

    Excluding CANCELLED from the count would be a one-word change, but the public lookup
    does not refuse a cancelled scan's link, so the dashboard would call dead a link a
    stranger can open. Nothing writes CANCELLED today; if something starts to, and its
    links should stop opening, that is a change to `link_would_open`, which the lookup and
    the count both follow.
    """
    agency_id = await _agency(client)
    _, token = await _scan(
        engine,
        agency_id,
        "cancelled.example",
        status=ScanStatus.CANCELLED,
        link_ends_at=_ten_days_on(),
    )
    assert token is not None

    assert await share_service.scan_for_share_token(session, token) is not None
    assert await _shown(client) == 1


async def test_a_link_ending_exactly_now_is_not_counted_and_one_microsecond_later_is(
    client: AsyncClient, engine, session
) -> None:
    """The edge of "live" is strict, and the count draws it where `link_is_live` does.

    The clock is passed in, so the test stands exactly on the edge instead of racing it.
    The count, the public lookup and `link_is_live` are asked at three ends: a microsecond
    before `now`, exactly `now` (over), and a microsecond after (live).
    """
    agency_id = await _agency(client)
    now = datetime(2031, 6, 1, 12, 0, 0, tzinfo=UTC)
    scan_id, token = await _scan(engine, agency_id, "edge.example", link_ends_at=now)
    assert token is not None

    for ends, live in (
        (now - MICROSECOND, False),
        (now, False),
        (now + MICROSECOND, True),
    ):
        await _change(engine, scan_id, share_expires_at=ends)

        counted = await share_service.count_links_that_open(session, agency_id, now=now)
        found = await share_service.scan_for_share_token(session, token, now=now)

        assert counted == (1 if live else 0), ends
        assert (found is not None) is live, ends
        assert share_service.link_is_live(token, ends, now) is live, ends


async def test_another_agencys_links_are_never_counted(
    client: AsyncClient, engine, session
) -> None:
    """Two live links of one agency are nothing to another, and the other way round."""
    first_email = "first-agency@test.example"
    first = await _agency(client, first_email)
    await _scan(engine, first, "first-one.example", link_ends_at=_ten_days_on())
    await _scan(engine, first, "first-two.example", link_ends_at=_ten_days_on())
    assert await _shown(client) == 2

    # A second agency's sign-up replaces the cookie: from here the caller is the second.
    second = await _agency(client, "second-agency@test.example")
    assert await _shown(client) == 0
    assert await share_service.count_links_that_open(session, second) == 0

    await _scan(engine, second, "second-one.example", link_ends_at=_ten_days_on())
    assert await _shown(client) == 1
    assert await share_service.count_links_that_open(session, second) == 1
    # The first agency's two are untouched by the second's, on the dashboard too.
    assert await share_service.count_links_that_open(session, first) == 2
    login = await client.post(
        f"{BASE}/auth/login", json={"email": first_email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert await _shown(client) == 2


@pytest.mark.parametrize("status", list(ScanStatus), ids=lambda s: s.value)
@pytest.mark.parametrize("link", ["live", "ended"])
async def test_the_dashboard_counts_a_link_exactly_when_the_public_lookup_opens_it(
    client: AsyncClient, engine, session, status: ScanStatus, link: str
) -> None:
    """Every status, with a link that is live and one that has ended: the two cannot differ.

    The lookup is asked about the token and the dashboard about the agency, and they must
    agree. The expectation is written out too (a link opens when it has not ended and its
    scan has not FAILED), so two queries that were wrong together would still fail here.
    """
    agency_id = await _agency(client)
    ends = _ten_days_on() if link == "live" else _yesterday()
    _, token = await _scan(engine, agency_id, "agree.example", status=status, link_ends_at=ends)
    assert token is not None

    opens = await share_service.scan_for_share_token(session, token) is not None
    shown = await _shown(client)

    assert shown == (1 if opens else 0)
    assert opens is (link == "live" and status is not ScanStatus.FAILED)


async def test_the_lookup_and_the_count_read_one_expression(
    client: AsyncClient, engine, session, monkeypatch
) -> None:
    """The two cannot drift because neither restates the rule: both read `link_would_open`.

    With the expression swapped for one nothing satisfies, a link that opened before opens
    nowhere and counts nowhere. A second copy of the rule in either query would not be
    swapped, and this would fail.
    """
    agency_id = await _agency(client)
    _, token = await _scan(engine, agency_id, "one-rule.example", link_ends_at=_ten_days_on())
    assert token is not None

    # Not vacuous: before the swap the link opens and is counted.
    assert await share_service.scan_for_share_token(session, token) is not None
    assert await _shown(client) == 1

    monkeypatch.setattr(share_service, "link_would_open", lambda now: false())

    assert await share_service.scan_for_share_token(session, token) is None
    assert await _shown(client) == 0


async def test_a_link_counts_only_while_it_would_open(client: AsyncClient, engine) -> None:
    """A link's life through the real endpoints: made, ended, made again, failed, withdrawn.

    The row keeps its token through the end and the failure, which is what a column
    that only says "a token is set" would go on counting.
    """
    agency_id = await _agency(client)
    scan_id, _ = await _scan(engine, agency_id, "lifecycle.example")
    assert await _shown(client) == 0

    made = await client.post(f"{BASE}/scans/{scan_id}/share")
    assert made.status_code == 200, made.text
    first = made.json()["token"]
    assert await _shown(client) == 1

    # The link reaches its end. The token is still on the row, and the link opens nothing.
    await _change(engine, scan_id, share_expires_at=_yesterday())
    assert await _token_on_row(engine, scan_id) == first
    assert await _shown(client) == 0

    # A press after the end makes a NEW link, and that one counts.
    again = await client.post(f"{BASE}/scans/{scan_id}/share")
    assert again.status_code == 200, again.text
    second = again.json()["token"]
    assert second != first
    assert await _shown(client) == 1

    # The scan fails. Its link stays on the row for the agency to withdraw, and opens nothing.
    await _change(engine, scan_id, status=ScanStatus.FAILED)
    assert await _token_on_row(engine, scan_id) == second
    assert await _shown(client) == 0

    # Withdrawing it changes nothing here: it was already not a link to anyone.
    assert (await client.delete(f"{BASE}/scans/{scan_id}/share")).status_code == 204
    assert await _token_on_row(engine, scan_id) is None
    assert await _shown(client) == 0
