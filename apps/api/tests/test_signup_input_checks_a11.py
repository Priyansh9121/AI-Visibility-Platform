"""A11 (the founder's D11): the routes that make an account refuse obviously bad input by SCHEMA
alone (length and format), with no blocklist and no outside service.

"Disposable" is not a list here: D11 says by schema only, so no domain list is kept, downloaded or
asked of anyone. What a schema can say is pinned below, field by field, on both routes that make an
agency: `POST /auth/sign-up` (S) and `POST /auth/google/complete` (G). A refusal is a 422 whose
`errors[]` names the field, with pydantic's (or the address library's) own message; the messages
are not pinned here, so a library's wording is not a test failure, and none was changed by A11.

What is pinned, by field (S: sign-up, G: Google complete):

* email (S): a well-formed address with an @ and a dotted domain; not on a reserved name
  (`.test`); at most 254 characters in all; and at most 64 before the @ (ADDED by A11: that one
  was missing). All here.
* agencyName (S, G): 2 to 200 characters once trimmed, so blanks do not count. Here.
* fullName (S, G): 1 to 200 characters once trimmed. Here.
* ticket (G): 1 to 512 characters. Here.
* password (S): 12 to 256 characters and 5 distinct ones. In `test_password_policy_a4.py`.
* any other key (S, G): refused (`extra_forbidden`). In `test_request_schemas_forbid_extra.py`.

The 64-character limit on the part before the @ was NOT enforced: email-validator checks it only
when it is asked to be strict, and pydantic's `EmailStr` does not ask. `SignUpRequest.email` now
asks (`schemas/auth.py`, `SignUpEmail`), and the refusal is the library's own sentence.

Last, not a refusal but the other way: an agency name the form accepts whose slug comes out under 2
characters (such as "A!") made a 500 on both routes, because the database refuses such a slug
(`ck_agencies_slug_min_length`). `slugify` now falls back to "agency" (fix round, security review
L4); the end of this file pins it, on both routes.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.models import Agency, User
from avp_api.security import token_digest
from avp_api.services.auth import slugify
from avp_api.services.google_oauth import TICKET_PREFIX, GoogleFlowStore, GoogleIdentity

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
SIGN_UP = f"{BASE}/auth/sign-up"
COMPLETE = f"{BASE}/auth/google/complete"
DOMAIN = "signup-checks.example"


def _address(local: int, total: int) -> str:
    """A well-formed address whose part before the @ has `local` characters and which has `total`
    in all: the domain is dotted labels of at most 63, ending in `.com`."""
    left = total - local - 1 - len(".com")
    labels: list[str] = []
    while left > 0:
        take = min(63, left)
        labels.append("d" * take)
        left -= take + 1  # the dot between labels
    address = "l" * local + "@" + ".".join(labels) + ".com"
    assert len(address) == total, (len(address), total)
    return address


def _sign_up_body(**changes: str) -> dict[str, str]:
    body = {
        "agencyName": "Checks Agency",
        "fullName": "Operator",
        "email": f"checks@{DOMAIN}",
        "password": PASSWORD,
    }
    body.update(changes)
    return body


async def _ticket(client: AsyncClient, settings: Settings, n: int = 1) -> str:
    """A ticket for a new Google address, bound to this client's flow cookie (set here)."""
    binding = f"binding-{n}-" + "b" * 32
    client.cookies.set(settings.google_flow_cookie_key, binding)
    identity = GoogleIdentity(
        sub=f"google-sub-{n}",
        email=f"gina{n}@{DOMAIN}",
        email_verified=True,
        name="Gina Google",
        nonce=None,
    )
    flow = GoogleFlowStore(redis_module.get_redis(settings), settings)
    return await flow.issue_ticket(identity, token_digest(binding))


async def _post(
    client: AsyncClient, settings: Settings, route: str, **changes: str
) -> tuple[Response, str | None]:
    """Make the request for `route` ("sign-up" or "google") with these fields changed.

    Returns the response and, for Google, the ticket it carried.
    """
    if route == "sign-up":
        return await client.post(SIGN_UP, json=_sign_up_body(**changes)), None
    ticket = await _ticket(client, settings)
    body = {"ticket": ticket, "agencyName": "Checks Agency", "fullName": "Operator"}
    body.update(changes)
    return await client.post(COMPLETE, json=body), ticket


def _refused(resp: Response, field: str, kind: str) -> None:
    """A 422 with exactly one error, on `field`, of pydantic's error type `kind`."""
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["type"] == "/problems/validation-failed"
    assert [(e["field"], e["type"]) for e in body["errors"]] == [(field, kind)], body["errors"]


async def _users(engine) -> int:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        return int((await s.execute(select(func.count()).select_from(User))).scalar_one())


