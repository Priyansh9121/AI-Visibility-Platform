"""A FAILED scan has no score, cannot be shared, and the link it already has opens nothing.

Checklist 3.24, founder decision: "Failed scans cannot be shared (API refuses
minting with a specific problem type); a failed scan's score is never shown or
counted on the dashboard or report. Partial scans stay shareable."

Checklist 3.29, founder decision (final): "The public report (JSON, page and PDF)
refuses a share link whose scan is FAILED, the same way it treats a withdrawn or
expired link (same status code, same dead-link page text). Do not delete the
token (the agency can still withdraw it)." A link minted while a scan was running
used to keep serving after the scan failed, without a score; `TestAFailedScansLinkIsDead`
and the one test above it that used to pin that now pin the refusal.

A scan whose deadline came after scoring is FAILED but keeps its Score row.
Scoring never re-scores history, so the row stays; these tests pin that it is
only not READ.
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from avp_api.errors import FailedScanNotShareable
from avp_api.models import Scan, ScanStatus, Score
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services import share as share_service
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "failedscan@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Scan Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
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
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler and well liked.",
                    citations=(
                        [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                        if engine is Engine.CLAUDE_SEARCH
                        else []
                    ),
                    latency_ms=5,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _scored_scan(client: AsyncClient, stub_engines) -> str:  # noqa: ANN001
    cid = (
        await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    ).json()["id"]
    stub_engines(n_prompts=4)
    sid = (
        await client.post(
            f"{BASE}/clients/{cid}/scans",
            json={"engines": ["claude", "claude_search", "chatgpt"]},
        )
    ).json()["id"]
    assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
    return sid


def _visible_text(pdf: bytes) -> str:
    """What the PDF draws: its content streams are compressed."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            out.append(literal.group(1).decode("latin-1"))
    return "\n".join(out)


async def _end(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    await session.execute(
        sa_update(Scan)
        .where(Scan.id == sid)
        .values(
            status=status,
            error_code=SCAN_DEADLINE_EXCEEDED,
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()


async def _give_the_audit_a_foundation_score(session, sid: str) -> None:  # noqa: ANN001
    """The scan's own audit, with a Technical Foundation value to be blanked."""
    from avp_api.models.technical_audit import TechnicalAudit

    audit = (
        await session.execute(select(TechnicalAudit).where(TechnicalAudit.scan_id == sid))
    ).scalar_one()
    audit.technical_foundation = Decimal("72.50")
    await session.commit()


async def _score_rows(session, sid: str) -> int:  # noqa: ANN001
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Score).where(Score.scan_id == sid)
            )
        ).scalar_one()
    )


async def _stranger_opens(client: AsyncClient, route: str, token: str) -> httpx.Response:
    """A public share route as its reader calls it: with no session cookie.

    The agency's cookie is put back afterwards, so a test can go on to withdraw.
    """
    saved = httpx.Cookies(client.cookies)
    client.cookies.clear()
    try:
        return await client.post(f"{BASE}{route}", json={"token": token})
    finally:
        client.cookies.update(saved)


async def _stranger_opens_both(
    client: AsyncClient, token: str
) -> tuple[httpx.Response, httpx.Response]:
    """The JSON report and the PDF, by the same token."""
    return (
        await _stranger_opens(client, "/reports/shared", token),
        await _stranger_opens(client, "/reports/shared/pdf", token),
    )


def _assert_dead_link_answer(resp: httpx.Response) -> None:
    """The one answer every dead link gets: a 404 `not-found` naming no reason."""
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["type"] == "/problems/not-found"
    assert body["detail"] == "No report with that link."
    # The route's own path, which has no token in it: the PDF route's is `.../shared/pdf`.
    assert body["instance"] == resp.request.url.path


# Headers that are per-request by nature, which two refusals of one kind cannot be
# expected to agree on. Every other header must match.
_PER_REQUEST_HEADERS = frozenset({"date", "x-request-id"})


