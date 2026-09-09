"""GET /clients/{id}/ai-crawler-access — Epic F.

The endpoint behind the AI crawlers screen. It READS what a scan's technical
audit already parsed out of robots.txt; it fetches nothing.

The assertions that matter are the ones that fail silently if wrong:

  * a scan whose robots.txt was unreadable rendering as a permissive policy,
  * `unspecified` and `allowed` collapsing into one another,
  * the vendor/purpose copied onto the row drifting back to a live lookup, so
    a historical scan silently restates today's roster,
  * a re-audit leaving last run's verdicts behind next to this run's.

`tests/test_ai_crawlers.py` covers the robots.txt semantics themselves. This
file is about persistence, projection and the response contract.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from avp_api.models.ai_crawler_access import AiCrawlerAccess
from avp_api.models.prompt import PromptIntent
from avp_api.models.technical_audit import TechnicalAudit
from avp_api.services import audit_runner, scan_runner
from avp_api.services.ai_crawlers import AGENTS, AccessVerdict, evaluate_robots, unknown_access
from avp_api.services.engines import EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.technical_audit import AuditSignals

BASE = "/api/v1"

# The policy `conftest`'s audit stub serves: one AI crawler blocked by name,
# the rest allowed through `*`. The shape of the only real finding in the
# development database.
STUB_BLOCKED = "CCBot"


@pytest.fixture(autouse=True)
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Keep the engine phase off the network.

    `stub_chain_externals` in conftest covers competitor discovery, the audit
    and the model, but NOT the engine phase itself — `test_answer_gaps.py`
    installs its own stub for that and this file originally did not.

    The cost was not theoretical. Every scan here made real outbound calls and
    sat on their timeouts: the suite took **12m27s for 17 tests**, roughly 44
    seconds each, and at least one run wedged entirely with two sessions idle
    in transaction and the process at zero CPU. Both symptoms are the same
    missing stub.

    The answers are deliberately dull — this file asserts nothing about
    mentions, tone or citations, only about the robots.txt policy the AUDIT
    records. A richer stub would be scenery.
    """

    async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
        return [GeneratedPrompt(text="a question", intent=PromptIntent.COMPARISON)], "stub"

    async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
        return [
            EngineAnswer(
                engine=engine,
                engine_version="stub",
                prompt_text=prompt,
                text="Nobody in particular.",
                citations=[],
            )
            for engine in engines
        ]

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """Stop the chain after the engine phase, and audit explicitly.

    This suite is about the policy the AUDIT records, not about the phases
    after it. Letting the full chain run made the file take **12m27s** for 17
    tests — scoring, fix generation and the report beat, none of which this
    file asserts anything about, on every one of them. Stopping early and
    calling `POST /scans/{id}/audit` exercises exactly the path under test.
    """
    return engines_only_executor


