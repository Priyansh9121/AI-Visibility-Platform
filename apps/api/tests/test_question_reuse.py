"""F6 section 1: a client keeps its questions.

A rescan copies the client's most recent all-generated set instead of asking the generator again;
the agency asks for new questions on purpose with a flag the next scan reads once and clears; every
set carries a fingerprint of its ordered (text, intent) list. Every model call is a stub.
"""

from __future__ import annotations

import itertools
import random
import time
from types import SimpleNamespace

import anthropic
import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from ulid import ULID

from avp_api import ids
from avp_api.models import Client, Prompt, PromptSet, Scan, ScanStatus
from avp_api.models.client import ClassificationStatus
from avp_api.models.prompt import PromptIntent
from avp_api.services import prompts, question_sets, scan_runner

BASE = "/api/v1"
_clock = itertools.count(int(time.time()) + 20_000)


def questions(n: int, *, prefix: str = "q") -> list[dict]:
    mix = (
        [PromptIntent.AWARENESS] * 9 + [PromptIntent.COMPARISON] * 7
        + [PromptIntent.BOTTOM_FUNNEL] * 4
    )
    return [
        {"text": f"{prefix} number {i} about choosing a provider", "intent": mix[i % 20].value}
        for i in range(n)
    ]


def stub_generator(monkeypatch: pytest.MonkeyPatch, payload: list[dict]) -> list[dict]:
    """Replace the one model call and record each request; nothing reaches a provider."""
    calls: list[dict] = []

    async def parse(self, **kwargs):  # noqa: ANN001, ANN003, ARG001
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=prompts.GeneratedPromptSet.model_validate({"prompts": payload}),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(anthropic.resources.messages.AsyncMessages, "parse", parse)
    return calls


async def sign_up(client: AsyncClient, email: str = "f6@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "F6 Agency", "fullName": "Op", "email": email,
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def client_row(
    client: AsyncClient, session: AsyncSession, domain: str = "rmit.com"
) -> Client:
    await sign_up(client)
    made = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert made.status_code == 201, made.text
    row = await session.get(Client, made.json()["id"])
    row.industry = "university"
    row.classification_status = ClassificationStatus.CLASSIFIED
    row.brand_name = "RMIT University"
    await session.commit()
    return row


async def new_scan(
    session: AsyncSession, row: Client, status: ScanStatus = ScanStatus.SUCCEEDED
) -> Scan:
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_timestamp(next(_clock))}",
        client_id=row.id, agency_id=row.agency_id, status=status,
    )
    session.add(scan)
    await session.commit()
    return scan


async def build(session, scan, row, settings, **kw):  # noqa: ANN001, ANN003, ANN202
    result = await scan_runner.build_prompt_set(
        session, scan, row, competitors=[("Monash", "monash.edu")], settings=settings, **kw
    )
    await session.commit()
    return result


async def stored(session: AsyncSession, set_id: str) -> list[tuple[str, str, int]]:
    rows = (
        await session.execute(
            select(Prompt.text, Prompt.intent, Prompt.position)
            .where(Prompt.prompt_set_id == set_id).order_by(Prompt.position)
        )
    ).all()
    return [(t, i.value, p) for t, i, p in rows]


async def add_set(
    session: AsyncSession, scan: Scan, *, generated_by: str | None, params: dict,
    n: int = 20, prefix: str = "old",
) -> PromptSet:
    ps = PromptSet(
        id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id, generated_by=generated_by,
        generation_params=params, prompts=[],
    )
    session.add(ps)
    await session.flush()
    for pos, q in enumerate(questions(n, prefix=prefix), start=1):
        ps.prompts.append(Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=ps.id, text=q["text"],
            intent=PromptIntent(q["intent"]), position=pos,
        ))
    await session.commit()
    return ps


async def ask_for_new_questions(
    client: AsyncClient, session: AsyncSession, row: Client
) -> None:
    """The agency's PUT; then the test's loaded copy of the client is brought up to date."""
    put = await client.put(
        f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
    )
    assert put.status_code == 200, put.text
    await session.refresh(row)
    assert row.new_questions_next_scan is True


# A set stored before the counts existed has no `fromGenerator`, and `clients_set` then also asks
# for at least `prompts.MIN_PROMPTS` questions: a smaller one was cut by a `promptLimit`, which
# nothing marked. A fixture without counts therefore holds that many, so that its name or its
# params alone decide whether it is the client's set.
FULL = prompts.MIN_PROMPTS

