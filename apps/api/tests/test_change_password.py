"""Authenticated password change — Epic 9.14.

The endpoint had no contract anywhere before this epic: not built, and not in
api-contracts.md's "planned" table either. It is recorded there now, in the same
format as the other Auth entries, as part of this work.

The two questions worth testing are not "does it change the password".

**Whose sessions die.** The reset flow revokes everything and signs the caller
out. This one revokes everything EXCEPT the caller, who keeps working. Those are
different answers to different threat models and both are asserted here, because
the failure mode of getting it wrong is silent: a change that leaves an
attacker's session alive looks exactly like one that does not.

**What a refusal says.** A wrong current password gets one answer with nothing
in it beyond "wrong". There is no "no such user" branch to leak — the caller is
authenticated — and the timing question is analysed in
`services/auth.change_password` rather than answered with reflexive theatre.
"""

from __future__ import annotations

import time

from httpx import ASGITransport, AsyncClient

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


async def _sign_up(client: AsyncClient, email: str = "changer@test.example") -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Change Password Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _change(client: AsyncClient, current: str, new: str):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": current, "newPassword": new},
    )


async def _live_sessions(user_id: str) -> int:
    """Session records Redis still holds for a user, read directly.

    Not through `SessionStore`. Epic 9.13's review flagged that a revocation
    test written through the store's own API can pass whether or not the
    revocation happened.
    """
    from avp_api.redis_client import get_redis

    redis = get_redis()
    digests = await redis.smembers(f"user-sessions:{user_id}")
    # A set comprehension, not `sum(...)` over a generator: `await` inside a
    # genexp makes it an ASYNC generator, which `sum` cannot consume.
    live = {d for d in digests if await redis.exists(f"sess:{d}")}
    return len(live)


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