# --- a valid request still works -----------------------------------------------------------------


async def test_a_valid_sign_up_still_works(client: AsyncClient) -> None:
    resp = await client.post(SIGN_UP, json=_sign_up_body())
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["email"] == f"checks@{DOMAIN}"


async def test_a_valid_google_completion_still_works(
    client: AsyncClient, settings: Settings
) -> None:
    resp, _ = await _post(client, settings, "google")
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["email"] == f"gina1@{DOMAIN}"


# --- email (sign-up only: Google's address is the ticket's) --------------------------------------


@pytest.mark.parametrize(
    ("email", "kind"),
    [
        pytest.param("plainaddress", "value_error", id="no-at-sign"),
        pytest.param("", "value_error", id="empty"),
        pytest.param("   ", "value_error", id="blanks-only"),
        pytest.param("user@localhost", "value_error", id="domain-without-a-dot"),
        pytest.param("a@b", "value_error", id="one-letter-domain-without-a-dot"),
        pytest.param(f"user@{DOMAIN}.test", "value_error", id="reserved-name"),
        pytest.param(_address(40, 255), "value_error", id="255-characters-in-all"),
        pytest.param("x" * 2049, "value_error", id="over-the-2048-pydantic-reads"),
        # The one A11 added: this was accepted before. 65 before the @, valid in every other way.
        pytest.param("l" * 65 + f"@{DOMAIN}", "value_error", id="65-before-the-at-sign"),
    ],
)
async def test_a_bad_address_is_refused_on_the_email_field(
    client: AsyncClient, engine, email: str, kind: str  # noqa: ANN001
) -> None:
    resp = await client.post(SIGN_UP, json=_sign_up_body(email=email))

    _refused(resp, "email", kind)
    assert await _users(engine) == 0


async def test_a_number_is_not_an_address(client: AsyncClient) -> None:
    resp = await client.post(SIGN_UP, json={**_sign_up_body(), "email": 5})

    _refused(resp, "email", "string_type")


@pytest.mark.parametrize(
    "email",
    [
        pytest.param(_address(40, 254), id="254-characters-in-all"),
        pytest.param("l" * 64 + f"@{DOMAIN}", id="64-before-the-at-sign"),
        pytest.param(f"  Spaced.Out@{DOMAIN}  ", id="blanks-trimmed"),
    ],
)
async def test_an_address_at_the_limits_is_accepted(client: AsyncClient, email: str) -> None:
    resp = await client.post(SIGN_UP, json=_sign_up_body(email=email))

    assert resp.status_code == 201, resp.text


async def test_the_added_limit_is_on_the_account_made_here_and_nowhere_else(
    client: AsyncClient,
) -> None:
    """Sign-in and reset-request take an address that may already exist; an account made before
    this check, or one with a long local part from anywhere, is not locked out by it."""
    long = "l" * 65 + f"@{DOMAIN}"

    login = await client.post(f"{BASE}/auth/login", json={"email": long, "password": PASSWORD})
    reset = await client.post(f"{BASE}/auth/reset-password/request", json={"email": long})

    assert login.status_code == 401, login.text  # not 422: it is simply no account
    assert reset.status_code == 200, reset.text


# --- agency name and full name, on both routes ---------------------------------------------------


@pytest.mark.parametrize("route", ["sign-up", "google"])
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        pytest.param("", "string_too_short", id="empty"),
        pytest.param("     ", "string_too_short", id="blanks-only"),
        pytest.param("A", "string_too_short", id="one-character"),
        pytest.param("  A  ", "string_too_short", id="one-character-once-trimmed"),
        pytest.param("a" * 201, "string_too_long", id="201-characters"),
    ],
)
async def test_a_bad_agency_name_is_refused_on_both_routes(
    client: AsyncClient, settings: Settings, engine, route: str, name: str, kind: str  # noqa: ANN001
) -> None:
    resp, ticket = await _post(client, settings, route, agencyName=name)

    _refused(resp, "agencyName", kind)
    assert await _users(engine) == 0
    if ticket is not None:  # a refused form does not spend the ticket
        redis = redis_module.get_redis(settings)
        assert await redis.exists(f"{TICKET_PREFIX}{token_digest(ticket)}") == 1


@pytest.mark.parametrize("route", ["sign-up", "google"])
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("AB", id="two-characters"),
        pytest.param("a" * 200, id="200-characters"),
        pytest.param("  Acme Search  ", id="blanks-trimmed"),
    ],
)
async def test_an_agency_name_at_the_limits_is_accepted_on_both_routes(
    client: AsyncClient, settings: Settings, route: str, name: str
) -> None:
    resp, _ = await _post(client, settings, route, agencyName=name)

    assert resp.status_code == 201, resp.text
    assert resp.json()["agency"]["name"] == name.strip()