# Prompt sets for the tests of how the client's set is found: (generated_by, params, questions).
# Sets `clients_set` must never return, one for each reason; only the last has no questions.
NEVER_THE_CLIENTS: dict[str, tuple[str | None, dict, int]] = {
    "reused": ("reused", {"fromGenerator": 20, "fromFallback": 0}, 2),
    "topped_up_name": (
        f"{prompts.GENERATOR_MODEL}+fallback", {"fromGenerator": 11, "fromFallback": 9}, 2
    ),
    # Stored before the counts existed: the name alone says templates were used.
    "topped_up_name_without_counts": (f"{prompts.GENERATOR_MODEL}+fallback", {}, FULL),
    # What the first version called a set of nothing but templates.
    "templates_only_name": ("fallback", {}, FULL),
    "fallback_count_under_a_plain_name": (
        prompts.GENERATOR_MODEL, {"fromGenerator": 19, "fromFallback": 1}, 2
    ),
    "cut_to_a_limit": (
        prompts.GENERATOR_MODEL, {"fromGenerator": 6, "fromFallback": 0, "limited": True}, 2
    ),
    # Stored before the counts existed and cut by a `promptLimit`: only its size says so.
    "old_set_cut_to_a_few": (prompts.GENERATOR_MODEL, {"requested": 3}, 3),
    "old_set_one_short": (prompts.GENERATOR_MODEL, {"requested": FULL - 1}, FULL - 1),
    "no_generator": ("none", {}, FULL),
    "blank_name": ("", {}, FULL),
    "no_name": (None, {}, FULL),
    "no_questions": (prompts.GENERATOR_MODEL, {"fromGenerator": 20, "fromFallback": 0}, 0),
}
# Sets it may return: a model name, no fallback count, not cut. A set without counts needs a full
# set of questions; one with counts is the client's even when it is smaller.
THE_CLIENTS: list[tuple[str | None, dict, int]] = [
    (prompts.GENERATOR_MODEL, {}, FULL),
    (prompts.GENERATOR_MODEL, {"fromGenerator": 20, "fromFallback": 0}, 2),
    (prompts.GENERATOR_MODEL, {"fromGenerator": 20, "fromFallback": 0, "limited": False}, 2),
    (prompts.GENERATOR_MODEL, {"fromGenerator": 17, "fromFallback": 0}, 17),
    ("another-model", {"requested": 20}, FULL),
    (prompts.GENERATOR_MODEL, {"requested": 24}, 24),
]


def transient_set(name: str | None, params: dict, n: int) -> PromptSet:
    """A prompt set that is never stored, with `n` questions: all the rule reads."""
    return PromptSet(
        generated_by=name,
        generation_params=params,
        prompts=[
            Prompt(text=f"q{i}", intent=PromptIntent.AWARENESS, position=i)
            for i in range(1, n + 1)
        ],
    )


class TestTheRuleItself:
    """`is_all_generated`, over the same tables the database tests use: the SQL must agree."""

    @pytest.mark.parametrize(
        "kind", [k for k in sorted(NEVER_THE_CLIENTS) if k != "no_questions"]
    )
    def test_a_set_the_client_never_keeps_is_refused(self, kind: str) -> None:
        # (An empty set is the caller's check, `clients_set`'s, so it is not in this list.)
        name, params, n = NEVER_THE_CLIENTS[kind]
        assert question_sets.is_all_generated(transient_set(name, params, n)) is False

    @pytest.mark.parametrize("index", range(len(THE_CLIENTS)))
    def test_a_set_the_client_keeps_is_accepted(self, index: int) -> None:
        name, params, n = THE_CLIENTS[index]
        assert question_sets.is_all_generated(transient_set(name, params, n)) is True


class TestFingerprint:
    def test_it_is_stable_and_ordered_and_covers_text_and_intent(self) -> None:
        a = [("one", "awareness"), ("two", "comparison")]
        assert question_sets.fingerprint(a) == question_sets.fingerprint(list(a))
        assert question_sets.fingerprint(a) != question_sets.fingerprint(a[::-1])
        assert question_sets.fingerprint(a) != question_sets.fingerprint(
            [("one", "awareness"), ("two", "bottom_funnel")]
        )
        assert question_sets.fingerprint(a) != question_sets.fingerprint(
            [("one", "awareness"), ("twos", "comparison")]
        )
        assert len(question_sets.fingerprint(a)) == 64

    def test_a_join_character_inside_a_text_cannot_make_two_lists_equal(self) -> None:
        assert question_sets.fingerprint([("a|b", "awareness")]) != question_sets.fingerprint(
            [("a", "awareness"), ("b", "awareness")]
        )


