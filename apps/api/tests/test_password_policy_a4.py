"""The password rule at its four doors, what it keeps, and the hash (hardening A4, D10).

D10 asks for a minimum of 12 characters for new and changed passwords, a maximum of at least 128
and every character allowed; no forced rotation; and the hashing algorithm and its parameters
reported, with a rehash on the next successful sign-in when they are below current. This file holds
the parts of that which are about the password itself. The breached-password lookup has its own,
`test_breached_passwords_a4.py`.

1. EVERY DOOR APPLIES THE SAME RULE. A password is SET in four places: sign-up, a reset, a password
   change and an invitation accept. Each is driven here through its real route with the same
   passwords: 11 characters are refused and 12 accepted, 128 and 256 accepted and 257 refused, a
   string of fewer than 5 distinct characters refused (NIST SP 800-63B allows refusing repetitive
   strings), and spaces, accents, other scripts, emoji and symbols all accepted, after which the
   same password signs in and a near miss does not.
2. WHAT THE RULE KEEPS. Leading and trailing whitespace is dropped from every password field by the
   request model (`str_strip_whitespace`), where it is set and where it is presented; Unicode is not
   normalised; sign-in and the change's current password take any length from 1 to 256, so a
   password set under an older rule still signs in and meets the rule at its next change. These are
   pinned, so a change to any of them is a decision and not an accident.
3. THE FOUR DOORS ARE THE ONLY PLACES A PASSWORD IS SET. A sweep of the API's source AND of its
   operator scripts lists every function that hashes a password and every one that runs the
   breached-password check, so a fifth place cannot arrive without applying the rule, and sign-in
   cannot start to call the check; nothing outside `security.py` builds a hasher of its own.
4. THE HASH. Argon2id at the minimum configuration the OWASP Password Storage Cheat Sheet names
   (19 MiB, 2 passes, 1 lane), and a successful sign-in rewrites a hash that is WEAKER than the
   configured parameters: a failed one, or a refused one, does not, and a hash that is stronger is
   never rewritten downward (the fix round, security review L2).
"""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.low_level import hash_secret
from httpx import AsyncClient, Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import avp_api
from avp_api import ids
from avp_api.config import Settings
from avp_api.models import Invitation, PasswordResetToken, User, UserStatus
from avp_api.schemas.auth import (
    AcceptInvitationRequest,
    ChangePasswordRequest,
    LoginRequest,
    Password,
    ResetPasswordConfirm,
    SignUpRequest,
)
from avp_api.security import hash_password, needs_rehash, token_digest, verify_password
from avp_api.services import auth as auth_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
EMAIL = "door@a4.example"
OWNER = "owner@a4.example"

Attempt = Callable[[str], Awaitable[Response]]


# --- the four doors, each driven through its real route -------------------------------------


def _factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _owner(client: AsyncClient, email: str = EMAIL) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "A4 Agency", "fullName": "Owner", "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _door_sign_up(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/sign-up",
            json={
                "agencyName": "A4 Agency",
                "fullName": "Door Person",
                "email": EMAIL,
                "password": password,
            },
        )

    return attempt, EMAIL


async def _door_change(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    await _owner(client)

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": password},
        )

    return attempt, EMAIL


async def _door_reset(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    await _owner(client)
    raw = "a4-reset-link-" + "r" * 24
    async with _factory(engine)() as s:
        user = (await s.execute(select(User).where(User.email == EMAIL))).scalar_one()
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user.id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": password}
        )

    return attempt, EMAIL


async def _door_invitation(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    agency_id = (await _owner(client, OWNER))["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": EMAIL, "role": "member"}
    )
    assert invited.status_code == 201, invited.text
    raw = "a4-invitation-" + "i" * 24
    async with _factory(engine)() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == EMAIL,
                    Invitation.accepted_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(raw)
        await s.commit()

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": raw, "fullName": "Invited Person", "password": password},
        )

    return attempt, EMAIL


