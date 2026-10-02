"""The share row lock must read the row fresh — found in the 3.24 security review.

`select(...).with_for_update()` returns the identity-mapped `Scan` already in the
session without refreshing it, so a `share_token` written by another transaction
after the scan was first loaded is invisible, and two simultaneous mints can each
create a token. `populate_existing` makes the locked read overwrite the stale copy.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.models import Client, Scan, ScanStatus
from avp_api.services import share as share_service

BASE = "/api/v1"


async def _agency(client: AsyncClient) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Lock Agency",
            "fullName": "Op",
            "email": "lockrefresh@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _scan(engine, agency_id: str) -> str:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        c = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name="Lock", domain="lock.example"
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
        await s.commit()
        return scan.id


async def _other_transaction_sets_token(engine, sid: str, token: str | None) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as other:
        await other.execute(
            sa_update(Scan)
            .where(Scan.id == sid)
            .values(
                share_token=token,
                share_expires_at=None if token is None else datetime.now(UTC) + timedelta(days=5),
            )
        )
        await other.commit()


async def test_a_mint_returns_the_token_another_transaction_just_created(
    client: AsyncClient, engine, session
) -> None:  # noqa: ANN001
    sid = await _scan(engine, await _agency(client))
    scan = await session.get(Scan, sid)  # loaded before the other write: share_token is None
    assert scan.share_token is None
    await _other_transaction_sets_token(engine, sid, "OTHERTOKEN" + "x" * 33)

    token, _expires = await share_service.get_or_create_share_token(session, scan)

    assert token == "OTHERTOKEN" + "x" * 33


async def test_a_revoke_sees_a_token_another_transaction_just_created(
    client: AsyncClient, engine, session
) -> None:  # noqa: ANN001
    sid = await _scan(engine, await _agency(client))
    scan = await session.get(Scan, sid)
    assert scan.share_token is None
    await _other_transaction_sets_token(engine, sid, "OTHERTOKEN" + "y" * 33)

    assert await share_service.revoke_share_token(session, scan) is True
    await session.commit()

    await session.refresh(scan)
    assert scan.share_token is None