class TestFirstScanGeneratesSecondReuses:
    async def test_the_first_scan_generates_and_writes_fingerprint_and_rivals(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        ps = await build(session, await new_scan(session, row), row, settings)
        assert len(calls) == 1
        assert ps.generated_by == prompts.GENERATOR_MODEL
        gp = ps.generation_params
        assert gp["writtenWithRivals"] == ["Monash"]
        assert gp["fingerprint"] == question_sets.fingerprint(
            [(t, i) for t, i, _ in await stored(session, ps.id)]
        )
        assert "reusedFromScanId" not in gp

    async def test_the_second_scan_copies_the_set_and_makes_no_generator_call(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        first_scan = await new_scan(session, row)
        first = await build(session, first_scan, row, settings)
        first_id, first_fp = first.id, first.generation_params["fingerprint"]
        first_rows = await stored(session, first_id)
        first_prompt_ids = set((await session.execute(select(Prompt.id))).scalars())

        second_scan = await new_scan(session, row)
        with structlog.testing.capture_logs() as events:
            second = await build(session, second_scan, row, settings)

        assert len(calls) == 1  # still the first scan's call only
        assert second.id != first_id and second.scan_id == second_scan.id
        assert second.generated_by == "reused"
        assert await stored(session, second.id) == first_rows
        new_prompt_ids = set(
            (await session.execute(select(Prompt.id).where(Prompt.prompt_set_id == second.id)))
            .scalars()
        )
        assert new_prompt_ids and not (new_prompt_ids & first_prompt_ids)
        gp = second.generation_params
        assert gp["reusedFromScanId"] == first_scan.id
        assert gp["fingerprint"] == first_fp
        assert (gp["fromGenerator"], gp["fromFallback"]) == (20, 0)
        assert gp["writtenWithRivals"] == ["Monash"]
        (reused,) = [e for e in events if e["event"] == "prompts.reused"]
        assert (reused["scan_id"], reused["from_scan_id"]) == (second_scan.id, first_scan.id)
        assert "number" not in repr(events)

    async def test_a_third_scan_reuses_the_original_not_the_copy(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first_scan = await new_scan(session, row)
        await build(session, first_scan, row, settings)
        await build(session, await new_scan(session, row), row, settings)
        third = await build(session, await new_scan(session, row), row, settings)
        assert third.generation_params["reusedFromScanId"] == first_scan.id

    async def test_the_rivals_a_reused_set_was_written_with_are_carried_not_current(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)
        second = await scan_runner.build_prompt_set(
            session, await new_scan(session, row), row,
            competitors=[("Deakin", "deakin.edu.au")], settings=settings,
        )
        assert second.generation_params["writtenWithRivals"] == ["Monash"]

    async def test_a_source_written_before_this_change_has_no_rivals_to_carry(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(session, await new_scan(session, row), generated_by=prompts.GENERATOR_MODEL,
                      params={"requested": 20})
        second = await build(session, await new_scan(session, row), row, settings)
        assert not calls
        assert "writtenWithRivals" not in second.generation_params
        assert "fromGenerator" not in second.generation_params
        assert second.generation_params["fingerprint"]


class TestWhichSetIsTheClients:
    async def test_a_template_filled_latest_set_is_skipped_for_the_one_before_it(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        good_scan = await new_scan(session, row)
        good = await add_set(session, good_scan, generated_by=prompts.GENERATOR_MODEL,
                             params={"fromGenerator": 20, "fromFallback": 0}, prefix="good")
        await add_set(session, await new_scan(session, row),
                      generated_by=f"{prompts.GENERATOR_MODEL}+fallback",
                      params={"fromGenerator": 11, "fromFallback": 9}, prefix="filler")
        # A fallback count alone disqualifies, even under a plain model name.
        await add_set(session, await new_scan(session, row), generated_by=prompts.GENERATOR_MODEL,
                      params={"fromGenerator": 19, "fromFallback": 1}, prefix="filler2")
        third = await build(session, await new_scan(session, row), row, settings)
        assert not calls
        assert third.generation_params["reusedFromScanId"] == good_scan.id
        assert [t for t, _, _ in await stored(session, third.id)] == [
            t for t, _, _ in await stored(session, good.id)
        ]

    async def test_only_template_filled_sets_means_a_fresh_generation(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(session, await new_scan(session, row),
                      generated_by=f"{prompts.GENERATOR_MODEL}+fallback",
                      params={"fromGenerator": 11, "fromFallback": 9})
        ps = await build(session, await new_scan(session, row), row, settings)
        assert len(calls) == 1 and ps.generated_by == prompts.GENERATOR_MODEL

    async def test_an_old_set_without_counts_qualifies_by_generated_by(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        old = await new_scan(session, row)
        await add_set(session, old, generated_by=prompts.GENERATOR_MODEL, params={})
        ps = await build(session, await new_scan(session, row), row, settings)
        assert not calls and ps.generation_params["reusedFromScanId"] == old.id

    async def test_an_old_filled_set_without_counts_is_known_by_its_name(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(session, await new_scan(session, row), generated_by="none", params={})
        await build(session, await new_scan(session, row), row, settings)
        assert len(calls) == 1

    async def test_another_clients_set_is_never_used(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        other = await client.post(f"{BASE}/clients", json={"url": "monash.com", "classify": False})
        other_row = await session.get(Client, other.json()["id"])
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(session, await new_scan(session, other_row),
                      generated_by=prompts.GENERATOR_MODEL, params={})
        await build(session, await new_scan(session, row), row, settings)
        assert len(calls) == 1

    async def test_a_scan_with_a_prompt_limit_neither_becomes_nor_reuses_a_full_set(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        small = await build(session, await new_scan(session, row), row, settings, limit=6)
        assert len(small.prompts) == 6 and small.generation_params["limited"] is True
        full = await build(session, await new_scan(session, row), row, settings)
        assert len(calls) == 2 and len(full.prompts) == 20


class TestNewQuestionsOnPurpose:
    async def test_the_flag_makes_the_next_scan_generate_and_is_cleared_with_it(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        first = await build(session, await new_scan(session, row), row, settings)
        put = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
        )
        assert put.status_code == 200, put.text
        # Section 3: the PUT answers with the GET's body (the flag and `currentSet`).
        assert put.json()["newQuestionsNextScan"] is True
        await session.refresh(row)
        assert row.new_questions_next_scan is True

        stub_generator(monkeypatch, questions(20, prefix="fresh"))
        fresh = await build(session, await new_scan(session, row), row, settings)
        assert fresh.generated_by == prompts.GENERATOR_MODEL
        assert fresh.generation_params["fingerprint"] != first.generation_params["fingerprint"]
        await session.refresh(row)
        assert row.new_questions_next_scan is False
        assert len(calls) == 1

        # The scan after that reuses the NEW set.
        after = await build(session, await new_scan(session, row), row, settings)
        assert after.generation_params["fingerprint"] == fresh.generation_params["fingerprint"]

    async def test_a_fresh_set_is_made_even_before_any_scan_and_the_flag_survives_a_failed_one(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        row.new_questions_next_scan = True
        await session.commit()
        stub_generator(monkeypatch, questions(3))  # too few: the phase fails, nothing is stored
        with pytest.raises(prompts.PromptsUnavailable):
            await scan_runner.build_prompt_set(
                session, await new_scan(session, row), row, competitors=[], settings=settings
            )
        await session.rollback()
        await session.refresh(row)
        assert row.new_questions_next_scan is True

    async def test_the_put_can_take_the_flag_back(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        url = f"{BASE}/clients/{row.id}/questions"
        assert (await client.put(url, json={"newQuestionsNextScan": True})).status_code == 200
        off = await client.put(url, json={"newQuestionsNextScan": False})
        assert off.json()["newQuestionsNextScan"] is False
        await session.refresh(row)
        assert row.new_questions_next_scan is False

    async def test_a_running_scan_of_the_client_is_a_409_and_nothing_is_written(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        await new_scan(session, row, ScanStatus.RUNNING)
        await new_scan(session, row, ScanStatus.SUCCEEDED)  # a later, finished one changes nothing
        resp = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
        )
        assert resp.status_code == 409, resp.text
        await session.refresh(row)
        assert row.new_questions_next_scan is False

    async def test_a_queued_or_finished_scan_is_no_reason_to_refuse(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        await new_scan(session, row, ScanStatus.QUEUED)
        await new_scan(session, row, ScanStatus.FAILED)
        resp = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
        )
        assert resp.status_code == 200

    async def test_another_agencys_or_an_unknown_client_is_a_404_and_nothing_is_written(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        await client.post(f"{BASE}/auth/logout")
        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await sign_up(other, "intruder@test.example")
            body = {"newQuestionsNextScan": True}
            mine = await other.put(f"{BASE}/clients/{row.id}/questions", json=body)
            unknown = await other.put(f"{BASE}/clients/{ids.CLIENT}_{ULID()}/questions", json=body)
        assert (mine.status_code, unknown.status_code) == (404, 404)
        assert mine.json()["detail"] == unknown.json()["detail"]
        await session.refresh(row)
        assert row.new_questions_next_scan is False

    async def test_it_needs_a_signed_in_user_and_a_boolean(self, client, session) -> None:  # noqa: ANN001
        row = await client_row(client, session)
        url = f"{BASE}/clients/{row.id}/questions"
        bad = await client.put(url, json={"newQuestionsNextScan": "x"})
        assert bad.status_code == 422
        await client.post(f"{BASE}/auth/logout")
        anon = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
        )
        assert anon.status_code == 401


class TestFingerprintsAcrossScans:
    async def test_equal_for_reused_different_for_fresh(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        a = await build(session, await new_scan(session, row), row, settings)
        b = await build(session, await new_scan(session, row), row, settings)
        assert a.generation_params["fingerprint"] == b.generation_params["fingerprint"]
        row.new_questions_next_scan = True
        await session.commit()
        stub_generator(monkeypatch, questions(20, prefix="other"))
        c = await build(session, await new_scan(session, row), row, settings)
        assert c.generation_params["fingerprint"] != a.generation_params["fingerprint"]
        count = (await session.execute(select(func.count()).select_from(PromptSet))).scalar_one()
        assert count == 3


class TestTheFlagIsSpentOnlyByASetThatBecomesTheClients:
    """F6 review, fix 1: a set the next rescan would not copy must not use the flag up.

    `clients_set` copies only an all-generated, uncut set. A fresh set topped up with templates
    (`+fallback`) or cut to a `promptLimit` is not one, so if it cleared the flag the next scan
    would silently copy the OLD set the agency had asked to replace.
    """

    async def test_a_topped_up_set_leaves_the_flag_set_and_the_next_full_scan_writes_again(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        # Only a domain with a market (.edu.au) lets the generator top a thin set up from templates.
        row = await client_row(client, session, domain="rmit.edu.au")
        stub_generator(monkeypatch, questions(20))
        old = await build(session, await new_scan(session, row), row, settings)
        await ask_for_new_questions(client, session, row)

        topped = stub_generator(monkeypatch, questions(17, prefix="topped"))
        second = await build(session, await new_scan(session, row), row, settings)
        assert len(topped) == 1
        assert second.generated_by == f"{prompts.GENERATOR_MODEL}+fallback"
        assert second.generation_params["fromFallback"] == 3
        await session.refresh(row)
        assert row.new_questions_next_scan is True

        full = stub_generator(monkeypatch, questions(20, prefix="fresh"))
        third_scan = await new_scan(session, row)
        third = await build(session, third_scan, row, settings)
        assert len(full) == 1  # it wrote again; it did not copy the old set
        assert third.generated_by == prompts.GENERATOR_MODEL
        assert third.generation_params["fingerprint"] not in {
            old.generation_params["fingerprint"], second.generation_params["fingerprint"],
        }
        await session.refresh(row)
        assert row.new_questions_next_scan is False

        # The scan after that copies the new full set, not the topped-up one.
        after = await build(session, await new_scan(session, row), row, settings)
        assert after.generation_params["reusedFromScanId"] == third_scan.id
        assert len(full) == 1

    @pytest.mark.parametrize("written", [20, 17])
    async def test_a_scan_cut_to_a_prompt_limit_leaves_the_flag_set(
        self, written, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """Twenty written and six kept; or 17 written, topped up, six kept (all the generator's)."""
        row = await client_row(client, session, domain="rmit.edu.au")
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)
        await ask_for_new_questions(client, session, row)

        stub_generator(monkeypatch, questions(written, prefix="cut"))
        cut = await build(session, await new_scan(session, row), row, settings, limit=6)
        assert len(cut.prompts) == 6 and cut.generation_params["limited"] is True
        assert cut.generation_params["fromFallback"] == 0  # the six kept are all the generator's
        assert cut.generated_by == (
            prompts.GENERATOR_MODEL if written == 20 else f"{prompts.GENERATOR_MODEL}+fallback"
        )
        await session.refresh(row)
        assert row.new_questions_next_scan is True

        full = stub_generator(monkeypatch, questions(20, prefix="fresh"))
        fresh = await build(session, await new_scan(session, row), row, settings)
        assert len(full) == 1 and fresh.generated_by == prompts.GENERATOR_MODEL
        assert len(fresh.prompts) == 20
        await session.refresh(row)
        assert row.new_questions_next_scan is False

    async def test_a_smaller_set_the_generator_alone_wrote_still_spends_the_flag(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """No market, so no templates: 17 questions, all the generator's, which a rescan copies."""
        row = await client_row(client, session, domain="rmit.com")
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)
        await ask_for_new_questions(client, session, row)

        stub_generator(monkeypatch, questions(17, prefix="small"))
        small_scan = await new_scan(session, row)
        small = await build(session, small_scan, row, settings)
        assert len(small.prompts) == 17 and small.generated_by == prompts.GENERATOR_MODEL
        assert small.generation_params["fromFallback"] == 0
        await session.refresh(row)
        assert row.new_questions_next_scan is False

        again = stub_generator(monkeypatch, questions(20, prefix="unused"))
        after = await build(session, await new_scan(session, row), row, settings)
        assert not again and after.generation_params["reusedFromScanId"] == small_scan.id

    @pytest.mark.parametrize("limit", [20, 25])
    async def test_a_limit_that_cuts_nothing_still_spends_the_flag(
        self, limit, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)
        await ask_for_new_questions(client, session, row)

        stub_generator(monkeypatch, questions(20, prefix="fresh"))
        fresh = await build(session, await new_scan(session, row), row, settings, limit=limit)
        assert len(fresh.prompts) == 20 and "limited" not in fresh.generation_params
        await session.refresh(row)
        assert row.new_questions_next_scan is False


class TestTheFlagIsReadFresh:
    """F6 review, fix 2: the scan reads the column when it builds its set.

    The client a scan is handed was loaded before the scan was claimed, and the session does not
    expire on commit, so a PUT committed in between is only in the database. Each test makes the
    PUT through the API, which runs in a session of its own, and first checks that the copy the
    scan is handed really is stale.
    """

    async def test_a_put_committed_after_the_client_was_loaded_is_obeyed(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)
        scan = await new_scan(session, row)

        put = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": True}
        )
        assert put.status_code == 200, put.text
        assert row.new_questions_next_scan is False  # the loaded copy has not seen the PUT

        calls = stub_generator(monkeypatch, questions(20, prefix="fresh"))
        fresh = await build(session, scan, row, settings)
        assert len(calls) == 1 and fresh.generated_by == prompts.GENERATOR_MODEL
        assert row.new_questions_next_scan is False  # and this set spent it

    async def test_a_take_back_committed_after_the_client_was_loaded_is_obeyed(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await build(session, await new_scan(session, row), row, settings)
        await ask_for_new_questions(client, session, row)
        scan = await new_scan(session, row)

        off = await client.put(
            f"{BASE}/clients/{row.id}/questions", json={"newQuestionsNextScan": False}
        )
        assert off.status_code == 200, off.text
        assert row.new_questions_next_scan is True  # the loaded copy has not seen the take-back

        calls = stub_generator(monkeypatch, questions(20, prefix="fresh"))
        copy = await build(session, scan, row, settings)
        assert not calls and copy.generated_by == question_sets.REUSED
        assert copy.generation_params["fingerprint"] == first.generation_params["fingerprint"]


async def the_load_everything_answer(
    session: AsyncSession, *, client_id: str, agency_id: str, exclude_scan_id: str
) -> tuple[PromptSet, str] | None:
    """What `clients_set` answered before it filtered in SQL: every set loaded, each tested here."""
    rows = await session.execute(
        select(PromptSet, PromptSet.scan_id)
        .join(Scan, Scan.id == PromptSet.scan_id)
        .where(
            Scan.client_id == client_id,
            Scan.agency_id == agency_id,
            Scan.id != exclude_scan_id,
        )
        .order_by(Scan.id.desc())
        .options(selectinload(PromptSet.prompts))
    )
    for prompt_set, scan_id in rows.all():
        if prompt_set.prompts and question_sets.is_all_generated(prompt_set):
            return prompt_set, scan_id
    return None


async def read_clients_set(
    session: AsyncSession, row: Client, exclude: Scan
) -> tuple[tuple[PromptSet, str] | None, list[str]]:
    """`clients_set` in a session of its own, and the id of every question it loaded to answer."""
    loaded: list[str] = []

    def count(target: Prompt, context: object) -> None:  # noqa: ARG001
        loaded.append(target.id)

    event.listen(Prompt, "load", count)
    try:
        async with async_sessionmaker(
            bind=session.bind, expire_on_commit=False, autoflush=False
        )() as reading:
            found = await question_sets.clients_set(
                reading, client_id=row.id, agency_id=row.agency_id, exclude_scan_id=exclude.id
            )
    finally:
        event.remove(Prompt, "load", count)
    return found, loaded


class TestTheSourceIsFoundInSql:
    """F6 review, fix 3: the database narrows to a few candidates, Python confirms them.

    `clients_set` used to load every past prompt set of the client with its questions and test each
    one in Python; a client rescanned thirty times carried thirty sets, twenty-nine of them copies.
    The WHERE clause now holds every test `is_all_generated` makes, so the answer is the same one.
    """

    async def test_only_the_candidates_questions_are_read_however_long_the_history(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        good_scan = await new_scan(session, row)
        await add_set(
            session, good_scan, generated_by=prompts.GENERATOR_MODEL,
            params={"fromGenerator": 20, "fromFallback": 0}, n=3, prefix="good",
        )
        for i, (name, params, n) in enumerate([*NEVER_THE_CLIENTS.values()] * 2):
            await add_set(
                session, await new_scan(session, row), generated_by=name, params=params,
                n=n, prefix=f"newer{i}",
            )
        found, loaded = await read_clients_set(session, row, await new_scan(session, row))
        assert found is not None and found[1] == good_scan.id
        assert len(loaded) == 3  # the good set's questions, and no other set's

    async def test_a_history_of_many_qualifying_sets_reads_only_the_window_of_the_newest(
        self, client, session  # noqa: ANN001
    ) -> None:
        """An agency that asked for new questions six times has six sets it could copy."""
        row = await client_row(client, session)
        scans = [await new_scan(session, row) for _ in range(6)]
        for i, scan in enumerate(scans):
            await add_set(
                session, scan, generated_by=prompts.GENERATOR_MODEL,
                params={"fromGenerator": 20, "fromFallback": 0}, n=2, prefix=f"kept{i}",
            )
        found, loaded = await read_clients_set(session, row, await new_scan(session, row))
        assert found is not None and found[1] == scans[-1].id  # the newest
        assert len(loaded) <= question_sets.CANDIDATES * 2

    @pytest.mark.parametrize("kind", sorted(NEVER_THE_CLIENTS))
    async def test_more_sets_of_one_kind_than_the_window_do_not_hide_the_clients_set(
        self, kind, client, session  # noqa: ANN001
    ) -> None:
        """The filter runs BEFORE the limit: the newest few sets are all of this kind."""
        row = await client_row(client, session)
        good_scan = await new_scan(session, row)
        await add_set(
            session, good_scan, generated_by=prompts.GENERATOR_MODEL,
            params={"fromGenerator": 20, "fromFallback": 0}, n=2, prefix="good",
        )
        name, params, n = NEVER_THE_CLIENTS[kind]
        for i in range(question_sets.CANDIDATES + 2):
            await add_set(
                session, await new_scan(session, row), generated_by=name, params=params,
                n=n, prefix=f"{kind}{i}",
            )
        this = await new_scan(session, row)
        found = await question_sets.clients_set(
            session, client_id=row.id, agency_id=row.agency_id, exclude_scan_id=this.id
        )
        assert found is not None and found[1] == good_scan.id

    async def test_the_python_check_has_the_last_word_over_the_candidates(
        self, client, session, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        counted = {"fromGenerator": 20, "fromFallback": 0}
        older = await new_scan(session, row)
        await add_set(session, older, generated_by=prompts.GENERATOR_MODEL, params=counted, n=2)
        newer = await new_scan(session, row)
        await add_set(session, newer, generated_by=prompts.GENERATOR_MODEL, params=counted, n=2)
        this = await new_scan(session, row)
        real = question_sets.is_all_generated
        monkeypatch.setattr(
            question_sets, "is_all_generated", lambda ps: ps.scan_id != newer.id and real(ps)
        )
        found = await question_sets.clients_set(
            session, client_id=row.id, agency_id=row.agency_id, exclude_scan_id=this.id
        )
        assert found is not None and found[1] == older.id

    async def test_it_answers_as_the_load_everything_version_did(
        self, client, session  # noqa: ANN001
    ) -> None:
        """Sixty random histories (a fixed seed), each asked both ways, from every direction."""
        row = await client_row(client, session)
        rng = random.Random(20261009)
        never = list(NEVER_THE_CLIENTS.values())
        stepped_over: list[int] = []
        answers: list[str | None] = []
        for _ in range(60):
            await session.execute(
                delete(Scan).where(Scan.client_id == row.id)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            made: list[Scan] = []
            for _ in range(rng.randint(0, 9)):
                scan = await new_scan(session, row)
                name, params, n = rng.choice(THE_CLIENTS if rng.random() < 0.25 else never)
                await add_set(session, scan, generated_by=name, params=params, n=n)
                made.append(scan)
            this = await new_scan(session, row)
            args = {
                "client_id": row.id, "agency_id": row.agency_id,
                "exclude_scan_id": rng.choice([this, *made]).id,
            }
            now = await question_sets.clients_set(session, **args)
            then = await the_load_everything_answer(session, **args)
            now_scan = now[1] if now else None
            assert now_scan == (then[1] if then else None), (
                [(s.id, s.id == args["exclude_scan_id"]) for s in made], now_scan
            )
            answers.append(now_scan)
            if now_scan is not None:
                stepped_over.append(
                    sum(1 for s in made if s.id > now_scan and s.id != args["exclude_scan_id"])
                )
        # The histories must reach the cases that matter, or this proves nothing.
        assert answers.count(None) >= 3 and len(stepped_over) >= 20
        assert max(stepped_over) > question_sets.CANDIDATES


class TestAnOldSetIsKnownByItsNameAndSize:
    """F6 review, round two: sets stored before the counts existed.

    Such a set has no `fromGenerator`. The first version named a set of nothing but templates
    "fallback", and wrote `requested` after a `promptLimit` had cut the set without marking the
    cut, so only the name and the number of questions can tell the client's set from the rest.
    """

    async def test_an_old_templates_only_set_is_not_copied_forever(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(
            session, await new_scan(session, row), generated_by="fallback",
            params={"requested": FULL}, n=FULL, prefix="template",
        )
        written_scan = await new_scan(session, row)
        written = await build(session, written_scan, row, settings)
        assert len(calls) == 1 and written.generated_by == prompts.GENERATOR_MODEL
        # The scan after that copies the set the generator wrote, never the templates.
        again = await build(session, await new_scan(session, row), row, settings)
        assert again.generation_params["reusedFromScanId"] == written_scan.id
        assert len(calls) == 1

    async def test_a_three_question_old_set_is_skipped_for_an_older_full_one(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """A verification scan with `promptLimit` 3 must not become the client's frozen set."""
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        full_scan = await new_scan(session, row)
        await add_set(
            session, full_scan, generated_by=prompts.GENERATOR_MODEL,
            params={"requested": FULL}, n=FULL, prefix="full",
        )
        await add_set(
            session, await new_scan(session, row), generated_by=prompts.GENERATOR_MODEL,
            params={"requested": 3}, n=3, prefix="cut",
        )
        copy = await build(session, await new_scan(session, row), row, settings)
        assert not calls
        assert copy.generation_params["reusedFromScanId"] == full_scan.id
        assert len(copy.prompts) == FULL

    @pytest.mark.parametrize(("held", "kept"), [(FULL - 1, False), (FULL, True), (24, True)])
    async def test_an_old_set_is_the_clients_at_the_minimum_and_not_below_it(
        self, held, kept, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        old_scan = await new_scan(session, row)
        await add_set(
            session, old_scan, generated_by=prompts.GENERATOR_MODEL,
            params={"requested": held}, n=held, prefix="old",
        )
        ps = await build(session, await new_scan(session, row), row, settings)
        if kept:
            assert not calls
            assert ps.generation_params["reusedFromScanId"] == old_scan.id
            assert len(ps.prompts) == held
        else:
            assert len(calls) == 1 and ps.generated_by == prompts.GENERATOR_MODEL

    async def test_a_set_with_counts_of_seventeen_generated_questions_is_still_the_clients(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """Counts exist: the size rule is for sets without them (no templates for this market)."""
        row = await client_row(client, session)
        calls = stub_generator(monkeypatch, questions(20))
        old_scan = await new_scan(session, row)
        await add_set(
            session, old_scan, generated_by=prompts.GENERATOR_MODEL,
            params={"fromGenerator": 17, "fromFallback": 0, "requested": 17}, n=17, prefix="p",
        )
        ps = await build(session, await new_scan(session, row), row, settings)
        assert not calls
        assert ps.generation_params["reusedFromScanId"] == old_scan.id
        assert len(ps.prompts) == 17


class TestTheScansOwnClientScopesTheLookup:
    """Defence in depth (F6 review): the lookup uses the scan's client and agency, not the job's."""

    @pytest.mark.parametrize("whose", ["same_agency", "other_agency"])
    async def test_a_client_object_that_is_not_the_scans_never_pulls_its_set(
        self, whose, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)  # the scan's own client, with no set of its own
        if whose == "same_agency":
            made = await client.post(
                f"{BASE}/clients", json={"url": "monash.com", "classify": False}
            )
            assert made.status_code == 201, made.text
            holder = await session.get(Client, made.json()["id"])
        else:
            transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
            async with AsyncClient(
                transport=transport, base_url="http://testserver",
                headers={"Origin": client.headers["Origin"]},
            ) as other:
                await sign_up(other, "agency-two@test.example")
                made = await other.post(
                    f"{BASE}/clients", json={"url": "deakin.com", "classify": False}
                )
                assert made.status_code == 201, made.text
            holder = await session.get(Client, made.json()["id"])
        assert holder is not None and holder.id != row.id
        calls = stub_generator(monkeypatch, questions(20))
        await add_set(
            session, await new_scan(session, holder), generated_by=prompts.GENERATOR_MODEL,
            params={"requested": FULL}, n=FULL, prefix="theirs",
        )
        scan = await new_scan(session, row)
        ps = await scan_runner.build_prompt_set(
            session, scan, holder, competitors=[], settings=settings
        )
        assert len(calls) == 1  # written for the scan's own client; nothing was copied
        assert "reusedFromScanId" not in ps.generation_params