# door -> (how to set it up, the field a refusal names, the status of a success)
DOORS: dict[str, tuple[Callable[..., Awaitable[tuple[Attempt, str]]], str, int]] = {
    "sign-up": (_door_sign_up, "password", 201),
    "change": (_door_change, "newPassword", 200),
    "reset": (_door_reset, "newPassword", 200),
    "invitation": (_door_invitation, "password", 200),
}
DOOR_NAMES = list(DOORS)


async def _sign_in(client: AsyncClient, email: str, password: str) -> int:
    """Sign out whatever this browser holds, then sign in; the status of the sign-in."""
    await client.post(f"{BASE}/auth/logout")
    resp = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    return resp.status_code


def _near_miss(password: str) -> str:
    return password[:-1] + ("Z" if password[-1] != "Z" else "Y")


def _only_error(resp: Response) -> dict[str, Any]:
    doc = resp.json()
    assert doc["type"] == "/problems/validation-failed", doc
    assert len(doc["errors"]) == 1, doc
    return doc["errors"][0]  # type: ignore[no-any-return]


async def _stored_hash(engine: AsyncEngine, email: str) -> str | None:
    async with _factory(engine)() as s:
        return (await s.execute(select(User.password_hash).where(User.email == email))).scalar_one()


# Passwords the rule must accept at every door, each with what is unusual about it. None of them has
# whitespace at an end, because the request model drops it (section 2).
ACCEPTED = {
    "twelve ASCII characters": "a1b2c3d4e5f6",
    "128 characters": ("abcdefghij" * 13)[:128],
    "256 characters": ("abcdefghij" * 26)[:256],
    "spaces inside": "correct  horse   battery staple",
    "accents and other scripts": "pässwörd-日本語-пароль-Ω",
    "twelve emoji": "".join(chr(0x1F300 + i) for i in range(12)),
    "256 emoji": "".join(chr(0x1F300 + (i % 200)) for i in range(256)),
    "only symbols": "!@#$%^&*()_+-=[]{};':\",./<>?\\|`~",
    "a tab and a newline inside": "tab\there and\nnewline inside",
}


# --- 1. every door applies the same rule ---------------------------------------------------------


