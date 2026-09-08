"""`collect_facts` counts the population the proof beat counts — 2026-09-08.

THE BUG THIS PINS
-----------------
From Epic 8 until the second pilot dry run, `fix_runner.collect_facts` filtered
engine results on `status is OK`, which means *answered and named the brand*.
`ANSWERED_NO_MENTION` — the status `api-contracts.md` labels in bold as "a
finding, not a failure" — was dropped. So every figure the fix generator was
handed came from the subset of answers that named the brand:

  * `answers_analysed` always equalled `answers_naming_subject`, which is how
    a real report came to say *"All 30 of 30 answers named Pirsch"* about a
    scan where 41 of 71 answers did not;
  * `citations_total` was tallied inside that subset — *"3 of 123 citations"*
    on the same page whose proof beat counted 323;
  * `top_cited_domains` missed the page's own top unclaimed source, because
    its 15 citations sat in answers that did not name the brand.

The proof beat (`report.py`) had counted the right population since Epic 7.1.
These tests hold the two to the same answer on a scan shaped like the one that
went wrong: some answers name the brand, some do not, and the ones that do not
carry citations of their own.

The scan is driven through the real chain with stubbed engines, so the rows
`collect_facts` reads were written by the real extraction path rather than
hand-built to fit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models import Client, Scan
from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import fix_runner, scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.report import build_report

BASE = "/api/v1"

NAMES_THE_BRAND = "Zendesk is popular. Help Scout is simpler and well liked."
DOES_NOT = "Zendesk is popular. Front is fine for larger teams."


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Facts", "fullName": "Op",
              "email": "facts@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_mixed_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Four prompts: even ones are answered naming the brand, odd ones are not.

    The grounded engine cites two sources on every answer. The subject's own
    domain is one of them each time; the other is `g2.com` on answers that
    name the brand and `capterra.com` on answers that do not — so a tally
    built from the naming answers alone never sees capterra.com at all, which
    is the shape of the `analytics-alternatives.com` miss on the real report.
    """
    generated = [
        GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
        for i in range(4)
    ]
    index_of = {p.text: i for i, p in enumerate(generated)}

    async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
        return generated, "stub"

    async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
        i = index_of[prompt]
        names = i % 2 == 0
        other = "g2.com" if names else "capterra.com"
        out = []
        for engine in engines:
            citations = (
                [CitedSource(url=f"https://{other}/x", domain=other, position=1),
                 CitedSource(url="https://helpscout.com/y", domain="helpscout.com", position=2)]
                if engine is Engine.CLAUDE_SEARCH else []
            )
            out.append(EngineAnswer(
                engine=engine, engine_version="stub", prompt_text=prompt,
                text=NAMES_THE_BRAND if names else DOES_NOT,
                citations=citations, latency_ms=5,
            ))
        return out

    async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)


async def _mixed_scan(client: AsyncClient) -> str:
    resp = await client.post(
        f"{BASE}/clients",
        json={"url": "helpscout.com", "name": "Help Scout", "classify": False},
    )
    cid = resp.json()["id"]
    return (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]


class TestCollectFactsPopulation:
    async def test_the_scan_really_is_mixed(
        self, client: AsyncClient, session, stub_mixed_engines
    ) -> None:  # noqa: ANN001
        """The premise, asserted: half the answers did not name the brand.

        If the stub ever stopped producing absences the tests below would pass
        for the wrong reason, so the shape is checked before anything is
        concluded from it.
        """
        await _sign_up(client)
        sid = await _mixed_scan(client)
        rows = await fix_runner._load_results(session, sid)
        by_status = {s: sum(1 for r in rows if r.status is s) for s in EngineResultStatus}
        assert by_status[EngineResultStatus.OK] == 6
        assert by_status[EngineResultStatus.ANSWERED_NO_MENTION] == 6
        assert sum(by_status.values()) == 12

    async def test_answers_analysed_is_every_answer_not_every_answer_that_named_the_brand(
        self, client: AsyncClient, session, stub_mixed_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _mixed_scan(client)
        scan = await session.get(Scan, sid)
        subject = await session.get(Client, scan.client_id)

        facts = await fix_runner.collect_facts(session, scan, subject)

        # Twelve answers came back; six named the brand. The old filter made
        # both of these 6, and the generator wrote "All 6 of 6" from it.
        assert facts.answers_analysed == 12
        assert facts.answers_naming_subject == 6
        assert facts.answers_naming_subject < facts.answers_analysed

    async def test_citations_in_answers_that_did_not_name_the_brand_still_count(
        self, client: AsyncClient, session, stub_mixed_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _mixed_scan(client)
        scan = await session.get(Scan, sid)
        subject = await session.get(Client, scan.client_id)

        facts = await fix_runner.collect_facts(session, scan, subject)

        # Four grounded answers, two citations each. The old filter saw only
        # the two that named the brand: 4 citations, 2 to the subject, and no
        # capterra.com anywhere.
        assert facts.citations_total == 8
        assert facts.citations_to_subject == 4
        assert dict(facts.top_cited_domains) == {"g2.com": 2, "capterra.com": 2}

    async def test_the_facts_agree_with_the_proof_beat_for_the_same_scan(
        self, client: AsyncClient, session, stub_mixed_engines
    ) -> None:  # noqa: ANN001
        """The regression as a reader would meet it.

        The fix list and the proof beat sit two beats apart on one page. On the
        pirsch.io report they disagreed — 30 of 30 against 30 of 71, 123
        citations against 323 — because each counted a different population.
        Whatever either counts in future, they count the same thing.
        """
        await _sign_up(client)
        sid = await _mixed_scan(client)
        scan = await session.get(Scan, sid)
        subject = await session.get(Client, scan.client_id)

        facts = await fix_runner.collect_facts(session, scan, subject)
        proof = (await build_report(session, scan)).proof

        assert facts.answers_analysed == proof.answered_results
        assert facts.answers_naming_subject == proof.results_mentioning_subject
        assert facts.citations_total == proof.total_citations
        assert facts.citations_to_subject == proof.subject_citations