async def test_the_password_actually_changes(client: AsyncClient) -> None:
    await _sign_up(client)

    resp = await _change(client, PASSWORD, NEW_PASSWORD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == "changer@test.example"

    await client.post(f"{BASE}/auth/logout")

    old = await client.post(
        f"{BASE}/auth/login", json={"email": "changer@test.example", "password": PASSWORD}
    )
    assert old.status_code == 401

    new = await client.post(
        f"{BASE}/auth/login",
        json={"email": "changer@test.example", "password": NEW_PASSWORD},
    )
    assert new.status_code == 200, new.text


async def test_the_caller_stays_signed_in_on_a_fresh_session(
    client: AsyncClient,
) -> None:
    """The stated decision, half one: this session survives.

    Signing the caller out of the session they are actively using would be
    friction with no security value — they just proved they hold the password.
    The cookie is REPLACED rather than spared, because "revoke all except this
    digest" is a special case in the revocation path.
    """
    await _sign_up(client)
    before = client.cookies.get("avp_session")

    resp = await _change(client, PASSWORD, NEW_PASSWORD)
    assert resp.status_code == 200
    after = resp.cookies.get("avp_session")

    assert after is not None
    assert after != before, "the session token should be re-minted, not reused"

    # Still working, with no second sign-in.
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200


async def test_every_other_session_is_revoked(client: AsyncClient) -> None:
    """The stated decision, half two: other devices are signed out.

    The commonest reason to change a password while signed in is believing
    somebody else has it. A change that left every other device alive would
    fail at the one job the user thought they were doing.
    """
    me = await _sign_up(client)
    user_id = me["user"]["id"]

    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        signed_in = await other.post(
            f"{BASE}/auth/login",
            json={"email": "changer@test.example", "password": PASSWORD},
        )
        assert signed_in.status_code == 200
        assert await _live_sessions(user_id) == 2

        assert (await _change(client, PASSWORD, NEW_PASSWORD)).status_code == 200

        # The other device is out.
        assert (await other.get(f"{BASE}/auth/me")).status_code == 401
        # Exactly one session survives: the caller's freshly minted one. Read
        # from Redis, so removing the revoke call fails this rather than
        # passing on `current_principal`'s fallback.
        assert await _live_sessions(user_id) == 1


async def test_it_is_not_the_reset_flow_s_answer(client: AsyncClient) -> None:
    """The two endpoints deliberately differ, and the difference is asserted.

    `reset-password/confirm` clears the cookie and leaves the caller signed
    out. This one hands back a working session. If someone later "unifies" the
    two, this test is what says the divergence was a decision.
    """
    await _sign_up(client)
    resp = await _change(client, PASSWORD, NEW_PASSWORD)

    assert resp.status_code == 200
    # A cleared cookie is a Set-Cookie with an empty value or Max-Age=0.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "avp_session=" in set_cookie
    assert "Max-Age=0" not in set_cookie
    assert 'avp_session=""' not in set_cookie


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


async def test_a_wrong_current_password_is_refused_and_changes_nothing(
    client: AsyncClient,
) -> None:
    await _sign_up(client)

    resp = await _change(client, "not-the-current-password", NEW_PASSWORD)
    assert resp.status_code == 401
    problem = resp.json()
    assert problem["type"] == "/problems/invalid-credentials"
    assert problem["detail"] == "That is not your current password."

    # Nothing moved: the original password still works.
    await client.post(f"{BASE}/auth/logout")
    assert (
        await client.post(
            f"{BASE}/auth/login",
            json={"email": "changer@test.example", "password": PASSWORD},
        )
    ).status_code == 200


async def test_a_failed_change_revokes_nothing(client: AsyncClient) -> None:
    """A wrong guess must not sign anybody out.

    Revoking on failure would turn a mistyped password into a denial of service
    against the account's other devices — and hand one to anybody holding a
    single stolen cookie.
    """
    me = await _sign_up(client)
    user_id = me["user"]["id"]

    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        await other.post(
            f"{BASE}/auth/login",
            json={"email": "changer@test.example", "password": PASSWORD},
        )
        assert await _live_sessions(user_id) == 2

        assert (await _change(client, "wrong", NEW_PASSWORD)).status_code == 401

        assert await _live_sessions(user_id) == 2
        assert (await other.get(f"{BASE}/auth/me")).status_code == 200


async def test_the_refusal_says_nothing_beyond_wrong(client: AsyncClient) -> None:
    """One answer, whatever the wrong password was.

    There is no account-existence question here — the caller is authenticated —
    so what is guarded is narrower: the refusal must not vary with the SHAPE of
    the guess, or it becomes a free oracle for probing what a password looks
    like against a session somebody already holds.
    """
    await _sign_up(client)

    # NOT `PASSWORD + " "`. `ApiModel` sets `str_strip_whitespace=True`, so a
    # trailing space is stripped before the field is validated and that guess
    # legitimately succeeds. Pre-existing and consistent across sign-up, login
    # and reset, so nobody can be locked out by it — but worth knowing that
    # passwords in this system are whitespace-trimmed on every path.
    bodies = set()
    for guess in ("", "x", "wrong", PASSWORD.upper(), "a" * 300):
        resp = await _change(client, guess, NEW_PASSWORD)
        assert resp.status_code in (401, 422)
        if resp.status_code == 401:
            bodies.add(resp.text)

    # Every rejected guess of a plausible shape gets a byte-identical body.
    assert len(bodies) == 1, bodies


async def test_a_wrong_current_password_costs_a_real_argon2_verification(
    client: AsyncClient, settings
) -> None:  # noqa: ANN001
    """The timing question item 17 raises, answered by measurement.

    A wrong current password DOES return faster than a right one, because
    success goes on to hash the new password and write it. That is not a leak:
    it tells an observer only that a guess made against a session they already
    control was wrong, which the 401 says outright — and there is no second
    account for the timing to distinguish it from.

    What WOULD be a defect is short-circuiting before the hash, which is how a
    "wrong password" and a "malformed password" would separate. So the property
    asserted is that a wrong guess still pays for a full verification: its cost
    is at least a bare Argon2 verify at the configured parameters, not a
    constant-time string comparison that returns in microseconds.
    """
    from avp_api.security import hash_password, verify_password

    await _sign_up(client)

    reference = hash_password(PASSWORD, settings)
    started = time.perf_counter()
    verify_password("a-wrong-password", reference, settings)
    argon2_cost = time.perf_counter() - started

    started = time.perf_counter()
    assert (await _change(client, "a-wrong-password", NEW_PASSWORD)).status_code == 401
    refusal_cost = time.perf_counter() - started

    assert refusal_cost >= argon2_cost * 0.5, (
        f"refusal took {refusal_cost:.4f}s against an Argon2 verify of "
        f"{argon2_cost:.4f}s — it looks like it short-circuited"
    )


async def test_the_new_password_obeys_the_sign_up_rules(client: AsyncClient) -> None:
    """The same `Password` annotation, imported rather than restated."""
    await _sign_up(client)

    short = await _change(client, PASSWORD, "short")
    assert short.status_code == 422
    assert short.json()["type"] == "/problems/validation-failed"

    repetitive = await _change(client, PASSWORD, "aaaaaaaaaaaaaaaa")
    assert repetitive.status_code == 422
    assert "repetitive" in repetitive.text

    fields = {e["field"] for e in repetitive.json()["errors"]}
    assert "newPassword" in fields


async def test_reusing_the_current_password_is_refused_on_the_field(
    client: AsyncClient,
) -> None:
    """Accepting it silently would revoke every other session for a change that
    did not happen, and the person would have no way to tell."""
    await _sign_up(client)

    resp = await _change(client, PASSWORD, PASSWORD)
    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-failed"
    error = resp.json()["errors"][0]
    assert error["field"] == "newPassword"
    assert error["message"] == "That is already your password. Choose a different one."


async def test_it_requires_a_session(client: AsyncClient) -> None:
    await _sign_up(client)
    await client.post(f"{BASE}/auth/logout")

    resp = await _change(client, PASSWORD, NEW_PASSWORD)
    assert resp.status_code == 401
    assert resp.json()["type"] == "/problems/authentication-required"


async def test_no_password_or_hash_is_echoed_back(client: AsyncClient) -> None:
    """Neither secret, and no hash either.

    Deliberately not a sweep for the word "password" — this suite's agency is
    called "Change Password Agency", so that assertion would fail on a slug and
    prove nothing. What must not appear are the two credentials themselves and
    the stored Argon2 digest.
    """
    await _sign_up(client)
    resp = await _change(client, PASSWORD, NEW_PASSWORD)
    body = resp.text
    assert PASSWORD not in body
    assert NEW_PASSWORD not in body
    assert "$argon2" not in body
    assert "passwordHash" not in body and "password_hash" not in body


async def test_an_invited_seat_cannot_change_a_password_it_has_not_set(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """An INVITED user has a null `password_hash`.

    `verify_password` cannot be called with None, so the guard has to be
    explicit — and its answer must be the ordinary refusal rather than a crash
    or a special case that says "you have no password", which would be a fact
    about the account leaking through an error.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from avp_api.errors import InvalidCredentials
    from avp_api.models import User
    from avp_api.services import auth as auth_service

    me = await _sign_up(client)
    await client.post(
        f"{BASE}/agencies/{me['agency']['id']}/invitations",
        json={"email": "pending@test.example", "role": "member"},
    )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        invited = (
            await s.execute(select(User).where(User.email == "pending@test.example"))
        ).scalar_one()
        assert invited.password_hash is None

        try:
            await auth_service.change_password(
                s, user=invited, current_password="anything", new_password=NEW_PASSWORD
            )
        except InvalidCredentials as exc:
            assert str(exc) == "That is not your current password."
        else:  # pragma: no cover - the guard is the point of the test
            raise AssertionError("a passwordless user must not be able to change one")