async def _sign_up(client: AsyncClient, email: str = "crawler@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Crawler Test Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _client_id(client: AsyncClient, url: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": url, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _run_scan(client: AsyncClient, cid: str) -> str:
    """Run a scan and audit it, which is what records the crawler policy.

    The audit is an explicit call because `scan_executor_factory` above stops
    the chain after the engine phase. `conftest`'s `fake_audit_site` supplies
    the robots.txt policy, so this is the same code path production takes,
    without the phases this file does not test.
    """
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    scan_id: str = resp.json()["id"]
    audited = await client.post(f"{BASE}/scans/{scan_id}/audit")
    assert audited.status_code == 201, audited.text
    return scan_id


async def _access(client: AsyncClient, cid: str, scan_id: str | None = None):  # noqa: ANN202
    url = f"{BASE}/clients/{cid}/ai-crawler-access"
    if scan_id is not None:
        url += f"?scanId={scan_id}"
    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestTheScanChainRecordsAPolicy:
    async def test_a_scan_records_one_verdict_per_agent_in_the_roster(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = await _access(client, cid)
        assert body is not None
        assert body["summary"]["total"] == len(AGENTS)
        assert len(body["agents"]) == len(AGENTS)
        # One row per agent, never two.
        assert len({a["agent"] for a in body["agents"]}) == len(AGENTS)

    async def test_the_blocked_crawler_is_reported_as_blocked_and_named(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = await _access(client, cid)
        blocked = [a for a in body["agents"] if a["verdict"] == "blocked"]
        assert [a["agent"] for a in blocked] == [STUB_BLOCKED]
        # `explicit`, because the stub's robots.txt names it — the fact that
        # lets an agency say whether the client typed this or inherited it.
        assert blocked[0]["ruleSource"] == "explicit"
        assert blocked[0]["matchedToken"] == STUB_BLOCKED.lower()

    async def test_the_worst_verdict_sorts_first(self, client: AsyncClient) -> None:
        # An operator opens this screen looking for what is wrong. A list that
        # opens on thirteen allows with the one block below the fold has
        # buried its own finding.
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = await _access(client, cid)
        assert body["agents"][0]["verdict"] == "blocked"

    async def test_the_response_offers_the_scans_that_have_a_policy(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        second = await _run_scan(client, cid)

        body = await _access(client, cid)
        assert set(body["availableScanIds"]) == {first, second}
        # Newest first, and the default selection is that newest scan.
        assert body["availableScanIds"][0] == second
        assert body["scanId"] == second

        # And an explicit older selection is honoured.
        older = await _access(client, cid, first)
        assert older["scanId"] == first


class TestAnUnreadableFileIsNotAPermissivePolicy:
    """The house rule Epics A, B and E each landed on, from a third direction."""

    async def test_every_verdict_is_unknown_and_the_flag_says_so(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)
        scan_id = await _run_scan(client, cid)

        # Re-audit the scan with a robots.txt that could not be read.
        async def unreadable(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return AuditSignals(
                url=url,
                domain=url.split("://", 1)[-1],
                http_status=200,
                ok=True,
                ai_crawler_access=unknown_access(),
            )

        monkeypatch.setattr(audit_runner, "audit_site", unreadable)
        resp = await client.post(f"{BASE}/scans/{scan_id}/audit")
        assert resp.status_code == 201, resp.text

        body = await _access(client, cid)
        assert body["robotsReadable"] is False
        assert body["summary"]["unknown"] == len(AGENTS)
        # None of it may be reported as permission.
        assert body["summary"]["allowed"] == 0
        assert body["summary"]["unspecified"] == 0
        assert all(a["matchedToken"] is None for a in body["agents"])

    async def test_a_readable_file_is_marked_readable(self, client: AsyncClient) -> None:
        # The positive control. Without it the assertion above could pass
        # because the flag is hard-wired false.
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = await _access(client, cid)
        assert body["robotsReadable"] is True


class TestSilenceAndPermissionAreDifferentAnswers:
    async def test_an_unnamed_agent_under_no_group_is_unspecified_not_allowed(
        self, client: AsyncClient, session, monkeypatch  # noqa: ANN001
    ) -> None:
        """A file with no applicable group states a policy; it just names nobody.

        Practically the agent may crawl either way. They are still different
        findings — one site decided, the other never considered it — and the
        screen sorts and counts on the difference.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        scan_id = await _run_scan(client, cid)

        async def only_googlebot(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return AuditSignals(
                url=url,
                domain=url.split("://", 1)[-1],
                http_status=200,
                ok=True,
                # Real shape: posthog.com names four search engines and no `*`.
                ai_crawler_access=evaluate_robots(
                    "User-agent: Googlebot\nDisallow: /*.md$\n"
                ),
            )

        monkeypatch.setattr(audit_runner, "audit_site", only_googlebot)
        assert (await client.post(f"{BASE}/scans/{scan_id}/audit")).status_code == 201

        body = await _access(client, cid)
        assert body["summary"]["unspecified"] == len(AGENTS)
        assert body["summary"]["allowed"] == 0
        assert body["robotsReadable"] is True
        # Distinct from UNKNOWN, which is the unreadable case.
        assert body["summary"]["unknown"] == 0


class TestTheCostlySubsetIsSeparated:
    async def test_search_blocked_counts_only_blocked_search_crawlers(
        self, client: AsyncClient, monkeypatch  # noqa: ANN001
    ) -> None:
        """Blocking training costs no citations; blocking search does.

        A single `blocked` count cannot tell an agency which of those happened,
        and they are different conversations with a client.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        scan_id = await _run_scan(client, cid)

        async def blocks_a_training_bot(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return AuditSignals(
                url=url,
                domain=url.split("://", 1)[-1],
                http_status=200,
                ok=True,
                ai_crawler_access=evaluate_robots(
                    "User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /\n"
                ),
            )

        monkeypatch.setattr(audit_runner, "audit_site", blocks_a_training_bot)
        assert (await client.post(f"{BASE}/scans/{scan_id}/audit")).status_code == 201

        body = await _access(client, cid)
        assert body["summary"]["blocked"] == 1
        # GPTBot is TRAINING, so nothing citation-costing is blocked.
        assert body["summary"]["searchBlocked"] == 0


class TestARerunCorrectsRatherThanAccumulates:
    async def test_re_auditing_replaces_the_previous_verdicts(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """A scan is a point-in-time claim, so a second audit is a correction.

        Without the replace, the unique constraint would reject the write — or
        worse, if it were ever dropped, every count on the screen would double.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        scan_id = await _run_scan(client, cid)

        assert (await client.post(f"{BASE}/scans/{scan_id}/audit")).status_code == 201
        assert (await client.post(f"{BASE}/scans/{scan_id}/audit")).status_code == 201

        rows = (
            await session.execute(
                select(func.count())
                .select_from(AiCrawlerAccess)
                .join(TechnicalAudit, TechnicalAudit.id == AiCrawlerAccess.audit_id)
                .where(TechnicalAudit.scan_id == scan_id)
            )
        ).scalar_one()
        assert rows == len(AGENTS)


class TestTheRowRemembersWhatWasClaimedAtTheTime:
    async def test_vendor_and_purpose_are_stored_not_looked_up(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """The roster will change; a historical scan must not silently change with it.

        Asserted by MUTATING the stored row and reading it back through the
        endpoint: if the projection joined the live roster instead, the edit
        would be overwritten and this would fail.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        row = (
            await session.execute(
                select(AiCrawlerAccess).where(AiCrawlerAccess.agent_token == "GPTBot")
            )
        ).scalars().first()
        assert row is not None
        row.vendor = "Renamed Vendor"
        await session.commit()

        body = await _access(client, cid)
        gptbot = next(a for a in body["agents"] if a["agent"] == "GPTBot")
        assert gptbot["vendor"] == "Renamed Vendor"


class TestEmptyAndScopedStates:
    async def test_a_client_with_no_audited_scan_returns_null(
        self, client: AsyncClient
    ) -> None:
        # Not a 404: a client whose scans predate the feature is a normal
        # state with an empty view, not a missing resource.
        await _sign_up(client)
        cid = await _client_id(client)
        resp = await client.get(f"{BASE}/clients/{cid}/ai-crawler-access")
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_a_scan_id_from_another_client_returns_null(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        first = await _client_id(client, "helpscout.com")
        second = await _client_id(client, "basecamp.com")
        other_scan = await _run_scan(client, second)
        await _run_scan(client, first)

        resp = await client.get(
            f"{BASE}/clients/{first}/ai-crawler-access?scanId={other_scan}"
        )
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_another_agency_cannot_read_this_client(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client, "one@test.example")
        cid = await _client_id(client)
        await _run_scan(client, cid)

        await _sign_up(client, "two@test.example")
        resp = await client.get(f"{BASE}/clients/{cid}/ai-crawler-access")
        # 404, not 403 — an id must not be probeable across agencies.
        assert resp.status_code == 404


class TestIpSafety:
    async def test_the_response_carries_no_part_of_the_robots_file(
        self, client: AsyncClient, monkeypatch  # noqa: ANN001
    ) -> None:
        """The facts-only rule. A verdict and a count, never a path or a rule body.

        The robots.txt below carries a distinctive path. If any field ever
        started echoing rule text — a "blocked paths" list is the obvious way
        this would happen — the phrase appears in the response and this fails.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        scan_id = await _run_scan(client, cid)

        secret = "/internal-pricing-model-q4/"

        async def with_a_telling_path(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return AuditSignals(
                url=url,
                domain=url.split("://", 1)[-1],
                http_status=200,
                ok=True,
                ai_crawler_access=evaluate_robots(
                    f"User-agent: *\nAllow: /\nDisallow: {secret}\n"
                ),
            )

        monkeypatch.setattr(audit_runner, "audit_site", with_a_telling_path)
        assert (await client.post(f"{BASE}/scans/{scan_id}/audit")).status_code == 201

        resp = await client.get(f"{BASE}/clients/{cid}/ai-crawler-access")
        assert secret not in resp.text
        assert "internal-pricing-model" not in resp.text
        # The COUNT of those rules IS present — that is the permitted case,
        # and it is what makes the omission of the paths a choice rather than
        # an oversight.
        assert any(a["disallowRules"] == 1 for a in resp.json()["agents"])

    async def test_no_column_on_the_table_can_hold_a_path(self) -> None:
        """Structural, not behavioural — the guarantee above cannot regress by
        someone adding a field, because there is nowhere for one to go."""
        columns = {c.name for c in AiCrawlerAccess.__table__.columns}
        assert columns == {
            "id",
            "audit_id",
            "agent_token",
            "vendor",
            "purpose",
            "verdict",
            "rule_source",
            "matched_token",
            "disallow_rules",
            "created_at",
            "updated_at",
        }


class TestTheEndpointDoesNotFetchAnything:
    async def test_reading_the_policy_makes_no_outbound_request(
        self, client: AsyncClient, monkeypatch  # noqa: ANN001
    ) -> None:
        """A read that re-fetched robots.txt would let the same URL answer
        differently between two page loads, and would turn this endpoint into a
        way to make the API issue outbound requests on demand."""
        await _sign_up(client)
        cid = await _client_id(client)
        await _run_scan(client, cid)

        called: list[str] = []

        async def must_not_run(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            called.append(url)
            raise AssertionError("the read path audited the site")

        monkeypatch.setattr(audit_runner, "audit_site", must_not_run)
        body = await _access(client, cid)
        assert called == []
        assert body["summary"]["total"] == len(AGENTS)


class TestVerdictVocabulary:
    def test_the_enum_has_no_partial(self) -> None:
        """Removed after being measured — it fired on seven of nine real client
        domains, purely from housekeeping disallows. A verdict that is almost
        always true carries as little information as one that never fires."""
        assert {v.value for v in AccessVerdict} == {
            "allowed",
            "blocked",
            "unspecified",
            "unknown",
        }
