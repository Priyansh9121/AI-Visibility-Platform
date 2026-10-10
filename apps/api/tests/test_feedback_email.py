"""`FEEDBACK_EMAIL` — where the "Send feedback" link writes to — 2026-10-06.

The founder's brief: a small link in the signed-in web app that opens an email to one
configured address, with the page in the subject. Off by default: with the setting unset the
web draws no link and nothing a user sees changes.

The address is an API setting and travels to the web in the one response the signed-in shell
already reads, `GET /auth/me` (`MeOut`, "the whole authenticated context in one call"). What is
asserted here is the API half:

- the setting: blank is unset, a good address is kept, and a bad one stops the start with a
  message that names the variable and does not repeat what was typed (a secret pasted into the
  wrong variable must not reach the log of a failed start);
- the exposure: the address is on the signed-in context and nowhere else. Not on a signed-out
  answer, not on a public route, not on a token route, and in no log line.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import avp_api
from avp_api.config import Settings
from avp_api.models import Invitation
from avp_api.security import token_digest

BASE = "/api/v1"
DB = "postgresql+asyncpg://avp:avp@db.internal:5432/avp"
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
AUTH_ROUTER = Path(avp_api.__file__).resolve().parent / "routers" / "auth.py"

ADDRESS = "pilot-feedback@avp-pilot.example"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OWNER = "owner@feedback-setting.example"
INVITEE = "invitee@feedback-setting.example"

# Every operation whose success body is `MeOut`, the signed-in context, and why each is one the
# caller is signed in on. `GET /auth/me` needs a session; the five POSTs are made by someone
# presenting a credential, and each sets the session cookie in the very response that carries
# the context. A new route that returns `MeOut` must be added here on purpose, which is the
# moment to ask whether it can be reached signed out.
CONTEXT_OPERATIONS = {
    ("get", f"{BASE}/auth/me"),
    ("post", f"{BASE}/auth/sign-up"),
    ("post", f"{BASE}/auth/login"),
    ("post", f"{BASE}/auth/change-password"),
    ("post", f"{BASE}/auth/invitations/accept"),
    ("post", f"{BASE}/auth/google/complete"),
}


def _settings(**given: Any) -> Settings:
    """Settings as the app builds them: from the environment, reading no dotenv file."""
    return Settings(
        _env_file=None, environment="local", database_url=DB, redis_url="redis://x/0", **given
    )


# ---------------------------------------------------------------------------
# the setting
# ---------------------------------------------------------------------------


def test_the_default_is_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEEDBACK_EMAIL", raising=False)
    assert _settings().feedback_email is None


@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
def test_a_blank_value_is_unset_like_every_other_blank_setting(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """`cp .env.example .env` and clearing the value in Render both leave `NAME=` behind.

    That is "unset" for every provider setting, and here it is no link: the safe direction,
    because a blank must never be a reason to show somebody an address that is not there.
    """
    monkeypatch.setenv("FEEDBACK_EMAIL", blank)
    assert _settings().feedback_email is None
    assert _settings(feedback_email=blank).feedback_email is None


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        (ADDRESS, ADDRESS),
        (f"  {ADDRESS}  ", ADDRESS),
        ("Feedback+AVP@Pilot.Example", "Feedback+AVP@pilot.example"),
        ("feedback@mail.avp-pilot.example", "feedback@mail.avp-pilot.example"),
    ],
)
def test_a_good_address_is_kept_stripped_and_normalised(
    monkeypatch: pytest.MonkeyPatch, raw: str, kept: str
) -> None:
    monkeypatch.setenv("FEEDBACK_EMAIL", raw)
    assert _settings().feedback_email == kept
    assert _settings(feedback_email=raw).feedback_email == kept


BAD = [
    "not-an-address",
    "pilot@",
    "@avp-pilot.example",
    "a@@avp-pilot.example",
    "a b@avp-pilot.example",
    "Pilot Feedback <pilot@avp-pilot.example>",
    "mailto:pilot@avp-pilot.example",
    "one@avp-pilot.example, two@avp-pilot.example",
    "pilot@localhost",
    "pilot@avp-pilot",
    # What somebody pastes into the wrong variable.
    "sk-ant" + "-PASTED-INTO-THE-WRONG-VARIABLE-0123456789",
]


@pytest.mark.parametrize("bad", BAD)
def test_a_bad_value_stops_the_start_naming_the_variable_and_not_the_value(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Said where somebody sees it, in the deploy log, and without repeating what was typed.

    Pydantic's own `ValidationError` prints `input_value='…'`, so a key pasted here by mistake
    would be written to Render's log by the failed start. The message is ours instead.
    """
    monkeypatch.setenv("FEEDBACK_EMAIL", bad)
    with pytest.raises(RuntimeError) as from_env:
        _settings()
    with pytest.raises(RuntimeError) as from_kwargs:
        _settings(feedback_email=bad)
    for caught in (from_env, from_kwargs):
        message = str(caught.value)
        assert "FEEDBACK_EMAIL" in message
        assert "leave it empty" in message
        assert bad not in message
        for piece in bad.replace(",", " ").replace("<", " ").replace(">", " ").split():
            if len(piece) > 8:
                assert piece not in message


