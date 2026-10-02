"""The public share link — Epic 9.8.

**This is the first unauthenticated read surface in the product.** Everything
else behind /api/v1 resolves a session cookie and scopes every query to one
agency; `POST /reports/shared` is reached by a stranger holding a URL. These
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
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from avp_api.models import Scan, ScanStatus
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import report_pdf as pdf_service
from avp_api.services import scan_runner
from avp_api.services import share as share_service
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"


async def _shared(client: AsyncClient, token: str):  # noqa: ANN202
    return await client.post(f"{BASE}/reports/shared", json={"token": token})


async def _shared_pdf(client: AsyncClient, token: str):  # noqa: ANN202
    return await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})


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
        """Idempotent while a link is live: the second press returns the link already made.

        Every extra token is a URL nobody is tracking, so a second click that
        minted a second token would quietly leave a live link nobody tracks. The
        end is the same too (`TestExpiry` holds that, checklist 3.28).
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

    async def test_two_presses_at_once_on_a_scan_with_no_link_make_one_link(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The row lock keeps a race to one token, and here the race is forced.

        Another connection holds the scan's row, so both presses have read the scan
        (no link yet) and are queued on the lock before either can write. That is the
        interleaving which, unlocked, mints two tokens and leaves the first caller
        holding a URL that no longer works. Then the row is let go. The first press
        through mints; the second must read that token off the locked row rather than
        act on the copy it read earlier, and must return it as it is: the same token
        and the same end, because a press on a live link writes nothing (3.28).
        """
        import asyncio

        from sqlalchemy import select

        await _sign_up(client, "Race Agency", "race1@test.example")
        sid = await _scored_scan(client, stub_engines)
        assert await _link_on_the_row(sid) == (None, None)

        await session.execute(select(Scan.id).where(Scan.id == sid).with_for_update())
        presses = [
            asyncio.create_task(client.post(f"{BASE}/scans/{sid}/share")) for _ in range(2)
        ]
        try:
            await _until_queued_on_a_row_lock(2)
        finally:
            await session.rollback()
            results = await asyncio.gather(*presses, return_exceptions=True)

        a, b = results
        assert not isinstance(a, BaseException), a
        assert not isinstance(b, BaseException), b
        assert a.status_code == b.status_code == 200, (a.text, b.text)
        assert a.json()["token"] == b.json()["token"]
        assert a.json()["expiresAt"] == b.json()["expiresAt"]
        assert await _link_on_the_row(sid) == (
            a.json()["token"],
            datetime.fromisoformat(a.json()["expiresAt"]),
        )

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
        resp = await client.post(f"{BASE}/reports/shared", json={"token": token})

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
        sweep in test_facts_only.py covers this response too. A second assembly
        path would be a second place for a snippet to slip in. Two fields may
        differ: `generatedAt`, a clock read, and `scanErrorCode`, which says why
        a scan is partial or failed and is for the agency alone (the next test
        pins that it is blanked, and the signed-in report keeps it). Since 3.25
        `shareExpiresAt`, when the agency's own link ends, is the same kind of
        field: test_share_expiry_on_report.py pins that it is blanked here.
        """
        await _sign_up(client, "Share Agency", "share5@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        private = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        client.cookies.clear()
        public = (await _shared(client, token)).json()

        assert private["shareExpiresAt"] is not None
        assert public["shareExpiresAt"] is None
        for differs in ("generatedAt", "scanErrorCode", "shareExpiresAt"):
            private.pop(differs)
            public.pop(differs)
        assert public == private

    async def test_a_partial_scan_s_public_report_keeps_its_status_and_drops_its_cause(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """checklist 3.15 — `scanErrorCode` is for the agency, not for a link holder.

        It says WHY a scan is partial or failed (`SCAN_DEADLINE_EXCEEDED`,
        `EXECUTOR_LOST`), which tells the agency whether the scan used up one of
        its scans and tells a stranger about our infrastructure, so the public
        route blanks it. The status stays: it has always been on the public
        report. The signed-in report keeps the code, and the web turns the pair
        into the sentence only on that screen.
        """
        await _sign_up(client, "Share Agency", "share5b@test.example")
        sid = await _scored_scan(client, stub_engines)
        await session.execute(
            sa_update(Scan).where(Scan.id == sid).values(
                status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED,
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        private = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        client.cookies.clear()
        resp = await _shared(client, token)

        assert resp.status_code == 200, resp.text
        public = resp.json()
        assert public["scanStatus"] == "partial"
        # Present and null, like every other absence on this payload.
        assert "scanErrorCode" in public
        assert public["scanErrorCode"] is None
        assert SCAN_DEADLINE_EXCEEDED not in resp.text
        # The agency's own view of the same scan still has it.
        assert private["scanStatus"] == "partial"
        assert private["scanErrorCode"] == SCAN_DEADLINE_EXCEEDED

    async def test_the_public_pdf_is_built_from_the_blanked_report_too(
        self, client: AsyncClient, session, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        """The share PDF builds a `ReportOut` of its own, and it is the public one.

        The renderer reads no scan status today, so this is about the day it
        does: the report handed to it by a link holder's route has no cause in
        it, and the agency's own PDF route hands it the same report with one.
        """
        handed: list = []

        def capture(report):  # noqa: ANN001, ANN202
            handed.append(report)
            return b"%PDF-1.4 stub"

        monkeypatch.setattr(pdf_service, "render_report_pdf", capture)
        await _sign_up(client, "Share Agency", "share5c@test.example")
        sid = await _scored_scan(client, stub_engines)
        await session.execute(
            sa_update(Scan).where(Scan.id == sid).values(
                status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED,
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        assert (await client.get(f"{BASE}/scans/{sid}/report.pdf")).status_code == 200
        client.cookies.clear()
        assert (await _shared_pdf(client, token)).status_code == 200

        signed_in, public = handed
        assert signed_in.scan_error_code == SCAN_DEADLINE_EXCEEDED
        assert public.scan_status == "partial"
        assert public.scan_error_code is None

    async def test_the_public_response_carries_no_operator_or_account_surface(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Assert what CANNOT be there, sweeping the whole payload.

        Same principle as test_facts_only.py's report sweep: a recursive walk
        over every key at every depth, so a field added to any nested schema
        later is caught rather than missed by a hand-picked list.
        """
        await _sign_up(client, "Share Agency", "share6@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()
        body = (await _shared(client, token)).json()

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

        raw = (await _shared(client, token)).text
        assert token not in raw


class TestRejectionIsIndistinguishable:
    """Every wrong token must look the same, or a guesser learns from guessing."""

    @pytest.mark.parametrize(
        "token",
        [
            "nope",                                    # too short
            "!!!not-base64url!!!",                     # wrong charset
            "x" * 43,                                  # right shape, does not exist
            "x" * 512,                                 # longest the body allows
            "../../../etc/passwd",                     # traversal
            "' OR 1=1 --",                             # injection shape
        ],
    )
    async def test_every_bad_token_is_a_404(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(f"{BASE}/reports/shared", json={"token": token})
        # 404 for a miss. Never 401, never 403, never 500. (Empty and over-512
        # tokens are the body's 422, tested in TestTheTokenIsNotInThePath.)
        assert resp.status_code == 404, f"{token!r} returned {resp.status_code}"

    async def test_malformed_and_well_formed_misses_are_byte_identical(
        self, client: AsyncClient
    ) -> None:
        """No response-shape difference to separate "not a token" from "not found".

        A different body for a malformed token would confirm the charset and
        length of a real one, which is the expensive half of enumerating.
        """
        malformed = await client.post(
            f"{BASE}/reports/shared", json={"token": "not-a-real-token"}
        )
        well_formed = await client.post(f"{BASE}/reports/shared", json={"token": "A" * 43})

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

        assert (await _shared(client, sid)).status_code == 404

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


async def _set_expiry(scan_id: str, at: datetime) -> None:
    """Put a link's end at a chosen instant: the row, not a patched clock — checklist 3.28."""
    from avp_api.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        await s.execute(sa_update(Scan).where(Scan.id == scan_id).values(share_expires_at=at))
        await s.commit()


async def _link_on_the_row(scan_id: str) -> tuple[str | None, datetime | None]:
    """The scan's share token and its end, read fresh from the database."""
    from sqlalchemy import select

    from avp_api.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                select(Scan.share_token, Scan.share_expires_at).where(Scan.id == scan_id)
            )
        ).one()
    return row.share_token, row.share_expires_at


async def _until_queued_on_a_row_lock(queued: int, *, seconds: float = 10.0) -> None:
    """Wait until `queued` statements in this database are waiting on a row lock.

    Asks `pg_stat_activity` from a connection of its own, so the lock the caller is
    holding is not disturbed. The query text is matched on `FOR UPDATE`, which is
    how `share.get_or_create_share_token` takes the scan.
    """
    import asyncio

    from sqlalchemy import text as sa_text

    from avp_api.db import get_sessionmaker

    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        async with get_sessionmaker()() as s:
            waiting = (
                await s.execute(
                    sa_text(
                        "SELECT count(*) FROM pg_stat_activity"
                        " WHERE datname = current_database()"
                        " AND pid <> pg_backend_pid()"
                        " AND wait_event_type = 'Lock'"
                        " AND query ILIKE '%for update%'"
                    )
                )
            ).scalar_one()
        if waiting >= queued:
            return
        if loop.time() > deadline:
            pytest.fail(f"{waiting} of {queued} presses were waiting on the scan's row lock")
        await asyncio.sleep(0.05)


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
        assert (await _shared(client, token)).status_code == 200
        assert (await _shared_pdf(client, token)).status_code == 200

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
        assert (await _shared(client, token)).status_code == 404
        assert (await _shared_pdf(client, token)).status_code == 404

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
        revoked = await client.post(f"{BASE}/reports/shared", json={"token": token})
        never = await client.post(f"{BASE}/reports/shared", json={"token": "A" * 43})

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
        assert (await _shared(client, token)).status_code == 200

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
        """After a withdrawal no link is live, so a press makes a new one — checklist 3.28.

        A new token with a fresh 30 days, and the address that was taken down stays
        down. Withdrawing and pressing again is therefore also how an agency gets a
        link with a new end, and it breaks every copy already sent.
        """
        await _sign_up(client, "Revoke Agency", "revoke7@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await client.delete(f"{BASE}/scans/{sid}/share")
        again = (await client.post(f"{BASE}/scans/{sid}/share")).json()
        second = again["token"]

        assert first != second
        remaining = datetime.fromisoformat(again["expiresAt"]) - datetime.now(UTC)
        assert timedelta(days=29) < remaining <= share_service.SHARE_TTL
        client.cookies.clear()
        assert (await _shared(client, first)).status_code == 404
        assert (await _shared(client, second)).status_code == 200


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
        assert (await _shared(client, token)).status_code == 200

        await _expire(sid)

        assert (await _shared(client, token)).status_code == 404
        assert (await _shared_pdf(client, token)).status_code == 404

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
        expired = await client.post(f"{BASE}/reports/shared", json={"token": token})
        never = await client.post(f"{BASE}/reports/shared", json={"token": "A" * 43})

        assert expired.status_code == never.status_code == 404
        # Every field except `instance`, which is the request path echoed back
        # — the caller's own input, so it tells them nothing they did not send.
        # The same comparison `test_malformed_and_well_formed_misses_...` makes.
        assert _problem(never) == _problem(expired)

    async def test_pressing_after_the_link_expired_makes_a_new_link_and_the_old_address_stays_dead(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """An ended link is not live, so a press makes a new one — checklist 3.28.

        This was `test_re_sharing_extends_an_almost_dead_link_without_changing_its_url`:
        a press on an expired link used to move the end of the SAME token, so the
        address that had stopped working opened the report again. A link's end is
        final now. Keeping a report going past it takes a new link at a new address,
        and the one that ended stays ended on both public routes.
        """
        await _sign_up(client, "Expiry Agency", "expiry4@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()
        await _expire(sid)

        client.cookies.clear()
        assert (await _shared(client, first["token"])).status_code == 404

        await client.post(
            f"{BASE}/auth/login",
            json={"email": "expiry4@test.example", "password": "correct-horse-battery-staple"},
        )
        second = (await client.post(f"{BASE}/scans/{sid}/share")).json()

        assert second["token"] != first["token"], "an address that has ended must not come back"
        assert second["url"] != first["url"]
        ends = datetime.fromisoformat(second["expiresAt"])
        assert timedelta(days=29) < ends - datetime.now(UTC) <= share_service.SHARE_TTL
        # The row holds the new link and nothing of the old one.
        assert await _link_on_the_row(sid) == (second["token"], ends)
        client.cookies.clear()
        assert (await _shared(client, first["token"])).status_code == 404
        assert (await _shared_pdf(client, first["token"])).status_code == 404
        assert (await _shared(client, second["token"])).status_code == 200
        assert (await _shared_pdf(client, second["token"])).status_code == 200

    async def test_pressing_on_a_live_link_returns_it_exactly_as_it_is(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Same token, same URL, same end, and nothing is written — checklist 3.28.

        The founder's decision, replacing Epic 9.21's refresh: what the share bar
        says ("The link works until ...") stays true of the link that was sent. The
        link is put a day from its end first, as an agency re-sending on day 29 would
        find it. A press that moved the end would show as a date a month out, where
        the day left cannot be mistaken for it, however fast the two presses are.
        """
        await _sign_up(client, "Expiry Agency", "expiry6@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()
        a_day_left = datetime.now(UTC) + timedelta(days=1)
        await _set_expiry(sid, a_day_left)
        before = await _link_on_the_row(sid)
        assert before == (first["token"], a_day_left)

        presses = [await client.post(f"{BASE}/scans/{sid}/share") for _ in range(2)]

        for press in presses:
            assert press.status_code == 200, press.text
            link = press.json()
            assert link["token"] == first["token"]
            assert link["url"] == first["url"]
            assert datetime.fromisoformat(link["expiresAt"]) == a_day_left
        # The row is as it was before the presses: no write moved the end.
        assert await _link_on_the_row(sid) == before
        # What the bar reads on the next page load says the same end.
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert report["shareExpiresAt"] == presses[1].json()["expiresAt"]
        # And the link was never off: a stranger opens it.
        client.cookies.clear()
        assert (await _shared(client, first["token"])).status_code == 200
        assert (await _shared_pdf(client, first["token"])).status_code == 200

    async def test_the_press_and_the_public_lookup_draw_the_line_at_the_same_microsecond(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """An end exactly at `now` is over; one microsecond later it is live — checklist 3.28.

        The rule is stated twice: in Python for the press (`share.link_is_live`) and
        in SQL for the public lookup (`scan_for_share_token`). They have to agree on
        the edge, or a link would be live to one and over to the other: handed back by
        a press that the lookup refuses, or replaced by a press while the lookup still
        serves it. The clock is passed in, so the test stands exactly on the edge
        instead of racing it.
        """
        await _sign_up(client, "Edge Agency", "edge1@test.example")
        sid = await _scored_scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        scan = await session.get(Scan, sid)
        now = datetime(2031, 6, 1, 12, 0, 0, tzinfo=UTC)

        # The end is exactly `now`: over. The lookup refuses it, and a press makes a NEW link.
        await _set_expiry(sid, now)
        assert await share_service.scan_for_share_token(session, first, now=now) is None
        token, ends = await share_service.get_or_create_share_token(session, scan, now=now)
        await session.commit()
        assert token != first
        assert ends == now + share_service.SHARE_TTL
        assert await _link_on_the_row(sid) == (token, ends)
        assert await share_service.scan_for_share_token(session, first, now=now) is None
        assert await share_service.scan_for_share_token(session, token, now=now) is not None

        # The end is `now` plus one microsecond: live. The lookup still serves it, and a
        # press returns it exactly as it is.
        edge = now + timedelta(microseconds=1)
        await _set_expiry(sid, edge)
        assert await share_service.scan_for_share_token(session, token, now=now) is not None
        pressed = await share_service.get_or_create_share_token(session, scan, now=now)
        await session.commit()
        assert pressed == (token, edge)
        assert await _link_on_the_row(sid) == (token, edge)

    def test_link_is_live_needs_a_token_and_an_end_strictly_after_now(self) -> None:
        """The one Python statement of the rule, as a table — checklist 3.28.

        It is what the press and the signed-in report's `shareExpiresAt` both ask. A
        token with no end, or an end with no token, cannot be written (the CHECK on
        `scans`), so this is the only place those shapes are asserted: neither is live.
        """
        now = datetime(2031, 6, 1, 12, 0, 0, tzinfo=UTC)
        microsecond = timedelta(microseconds=1)
        live = share_service.link_is_live

        assert live("token", now + microsecond, now) is True
        assert live("token", now, now) is False
        assert live("token", now - microsecond, now) is False
        assert live("token", None, now) is False
        assert live(None, now + timedelta(days=1), now) is False
        assert live(None, None, now) is False

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
            assert (await _shared(client, token)).status_code == 404
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

    async def test_a_scan_reaped_while_it_is_shared_keeps_its_token_and_its_link_stops_opening(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A shared link does not pin a scan against the reaper, and a reaped scan's link is dead.

        Sharing writes `share_token`; reaping reads `lease_expires_at` and
        writes `status`. Neither predicate mentions the other's columns, so a
        report can be shared and its executor still be found dead.

        What the link does afterwards changed with checklist 3.29. This test used
        to end "and the link still works: reaping is a statement about the
        executor, not about whether the report may be read". A FAILED scan's link
        now opens nothing, the way a withdrawn one does; the token stays on the
        row, so the agency can still withdraw it.
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
            # The token is kept, so the agency can still take the link down.
            assert scan.share_token == token

        # The link opens nothing now, and says what a token that never existed says.
        client.cookies.clear()
        refused = await _shared(client, token)
        never = await _shared(client, "A" * 43)
        assert refused.status_code == never.status_code == 404
        assert _problem(refused) == _problem(never)
        assert (await _shared_pdf(client, token)).status_code == 404

        await client.post(
            f"{BASE}/auth/login",
            json={"email": "leaseshare3@test.example", "password": "correct-horse-battery-staple"},
        )
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204


class TestTheTokenIsNotInThePath:
    """The share token travels in a POST body, so it never reaches a request log."""

    async def test_the_minted_url_carries_the_token_in_the_fragment(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Frag Agency", "frag@test.example")
        sid = await _scored_scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/share")).json()

        before, hash_, after = body["url"].partition("#")
        assert hash_ == "#"
        assert after == body["token"]
        assert before.endswith("/share")
        assert body["token"] not in before

    async def test_the_old_get_routes_are_gone(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Old Agency", "oldget@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()

        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 404
        assert (await client.get(f"{BASE}/reports/{token}.pdf")).status_code == 404
        # The new paths answer only POST.
        assert (await client.get(f"{BASE}/reports/shared")).status_code == 405
        assert (await client.get(f"{BASE}/reports/shared/pdf")).status_code == 405

    @pytest.mark.parametrize("route", ["/reports/shared", "/reports/shared/pdf"])
    @pytest.mark.parametrize(
        "body",
        [{}, {"token": ""}, {"token": "A" * 513}, {"token": None}, {"token": 5}, {"tok": "x"}],
    )
    async def test_a_missing_empty_or_over_long_token_is_422(
        self, client: AsyncClient, route: str, body: dict
    ) -> None:
        resp = await client.post(f"{BASE}{route}", json=body)
        assert resp.status_code == 422, resp.text

    async def test_the_longest_allowed_token_is_an_ordinary_404(
        self, client: AsyncClient
    ) -> None:
        for route in ("/reports/shared", "/reports/shared/pdf"):
            resp = await client.post(f"{BASE}{route}", json={"token": "A" * 512})
            assert resp.status_code == 404

    async def test_both_routes_are_uncacheable_and_the_pdf_keeps_its_headers(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "Hdr Agency", "hdr@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()

        web = await client.post(f"{BASE}/reports/shared", json={"token": token})
        pdf = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
        assert web.status_code == pdf.status_code == 200
        assert web.headers["cache-control"] == "private, no-store"
        assert pdf.headers["cache-control"] == "private, no-store"
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["content-disposition"].startswith('attachment; filename="')
        assert pdf.content.startswith(b"%PDF")

    async def test_the_token_never_appears_in_a_log_line(
        self, client: AsyncClient, stub_engines, capsys, caplog  # noqa: ANN001
    ) -> None:
        import json

        import structlog

        await _sign_up(client, "Log Agency", "logshare@test.example")
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()
        capsys.readouterr()
        caplog.clear()
        caplog.set_level("DEBUG")

        with structlog.testing.capture_logs() as events:
            for route in ("/reports/shared", "/reports/shared/pdf"):
                assert (
                    await client.post(f"{BASE}{route}", json={"token": token})
                ).status_code == 200
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        assert token not in everything

