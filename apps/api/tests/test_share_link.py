"""The public share link — Epic 9.8.

**This is the first unauthenticated read surface in the product.** Everything
else behind /api/v1 resolves a session cookie and scopes every query to one
agency; `GET /reports/{token}` is reached by a stranger holding a URL. These
tests are therefore weighted toward what must NOT be reachable, not toward the
happy path — the happy path is one request and it is the least interesting
thing here.

Engine execution is stubbed (Epic 4's stubs), so nothing here costs money.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, agency: str, email: str) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    def _install(n_prompts: int = 4):
        generated = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                citations = (
                    [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                    if engine is Engine.CLAUDE_SEARCH else []
                )
                out.append(EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler and well liked.",
                    citations=citations, latency_ms=5))
            return out

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _scored_scan(client: AsyncClient, stub_engines, domain: str = "helpscout.com") -> str:  # noqa: ANN001
    cid = (await client.post(
        f"{BASE}/clients", json={"url": domain, "classify": False}
    )).json()["id"]
    stub_engines(n_prompts=4)
    sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
    assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
    return sid


class TestMinting:
    async def test_the_same_scan_returns_the_same_token_every_time(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Idempotent, because there is no revocation.

        Every token minted is a URL that works forever, so a second click that
        minted a second token would quietly leave a live link nobody tracks.
        """
        await _sign_up(client, "Share Agency", "share@test.example")
        sid = await _scored_scan(client, stub_engines)

        first = await client.post(f"{BASE}/scans/{sid}/share")
        second = await client.post(f"{BASE}/scans/{sid}/share")

        assert first.status_code == 200, first.text
        assert second.status_code == 200
        assert first.json()["token"] == second.json()["token"]
        assert first.json()["url"] == second.json()["url"]
        # 200 rather than 201: the second call creates nothing and must not say
        # it did.
        assert first.json()["scanId"] == sid

    async def test_two_scans_get_two_different_unguessable_tokens(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Share Agency", "share2@test.example")
        first_scan = await _scored_scan(client, stub_engines, "helpscout.com")
        second_scan = await _scored_scan(client, stub_engines, "basecamp.com")

        a = (await client.post(f"{BASE}/scans/{first_scan}/share")).json()["token"]
        b = (await client.post(f"{BASE}/scans/{second_scan}/share")).json()["token"]

        assert a != b
        # Neither token may contain, or be derived from, the scan id — the id
        # is already public to anyone who has seen the authenticated URL.
        assert first_scan not in a and second_scan not in b
        # 32 random bytes, base64url-encoded, is 43 characters. Asserted as a
        # floor on entropy rather than an exact match, so the constant can rise
        # without editing a test, but never silently fall.
        assert len(a) >= 43 and len(b) >= 43

    async def test_another_agency_cannot_mint_a_link_for_someone_elses_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """404, not 403 — confirming the id exists is the cross-tenant leak."""
        await _sign_up(client, "Agency A", "a@test.example")
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/auth/logout")

        await _sign_up(client, "Agency B", "b@test.example")
        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 404

    async def test_minting_requires_a_session(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Creating a public link is an agency decision, so it needs a caller."""
        await _sign_up(client, "Share Agency", "share3@test.example")
        sid = await _scored_scan(client, stub_engines)
        client.cookies.clear()

        assert (await client.post(f"{BASE}/scans/{sid}/share")).status_code == 401


class TestPublicRead:
    async def test_a_stranger_with_no_cookie_can_read_the_report(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The whole point of the epic: Epic 9's "send" acceptance criterion.

        The prospect a report is about has no account and must not need one.
        """
        await _sign_up(client, "Share Agency", "share4@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        client.cookies.clear()
        resp = await client.get(f"{BASE}/reports/{token}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scanId"] == sid
        # A complete report, not a teaser: every beat's material is present.
        assert body["score"]["status"] == "scored"
        assert body["dimensions"]
        assert body["proof"]["engineResults"] > 0
        assert body["agency"]["name"] == "Share Agency"

    async def test_the_public_report_is_byte_identical_to_the_authenticated_one(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """One projection, not two.

        `build_report` is reused rather than reimplemented, so the facts-only
        sweep in test_ip_safety.py covers this response too. A second assembly
        path would be a second place for a snippet to slip in. `generatedAt` is
        a clock read and is the only field allowed to differ.
        """
        await _sign_up(client, "Share Agency", "share5@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        private = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        client.cookies.clear()
        public = (await client.get(f"{BASE}/reports/{token}")).json()

        private.pop("generatedAt")
        public.pop("generatedAt")
        assert public == private

    async def test_the_public_response_carries_no_operator_or_account_surface(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Assert what CANNOT be there, sweeping the whole payload.

        Same principle as test_ip_safety.py's report sweep: a recursive walk
        over every key at every depth, so a field added to any nested schema
        later is caught rather than missed by a hand-picked list.
        """
        await _sign_up(client, "Share Agency", "share6@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()
        body = (await client.get(f"{BASE}/reports/{token}")).json()

        forbidden = {
            # who is behind the account
            "email", "password", "passwordHash", "fullName", "users", "user",
            "role", "seats", "seatLimit", "seatUsage", "invitations",
            # how to become them
            "session", "sessionToken", "token", "shareToken", "apiKey", "secret",
            # what an operator can do that a reader must not
            "rerun", "reRun", "canRerun", "canEdit", "editable", "actions",
        }

        seen: set[str] = set()

        def walk(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    seen.add(key)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(body)
        leaked = forbidden & seen
        assert not leaked, f"the public report exposes {sorted(leaked)}"
        # The sweep must actually have walked a real document, or it proves
        # nothing — a typo'd empty body would pass every assertion above.
        assert len(seen) > 30, f"only {len(seen)} keys walked; the sweep is not covering"
        assert "scanId" in seen and "dimensions" in seen

    async def test_the_token_does_not_come_back_in_the_public_report(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A report forwarded as a screenshot must not carry its own credential."""
        await _sign_up(client, "Share Agency", "share7@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()

        raw = (await client.get(f"{BASE}/reports/{token}")).text
        assert token not in raw


class TestRejectionIsIndistinguishable:
    """Every wrong token must look the same, or a guesser learns from guessing."""

    @pytest.mark.parametrize(
        "token",
        [
            "nope",                                    # too short
            "!!!not-base64url!!!",                     # wrong charset
            "",                                        # empty (falls to another route)
            "x" * 43,                                  # right shape, does not exist
            "x" * 4000,                                # absurdly long
            "../../../etc/passwd",                     # traversal
            "' OR 1=1 --",                             # injection shape
        ],
    )
    async def test_every_bad_token_is_a_404(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{BASE}/reports/{token}")
        # 404 for a miss; an empty token cannot match the route at all, which
        # is also a 404. Never 401, never 403, never 500.
        assert resp.status_code == 404, f"{token!r} returned {resp.status_code}"

    async def test_malformed_and_well_formed_misses_are_byte_identical(
        self, client: AsyncClient
    ) -> None:
        """No response-shape difference to separate "not a token" from "not found".

        A different body for a malformed token would confirm the charset and
        length of a real one, which is the expensive half of enumerating.
        """
        malformed = await client.get(f"{BASE}/reports/not-a-real-token")
        well_formed = await client.get(f"{BASE}/reports/{'A' * 43}")

        assert malformed.status_code == well_formed.status_code == 404
        assert malformed.json()["detail"] == well_formed.json()["detail"]
        assert malformed.json()["title"] == well_formed.json()["title"]
        assert malformed.json()["type"] == well_formed.json()["type"]

    async def test_an_unshared_scan_is_not_reachable_by_its_own_id(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The scan ULID must not double as a share token.

        It is already in the authenticated URL and in logs. If it worked here,
        every scan ever run would be publicly readable by anyone who had seen
        its id.
        """
        await _sign_up(client, "Share Agency", "share8@test.example")
        sid = await _scored_scan(client, stub_engines)
        client.cookies.clear()

        assert (await client.get(f"{BASE}/reports/{sid}")).status_code == 404

    async def test_a_scan_that_was_never_shared_has_no_live_link(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Tokens are minted on demand, so an unshared scan has none at all."""
        from sqlalchemy import select

        from avp_api.db import get_sessionmaker
        from avp_api.models import Scan

        await _sign_up(client, "Share Agency", "share9@test.example")
        sid = await _scored_scan(client, stub_engines)

        async with get_sessionmaker()() as s:
            scan = (await s.execute(select(Scan).where(Scan.id == sid))).scalar_one()
            assert scan.share_token is None, "a token was minted without being asked for"