@pytest.mark.parametrize("route", ["sign-up", "google"])
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        pytest.param("", "string_too_short", id="empty"),
        pytest.param("     ", "string_too_short", id="blanks-only"),
        pytest.param("a" * 201, "string_too_long", id="201-characters"),
    ],
)
async def test_a_bad_full_name_is_refused_on_both_routes(
    client: AsyncClient, settings: Settings, engine, route: str, name: str, kind: str  # noqa: ANN001
) -> None:
    resp, ticket = await _post(client, settings, route, fullName=name)

    _refused(resp, "fullName", kind)
    assert await _users(engine) == 0
    if ticket is not None:
        redis = redis_module.get_redis(settings)
        assert await redis.exists(f"{TICKET_PREFIX}{token_digest(ticket)}") == 1


@pytest.mark.parametrize("route", ["sign-up", "google"])
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("D", id="one-character"),
        pytest.param("a" * 200, id="200-characters"),
    ],
)
async def test_a_full_name_at_the_limits_is_accepted_on_both_routes(
    client: AsyncClient, settings: Settings, route: str, name: str
) -> None:
    resp, _ = await _post(client, settings, route, fullName=name)

    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["fullName"] == name


# --- the Google ticket field ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticket", "kind"),
    [("", "string_too_short"), ("t" * 513, "string_too_long")],
    ids=["empty", "513-characters"],
)
async def test_a_ticket_that_cannot_be_one_is_refused_before_anything_is_read(
    client: AsyncClient, ticket: str, kind: str
) -> None:
    resp = await client.post(
        COMPLETE, json={"ticket": ticket, "agencyName": "Checks Agency", "fullName": "Operator"}
    )

    _refused(resp, "ticket", kind)


# --- a name whose slug is short (fix round, security review L4) -----------------------------------
#
# A valid agency name made a 500 when its slug came out as one character
# (`ck_agencies_slug_min_length`). Valid input is not the schema's to refuse, so the slug changed.


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        pytest.param("A!", "agency", id="a-letter-and-a-symbol"),
        pytest.param("A\u65e5", "agency", id="a-latin-letter-then-a-non-latin-one"),
        pytest.param("\u65e5\u672c", "agency", id="nothing-latin-at-all"),
        pytest.param("!!", "agency", id="nothing-but-symbols"),
        pytest.param("a" * 59 + " b", "a" * 59, id="the-cut-at-60-ends-in-a-dash"),
        pytest.param("a" * 59 + " b" * 5, "a" * 59, id="the-cut-ends-in-a-dash-before-more"),
        # What did not change.
        pytest.param("AB", "ab", id="two-letters"),
        pytest.param("A1", "a1", id="a-letter-and-a-digit"),
        pytest.param("\u00c4\u0031", "a1", id="an-accent-is-folded-away"),
        pytest.param("a" * 60, "a" * 60, id="sixty-letters"),
        pytest.param("a" * 80, "a" * 60, id="eighty-letters-are-cut-to-sixty"),
        pytest.param("  Meridian  Search & Partners!  ", "meridian-search-partners", id="noise"),
    ],
)
def test_slugify_gives_a_slug_the_database_accepts(name: str, slug: str) -> None:
    assert slugify(name) == slug
    # Whatever it is, it satisfies the constraint and the column.
    assert 2 <= len(slugify(name)) <= 60 and not slugify(name).endswith("-")


async def test_a_password_sign_up_whose_agency_name_has_a_one_character_slug_is_made(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    first = await client.post(SIGN_UP, json=_sign_up_body(agencyName="A!"))
    second = await client.post(
        SIGN_UP, json=_sign_up_body(agencyName="B!", email=f"second@{DOMAIN}")
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["agency"]["name"] == "A!"
    # Both fall back to "agency", and the second is numbered because the first has it.
    assert first.json()["agency"]["slug"] == "agency"
    assert second.json()["agency"]["slug"] == "agency-1"
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        slugs = (await s.execute(select(Agency.slug).order_by(Agency.slug))).scalars().all()
    assert slugs == ["agency", "agency-1"]


async def test_a_google_completion_whose_agency_name_has_a_one_character_slug_is_made(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    resp, ticket = await _post(client, settings, "google", agencyName="A\u65e5")

    assert resp.status_code == 201, resp.text
    assert resp.json()["agency"]["name"] == "A\u65e5"
    assert resp.json()["agency"]["slug"] == "agency"
    # The ticket was spent by the completion that worked, not by a 500 that left the person nothing.
    redis = redis_module.get_redis(settings)
    assert ticket is not None
    assert await redis.exists(f"{TICKET_PREFIX}{token_digest(ticket)}") == 0
    assert await _users(engine) == 1
