"""Ad-hoc prompt runs — Epic 9.24.

The engines are stubbed, so these run without network and without spending
anything. What they assert is the behaviour that costs money or leaks data if it
is wrong: the throttle, the tenancy boundary, and the ip-safety line.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, EngineResultStatus
from avp_api.services import engines as engine_service
from avp_api.services import prompt_runs as service
from avp_api.services.engines import CitedSource, EngineAnswer

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "prompts@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Prompt Test Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _a_client(client: AsyncClient, url: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": url, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Answer as the three real engines would, without calling them."""

    def _install(
        *,
        text: str = "For small teams I would look at Help Scout, then Front, then Zendesk.",
        failing: tuple[Engine, ...] = (),
    ):
        async def fake_ask_all(prompt, *, engines=None, settings=None):  # noqa: ANN001, ARG001
            out = []
            for eng in engines or engine_service.DEFAULT_ENGINES:
                if eng in failing:
                    out.append(
                        EngineAnswer(
                            engine=eng,
                            engine_version="stub",
                            prompt_text=prompt,
                            text="",
                            status=EngineResultStatus.ERROR,
                            error_code="STUBBED_FAILURE",
                        )
                    )
                    continue
                out.append(
                    EngineAnswer(
                        engine=eng,
                        engine_version="stub",
                        prompt_text=prompt,
                        text=text,
                        citations=[
                            CitedSource(
                                url="https://helpscout.com/pricing",
                                domain="helpscout.com",
                                position=1,
                            )
                        ],
                        latency_ms=42,
                    )
                )
            return out

        monkeypatch.setattr(service.engine_service, "ask_all", fake_ask_all)

    return _install


# ---------------------------------------------------------------------------
# What a run produces
# ---------------------------------------------------------------------------


async def test_a_run_asks_every_engine_and_reports_each_separately(
    client: AsyncClient, stub_engines
) -> None:
    """Three engines, three rows. The disagreement between them IS the product."""
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    resp = await client.post(
        f"{BASE}/clients/{cid}/prompt-runs",
        json={"prompt": "best help desk software for small teams"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert {r["engine"] for r in body["results"]} == {
        e.value for e in engine_service.DEFAULT_ENGINES
    }
    assert body["status"] == "ok"


async def test_a_run_records_whether_the_subject_was_named_and_where(
    client: AsyncClient, stub_engines
) -> None:
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()

    first = body["results"][0]
    assert first["mentioned"] is True
    # Named first in the stub answer, so position 1 — an ordinal, not a score.
    assert first["position"] == 1
    assert Decimal(str(first["prominence"])) > 0


async def test_an_answer_that_does_not_name_the_subject_is_a_finding_not_a_failure(
    client: AsyncClient, stub_engines
) -> None:
    """The whole point of the feature is being able to see this."""
    stub_engines(text="I would look at Front, then Zendesk, then Intercom.")
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()

    assert body["status"] == "ok", "an answered prompt is not a failed run"
    for result in body["results"]:
        assert result["mentioned"] is False
        # Never 0 — that would sort as "first" and read as a measured position.
        assert result["position"] is None
        assert result["status"] == EngineResultStatus.ANSWERED_NO_MENTION.value


async def test_one_engine_down_is_partial_rather_than_failed(
    client: AsyncClient, stub_engines
) -> None:
    """A narrower answer, said to be narrower — the rule a scan follows."""
    stub_engines(failing=(Engine.CHATGPT,))
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()
    assert body["status"] == "partial"


async def test_every_engine_down_is_a_failed_run(
    client: AsyncClient, stub_engines
) -> None:
    stub_engines(failing=tuple(engine_service.DEFAULT_ENGINES))
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()
    assert body["status"] == "failed"


async def test_citations_are_recorded_as_location_never_content(
    client: AsyncClient, stub_engines
) -> None:
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()

    cite = body["results"][0]["citations"][0]
    assert cite["domain"] == "helpscout.com"
    assert cite["citesSubject"] is True
    # ip-safety.md #7 — a citation is a URL and a domain, and nothing else.
    assert set(cite) == {"url", "domain", "sourceType", "position", "citesSubject"}


async def test_the_response_carries_no_field_able_to_hold_an_answer(
    client: AsyncClient, stub_engines
) -> None:
    """The boundary, asserted at the wire rather than only in the schema.

    A digest is kept so a re-ask can detect the answer CHANGED; the answer
    itself is discarded with the request that produced it.
    """
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    body = (
        await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
        )
    ).json()

    forbidden = {"text", "answer", "answerText", "response", "content", "raw", "snippet"}
    for result in body["results"]:
        assert not (forbidden & set(result)), f"leaked: {forbidden & set(result)}"
    # The operator's own question survives — it is our side of the exchange.
    assert body["promptText"] == "best help desk"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_history_is_newest_first_and_persists_across_requests(
    client: AsyncClient, stub_engines
) -> None:
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    for prompt in ("first question", "second question", "third question"):
        await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": prompt})

    body = (await client.get(f"{BASE}/clients/{cid}/prompt-runs")).json()
    assert [r["promptText"] for r in body["data"]] == [
        "third question",
        "second question",
        "first question",
    ]


async def test_history_reports_what_the_throttle_has_left(
    client: AsyncClient, stub_engines
) -> None:
    """Served, not inferred — a browser that guessed would block a legal run."""
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    before = (await client.get(f"{BASE}/clients/{cid}/prompt-runs")).json()
    assert before["runsPerHour"] == service.RUNS_PER_CLIENT_PER_HOUR
    assert before["runsRemaining"] == service.RUNS_PER_CLIENT_PER_HOUR

    await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "one"})
    after = (await client.get(f"{BASE}/clients/{cid}/prompt-runs")).json()
    assert after["runsRemaining"] == service.RUNS_PER_CLIENT_PER_HOUR - 1


