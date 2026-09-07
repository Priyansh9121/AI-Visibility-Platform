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

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services import share as share_service
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


def _problem(resp) -> dict:  # noqa: ANN001
    """An RFC 7807 body minus `instance`, which is the request path echoed back."""
    return {k: v for k, v in resp.json().items() if k != "instance"}


async def _expire(scan_id: str) -> None:
    """Age a live link out, as the clock would — Epic 9.21.

    Writes the column rather than sleeping or monkeypatching the TTL: expiry is
    a property of the row the read path reads, and moving the row's own value
    is the smallest thing that makes the assertion true for the right reason.
    """
    from sqlalchemy import update

    from avp_api.db import get_sessionmaker
    from avp_api.models import Scan

    async with get_sessionmaker()() as s:
        await s.execute(
            update(Scan)
            .where(Scan.id == scan_id)
            .values(share_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()


class TestRevocation:
    """Taking a link back — Epic 9.21.

    `models/scan.py` carried the gap as a named one: "the first agency that
    shares a report with the wrong prospect has no way to take it back." These
    tests are about what stops being reachable, which is the whole feature.
    """

    async def test_a_revoked_link_is_dead_on_both_read_routes(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """JSON and PDF, because a half-revoked link is worse than none.

        The two routes share one lookup precisely so this cannot drift; the
        test asserts the shared behaviour rather than trusting the sharing.
        """
        await _sign_up(client, "Revoke Agency", "revoke1@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 200
        assert (await client.get(f"{BASE}/reports/{token}.pdf")).status_code == 200

        await _sign_up(client, "Revoke Agency", "revoke1b@test.example")
        # A fresh agency cannot revoke it; the owner must sign back in.
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 404
        await client.post(f"{BASE}/auth/logout")
        await client.post(
            f"{BASE}/auth/login",
            json={"email": "revoke1@test.example", "password": "correct-horse-battery-staple"},
        )

        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204

        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 404
        assert (await client.get(f"{BASE}/reports/{token}.pdf")).status_code == 404

    async def test_a_revoked_token_is_indistinguishable_from_one_that_never_existed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Revocation is not its own kind of wrong — it is the same 404.

        A distinct body or status would confirm to a holder that their token
        WAS real, which is the bit an enumerator wants and the reason the rule
        lives in one lookup rather than in either route.
        """
        await _sign_up(client, "Revoke Agency", "revoke2@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204

        client.cookies.clear()
        revoked = await client.get(f"{BASE}/reports/{token}")
        never = await client.get(f"{BASE}/reports/{'A' * 43}")

        assert revoked.status_code == never.status_code == 404
        # Every field except `instance`, which is the request path echoed back
        # — the caller's own input, so it tells them nothing they did not send.
        # The same comparison `test_malformed_and_well_formed_misses_...` makes.
        assert _problem(never) == _problem(revoked)

    async def test_revoking_twice_is_not_an_error(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The caller asked for it not to be readable, and it is not.

        A 404 or a 409 on the second call would report on a state they did not
        ask about and invite a retry loop over something already correct.
        """
        await _sign_up(client, "Revoke Agency", "revoke3@test.example")
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")

        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204

    async def test_revoking_a_scan_that_was_never_shared_is_not_an_error(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Revoke Agency", "revoke4@test.example")
        sid = await _scored_scan(client, stub_engines)

        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204

    async def test_another_agency_cannot_revoke_someone_elses_link(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """404, not 403 — the same cross-tenant rule as the mint beside it.

        And the link must still work afterwards: a failed revoke that half
        succeeded would be worse than one that refused outright.
        """
        await _sign_up(client, "Agency A", "revoke5a@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await client.post(f"{BASE}/auth/logout")

        await _sign_up(client, "Agency B", "revoke5b@test.example")
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 404

        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 200

    async def test_revoking_requires_a_session(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Revoke Agency", "revoke6@test.example")
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")
        client.cookies.clear()

        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 401

    async def test_a_link_minted_after_a_revoke_is_a_new_one_and_the_old_stays_dead(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Re-sharing issues a new capability, which is why re-mint refreshes.

        This is the asymmetry that decided it: revoke-and-mint is the only
        other way to extend a link, and it breaks every copy already sent.
        """
        await _sign_up(client, "Revoke Agency", "revoke7@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await client.delete(f"{BASE}/scans/{sid}/share")
        second = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        assert first != second
        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{first}")).status_code == 404
        assert (await client.get(f"{BASE}/reports/{second}")).status_code == 200


class TestExpiry:
    """The link nobody remembers sending — Epic 9.21.

    Revocation needs somebody to decide. Expiry is for the larger population no
    UI will ever catch, because forgetting is the failure mode.
    """

    async def test_a_mint_returns_when_the_link_stops_working(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The operator is about to paste this URL somewhere permanent.

        The one thing they cannot read off the URL is its lifetime, so the
        response carries it.
        """
        await _sign_up(client, "Expiry Agency", "expiry1@test.example")
        sid = await _scored_scan(client, stub_engines)

        body = (await client.post(f"{BASE}/scans/{sid}/share")).json()

        expires_at = datetime.fromisoformat(body["expiresAt"])
        remaining = expires_at - datetime.now(UTC)
        assert timedelta(days=29) < remaining <= share_service.SHARE_TTL
        assert timedelta(days=30) == share_service.SHARE_TTL

    async def test_an_expired_link_is_dead_on_both_read_routes(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Expiry Agency", "expiry2@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 200

        await _expire(sid)

        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 404
        assert (await client.get(f"{BASE}/reports/{token}.pdf")).status_code == 404

    async def test_an_expired_token_is_indistinguishable_from_an_unknown_one(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Same rule as revocation, and for the same reason.

        "Expired" would tell a holder the token was real and that guessing the
        namespace is worthwhile.
        """
        await _sign_up(client, "Expiry Agency", "expiry3@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await _expire(sid)

        client.cookies.clear()
        expired = await client.get(f"{BASE}/reports/{token}")
        never = await client.get(f"{BASE}/reports/{'A' * 43}")

        assert expired.status_code == never.status_code == 404
        # Every field except `instance`, which is the request path echoed back
        # — the caller's own input, so it tells them nothing they did not send.
        # The same comparison `test_malformed_and_well_formed_misses_...` makes.
        assert _problem(never) == _problem(expired)

    async def test_re_sharing_extends_an_almost_dead_link_without_changing_its_url(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The whole argument for refresh-on-re-mint, as a test.

        Without it an agency re-sending on day 29 would have to revoke and
        re-mint, which issues a different URL and breaks the copy they are
        re-sending. The token is stable; the clock is not.
        """
        await _sign_up(client, "Expiry Agency", "expiry4@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()
        await _expire(sid)

        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{first['token']}")).status_code == 404

        await client.post(
            f"{BASE}/auth/login",
            json={"email": "expiry4@test.example", "password": "correct-horse-battery-staple"},
        )
        second = (await client.post(f"{BASE}/scans/{sid}/share")).json()

        assert second["token"] == first["token"], "the URL already sent must survive"
        assert second["url"] == first["url"]
        assert datetime.fromisoformat(second["expiresAt"]) > datetime.fromisoformat(
            first["expiresAt"]
        )
        client.cookies.clear()
        assert (await client.get(f"{BASE}/reports/{first['token']}")).status_code == 200

    async def test_a_token_without_an_expiry_serves_nothing(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Fail-closed, for a credential.

        The CHECK constraint on `scans` makes this state unwritable through the
        ORM, so the row is forced past it to prove the READ path does not treat
        a missing expiry as "never expires". Both guards, independently.
        """
        from sqlalchemy import text as sa_text

        from avp_api.db import get_sessionmaker

        await _sign_up(client, "Expiry Agency", "expiry5@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        async with get_sessionmaker()() as s:
            with pytest.raises(IntegrityError):
                await s.execute(
                    sa_text("UPDATE scans SET share_expires_at = NULL WHERE id = :i"),
                    {"i": sid},
                )
                await s.commit()
            await s.rollback()
            await s.execute(
                sa_text(
                    "ALTER TABLE scans DROP CONSTRAINT"
                    " ck_scans_share_token_and_expiry_together"
                )
            )
            await s.execute(
                sa_text("UPDATE scans SET share_expires_at = NULL WHERE id = :i"),
                {"i": sid},
            )
            await s.commit()

        client.cookies.clear()
        try:
            assert (await client.get(f"{BASE}/reports/{token}")).status_code == 404
        finally:
            async with get_sessionmaker()() as s:
                await s.execute(
                    sa_text("UPDATE scans SET share_token = NULL WHERE id = :i"), {"i": sid}
                )
                await s.execute(
                    sa_text(
                        "ALTER TABLE scans ADD CONSTRAINT"
                        " ck_scans_share_token_and_expiry_together"
                        " CHECK ((share_token IS NULL) = (share_expires_at IS NULL))"
                    )
                )
                await s.commit()


class TestSharingAndTheLeaseShareOneRow:
    """Share/revoke and the lease both write `scans`, and both take the row.

    `get_or_create_share_token` and `revoke_share_token` hold
    `SELECT ... FOR UPDATE` on the scan row; the lease's claim, heartbeat and
    reaper all UPDATE the same row on their own sessions, on a clock. Raised as
    a lock-ordering question when Epic 9.22 shipped and answered here rather
    than assumed away, because the premise turned out to be true: **nothing
    stops a RUNNING scan from being shared.** The share routes have no status
    guard, deliberately — an operator watching a scan run can send the link
    before it finishes.

    What makes the overlap benign is that every writer takes exactly ONE
    resource and holds it across a flush and a commit with no network call in
    between, so the worst case is serialisation measured in milliseconds and a
    deadlock has no second resource to form a cycle with. `services/share.py`
    states the invariant that keeps it that way.
    """

    async def _running_scan(self, client: AsyncClient, stub_engines) -> str:  # noqa: ANN001
        """A scan left RUNNING with a live lease, as an executor would hold it."""
        from datetime import timedelta

        from sqlalchemy import update as sa_update

        from avp_api.db import get_sessionmaker
        from avp_api.models import Scan, ScanStatus

        sid = await _scored_scan(client, stub_engines)
        async with get_sessionmaker()() as s:
            await s.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(
                    status=ScanStatus.RUNNING,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=180),
                )
            )
            await s.commit()
        return sid

    async def test_a_running_scan_can_be_shared_while_its_lease_is_renewed(
        self, client: AsyncClient, stub_engines, settings
    ) -> None:  # noqa: ANN001
        """Both writers touch the row at once. Both must finish.

        Genuinely concurrent rather than sequential: a test that ran them one
        after the other would pass against a design that deadlocks.
        """
        import asyncio

        from avp_api.db import get_sessionmaker
        from avp_api.models import Scan
        from avp_api.services.scan_executor import renew_lease

        await _sign_up(client, "Lease Share Agency", "leaseshare1@test.example")
        sid = await self._running_scan(client, stub_engines)

        share, renewed = await asyncio.gather(
            client.post(f"{BASE}/scans/{sid}/share"),
            renew_lease(sid, settings=settings),
        )

        assert share.status_code == 200, share.text
        assert renewed is True, "the heartbeat could not renew past the share lock"

        # Both writes landed: they touch different column families on one row.
        async with get_sessionmaker()() as s:
            scan = await s.get(Scan, sid)
            assert scan.share_token == share.json()["token"]
            assert scan.lease_expires_at is not None

    async def test_revoking_and_renewing_at_once_deadlocks_neither(
        self, client: AsyncClient, stub_engines, settings
    ) -> None:  # noqa: ANN001
        """The other lock site, and the one an agency reaches under pressure.

        Revocation is what somebody does the moment they realise a link went to
        the wrong address, which is exactly when the scan behind it may still
        be running.
        """
        import asyncio

        from avp_api.db import get_sessionmaker
        from avp_api.models import Scan
        from avp_api.services.scan_executor import renew_lease

        await _sign_up(client, "Lease Share Agency", "leaseshare2@test.example")
        sid = await self._running_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")

        revoke, renewed = await asyncio.gather(
            client.delete(f"{BASE}/scans/{sid}/share"),
            renew_lease(sid, settings=settings),
        )

        assert revoke.status_code == 204
        assert renewed is True

        async with get_sessionmaker()() as s:
            scan = await s.get(Scan, sid)
            assert scan.share_token is None
            assert scan.share_expires_at is None
            # The lease is untouched by revocation: different column family,
            # and an executor must not lose its claim because somebody
            # un-shared the report it is still producing.
            assert scan.lease_expires_at is not None

    async def test_the_reaper_can_still_take_a_scan_that_is_being_shared(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A shared link does not pin a scan against the reaper.

        Sharing writes `share_token`; reaping reads `lease_expires_at` and
        writes `status`. Neither predicate mentions the other's columns, so a
        report can be shared and its executor still be found dead.
        """
        from datetime import timedelta

        from sqlalchemy import update as sa_update

        from avp_api.db import get_sessionmaker
        from avp_api.models import Scan, ScanStatus
        from avp_api.services.scan_executor import EXECUTOR_LOST, reap_stale_scans

        await _sign_up(client, "Lease Share Agency", "leaseshare3@test.example")
        sid = await self._running_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        async with get_sessionmaker()() as s:
            await s.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await s.commit()
            assert await reap_stale_scans(s) == 1
            await s.commit()

            scan = await s.get(Scan, sid)
            await s.refresh(scan)
            assert scan.status is ScanStatus.FAILED
            assert scan.error_code == EXECUTOR_LOST
            # And the link still works: reaping is a statement about the
            # executor, not about whether the report may be read.
            assert scan.share_token == token