@pytest.mark.parametrize("door", DOOR_NAMES)
class TestEveryDoorAppliesTheSameRule:
    async def test_eleven_characters_are_refused_and_twelve_are_accepted(
        self, door: str, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        setup, field, success = DOORS[door]
        attempt, email = await setup(client, engine)

        short = await attempt("a1b2c3d4e5f")
        assert short.status_code == 422, short.text
        error = _only_error(short)
        assert (error["field"], error["type"]) == (field, "string_too_short")

        twelve = "a1b2c3d4e5f6"
        assert (await attempt(twelve)).status_code == success
        assert await _sign_in(client, email, twelve) == 200

    async def test_the_maximum_is_256_and_one_more_is_refused(
        self, door: str, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        setup, field, success = DOORS[door]
        attempt, email = await setup(client, engine)

        long = await attempt(("abcdefghij" * 26)[:257])
        assert long.status_code == 422, long.text
        error = _only_error(long)
        assert (error["field"], error["type"]) == (field, "string_too_long")

        assert (await attempt(("abcdefghij" * 26)[:256])).status_code == success

    async def test_characters_are_counted_and_not_bytes(
        self, door: str, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """Eleven emoji are 44 bytes and still eleven characters; twelve are enough."""
        setup, field, success = DOORS[door]
        attempt, _email = await setup(client, engine)

        eleven = "".join(chr(0x1F300 + i) for i in range(11))
        assert len(eleven) == 11 and len(eleven.encode()) > 12
        refused = await attempt(eleven)
        assert refused.status_code == 422, refused.text
        assert _only_error(refused)["field"] == field

        assert (await attempt("".join(chr(0x1F300 + i) for i in range(12)))).status_code == success

    async def test_fewer_than_five_distinct_characters_is_refused_and_five_is_not(
        self, door: str, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """NIST SP 800-63B allows refusing repetitive strings, and D10 keeps the rule."""
        setup, field, success = DOORS[door]
        attempt, email = await setup(client, engine)

        for repetitive in ("aaaaaaaaaaaa", "abababababab", "abcdabcdabcd"):
            resp = await attempt(repetitive)
            assert resp.status_code == 422, (repetitive, resp.text)
            error = _only_error(resp)
            assert (error["field"], error["type"]) == (field, "value_error")
            assert "repetitive" in error["message"]

        five = "abcdeabcdeab"
        assert len(set(five)) == 5
        assert (await attempt(five)).status_code == success

    @pytest.mark.parametrize("label", list(ACCEPTED))
    async def test_what_is_allowed_is_accepted_and_the_same_password_signs_in(
        self, door: str, label: str, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        password = ACCEPTED[label]
        setup, _field, success = DOORS[door]
        attempt, email = await setup(client, engine)

        resp = await attempt(password)
        assert resp.status_code == success, (label, resp.text)
        # The password that was set is the whole of what was sent: it signs in as it is...
        assert await _sign_in(client, email, password) == 200, label
        # ...and not on its first characters, or any other part of it.
        assert await _sign_in(client, email, _near_miss(password)) == 401, label


# --- 2. what the rule keeps ----------------------------------------------------------------------


class TestWhatTheRuleKeeps:
    async def test_whitespace_at_the_two_ends_is_dropped_where_a_password_is_set_and_presented(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """Pinned, not endorsed. `ApiModel` sets `str_strip_whitespace`, so a password's leading
        and trailing whitespace (spaces, tabs, newlines, no-break and ideographic spaces) never
        reaches the hash, on any path and at sign-in alike: a password set with it signs in with
        or without it, and nobody is locked out by the trim. A space INSIDE is part of the
        password. D10 says every character is allowed; this is the one place it is not, and
        stopping the trim would lock out anybody whose stored password was trimmed when they
        set it. The Epic 9.14 notes in `build-log.md` recorded the trim as a surprise; this
        holds it still until somebody decides."""
        setup, _field, success = DOORS["sign-up"]
        attempt, email = await setup(client, engine)

        assert (await attempt("  spaced out phrase \t\n")).status_code == success
        assert await _sign_in(client, email, "spaced out phrase") == 200
        assert await _sign_in(client, email, " 　spaced out phrase　") == 200
        # Inside the string every character counts.
        assert await _sign_in(client, email, "spaced  out phrase") == 401

    async def test_the_minimum_counts_what_is_left_after_the_ends_are_trimmed(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        setup, field, success = DOORS["sign-up"]
        attempt, _email = await setup(client, engine)

        eleven_and_a_space = await attempt("a1b2c3d4e5f ")
        assert eleven_and_a_space.status_code == 422, eleven_and_a_space.text
        assert _only_error(eleven_and_a_space)["type"] == "string_too_short"
        assert (await attempt(" a1b2c3d4e5f6 ")).status_code == success

    async def test_unicode_is_hashed_as_it_was_sent_and_not_normalised(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """An é as one character and as two (an e and a combining accent) are two passwords.

        Normalising now would change what every existing hash means for a person who set a
        password with such a character, so it is not done (and the NFKD in
        `services/auth.slugify` is the agency's slug, not a password)."""
        composed = "café-au-lait-chaud"
        decomposed = "café-au-lait-chaud"
        assert composed != decomposed

        setup, _field, success = DOORS["sign-up"]
        attempt, email = await setup(client, engine)
        assert (await attempt(composed)).status_code == success
        assert await _sign_in(client, email, composed) == 200
        assert await _sign_in(client, email, decomposed) == 401

    async def test_the_two_forms_are_both_accepted_as_new_passwords(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        setup, _field, success = DOORS["change"]
        attempt, email = await setup(client, engine)
        decomposed = "café-au-lait-chaud"
        assert (await attempt(decomposed)).status_code == success
        assert await _sign_in(client, email, decomposed) == 200

    def test_sign_in_and_the_current_password_take_any_length_from_one_to_256(self) -> None:
        """Presented, not set: a password set under an older rule must still be able to sign in,
        so these carry no minimum and no repetitiveness rule."""
        for password in ("x", "aaaa", "y" * 256):
            assert LoginRequest.model_validate({"email": "a@b.example", "password": password})
            assert ChangePasswordRequest.model_validate(
                {"currentPassword": password, "newPassword": "a-new-passphrase-here"}
            )
        for password in ("", "y" * 257):
            with pytest.raises(ValueError, match="password"):
                LoginRequest.model_validate({"email": "a@b.example", "password": password})

    async def test_an_account_from_before_the_rule_signs_in_and_meets_it_at_its_next_change(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        """D10: existing users are not forced to change; the rule applies on their next change."""
        me = await _owner(client, OWNER)
        async with _factory(engine)() as s:
            await auth_service.create_user_in_agency(
                s,
                agency_id=me["agency"]["id"],
                email=EMAIL,
                full_name="Older Account",
                password="short-pw",  # 8 characters: under today's rule, valid under an older one
                settings=settings,
            )
            await s.commit()

        assert await _sign_in(client, EMAIL, "short-pw") == 200
        change = {"currentPassword": "short-pw"}
        eleven = await client.post(
            f"{BASE}/auth/change-password", json={**change, "newPassword": "a1b2c3d4e5f"}
        )
        assert eleven.status_code == 422, eleven.text
        twelve = await client.post(
            f"{BASE}/auth/change-password", json={**change, "newPassword": "a1b2c3d4e5f6"}
        )
        assert twelve.status_code == 200, twelve.text


# --- 3. the four doors are the only places a password is set -------------------------------------

SRC = Path(avp_api.__file__).resolve().parent
SCRIPTS = SRC.parents[1] / "scripts"  # apps/api/scripts: what an operator runs by hand
_FUNCTIONS = ast.FunctionDef | ast.AsyncFunctionDef
# What builds a hasher of its own, which only `security.py` may do.
DIRECT_HASHERS = {"PasswordHasher", "hash_secret", "hash_secret_raw"}

# The functions that SET a password, and so apply the rule and the breached-password check.
THE_FOUR_DOORS = {
    "services/auth.py:sign_up_agency",
    "services/auth.py:change_password",
    "services/password_reset.py:confirm_reset",
    "services/invitations.py:accept_invitation",
}
# The functions that hash a password without setting one that a person chose.
HASH_WITHOUT_SETTING = {
    "services/auth.py:authenticate": "writes the password just verified again, at today's cost",
    "services/auth.py:create_user_in_agency": "no route calls it; tests make users with it",
}


def _own_nodes(function: ast.AST) -> Iterator[ast.AST]:
    """Every node `function` runs itself: its statements, not the bodies of functions it defines."""
    pending: list[ast.AST] = list(function.body)  # type: ignore[attr-defined]
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _FUNCTIONS | ast.Lambda):
            pending.extend(ast.iter_child_nodes(node))


def _sources() -> list[tuple[str, Path]]:
    """Every Python file the API runs or an operator runs by hand, as (label, path): the package by
    its path inside `src/avp_api`, and `apps/api/scripts` as `scripts/<name>`. `security.py` is left
    out, because it defines the hashing."""
    files = [
        (path.relative_to(SRC).as_posix(), path)
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).as_posix() != "security.py"
    ]
    files += [
        (f"scripts/{path.relative_to(SCRIPTS).as_posix()}", path)
        for path in sorted(SCRIPTS.rglob("*.py"))
    ]
    return files


def _calls(names: set[str]) -> dict[str, list[int]]:
    """{ "module.py:function": [line of each call to one of `names`] } across `_sources()`.

    A call made at module level, where a script's statements often are, is filed under
    `<module>`; a call in a function under the function's name."""
    found: dict[str, list[int]] = {}
    for label, path in _sources():
        for scope in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(scope, ast.Module | _FUNCTIONS):
                continue
            where = "<module>" if isinstance(scope, ast.Module) else scope.name
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Call):
                    continue
                called = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                if called in names:
                    found.setdefault(f"{label}:{where}", []).append(node.lineno)
    return found


class TestTheFourDoorsAreTheOnlyPlacesAPasswordIsSet:
    def test_the_sweep_reaches_the_package_and_the_scripts(self) -> None:
        """So that passing means something: it read the scripts, and it finds a known call."""
        labels = [label for label, _ in _sources()]
        assert len([label for label in labels if label.startswith("scripts/")]) >= 20
        assert "scripts/issue_password_reset.py" in labels
        assert "services/auth.py" in labels
        assert "security.py" not in labels
        assert "services/auth.py:authenticate" in _calls({"hash_password_async"})

    def test_no_script_sets_a_password(self) -> None:
        """A reset by hand is a link (`scripts/issue_password_reset.py`), never a password."""
        scripts = [
            where
            for where in _calls({"hash_password", "hash_password_async"} | DIRECT_HASHERS)
            if where.startswith("scripts/")
        ]
        assert scripts == []

    def test_nothing_outside_security_py_builds_a_hasher_of_its_own(self) -> None:
        """A second hasher would hash at parameters this file does not pin, and by a path the rule
        and the check do not know about."""
        assert _calls(DIRECT_HASHERS) == {}

    def test_the_functions_that_hash_a_password_are_these_and_no_others(self) -> None:
        hashers = _calls({"hash_password", "hash_password_async"})
        assert set(hashers) == THE_FOUR_DOORS | set(HASH_WITHOUT_SETTING), (
            "a function that hashes a password is new or gone: if it SETS one, it must apply the "
            "password rule and the breached-password check, and join THE_FOUR_DOORS"
        )

    def test_each_door_runs_the_breached_password_check_before_it_hashes(self) -> None:
        hashers = _calls({"hash_password_async"})
        checkers = _calls({"refuse_if_breached"})
        for door in sorted(THE_FOUR_DOORS):
            assert door in checkers, f"{door} never runs the breached-password check"
            assert min(checkers[door]) < min(hashers[door]), f"{door} hashes before it checks"

    def test_nothing_else_runs_it_and_sign_in_never_does(self) -> None:
        checkers = _calls({"refuse_if_breached"})
        assert set(checkers) == THE_FOUR_DOORS
        assert "services/auth.py:authenticate" not in checkers

    def test_every_door_takes_its_password_as_the_one_annotated_type(self) -> None:
        class Reference(BaseModel):
            password: Password

        reference = Reference.model_fields["password"].metadata
        assert reference, "the reference lost its constraints"
        for model, name in (
            (SignUpRequest, "password"),
            (ResetPasswordConfirm, "new_password"),
            (ChangePasswordRequest, "new_password"),
            (AcceptInvitationRequest, "password"),
        ):
            assert model.model_fields[name].metadata == reference, f"{model.__name__}.{name}"


# --- 4. the hash ---------------------------------------------------------------------------------


class TestTheHash:
    def test_the_shipped_parameters_are_the_owasp_minimum_for_argon2id(
        self, settings: Settings
    ) -> None:
        """Argon2id, 19 MiB of memory, 2 passes, 1 lane: the minimum configuration of the OWASP
        Password Storage Cheat Sheet. Read from the defaults in `config.py`, because the suite
        runs at the library's minimum cost, and pinned so that lowering one is seen."""
        fields = Settings.model_fields
        shipped = (
            fields["argon2_time_cost"].default,
            fields["argon2_memory_cost_kib"].default,
            fields["argon2_parallelism"].default,
        )
        assert shipped == (2, 19456, 1)

        stored = hash_password(
            "any password at all",
            settings.model_copy(
                update={
                    "argon2_time_cost": shipped[0],
                    "argon2_memory_cost_kib": shipped[1],
                    "argon2_parallelism": shipped[2],
                }
            ),
        )
        parameters = extract_parameters(stored)
        assert parameters.type is Type.ID
        assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
        assert parameters.memory_cost >= 19 * 1024
        assert parameters.time_cost >= 2
        assert parameters.parallelism >= 1

    async def test_a_successful_sign_in_rewrites_a_hash_made_at_lower_parameters(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _owner(client)
        before = await _stored_hash(engine, EMAIL)
        assert before is not None
        assert (extract_parameters(before).time_cost, extract_parameters(before).memory_cost) == (
            1,
            8,
        )

        # The cost is raised, as a deploy would raise it.
        monkeypatch.setattr(settings, "argon2_time_cost", 2)
        monkeypatch.setattr(settings, "argon2_memory_cost_kib", 16)
        assert needs_rehash(before, settings)

        # A failed sign-in does not touch the stored hash.
        assert await _sign_in(client, EMAIL, "a-wrong-password-entirely") == 401
        assert await _stored_hash(engine, EMAIL) == before

        # A successful one rewrites it at the current parameters, for the same password.
        assert await _sign_in(client, EMAIL, PASSWORD) == 200
        after = await _stored_hash(engine, EMAIL)
        assert after is not None and after != before
        rewritten = extract_parameters(after)
        assert (rewritten.type, rewritten.time_cost, rewritten.memory_cost) == (Type.ID, 2, 16)
        assert rewritten.parallelism == 1
        assert not needs_rehash(after, settings)
        assert verify_password(PASSWORD, after, settings)

        # A hash that is already current is left as it is.
        assert await _sign_in(client, EMAIL, PASSWORD) == 200
        assert await _stored_hash(engine, EMAIL) == after

    async def test_a_hash_that_is_stronger_than_the_configured_parameters_is_left_as_it_is(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
    ) -> None:
        """The security review's L2: a lowered setting must not turn stronger hashes into weaker
        ones at their owners' next sign-in. The suite's parameters are (1, 8, 1); this account
        holds a hash at (3, 32, 1), so the configured ones are the weaker."""
        await _owner(client)
        stronger = hash_password(
            PASSWORD,
            settings.model_copy(update={"argon2_time_cost": 3, "argon2_memory_cost_kib": 32}),
        )
        async with _factory(engine)() as s:
            await s.execute(update(User).where(User.email == EMAIL).values(password_hash=stronger))
            await s.commit()
        assert not needs_rehash(stronger, settings)

        assert await _sign_in(client, EMAIL, PASSWORD) == 200
        assert await _stored_hash(engine, EMAIL) == stronger, "a stronger hash was rewritten lower"

    async def test_a_sign_in_that_is_refused_rewrites_nothing(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The right password for an account that is not active is still refused, and the refusal
        happens before the rewrite: nothing is written for a sign-in that does not happen."""
        await _owner(client)
        before = await _stored_hash(engine, EMAIL)
        async with _factory(engine)() as s:
            await s.execute(
                update(User).where(User.email == EMAIL).values(status=UserStatus.SUSPENDED)
            )
            await s.commit()
        monkeypatch.setattr(settings, "argon2_time_cost", 2)
        monkeypatch.setattr(settings, "argon2_memory_cost_kib", 16)

        assert await _sign_in(client, EMAIL, PASSWORD) == 401
        assert await _stored_hash(engine, EMAIL) == before


def _stored(*, time_cost: int, memory: int, parallelism: int = 1) -> str:
    return PasswordHasher(
        time_cost=time_cost, memory_cost=memory, parallelism=parallelism
    ).hash("a stored password")


def _configured(settings: Settings, **more: int) -> Settings:
    """The suite's settings with the parameters every row below is judged against: (2, 32, 1)."""
    return settings.model_copy(
        update={
            "argon2_time_cost": 2,
            "argon2_memory_cost_kib": 32,
            "argon2_parallelism": 1,
            **more,
        }
    )


class TestWhichHashesAreRewritten:
    """`security.needs_rehash`: only a hash that is weaker than the configured parameters, or is not
    Argon2id at the library's version and lengths; never one that is stronger, and never for the
    parallelism alone. The configured parameters in every row are (2 passes, 32 KiB, 1 lane)."""

    @pytest.mark.parametrize(
        ("stored", "rewritten"),
        [
            pytest.param(dict(time_cost=2, memory=32), False, id="the same parameters"),
            pytest.param(dict(time_cost=1, memory=32), True, id="fewer passes"),
            pytest.param(dict(time_cost=2, memory=16), True, id="less memory"),
            pytest.param(dict(time_cost=1, memory=16), True, id="fewer passes and less memory"),
            pytest.param(dict(time_cost=3, memory=32), False, id="more passes"),
            pytest.param(dict(time_cost=2, memory=64), False, id="more memory"),
            pytest.param(dict(time_cost=4, memory=64), False, id="more of both"),
            pytest.param(dict(time_cost=1, memory=64), False, id="more memory and fewer passes"),
            pytest.param(dict(time_cost=4, memory=16), False, id="more passes and less memory"),
            pytest.param(
                dict(time_cost=2, memory=32, parallelism=2), False, id="only more lanes"
            ),
            pytest.param(
                dict(time_cost=1, memory=32, parallelism=2), True, id="fewer passes, more lanes"
            ),
        ],
    )
    def test_the_rule_by_parameters(
        self, settings: Settings, stored: dict[str, int], rewritten: bool
    ) -> None:
        assert needs_rehash(_stored(**stored), _configured(settings)) is rewritten

    def test_only_the_parallelism_differing_is_left_alone_either_way(
        self, settings: Settings
    ) -> None:
        """Lanes split the same memory and passes between threads: the work per guess is the same,
        so the hash is neither weaker nor stronger, and a rewrite would cost a hash for nothing."""
        one_lane = _stored(time_cost=2, memory=32, parallelism=1)
        two_lanes = _stored(time_cost=2, memory=32, parallelism=2)
        assert not needs_rehash(one_lane, _configured(settings, argon2_parallelism=2))
        assert not needs_rehash(two_lanes, _configured(settings, argon2_parallelism=1))

    @pytest.mark.parametrize("kind", [Type.I, Type.D], ids=["argon2i", "argon2d"])
    def test_another_algorithm_is_rewritten_to_argon2id(
        self, settings: Settings, kind: Type
    ) -> None:
        other = hash_secret(
            b"a stored password",
            b"saltsaltsaltsalt",
            time_cost=2,
            memory_cost=32,
            parallelism=1,
            hash_len=32,
            type=kind,
        ).decode()
        assert extract_parameters(other).type is kind
        assert needs_rehash(other, _configured(settings))

    def test_another_version_is_rewritten(self, settings: Settings) -> None:
        old = hash_secret(
            b"a stored password",
            b"saltsaltsaltsalt",
            time_cost=2,
            memory_cost=32,
            parallelism=1,
            hash_len=32,
            type=Type.ID,
            version=0x10,
        ).decode()
        assert extract_parameters(old).version == 0x10
        assert needs_rehash(old, _configured(settings))

    @pytest.mark.parametrize(
        ("salt", "tag", "rewritten"),
        [(8, 32, True), (16, 16, True), (32, 32, False), (16, 64, False)],
        ids=["a shorter salt", "a shorter tag", "a longer salt", "a longer tag"],
    )
    def test_a_shorter_salt_or_tag_is_rewritten_and_a_longer_one_is_not(
        self, settings: Settings, salt: int, tag: int, rewritten: bool
    ) -> None:
        stored = hash_secret(
            b"a stored password",
            b"s" * salt,
            time_cost=2,
            memory_cost=32,
            parallelism=1,
            hash_len=tag,
            type=Type.ID,
        ).decode()
        assert needs_rehash(stored, _configured(settings)) is rewritten

    @pytest.mark.parametrize(
        "garbage",
        ["", "not a hash", "$argon2id$v=19$m=x,t=2,p=1$abc$def"],
        ids=["empty", "text", "bad"],
    )
    def test_a_hash_that_cannot_be_read_is_rewritten(
        self, settings: Settings, garbage: str
    ) -> None:
        assert needs_rehash(garbage, _configured(settings)) is True