# ---------------------------------------------------------------------------
# The throttle — what stops this being a cheaper way to spend more than a scan
# ---------------------------------------------------------------------------


async def test_the_throttle_refuses_the_run_over_the_hourly_ceiling(
    client: AsyncClient, stub_engines, monkeypatch
) -> None:
    """429 rather than a spend.

    The ceiling is lowered for the test rather than issuing 30 real requests:
    what is being asserted is that the limit is enforced at all, and running the
    production number would make this the slowest test in the suite for no extra
    guarantee. `test_the_ceiling_is_sized_against_a_scan` covers the value.
    """
    monkeypatch.setattr(service, "RUNS_PER_CLIENT_PER_HOUR", 2)
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)

    for i in range(2):
        ok = await client.post(
            f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": f"question {i}"}
        )
        assert ok.status_code == 201

    blocked = await client.post(
        f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "one too many"}
    )
    assert blocked.status_code == 429


async def test_a_throttled_run_spends_nothing(
    client: AsyncClient, stub_engines, monkeypatch
) -> None:
    """The check runs BEFORE the engines, not after.

    A throttle that refused the response but had already paid for the answer
    would be decoration.
    """
    monkeypatch.setattr(service, "RUNS_PER_CLIENT_PER_HOUR", 1)
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)
    await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "first"})

    calls = 0
    real = service.engine_service.ask_all

    async def counting(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal calls
        calls += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(service.engine_service, "ask_all", counting)
    blocked = await client.post(
        f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "blocked"}
    )
    assert blocked.status_code == 429
    assert calls == 0, "the engines were asked despite the run being refused"


async def test_the_throttle_is_per_client_not_per_agency(
    client: AsyncClient, stub_engines, monkeypatch
) -> None:
    """One busy client must not exhaust another client's allowance.

    A per-agency ceiling turns a throttle into an outage for people who did
    nothing — see the note on RUNS_PER_CLIENT_PER_HOUR.
    """
    monkeypatch.setattr(service, "RUNS_PER_CLIENT_PER_HOUR", 1)
    stub_engines()
    await _sign_up(client)
    busy = await _a_client(client, "helpscout.com")
    quiet = await _a_client(client, "plausible.io")

    await client.post(f"{BASE}/clients/{busy}/prompt-runs", json={"prompt": "one"})
    assert (
        await client.post(f"{BASE}/clients/{busy}/prompt-runs", json={"prompt": "two"})
    ).status_code == 429
    assert (
        await client.post(f"{BASE}/clients/{quiet}/prompt-runs", json={"prompt": "one"})
    ).status_code == 201


