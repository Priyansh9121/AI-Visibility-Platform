"""`GET`/`PUT /clients/{clientId}/auto-rescan` — F2, 2026-10-06 (`AUTO_RESCAN_ENABLED`).

Off, the DEFAULT, the switch does not exist: the GET says it is not available and the PUT is a 404
with an unknown path's body. On, an owner or admin of the client's agency may switch it; a member
may not and is told nothing; another agency's client is a 404.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api.models import Client, User, UserRole
from avp_api.services import auto_rescan

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def rescan_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "auto_rescan_enabled", True)


async def _sign_up(client: AsyncClient, email: str = "rescan@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Rescan Agency", "fullName": "Op", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _url(cid: str) -> str:
    return f"{BASE}/clients/{cid}/auto-rescan"


async def _row(engine, cid: str) -> Client:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(Client).where(Client.id == cid))).scalar_one()


async def _set_role(engine, role: UserRole) -> None:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        for user in (await s.execute(select(User))).scalars():
            user.role = role
        await s.commit()


class TestOff:
    async def test_the_get_says_it_is_not_available_and_nothing_else(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.get(_url(cid))
        assert resp.status_code == 200
        assert resp.json() == {
            "available": False, "enabled": False, "skipReason": None,
            "skippedAt": None, "retriesAt": None,
        }

    async def test_the_put_is_a_404_with_an_unknown_paths_body_and_changes_nothing(
        self, client: AsyncClient, engine  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.put(_url(cid), json={"enabled": True})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Not Found"
        assert (await _row(engine, cid)).auto_rescan is False


class TestOn:
    async def test_an_owner_switches_it_on_and_off(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        first = (await client.get(_url(cid))).json()
        assert first["available"] is True and first["enabled"] is False
        on = await client.put(_url(cid), json={"enabled": True})
        assert on.status_code == 200 and on.json()["enabled"] is True
        row = await _row(engine, cid)
        assert row.auto_rescan is True and row.auto_rescan_by_user_id is not None
        off = await client.put(_url(cid), json={"enabled": False})
        assert off.json()["enabled"] is False
        row = await _row(engine, cid)
        assert row.auto_rescan is False and row.auto_rescan_by_user_id is None

    async def test_an_admin_may_too(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _set_role(engine, UserRole.ADMIN)
        assert (await client.put(_url(cid), json={"enabled": True})).status_code == 200

    async def test_a_member_is_told_nothing_and_may_change_nothing(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await client.put(_url(cid), json={"enabled": True})).status_code == 200
        await _set_role(engine, UserRole.MEMBER)
        seen = (await client.get(_url(cid))).json()
        assert seen == {
            "available": False, "enabled": False, "skipReason": None,
            "skippedAt": None, "retriesAt": None,
        }
        refused = await client.put(_url(cid), json={"enabled": False})
        assert refused.status_code == 403
        assert (await _row(engine, cid)).auto_rescan is True

    async def test_another_agencys_client_is_a_404_for_both(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        other = AsyncClient(
            transport=client._transport, base_url="http://testserver",  # noqa: SLF001
            headers={"Origin": client.headers["Origin"]},
        )
        async with other:
            await _sign_up(other, "second@test.example")
            assert (await other.get(_url(cid))).status_code == 404
            assert (await other.put(_url(cid), json={"enabled": True})).status_code == 404
        assert (await _row(engine, cid)).auto_rescan is False

    async def test_pressing_on_again_as_the_same_user_changes_nothing_and_a_new_user_resets(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await client.put(_url(cid), json={"enabled": True})
        stamp = datetime(2026, 10, 1, tzinfo=UTC)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            row = (await s.execute(select(Client).where(Client.id == cid))).scalar_one()
            row.auto_rescan_attempted_at = stamp
            row.auto_rescan_skip_reason = auto_rescan.SKIP_ALLOWANCE
            await s.commit()
        same = (await client.put(_url(cid), json={"enabled": True})).json()
        assert same["skipReason"] == "ALLOWANCE_USED"
        # Handing it to another user resets the notice and the claim.
        async with factory() as s:
            row = (await s.execute(select(Client).where(Client.id == cid))).scalar_one()
            row.auto_rescan_by_user_id = None
            await s.commit()
        reset = (await client.put(_url(cid), json={"enabled": True})).json()
        assert reset["skipReason"] is None and reset["skippedAt"] is None
        assert (await _row(engine, cid)).auto_rescan_attempted_at is None

    async def test_a_skip_is_shown_with_when_and_the_first_of_next_month(
        self, client: AsyncClient, engine, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await client.put(_url(cid), json={"enabled": True})
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            row = (await s.execute(select(Client).where(Client.id == cid))).scalar_one()
            row.auto_rescan_attempted_at = datetime(2026, 12, 20, 9, tzinfo=UTC)
            row.auto_rescan_skip_reason = auto_rescan.SKIP_NOT_PAID
            await s.commit()
        body = (await client.get(_url(cid))).json()
        assert body["skipReason"] == "AGENCY_NOT_PAID"
        assert body["skippedAt"].startswith("2026-12-20T09:00:00")
        assert body["retriesAt"].startswith("2027-01-01T00:00:00")

    @pytest.mark.parametrize("body", [{"enabled": "true"}, {"enabled": 1}, {"enabled": None}])
    async def test_enabled_must_be_a_real_boolean(
        self, client: AsyncClient, rescan_on, body: dict  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await client.put(_url(cid), json=body)).status_code == 422

    async def test_an_extra_field_is_refused(self, client: AsyncClient, rescan_on) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.put(_url(cid), json={"enabled": True, "by": "someone"})
        assert resp.status_code == 422

    async def test_the_body_must_say_enabled(self, client: AsyncClient, rescan_on) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await client.put(_url(cid), json={})).status_code == 422

    async def test_the_clients_own_response_is_unchanged(
        self, client: AsyncClient, rescan_on  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        body = (await client.get(f"{BASE}/clients/{cid}")).json()
        assert not [k for k in body if "escan" in k]