def _comparable_headers(resp: httpx.Response) -> dict[str, str]:
    """A response's whole header set, less the per-request ones.

    `content-length` stays in: the bodies are asserted equal as well, so it must agree.
    """
    return {
        name.lower(): value
        for name, value in resp.headers.items()
        if name.lower() not in _PER_REQUEST_HEADERS
    }


class TestSharing:
    async def test_minting_for_a_failed_scan_is_refused_with_its_own_problem_type(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["type"] == "/problems/failed-scan-not-shareable"
        assert body["detail"] == "This scan failed, so its report cannot be shared."
        assert "token" not in body
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.share_token is None

    async def test_another_agencys_failed_scan_is_a_404_not_a_409(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The tenancy check runs before the status check: a 409 would confirm the id exists."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)
        client.cookies.clear()
        await _sign_up(client, "otheragency@test.example")

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "/problems/not-found"

    async def test_a_partial_scan_still_mints(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.PARTIAL)

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 200, resp.text
        assert resp.json()["token"]

    async def test_a_succeeded_scan_still_mints(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{sid}/share")).status_code == 200


class TestAScanThatFailsWhileItIsBeingShared:
    """3.29 review: the lock re-reads the status, so a scan that fails in flight gets no link.

    `create_share_link` reads the scan, lets it through if it has not failed, and only
    then takes the row lock inside `get_or_create_share_token`. A scan failed by the
    reaper or a deadline in that gap used to get a link, or be handed a live one that
    no longer opens the report, from a request that had read it a moment before. The
    service now looks at the locked row's own status and raises the route's own 409
    before it writes anything, and before it hands back a link that is live.
    """

    @pytest.mark.parametrize(
        "already_shared", [False, True], ids=["first link", "link already live"]
    )
    async def test_a_scan_that_fails_after_the_routes_read_is_refused_and_nothing_is_written(
        self,
        client: AsyncClient,
        session,
        stub_engines,
        monkeypatch,
        already_shared: bool,
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        if already_shared:
            assert (await client.post(f"{BASE}/scans/{sid}/share")).status_code == 200
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        before = (scan.share_token, scan.share_expires_at)
        assert (before[0] is not None) is already_shared

        real = share_service.get_or_create_share_token
        seen_by_the_route: list[ScanStatus] = []

        async def fails_after_the_routes_read(db, scan_):  # noqa: ANN001, ANN202
            # The route has read this scan and let it through...
            seen_by_the_route.append(scan_.status)
            # ...and it fails now, on another connection, before the lock is taken.
            await _end(session, sid, ScanStatus.FAILED)
            return await real(db, scan_)

        monkeypatch.setattr(share_service, "get_or_create_share_token", fails_after_the_routes_read)

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        # Not vacuous: the route's own check passed a scan that had not failed.
        assert seen_by_the_route
        assert ScanStatus.FAILED not in seen_by_the_route
        # The guard: the FAILED refusal comes before the live-link shortcut. With a link
        # already live, that shortcut would answer 200 and hand its token back, so the
        # 409, and no `token` in the body, are what show the refusal is made first.
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["type"] == "/problems/failed-scan-not-shareable"
        assert body["detail"] == "This scan failed, so its report cannot be shared."
        assert "token" not in body
        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        # And nothing was written: no link minted, and a live one left as it was.
        assert (scan.share_token, scan.share_expires_at) == before

    async def test_the_service_looks_at_the_locked_row_not_at_the_scan_it_was_handed(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stale = await session.get(Scan, sid)
        # Cut loose from the session so that nothing which follows can refresh it: this
        # is the scan as a request read it before the lock.
        session.expunge(stale)
        await _end(session, sid, ScanStatus.FAILED)
        assert stale.status is not ScanStatus.FAILED

        with pytest.raises(FailedScanNotShareable) as refused:
            await share_service.get_or_create_share_token(session, stale)

        assert refused.value.detail == "This scan failed, so its report cannot be shared."
        await session.rollback()
        row = await session.get(Scan, sid)
        await session.refresh(row)
        assert row.status is ScanStatus.FAILED
        assert row.share_token is None
        assert row.share_expires_at is None


class TestTheScoreIsNotShown:
    async def test_the_dashboard_shows_no_composite_and_does_not_count_a_failed_scan(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        before = (await client.get(f"{BASE}/dashboard")).json()
        assert before["scoredScanCount"] == 1
        assert before["recentScans"][0]["compositeScore"] is not None

        await _end(session, sid, ScanStatus.FAILED)
        body = (await client.get(f"{BASE}/dashboard")).json()

        entry = next(s for s in body["recentScans"] if s["id"] == sid)
        assert entry["status"] == "failed"
        assert entry["compositeScore"] is None
        assert body["scoredScanCount"] == 0
        # The row is not deleted or changed: it is only not read.
        assert await _score_rows(session, sid) == 1

    async def test_a_partial_scans_score_is_still_shown_and_counted(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.PARTIAL)

        body = (await client.get(f"{BASE}/dashboard")).json()
        assert body["scoredScanCount"] == 1
        assert body["recentScans"][0]["compositeScore"] is not None

    async def test_the_report_of_a_failed_scan_carries_no_score(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _give_the_audit_a_foundation_score(session, sid)
        shown = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        # Not vacuous: everything the failed report must blank is present first.
        assert shown["score"] is not None
        assert shown["dimensions"]
        assert shown["audit"]["technicalFoundation"] is not None
        before_items = shown["actionItems"]
        assert any(i["pointsUpside"] is not None for i in before_items)
        rivals = shown["competitorSet"]["competitors"]
        assert rivals
        assert any(r["mentionRate"] is not None for r in rivals)

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.get(f"{BASE}/scans/{sid}/report")

        assert resp.status_code == 200
        body = resp.json()
        assert body["scanStatus"] == "failed"
        assert body["score"] is None
        assert body["dimensions"] == []
        # Score-derived numbers go with it: the sub-score. So do the fixes the generator
        # wrote from the score (3.30; `test_failed_scan_fixes_and_audit.py`): the list is
        # empty, not the same fixes with their points blanked, and the fix beat words
        # itself from its own copy.
        assert body["audit"] is not None
        assert body["audit"]["technicalFoundation"] is None
        assert body["actionItems"] == []
        for rival in body["competitorSet"]["competitors"]:
            assert rival["mentionRate"] is None
            assert rival["shareOfVoice"] is None
        assert await _score_rows(session, sid) == 1

    async def test_the_pdf_of_a_failed_scan_prints_no_composite(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)

        resp = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert resp.status_code == 200
        text = _visible_text(resp.content)
        assert "This scan failed, so it has no score." in text
        assert "/ 100" not in text

    async def test_an_already_shared_scan_that_then_fails_is_refused_like_a_withdrawn_link(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """3.29: a link minted before the scan failed no longer opens its report.

        This test used to pin the opposite ("serves the report, without a score",
        3.24). The founder's answer to that open question is that the public report
        refuses a failed scan's link, so what is pinned now is that the link holder
        gets nothing at all, and gets it the way a withdrawn link's holder does.
        """
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        # Not vacuous: until the scan fails, the same two requests are served.
        live_json, live_pdf = await _stranger_opens_both(client, token)
        assert live_json.status_code == live_pdf.status_code == 200

        await _end(session, sid, ScanStatus.FAILED)
        failed_json, failed_pdf = await _stranger_opens_both(client, token)

        _assert_dead_link_answer(failed_json)
        _assert_dead_link_answer(failed_pdf)
        # Nothing of the report comes back with it, a score or not.
        assert "score" not in failed_json.json()
        assert not failed_pdf.content.startswith(b"%PDF")
        # The same token, withdrawn, is answered field for field the same way,
        # and so is a token that never existed: the refusal names no reason. That is
        # the whole problem document and the whole header set (less the headers that
        # are per-request by nature), on the JSON route and on the PDF route alike.
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204
        withdrawn_json, withdrawn_pdf = await _stranger_opens_both(client, token)
        never_json, never_pdf = await _stranger_opens_both(client, "A" * 43)
        for failed, withdrawn, never in (
            (failed_json, withdrawn_json, never_json),
            (failed_pdf, withdrawn_pdf, never_pdf),
        ):
            assert failed.status_code == withdrawn.status_code == never.status_code == 404
            assert failed.json() == withdrawn.json() == never.json()
            assert (
                _comparable_headers(failed)
                == _comparable_headers(withdrawn)
                == _comparable_headers(never)
            )
            # Not vacuous: there is a header set to compare.
            assert _comparable_headers(failed)["content-type"].startswith(
                "application/problem+json"
            )

    async def test_a_failed_scan_is_not_a_point_on_the_clients_history(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)
        scan = await session.get(Scan, sid)
        resp = await client.get(f"{BASE}/clients/{scan.client_id}/history")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scans"] == []
        assert body["scansWithoutData"] == 1


class TestAFailedScansLinkIsDead:
    """3.29: the public lookup refuses a FAILED scan's token, and changes nothing else.

    The refusal lives in `share.scan_for_share_token`, the one lookup both public
    routes call, so these go at it from three sides: the lookup itself, both
    routes, and every scan status (FAILED is the only one refused). The token is
    NOT cleared, which is what lets the agency still withdraw it (3.25).
    """

    async def test_the_token_stays_on_the_row_and_the_agency_still_sees_when_it_ends(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        minted = (await client.post(f"{BASE}/scans/{sid}/share")).json()
        token = minted["token"]
        await _end(session, sid, ScanStatus.FAILED)

        # Refused on both public routes, and the refusals write nothing.
        for resp in await _stranger_opens_both(client, token):
            _assert_dead_link_answer(resp)
        # The path-token GET routes are gone for every token, this one included.
        assert (await client.get(f"{BASE}/reports/{token}")).status_code == 404
        assert (await client.get(f"{BASE}/reports/{token}.pdf")).status_code == 404
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        assert scan.share_token == token
        assert scan.share_expires_at is not None

        # The agency's own report still says when the link ends: that is what keeps
        # "Withdraw link" on the screen for it.
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert report["scanStatus"] == "failed"
        assert report["shareExpiresAt"] == minted["expiresAt"]

        # Pressing "Get shareable link" is still refused, and leaves the token alone.
        again = await client.post(f"{BASE}/scans/{sid}/share")
        assert again.status_code == 409, again.text
        assert again.json()["type"] == "/problems/failed-scan-not-shareable"
        await session.refresh(scan)
        assert scan.share_token == token

    async def test_a_failed_scans_link_can_still_be_withdrawn(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await _end(session, sid, ScanStatus.FAILED)

        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.share_token is None
        assert scan.share_expires_at is None
        assert (await client.get(f"{BASE}/scans/{sid}/report")).json()["shareExpiresAt"] is None
        for resp in await _stranger_opens_both(client, token):
            _assert_dead_link_answer(resp)

    async def test_the_lookup_both_public_routes_share_refuses_a_failed_scan(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The rule is in `scan_for_share_token`, not in a route, so no route can drift from it."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        found = await share_service.scan_for_share_token(session, token)
        assert found is not None
        assert found.id == sid

        await _end(session, sid, ScanStatus.FAILED)

        assert await share_service.scan_for_share_token(session, token) is None

    @pytest.mark.parametrize("status", list(ScanStatus), ids=lambda s: s.value)
    async def test_failed_is_the_only_status_whose_link_is_refused(
        self, client: AsyncClient, session, stub_engines, status: ScanStatus
    ) -> None:  # noqa: ANN001
        """Partial scans stay shareable, and so does every other status."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(status=status))
        await session.commit()

        as_json, as_pdf = await _stranger_opens_both(client, token)

        if status is ScanStatus.FAILED:
            _assert_dead_link_answer(as_json)
            _assert_dead_link_answer(as_pdf)
        else:
            assert as_json.status_code == 200, as_json.text
            assert as_json.json()["scanStatus"] == status.value
            assert as_pdf.status_code == 200, as_pdf.text
            assert as_pdf.content.startswith(b"%PDF")