def test_a_value_that_is_not_text_stops_the_start_too() -> None:
    for odd in (25, True, ["a@avp-pilot.example"]):
        with pytest.raises(RuntimeError, match="FEEDBACK_EMAIL"):
            _settings(feedback_email=odd)


def test_the_failed_start_prints_no_other_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole environment is in reach of the error, and none of it is printed."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_" + "test_NEIGHBOURKEY0123456789")
    monkeypatch.setenv("APP_SECRET", "NEIGHBOUR-APP-SECRET-0123456789abcdef")
    monkeypatch.setenv("FEEDBACK_EMAIL", "bad")
    with pytest.raises(RuntimeError) as caught:
        _settings()
    for neighbour in ("NEIGHBOURKEY", "NEIGHBOUR-APP-SECRET"):
        assert neighbour not in str(caught.value)


def test_the_template_documents_it_and_ships_it_blank() -> None:
    """A deployment that copies `.env.example` gets no link until somebody decides one."""
    lines = TEMPLATE.read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == "FEEDBACK_EMAIL=")
    comment: list[str] = []
    for line in reversed(lines[:at]):
        if not line.startswith("#"):
            break
        comment.append(line.lower())
    said = " ".join(comment)
    assert "send feedback" in said
    assert "no link" in said
    assert "not a secret" in said


# ---------------------------------------------------------------------------
# the exposure: the signed-in context, and nowhere else
# ---------------------------------------------------------------------------


@pytest.fixture
def feedback_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "feedback_email", ADDRESS)
    return ADDRESS


async def _sign_up(client: AsyncClient, *, email: str = OWNER) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Feedback Setting Agency",
            "fullName": "Owner Person",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_unset_the_signed_in_context_says_null(client: AsyncClient) -> None:
    """The default, which is what every deployment has until the founder sets it."""
    await _sign_up(client)
    resp = await client.get(f"{BASE}/auth/me")
    assert resp.status_code == 200, resp.text
    # The key is there, with null: the web reads "no link" off it and never has to guess.
    assert resp.json()["feedbackEmail"] is None


async def test_set_the_signed_in_context_carries_the_address(
    client: AsyncClient, feedback_on: str
) -> None:
    await _sign_up(client)
    resp = await client.get(f"{BASE}/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["feedbackEmail"] == feedback_on
    # Nothing else about the context moved.
    assert set(resp.json()) == {"user", "agency", "seats", "feedbackEmail", "scanEmails"}


async def test_signed_out_the_context_is_refused_and_carries_no_address(
    client: AsyncClient, feedback_on: str
) -> None:
    await _sign_up(client)
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204
    resp = await client.get(f"{BASE}/auth/me")
    assert resp.status_code == 401
    assert feedback_on not in resp.text
    assert "feedback" not in resp.text.lower()


async def test_a_session_that_never_began_gets_no_address_either(
    client: AsyncClient, feedback_on: str
) -> None:
    resp = await client.get(f"{BASE}/auth/me")
    assert resp.status_code == 401
    assert feedback_on not in resp.text


async def test_a_failed_sign_in_carries_no_address(
    client: AsyncClient, feedback_on: str
) -> None:
    await _sign_up(client)
    await client.post(f"{BASE}/auth/logout")
    wrong = await client.post(
        f"{BASE}/auth/login", json={"email": OWNER, "password": "not-the-password-at-all"}
    )
    assert wrong.status_code == 401
    assert feedback_on not in wrong.text
    refused = await client.post(f"{BASE}/auth/login", json={"email": OWNER})
    assert refused.status_code == 422
    assert feedback_on not in refused.text


async def test_public_and_token_routes_never_carry_it(
    client: AsyncClient, feedback_on: str
) -> None:
    """No session, no address: the public plan, liveness, a reset request, a share token."""
    answers = [
        await client.get(f"{BASE}/billing/plan"),
        await client.get(f"{BASE}/health"),
        await client.post(f"{BASE}/auth/reset-password/request", json={"email": OWNER}),
        await client.post(f"{BASE}/reports/shared", json={"token": "no-such-share-token"}),
        await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": "no-such-invitation", "fullName": "Nobody", "password": NEW_PASSWORD},
        ),
    ]
    assert [a.status_code for a in answers] == [200, 200, 200, 404, 400]
    for answer in answers:
        assert feedback_on not in answer.text, answer.request.url
        assert "feedback" not in answer.text.lower(), answer.request.url
    # The public plan is still exactly the trial's length and nothing more.
    assert set(answers[0].json()) == {"trialDays"}


