"""When email is not configured, say so instead of pretending — 2026-09-29.

Production runs with no `RESEND_API_KEY` (no verified sending domain yet), so
two flows used to promise something that never happened: "a reset link is on
its way", and an invitation the invitee never heard about. Now the reset
response says whether email is configured — the same answer for every address,
so it tells nobody whether an account exists — and an invitation made without
email hands its link back, once, to the owner or admin who made it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from avp_api.services import email as email_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def resend_configured(settings, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:  # noqa: ANN001
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "email_test"})

    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(email_service.httpx, "AsyncClient", factory)
    return sent


async def _sign_up(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Email Test Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _reset(client: AsyncClient, email: str) -> dict[str, Any]:
    resp = await client.post(f"{BASE}/auth/reset-password/request", json={"email": email})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestTheResetRequestIsHonest:
    async def test_without_email_it_says_no_link_was_sent(self, client: AsyncClient) -> None:
        await _sign_up(client, "real@reset.example")
        body = await _reset(client, "real@reset.example")
        assert body["emailConfigured"] is False
        assert "not available" in body["detail"]
        assert "on its way" not in body["detail"]

    async def test_with_email_it_says_the_link_is_on_its_way(
        self, client: AsyncClient, resend_configured
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "real@reset.example")
        body = await _reset(client, "real@reset.example")
        assert body["emailConfigured"] is True
        assert "on its way" in body["detail"]
        assert len(resend_configured) == 1

    @pytest.mark.parametrize("configured", [False, True])
    async def test_the_answer_never_depends_on_the_address(
        self, client: AsyncClient, settings, monkeypatch, configured: bool
    ) -> None:  # noqa: ANN001
        """The anti-enumeration property, in both modes: an account or none, same body."""
        if configured:
            monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))

            async def quiet_send(*args: Any, **kwargs: Any) -> None:
                return None

            monkeypatch.setattr(email_service, "send_password_reset", quiet_send)
        await _sign_up(client, "real@reset.example")
        assert await _reset(client, "real@reset.example") == await _reset(
            client, "nobody@reset.example"
        )


async def _invite(client: AsyncClient, agency_id: str, email: str) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": "member"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestAnInvitationWithoutEmail:
    async def test_hands_the_link_to_the_inviter_once_and_it_works(
        self, client: AsyncClient, settings
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client, "owner@invite.example")
        body = await _invite(client, agency_id, "new.seat@invite.example")
        link = body["acceptUrl"]
        assert link is not None
        url = urlparse(link)
        assert f"{url.scheme}://{url.netloc}" == settings.public_web_base_url.rstrip("/")
        assert url.path == "/invite"
        assert url.fragment

        # The roster never shows it again: the link is handed over once.
        roster = await client.get(f"{BASE}/agencies/{agency_id}/seats")
        assert roster.status_code == 200, roster.text
        assert "/invite#" not in roster.text

        # And it is a real link: the invitee can redeem it.
        await client.post(f"{BASE}/auth/logout")
        token = url.fragment
        accepted = await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "New Seat", "password": PASSWORD},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["user"]["email"] == "new.seat@invite.example"

    async def test_re_inviting_hands_back_a_fresh_link(self, client: AsyncClient) -> None:
        agency_id = await _sign_up(client, "owner@invite.example")
        first = (await _invite(client, agency_id, "again@invite.example"))["acceptUrl"]
        second = await _invite(client, agency_id, "again@invite.example")
        assert second["seatConsumed"] is False
        assert second["acceptUrl"] is not None and second["acceptUrl"] != first


class TestAnInvitationWithEmail:
    async def test_never_returns_the_link_and_emails_it_instead(
        self, client: AsyncClient, resend_configured
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client, "owner@invite.example")
        body = await _invite(client, agency_id, "new.seat@invite.example")
        assert body["acceptUrl"] is None
        assert [m["to"] for m in resend_configured] == [["new.seat@invite.example"]]
        assert "/invite#" in resend_configured[0]["text"]