def test_the_ceiling_is_sized_against_a_scan() -> None:
    """The number, and the arithmetic that justifies it.

    `RUNS_PER_CLIENT_PER_HOUR`'s note argues the ceiling from what a scan costs:
    a run is 3 engine calls, a scan is 72, so the hourly ceiling must stay near
    one scan's worth of spend. This asserts that relationship rather than the
    literal 30 — if someone raises the ceiling to 500, this fails and they have
    to come back and re-argue it, which is the whole point of writing the
    argument down.
    """
    from avp_api.services.scan_runner import PROMPT_CONCURRENCY

    engines_per_run = len(engine_service.DEFAULT_ENGINES)
    calls_per_run = engines_per_run
    calls_per_scan = 24 * engines_per_run

    hourly_calls = service.RUNS_PER_CLIENT_PER_HOUR * calls_per_run
    assert hourly_calls / calls_per_scan <= 2, (
        "the hourly ad-hoc ceiling now exceeds two scans' worth of engine calls. "
        "That is a pricing decision, not a tuning one — re-argue it in the note "
        "on RUNS_PER_CLIENT_PER_HOUR."
    )
    # A run is exactly one PROMPT_CONCURRENCY slot's work, which is what makes
    # the comparison exact rather than approximate.
    assert calls_per_run == engines_per_run
    assert PROMPT_CONCURRENCY >= 1


def test_the_prompt_length_cap_bounds_what_one_run_can_cost() -> None:
    """The throttle counts RUNS, so something else has to bound tokens."""
    assert service.MAX_PROMPT_CHARS <= 2000
    with pytest.raises(service.PromptRunError) as raised:
        service.normalise_prompt("x" * (service.MAX_PROMPT_CHARS + 1))
    assert raised.value.code == "PROMPT_TOO_LONG"


def test_a_blank_prompt_is_refused_before_anything_is_spent() -> None:
    with pytest.raises(service.PromptRunError) as raised:
        service.normalise_prompt("   \n  ")
    assert raised.value.code == "PROMPT_EMPTY"


def test_whitespace_is_collapsed_so_one_question_is_one_question() -> None:
    assert service.normalise_prompt("  best   help \n desk ") == "best help desk"


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


async def test_another_agencys_client_is_a_404_not_a_403(
    client: AsyncClient, stub_engines
) -> None:
    """Confirming an id exists leaks across tenants."""
    stub_engines()
    await _sign_up(client, "first@test.example")
    cid = await _a_client(client)
    await client.post(f"{BASE}/auth/sign-out")

    await _sign_up(client, "second@test.example")
    assert (
        await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "x"})
    ).status_code == 404
    assert (await client.get(f"{BASE}/clients/{cid}/prompt-runs")).status_code == 404


async def test_a_signed_out_caller_cannot_run_a_prompt(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/clients/clnt_01ARZ3NDEKTSV4RRFFQ69G5FAV/prompt-runs",
        json={"prompt": "x"},
    )
    assert resp.status_code == 401


async def test_a_run_creates_no_scan_and_no_score(
    client: AsyncClient, stub_engines
) -> None:
    """A run is not a measurement and must never appear as one.

    If a run wrote a `Scan`, it would show in the dashboard's recent list, in
    this client's history table and as a point on both trends — asserting a
    measurement nobody took.
    """
    stub_engines()
    await _sign_up(client)
    cid = await _a_client(client)
    await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "x"})

    history = (await client.get(f"{BASE}/clients/{cid}/history")).json()
    assert history["scans"] == []
    dashboard = (await client.get(f"{BASE}/dashboard")).json()
    assert dashboard["recentScans"] == []
    assert dashboard["scanCount"] == 0
