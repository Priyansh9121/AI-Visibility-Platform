"""When one slot of a fan-out fails, the others are ended (checklist 3.77, U1).

`asyncio.gather` hands the first exception to its caller and leaves every other task running, so
a failed scan's remaining prompt slots, a failed detection's remaining searches and seed prompts
and a failed prompt's remaining engines went on making paid calls whose answers nobody read.
`concurrency.gather_cancelling` ends them. Each test makes one slot raise while its siblings are
mid-call (held on an event nobody sets, so nothing here depends on a clock) and asserts that the
siblings were cancelled and that the caller got the original exception, of its own type.

A cancelled call is not an answered one: `TestACancelledCallIsNotAnAnsweredOne` pins what the
usage lines say of it. The engine-loop group of `scan_runner.run_scan` is in
`test_scan_deadline.py`, beside the harness it needs.
"""

from __future__ import annotations

import asyncio

import pytest
import structlog

from avp_api.models.engine_result import Engine
from avp_api.services import cocitation, engines, serp, usage
from avp_api.services.concurrency import gather_cancelling


class Boom(Exception):  # noqa: N818 - a stand-in for any bug, not an error class of ours
    """The one failure the callers must see, as itself."""


class Fanout:
    """`n` slots that wait for ever and one that fails once they are all waiting."""

    def __init__(self, waiting: int) -> None:
        self.waiting = waiting
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.finished: list[str] = []
        self.all_waiting = asyncio.Event()

    async def wait(self, name: str) -> str:
        self.started.append(name)
        if len(self.started) == self.waiting:
            self.all_waiting.set()
        try:
            await asyncio.Event().wait()  # nobody sets it: only a cancellation ends this
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        self.finished.append(name)
        return name

    async def fail(self) -> str:
        async with asyncio.timeout(30):  # a safety net for a broken fix, never a pause
            await self.all_waiting.wait()
        raise Boom

    def assert_siblings_ended(self, names: list[str]) -> None:
        assert sorted(self.started) == sorted(names), "a sibling never started"
        assert sorted(self.cancelled) == sorted(names), "a sibling was left running"
        assert self.finished == []


class TestTheHelper:
    async def test_the_first_error_cancels_the_siblings_and_is_raised_as_itself(self) -> None:
        fan = Fanout(2)
        with pytest.raises(Boom) as raised:
            await gather_cancelling(fan.wait("a"), fan.fail(), fan.wait("b"))
        assert type(raised.value) is Boom
        fan.assert_siblings_ended(["a", "b"])

    async def test_nothing_to_run_is_an_empty_list(self) -> None:
        assert await gather_cancelling() == []

    async def test_results_come_back_in_the_order_given(self) -> None:
        later = asyncio.Event()

        async def finishes_second() -> str:
            await later.wait()
            return "first given"

        async def finishes_first() -> str:
            later.set()
            return "second given"

        assert await gather_cancelling(finishes_second(), finishes_first()) == [
            "first given", "second given"
        ]

    async def test_a_cancellation_of_the_caller_cancels_every_child(self) -> None:
        fan = Fanout(2)
        task = asyncio.ensure_future(gather_cancelling(fan.wait("a"), fan.wait("b")))
        await fan.all_waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        fan.assert_siblings_ended(["a", "b"])

    async def test_a_cancellation_that_lands_as_a_child_fails_is_still_a_cancellation(self) -> None:
        fan = Fanout(1)

        async def fail_when_cancelled() -> None:
            await fan.all_waiting.wait()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise Boom from None

        task = asyncio.ensure_future(gather_cancelling(fan.wait("a"), fail_when_cancelled()))
        await fan.all_waiting.wait()
        await asyncio.sleep(0)  # let the second slot reach its wait
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestEachGroup:
    async def test_the_seed_prompts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fan = Fanout(2)

        async def run_seed_prompt(prompt, **kwargs):  # noqa: ANN001, ANN003, ANN202
            return await fan.fail() if prompt == "boom" else await fan.wait(prompt)

        # `conftest.py` fakes `run_seed_prompts` for every test; this one needs the real group.
        monkeypatch.undo()
        monkeypatch.setattr(cocitation, "run_seed_prompt", run_seed_prompt)
        with pytest.raises(Boom):
            await cocitation.run_seed_prompts(
                ["one", "boom", "two"], subject_brand="Acme", settings=None
            )
        fan.assert_siblings_ended(["one", "two"])

    async def test_the_searches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fan = Fanout(2)

        async def search(query, **kwargs):  # noqa: ANN001, ANN003, ANN202
            return await fan.fail() if query == "boom" else await fan.wait(query)

        monkeypatch.undo()  # `conftest.py` fakes `search_many` too
        monkeypatch.setattr(serp, "search", search)
        with pytest.raises(Boom):
            await serp.search_many(["one", "boom", "two"], settings=None)
        fan.assert_siblings_ended(["one", "two"])

    async def test_the_engines_of_one_prompt(self, settings, monkeypatch) -> None:  # noqa: ANN001
        fan = Fanout(2)

        async def ask_gated(engine, prompt, settings):  # noqa: ANN001, ARG001, ANN202
            return await fan.fail() if engine is Engine.CHATGPT else await fan.wait(engine.value)

        monkeypatch.setattr(engines, "_ask_gated", ask_gated)
        others = [e for e in engines.DEFAULT_ENGINES if e is not Engine.CHATGPT][:2]
        with pytest.raises(Boom):
            await engines.ask_all(
                "q", engines=(others[0], Engine.CHATGPT, others[1]), settings=settings
            )
        fan.assert_siblings_ended([e.value for e in others])


class TestACancelledCallIsNotAnAnsweredOne:
    async def test_it_is_logged_and_counted_as_unanswered_with_no_tokens(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """What a sibling's cancellation leaves in the usage lines: a request with no answer."""
        fan = Fanout(2)

        async def ask_gated(engine, prompt, settings):  # noqa: ANN001, ARG001, ANN202
            if engine is Engine.CHATGPT:
                return await fan.fail()
            with usage.track(usage.ANTHROPIC, usage.Purpose.ENGINE, model="m", engine=engine.value):
                return await fan.wait(engine.value)

        monkeypatch.setattr(engines, "_ask_gated", ask_gated)
        others = [e for e in engines.DEFAULT_ENGINES if e is not Engine.CHATGPT][:2]
        with (
            structlog.testing.capture_logs() as events,
            usage.scan_scope("scan_1") as scan,
            pytest.raises(Boom),
        ):
            await engines.ask_all(
                "q", engines=(others[0], Engine.CHATGPT, others[1]), settings=settings
            )

        calls = [e for e in events if e["event"] == "llm.call"]
        assert len(calls) == 2
        assert all(c["ok"] is False and c["error"] == "CancelledError" for c in calls)
        assert all(c["input_tokens"] == 0 and c["output_tokens"] == 0 for c in calls)
        line = scan.log_fields()
        assert line["engines_calls"] == 2
        assert line["engines_unanswered"] == 2
        assert "engines_model" not in line, "a cancelled call named a model that served it"