async def test_the_context_is_the_only_schema_that_names_it(client: AsyncClient) -> None:
    """One home in the contract: no other response model can carry the address."""
    schema = (await client.get(f"{BASE}/openapi.json")).json()
    homes = [
        name
        for name, body in schema["components"]["schemas"].items()
        if "feedbackEmail" in body.get("properties", {})
    ]
    assert homes == ["MeOut"]

    me = schema["components"]["schemas"]["MeOut"]
    # Required, so every route that builds the context has to decide it; and nullable.
    assert "feedbackEmail" in me["required"]
    kinds = {option.get("type") for option in me["properties"]["feedbackEmail"]["anyOf"]}
    assert kinds == {"string", "null"}


async def test_the_routes_that_return_the_context_are_exactly_these(
    client: AsyncClient,
) -> None:
    schema = (await client.get(f"{BASE}/openapi.json")).json()
    found: set[tuple[str, str]] = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for code, response in operation.get("responses", {}).items():
                if not code.startswith("2"):
                    continue
                ref = response.get("content", {}).get("application/json", {}).get("schema", {})
                if ref.get("$ref", "").endswith("/MeOut"):
                    found.add((method, path))
    assert found == CONTEXT_OPERATIONS


def test_every_construction_of_the_context_decides_the_address() -> None:
    """Read off the source, so the route nobody drives here (Google's) is held to it too."""
    tree = ast.parse(AUTH_ROUTER.read_text())
    builds = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "MeOut"
    ]
    assert builds, "no MeOut is built in the auth router: this test has lost what it reads"
    for build in builds:
        given = {keyword.arg: ast.unparse(keyword.value) for keyword in build.keywords}
        assert given.get("feedback_email") == "settings.feedback_email", ast.unparse(build)


async def _invite_and_accept(client: AsyncClient, engine: Any, agency_id: str) -> Any:
    sent = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": INVITEE, "role": "member"}
    )
    assert sent.status_code == 201, sent.text
    # The raw token is never returned, so write a known digest onto the live row.
    token = f"test-token-{INVITEE}"
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id, Invitation.email == INVITEE
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()
    await client.post(f"{BASE}/auth/logout")
    return await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "New Seat", "password": NEW_PASSWORD},
    )


async def test_the_sign_in_family_carries_it_with_the_session_it_starts(
    client: AsyncClient, engine: Any, settings: Settings, feedback_on: str
) -> None:
    """Sign-up, login, change-password and an accepted invitation each return the context.

    Each does so in the same response that sets the caller's session cookie, so the address
    reaches somebody who is signed in by it and nobody else; and it is the SAME address
    `GET /auth/me` says, not a second reading of the setting.
    """
    signed_up = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Feedback Setting Agency",
            "fullName": "Owner Person",
            "email": OWNER,
            "password": PASSWORD,
        },
    )
    assert signed_up.status_code == 201, signed_up.text
    assert signed_up.json()["feedbackEmail"] == feedback_on
    assert settings.session_cookie_key in signed_up.cookies
    agency_id = signed_up.json()["agency"]["id"]

    changed = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["feedbackEmail"] == feedback_on

    accepted = await _invite_and_accept(client, engine, agency_id)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["feedbackEmail"] == feedback_on
    assert settings.session_cookie_key in accepted.cookies

    await client.post(f"{BASE}/auth/logout")
    logged_in = await client.post(
        f"{BASE}/auth/login", json={"email": OWNER, "password": NEW_PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["feedbackEmail"] == feedback_on
    assert settings.session_cookie_key in logged_in.cookies
    assert (await client.get(f"{BASE}/auth/me")).json()["feedbackEmail"] == feedback_on


async def test_unset_every_one_of_them_says_null(client: AsyncClient) -> None:
    signed_up = await _sign_up(client)
    assert signed_up["feedbackEmail"] is None
    await client.post(f"{BASE}/auth/logout")
    logged_in = await client.post(
        f"{BASE}/auth/login", json={"email": OWNER, "password": PASSWORD}
    )
    assert logged_in.json()["feedbackEmail"] is None


# ---------------------------------------------------------------------------
# nothing in a log
# ---------------------------------------------------------------------------


async def test_the_address_reaches_no_log_line(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys, caplog  # noqa: ANN001
) -> None:
    """Sign up, read the context, log in, read the public plan: the address is in no line.

    structlog's events, stdout (where structlog prints in this project) and stdlib logging
    (where httpx writes) are all searched, as `test_secrets_stay_out_of_logs.py` does.
    """
    sentinel = "LOGSENTINEL-feedback@avp-pilot.example"
    monkeypatch.setattr(settings, "feedback_email", sentinel)
    caplog.set_level("DEBUG")
    with structlog.testing.capture_logs() as events:
        await _sign_up(client)
        assert (await client.get(f"{BASE}/auth/me")).json()["feedbackEmail"] == sentinel
        await client.post(f"{BASE}/auth/logout")
        await client.post(f"{BASE}/auth/login", json={"email": OWNER, "password": PASSWORD})
        await client.get(f"{BASE}/billing/plan")
        await client.post(f"{BASE}/auth/logout")
        assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    assert "LOGSENTINEL" not in everything
    assert "avp-pilot.example" not in everything
