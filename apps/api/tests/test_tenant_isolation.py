"""Cross-tenant isolation.

Every read is scoped to the caller's agency. These tests build two agencies
with data in both and assert neither can see the other's — the failure mode
that matters most in multi-tenant software handling client data.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api.models import Client, Scan

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, agency: str, email: str) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": agency,
            "fullName": "Operator",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_client_and_scan(engine, agency_id: str, domain: str) -> tuple[str, str]:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        c = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=domain, domain=domain
        )
        s.add(c)
        await s.flush()
        scan = Scan(id=ids.new_id(ids.SCAN), client_id=c.id, agency_id=agency_id)
        s.add(scan)
        await s.commit()
        return c.id, scan.id


async def test_dashboard_shows_only_the_callers_agency(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    first = await _sign_up(client, "Agency One", "one@isolation.example")
    await _seed_client_and_scan(engine, first["agency"]["id"], "one.example")
    await client.post(f"{BASE}/auth/logout")

    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        second = await _sign_up(other, "Agency Two", "two@isolation.example")
        await _seed_client_and_scan(engine, second["agency"]["id"], "two.example")

        dash = (await other.get(f"{BASE}/dashboard")).json()
        assert dash["clientCount"] == 1
        assert dash["scanCount"] == 1
        assert dash["agency"]["id"] == second["agency"]["id"]
        domains = {s["clientDomain"] for s in dash["recentScans"]}
        assert domains == {"two.example"}
        assert "one.example" not in domains


async def test_two_agencies_may_prospect_the_same_domain(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """Uniqueness is (agency_id, domain), not domain alone.

    Two agencies pitching the same prospect is normal and must not collide.
    """
    first = await _sign_up(client, "Agency One", "one@shared.example")
    await _seed_client_and_scan(engine, first["agency"]["id"], "contested.example")
    await client.post(f"{BASE}/auth/logout")

    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        second = await _sign_up(other, "Agency Two", "two@shared.example")
        # Must not raise — this is the point of the composite unique key.
        await _seed_client_and_scan(engine, second["agency"]["id"], "contested.example")
        dash = (await other.get(f"{BASE}/dashboard")).json()
        assert dash["clientCount"] == 1


async def test_session_of_a_deleted_agency_stops_working(
    client: AsyncClient, engine, session: AsyncSession
) -> None:  # noqa: ANN001
    """Authorisation is re-checked against the database on every request."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from avp_api.models import Agency

    body = await _sign_up(client, "Doomed Agency", "doomed@isolation.example")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == body["agency"]["id"]))
        ).scalar_one()
        agency.deleted_at = datetime.now(UTC)
        await s.commit()

    # No new login, no cookie change — the very next request is rejected.
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_suspended_user_loses_access_immediately(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The revocation property that makes seat-based billing enforceable."""
    from sqlalchemy import select

    from avp_api.models import User, UserStatus

    body = await _sign_up(client, "Suspend Test", "suspend@isolation.example")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.id == body["user"]["id"]))
        ).scalar_one()
        user.status = UserStatus.SUSPENDED
        await s.commit()

    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
