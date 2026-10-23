"""Two callbacks racing on one state — A6 part 1, the last of the state's checks.

A `state` is single use: the callback takes it (reads and deletes it) before it does anything else.
`test_google_sign_in.py` proves that a SECOND callback, after the first, finds nothing. That is not
the same claim as "two callbacks at once cannot both take it", which is the one that matters to a
person who presses the link in a mail twice, or to anyone replaying a captured callback inside the
window: if the read and the delete were two commands, both could read before either deleted, and
two sign-ins, two code exchanges and two sessions would come of one state. `GoogleFlowStore._take`
does the read and the delete in one MULTI/EXEC.

Running two requests with `asyncio.gather` proves little: the two can simply run one after the
other, and a split `GET` then `DELETE` would pass. `_MeetOnTheWire` makes them meet. The command
each callback sends to take the state (the one that names its `google-state:` key, and nothing
else) is held, after it has been sent and before its answer is read, until both are on the wire. A
`_take` that read and deleted in two commands would then give both callbacks the record; the one
transaction gives it to one. (`tests/test_session_cap.py` has the same idea for
`SessionStore.create`; that one hooks the plain commands, and `_take` is a transaction, so the hook
here is one level down, at the connection.)
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from redis.asyncio.connection import AbstractConnection

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.services.google_oauth import STATE_PREFIX
from avp_api.sessions import USER_SESSIONS_PREFIX
from test_google_sign_in import (  # noqa: F401 - `google` is a fixture, used by name
    BASE,
    FakeGoogleProvider,
    callback,
    complete,
    google,
    identity,
    new_address_ticket,
    start_with_cookie,
)


class _MeetOnTheWire:
    """Make `parties` concurrent calls meet at Redis, and say whether they did.

    Wraps `AbstractConnection.send_packed_command`, the one place a plain command AND a
    transaction go through. Only a command whose packed bytes carry the key prefix (the state's,
    `google-state:`, unless a test names another: `tests/test_google_invitation_choice.py` makes
    two answers to one ticket meet at `google-ticket:`) is ever held; everything else goes straight
    through, the handshake a pooled connection makes when it is opened (`CLIENT SETINFO`,
    `SELECT`), a health-check PING, and every command on any other key. Of those, the first
    `parties` are each held, after the bytes are
    written and before the answer is read, at an `asyncio.Barrier` for all of them. So what meets
    is the callbacks' takes of the state, and not whatever a connection happened to send first.
    Each wait is bounded: a call that never arrives fails the test, it does not hang it.

    `released_with` records, for each held call, how many had been sent when it was let go: the
    number of parties for every one of them when they met. A test asserts it, so a hold that did
    not happen is a failure and not a quiet pass.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, parties: int, prefix: str = STATE_PREFIX
    ) -> None:
        self.parties = parties
        self.sent = 0
        self.released_with: list[int] = []
        self._barrier = asyncio.Barrier(parties)
        send = AbstractConnection.send_packed_command

        async def hold_after_sending(
            connection: Any, command: Any, check_health: bool = True
        ) -> None:
            # redis-py hands over bytes, a str, or a list of byte chunks; a list is looked through
            # once, here, so that what is looked at is exactly what is sent.
            if isinstance(command, str | bytes):
                packed, raw = command, command.encode() if isinstance(command, str) else command
            else:
                packed = list(command)
                raw = b"".join(bytes(chunk) for chunk in packed)
            await send(connection, packed, check_health=check_health)
            if prefix.encode() in raw and self.sent < self.parties:
                self.sent += 1
                await asyncio.wait_for(self._barrier.wait(), timeout=10)
                self.released_with.append(self.sent)

        monkeypatch.setattr(AbstractConnection, "send_packed_command", hold_after_sending)


async def test_two_callbacks_racing_on_one_state_make_one_exchange_and_one_session(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An account that signs in through Google, signed out again: the callback below is a sign-in
    # and not a sign-up, and the account holds no session before it.
    ticket, _, _ = await new_address_ticket(client, google)
    done = await complete(client, ticket)
    assert done.status_code == 201, done.text
    user_id = done.json()["user"]["id"]
    await client.post(f"{BASE}/auth/logout")
    redis = redis_module.get_redis(settings)
    index = f"{USER_SESSIONS_PREFIX}{user_id}"
    assert await redis.scard(index) == 0
    google.exchanges.clear()

    google.identities["code-2"] = identity("new-person@example.com", sub="sub-new")
    state, _ = await start_with_cookie(client)
    meeting = _MeetOnTheWire(monkeypatch, parties=2)
    first, second = await asyncio.gather(
        asyncio.wait_for(callback(client, code="code-2", state=state), timeout=30),
        asyncio.wait_for(callback(client, code="code-2", state=state), timeout=30),
    )

    assert meeting.released_with == [2, 2]  # both were sent before either was answered
    locations = [r.headers["location"] for r in (first, second)]
    landed_on = {urlparse(r.headers["location"]).path: r for r in (first, second)}
    assert sorted(landed_on) == ["/", "/dashboard"], locations  # one in, one refused
    refused = parse_qs(urlparse(landed_on["/"].headers["location"]).query)
    assert refused == {"google": ["error"], "reason": ["invalid-state"]}
    assert len(google.exchanges) == 1
    assert await redis.scard(index) == 1
