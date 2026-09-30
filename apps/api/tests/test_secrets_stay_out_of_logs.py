"""Live tokens never reach the logs, and a blank key is no key — 2026-09-29.

Until this date, with `RESEND_API_KEY` unset, the reset and invitation paths
logged the full link, live token included, to stdout, which Render keeps: anyone
who could read the logs could take over any account that asked for a reset.
And a blank `RESEND_API_KEY=` counted as a key, so the message went to Resend
with an empty credential.

The log tests capture everything a run can write — structlog's events
(`capture_logs`), stdout (`capsys`, where structlog prints in this project) and
stdlib logging (`caplog`, where httpx writes) — and search all of it. The token
is made findable by minting a known sentinel in place of the random one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr

from avp_api.config import PROVIDER_SETTINGS, Settings
from avp_api.services import email as email_service
from avp_api.services import invitations as invitation_service
from avp_api.services import password_reset as reset_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
OWNER = "owner@logs.example"
INVITEE = "invitee@logs.example"
STRANGER = "nobody-here@logs.example"
RESET_TOKEN = "RESETSENTINELtoken0123456789abcdefghijklmnopqrstu"
INVITE_TOKEN = "INVITESENTINELtoken0123456789abcdefghijklmnopqrst"


# ---------------------------------------------------------------------------
# a blank key is unset, for every provider setting
# ---------------------------------------------------------------------------


class TestABlankKeyIsNoKey:
    @pytest.mark.parametrize("name", PROVIDER_SETTINGS)
    def test_blank_or_whitespace_in_the_environment_is_unset(
        self, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for blank in ("", "   "):
            monkeypatch.setenv(name.upper(), blank)
            assert getattr(Settings(_env_file=None), name) is None, (name, blank)

    @pytest.mark.parametrize("name", PROVIDER_SETTINGS)
    def test_a_real_value_is_kept(self, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(name.upper(), "set-to-something")
        value = getattr(Settings(_env_file=None), name)
        assert value is not None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        assert raw == "set-to-something"

    def test_every_provider_setting_is_covered(self) -> None:
        """A new provider key must join the one normaliser, not get its own reading."""
        fields = Settings.model_fields
        keyish = {
            name
            for name in fields
            if name.endswith(("_api_key", "_key", "_secret"))
            and name not in ("app_secret",)
        }
        assert keyish <= set(PROVIDER_SETTINGS), keyish - set(PROVIDER_SETTINGS)

    async def test_a_blank_resend_key_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing reaches Resend with an empty credential — not even a request."""
        monkeypatch.setenv("RESEND_API_KEY", "")
        settings = Settings(_env_file=None)
        assert settings.email_configured is False

        def no_client(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("an HTTP client was opened with no key configured")

        monkeypatch.setattr(email_service.httpx, "AsyncClient", no_client)
        await email_service.send_password_reset(
            OWNER, "http://localhost:3000/reset-password#x", user_id="user_x", settings=settings
        )


# ---------------------------------------------------------------------------
# no token, no link and no address in any log line
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mint known tokens, so their absence from the logs can be asserted."""
    monkeypatch.setattr(reset_service, "new_session_token", lambda: RESET_TOKEN)
    monkeypatch.setattr(invitation_service, "new_session_token", lambda: INVITE_TOKEN)


@pytest.fixture
def resend_configured(settings, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:  # noqa: ANN001
    """A key set, and Resend answered by a fake that records what it was sent."""
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "email_test"})

    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(email_service.httpx, "AsyncClient", factory)
    return sent


async def _drive_both_paths(client: AsyncClient) -> None:
    """Sign up, ask for a reset (a real address and an unknown one), invite a seat."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Log Test Agency", "fullName": "Owner", "email": OWNER,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    agency_id = resp.json()["agency"]["id"]
    for address in (OWNER, STRANGER):
        reset = await client.post(f"{BASE}/auth/reset-password/request", json={"email": address})
        assert reset.status_code == 200, reset.text
    invite = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": INVITEE, "role": "member"}
    )
    assert invite.status_code == 201, invite.text


def _assert_nothing_secret(everything: str, web: str) -> None:
    # The links as the code builds them — the web app's address, then the
    # token. (The API's own `/auth/reset-password/request` path is not one:
    # the test client logs every request it makes, and that path is public.)
    links = (f"{web.rstrip('/')}/reset-password#", f"{web.rstrip('/')}/invite#")
    for secret in (RESET_TOKEN, INVITE_TOKEN, *links, OWNER, INVITEE, STRANGER):
        assert secret not in everything, f"{secret!r} reached the logs"


class TestNothingSecretIsLogged:
    async def test_with_email_not_configured(
        self, client: AsyncClient, settings, sentinel_tokens, capsys, caplog  # noqa: ANN001
    ) -> None:
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            await _drive_both_paths(client)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        _assert_nothing_secret(everything, settings.public_web_base_url)
        # And the events that say what happened are there, by id.
        kinds = {e["event"] for e in events}
        assert "email.password_reset.not_sent" in kinds
        assert "email.invitation.not_sent" in kinds
        assert "password_reset.request.no_account" in kinds
        assert all(e.get("user_id") for e in events if e["event"].startswith("email."))

    async def test_with_email_configured(
        self,
        client: AsyncClient,
        settings,  # noqa: ANN001
        sentinel_tokens,  # noqa: ANN001
        resend_configured,  # noqa: ANN001
        capsys,  # noqa: ANN001
        caplog,  # noqa: ANN001
    ) -> None:
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            await _drive_both_paths(client)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        _assert_nothing_secret(everything, settings.public_web_base_url)
        kinds = {e["event"] for e in events}
        assert {"email.password_reset.sent", "email.invitation.sent"} <= kinds
        # The links DID go out — to the inboxes, and only there.
        bodies = " ".join(message["text"] for message in resend_configured)
        assert f"/reset-password#{RESET_TOKEN}" in bodies
        assert f"/invite#{INVITE_TOKEN}" in bodies
        assert [m["to"] for m in resend_configured] == [[OWNER], [INVITEE]]
