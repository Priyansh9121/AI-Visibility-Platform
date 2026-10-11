"""Paid AI calls outside a scan are gated, throttled atomically and capped — F3, 2026-10-10.

Three routes pay a provider and are not a scan: ad hoc questions
(`POST /clients/{id}/prompt-runs`), a scan's fixes written again (`POST /scans/{id}/fixes`) and a
site classified (`POST /clients` with `classify`, `POST /clients/{id}/reclassify`). Each now has,
in this order, a plan gate, a per-caller limit taken ATOMICALLY in Redis before the call, and
D2's platform cap (`services/spend_caps`).

The providers are stubbed where each route reaches them and the stubs COUNT their calls, so "at
most the limit reaches a provider" is read off a counter: `ask_all` for questions,
`fix_runner.generate_for_scan` for fixes, `intake.run_classification` (the crawl and the model
call) for classifications.
"""

from __future__ import annotations

import ast
import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update

import avp_api
from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.db import get_sessionmaker
from avp_api.errors import PlatformCapReached, RateLimited
from avp_api.models import Agency, Client, Invitation, Scan, ScanStatus, ScanTrigger, User
from avp_api.models.engine_result import Sentiment
from avp_api.security import token_digest
from avp_api.services import browser_slot, fix_runner, intake, rate_limit, spend_caps
from avp_api.services import engines as engine_service
from avp_api.services import prompt_runs as prompt_service
from avp_api.services.browser_slot import crawler_busy
from avp_api.services.engines import EngineAnswer

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
OWNER = "owner@paid-calls.example"
OPERATOR = "operator@example.com"

# What the OWNER is told: the owner starts a plan, in Settings, under Billing.
PROMPTS_NEED_PLAN = "Asking your own questions needs a plan. Start one in Settings, under Billing."
FIXES_NEED_PLAN = "Writing a scan's fixes again needs a plan. Start one in Settings, under Billing."
# What an admin or a member is told (launch checklist 3.130, 2026-10-11): neither can start a plan,
# so each is told what the founder's line says. Written out here rather than imported.
PROMPTS_ASK_AN_OWNER = "Asking your own questions needs a plan. Ask an owner to manage billing."
FIXES_ASK_AN_OWNER = "Writing a scan's fixes again needs a plan. Ask an owner to manage billing."
PROMPT_CAP = (
    "AVP has answered as many questions as it allows in a day. Try again after midnight UTC."
)
FIX_CAP = "AVP has written as many fixes as it allows in a day. Try again after midnight UTC."
CLASSIFY_CAP = (
    "AVP has classified as many sites as it allows in a day. Try again after midnight UTC."
)
AGENCY_CLASSIFY_CAP = (
    "This agency has classified as many sites as it can today. Try again after midnight UTC."
)
UNAVAILABLE = "This is temporarily unavailable. Please try again shortly."


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def billing_off(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "stripe_price_id", None)


@pytest.fixture
def billing_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The three providers, stubbed where each route reaches them, each counting its calls."""
    seen = SimpleNamespace(engines=0, fixes=0, classify=0)

    async def fake_ask_all(prompt, *, engines=None, settings=None):  # noqa: ANN001, ARG001
        seen.engines += 1
        await asyncio.sleep(0.01)  # let a burst interleave at the provider
        return [
            EngineAnswer(
                engine=e, engine_version="stub", prompt_text=prompt,
                text="Help Scout is a good option.", citations=[], latency_ms=1,
            )
            for e in (engines or engine_service.DEFAULT_ENGINES)
        ]

    async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    async def fake_fixes(session, scan, client, *, settings=None, persist=True):  # noqa: ANN001, ARG001
        seen.fixes += 1
        await asyncio.sleep(0.01)
        return [], SimpleNamespace(status="generated", reason_code=None)

    async def fake_candidates(session, scan):  # noqa: ANN001, ARG001
        return ["a candidate"]

    async def fake_classification(session, client, *, settings=None, max_pages=3):  # noqa: ANN001, ARG001
        seen.classify += 1
        await asyncio.sleep(0.01)
        return client, None

    monkeypatch.setattr(prompt_service.engine_service, "ask_all", fake_ask_all)
    monkeypatch.setattr(prompt_service.extraction_service, "classify_sentiment", fake_sentiment)
    monkeypatch.setattr(fix_runner, "generate_for_scan", fake_fixes)
    monkeypatch.setattr(fix_runner, "build_candidates_for", fake_candidates)
    monkeypatch.setattr(intake, "run_classification", fake_classification)
    return seen


# --------------------------------------------------------------------------- helpers


async def _sign_up(
    client: AsyncClient, email: str = OWNER, *, google: bool = False, agency: str = "Paid Agency"
) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Op", "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    if google:
        async with get_sessionmaker()() as db:
            user = (
                await db.execute(select(User).where(User.id == resp.json()["user"]["id"]))
            ).scalar_one()
            user.google_sub = f"sub-{email}"
            await db.commit()
    return resp.json()["agency"]["id"]


async def _become(client: AsyncClient, agency_id: str, role: str) -> None:
    """Invite a seat of `role`, accept the invitation, and end up signed in as them.

    Accepting sets the seat's cookie on `client`, replacing the owner's: every call after this one
    is made as that seat, in the agency the owner made.
    """
    email = f"{role}@paid-calls.example"
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": role}
    )
    assert invited.status_code == 201, invited.text
    token = f"test-token-{role}"
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await db.commit()
    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Seat Person", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["role"] == role


async def _a_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _a_scan(agency_id: str, client_id: str) -> str:
    scan_id = ids.new_id(ids.SCAN)
    async with get_sessionmaker()() as db:
        db.add(
            Scan(
                id=scan_id, client_id=client_id, agency_id=agency_id,
                status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL,
                formula_version="test", prompt_count=0, engine_result_count=0,
            )
        )
        await db.commit()
    return scan_id


async def _subscribe(agency_id: str, status: str | None) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Agency).where(Agency.id == agency_id).values(subscription_status=status)
        )
        await db.commit()


async def _keys(settings: Settings, pattern: str) -> list[str]:
    redis = redis_module.get_redis(settings)
    return sorted([k async for k in redis.scan_iter(pattern)])


async def _all_claims(settings: Settings) -> list[str]:
    """Every key a claim of any kind could have written: none means nothing was spent."""
    return [
        *await _keys(settings, "cap:*"),
        *await _keys(settings, "rl:prompt_run:*"),
        *await _keys(settings, "rl:fix_regenerate:*"),
    ]


async def _cap_count(settings: Settings, kind: str) -> int:
    raw = await redis_module.get_redis(settings).get(spend_caps.day_key(kind, datetime.now(UTC)))
    return int(raw) if raw else 0


async def _client_rows(agency_id: str) -> int:
    async with get_sessionmaker()() as db:
        return int(
            (
                await db.execute(
                    select(func.count()).select_from(Client).where(Client.agency_id == agency_id)
                )
            ).scalar_one()
        )


def _ask(client: AsyncClient, cid: str, text: str = "best help desk") -> Any:
    return client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": text})


def _fixes(client: AsyncClient, sid: str) -> Any:
    return client.post(f"{BASE}/scans/{sid}/fixes")


def _create(client: AsyncClient, domain: str) -> Any:
    return client.post(f"{BASE}/clients", json={"url": domain, "classify": True})


def _reclassify(client: AsyncClient, cid: str) -> Any:
    return client.post(f"{BASE}/clients/{cid}/reclassify")


def _no_number(resp: Any) -> None:
    """A platform cap's body says no limit and no count: the sentence has no digit, and no
    field carries one beyond the status, the Retry-After seconds being a header."""
    body = resp.json()
    assert not re.search(r"\d", body["detail"]), body["detail"]
    assert set(body) == {"type", "title", "status", "detail", "instance", "cap"}, body
    assert "limit" not in resp.text.lower().replace("daily limit reached", "")


@pytest.fixture(autouse=True)
async def _fresh_slot():  # noqa: ANN202
    """The browser slot's per-agency count is process-wide; leave it as found."""
    yield
    browser_slot._agencies_with_a_run.clear()  # noqa: SLF001


# =========================================================================== (a) prompt runs


class TestPromptRuns:
    async def test_twenty_parallel_questions_let_at_most_the_limit_reach_the_engines(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "prompt_runs_per_client_per_hour", 3)
        await _sign_up(client)
        cid = await _a_client(client)

        got = await asyncio.gather(*(_ask(client, cid, f"question {i}") for i in range(20)))

        statuses = sorted(r.status_code for r in got)
        assert statuses == [201] * 3 + [429] * 17, statuses
        assert calls.engines == 3
        assert await _cap_count(settings, "prompt_runs") == 3
        refused = next(r for r in got if r.status_code == 429)
        assert refused.json()["type"] == "/problems/rate-limited"
        assert "This client has run 3 prompts in the last hour, which is the limit of 3." in (
            refused.json()["detail"]
        )
        assert 1 <= int(refused.headers["Retry-After"]) <= 3600

    async def test_the_row_count_still_refuses_after_the_redis_claims_are_wiped(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The second check survives a Redis wipe, and its refusal spends no claim."""
        monkeypatch.setattr(settings, "prompt_runs_per_client_per_hour", 2)
        await _sign_up(client)
        cid = await _a_client(client)
        for i in range(2):
            assert (await _ask(client, cid, f"q{i}")).status_code == 201
        await redis_module.get_redis(settings).delete(*await _keys(settings, "rl:prompt_run:*"))
        before = await _cap_count(settings, "prompt_runs")

        blocked = await _ask(client, cid, "one too many")

        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1
        assert calls.engines == 2
        assert await _keys(settings, "rl:prompt_run:*") == []  # the refusal took no claim
        assert await _cap_count(settings, "prompt_runs") == before

    @pytest.mark.parametrize("subscription", [None, "past_due", "canceled", "incomplete"])
    async def test_without_a_plan_it_is_a_402_and_nothing_is_spent(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace,
        subscription: str | None,
    ) -> None:
        agency_id = await _sign_up(client)
        await _subscribe(agency_id, subscription)
        cid = await _a_client(client)

        with structlog.testing.capture_logs() as logs:
            resp = await _ask(client, cid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["type"] == "/problems/subscription-required"
        assert resp.json()["detail"] == PROMPTS_NEED_PLAN
        assert calls.engines == 0
        assert await _all_claims(settings) == []
        refusal = [e for e in logs if e["event"] == "prompt_run.subscription_required"]
        assert len(refusal) == 1
        assert set(refusal[0]) == {"event", "log_level", "agency_id", "client_id"}
        assert OWNER not in repr(logs)

    @pytest.mark.parametrize("role", ["admin", "member"])
    async def test_an_admin_or_a_member_is_told_to_ask_an_owner_and_nothing_is_spent(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace,
        role: str,
    ) -> None:
        """Neither can start a plan, so the owner's "Start one in Settings" is not said to them."""
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        await _become(client, agency_id, role)

        with structlog.testing.capture_logs() as logs:
            resp = await _ask(client, cid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["type"] == "/problems/subscription-required"
        assert resp.json()["detail"] == PROMPTS_ASK_AN_OWNER
        assert "Settings" not in resp.text
        assert calls.engines == 0
        assert await _all_claims(settings) == []
        refusal = [e for e in logs if e["event"] == "prompt_run.subscription_required"]
        assert len(refusal) == 1
        assert set(refusal[0]) == {"event", "log_level", "agency_id", "client_id"}

    @pytest.mark.parametrize("who", ["active", "trialing", "operator", "billing-off"])
    async def test_a_paid_agency_the_operator_and_an_ungated_platform_are_allowed(
        self, client: AsyncClient, settings: Settings, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, who: str,
    ) -> None:
        if who == "billing-off":
            monkeypatch.setattr(settings, "stripe_secret_key", None)
            monkeypatch.setattr(settings, "stripe_price_id", None)
        else:
            monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
            monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")
        if who == "operator":
            monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        agency_id = await _sign_up(
            client, OPERATOR if who == "operator" else OWNER, google=who == "operator"
        )
        if who in ("active", "trialing"):
            await _subscribe(agency_id, who)
        cid = await _a_client(client)

        resp = await _ask(client, cid)

        assert resp.status_code == 201, resp.text
        assert calls.engines == 1
        # Billing off or on, the platform's day is still counted.
        assert await _cap_count(settings, "prompt_runs") == 1

    async def test_the_platform_cap_refuses_with_its_own_sentence_and_no_number(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_prompt_runs_per_day_platform", 1)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        assert (await _ask(client, cid, "first")).status_code == 201

        with structlog.testing.capture_logs() as logs:
            resp = await _ask(client, cid, "second")

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["type"] == "/problems/platform-cap-reached"
        assert body["title"] == "Daily limit reached"
        assert body["detail"] == PROMPT_CAP
        assert body["cap"] == "prompt_runs"
        _no_number(resp)
        assert 1 <= int(resp.headers["Retry-After"]) <= 86400
        assert calls.engines == 1
        reached = [e for e in logs if e["event"] == "ai.platform_cap_reached"]
        assert reached == [
            {"event": "ai.platform_cap_reached", "log_level": "warning",
             "cap": "prompt_runs", "limit": 1, "agency_id": agency_id}
        ]

    async def test_cap_zero_refuses_every_question(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_prompt_runs_per_day_platform", 0)
        await _sign_up(client)
        cid = await _a_client(client)
        assert (await _ask(client, cid)).status_code == 503
        assert calls.engines == 0

    @pytest.mark.parametrize(
        "which", [rate_limit, spend_caps], ids=["client-claim", "platform-claim"]
    )
    async def test_redis_down_is_a_503_and_nothing_is_asked(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, which: Any,
    ) -> None:
        await _sign_up(client)
        cid = await _a_client(client)

        async def down(*args: Any, **kwargs: Any) -> Any:
            raise OSError("SENTINEL-redis-host is down")

        monkeypatch.setattr(which, "_eval", down)
        with structlog.testing.capture_logs() as logs:
            resp = await _ask(client, cid)

        assert resp.status_code == 503, resp.text
        assert resp.json()["type"] == "/problems/service-unavailable"
        assert resp.json()["detail"] == UNAVAILABLE
        assert "SENTINEL" not in resp.text and "SENTINEL" not in repr(logs)
        assert calls.engines == 0

    async def test_a_blank_prompt_takes_no_claim(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _a_client(client)
        resp = await client.post(f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "   "})
        assert resp.status_code in (422, 400)
        assert calls.engines == 0
        assert await _all_claims(settings) == []

    async def test_another_agencys_client_is_a_404_whatever_the_plan(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _a_client(client)
        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, "other@paid-calls.example", agency="Other Agency")
        resp = await _ask(client, cid)
        assert resp.status_code == 404
        assert await _all_claims(settings) == []


# =========================================================================== (b) fixes


class TestFixRegeneration:
    async def test_twenty_parallel_regenerations_of_one_scan_reach_the_model_once(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))

        got = await asyncio.gather(*(_fixes(client, sid) for _ in range(20)))

        assert sorted(r.status_code for r in got) == [201] + [429] * 19
        assert calls.fixes == 1
        assert await _cap_count(settings, "fix_runs") == 1

    async def test_twenty_parallel_regenerations_across_scans_stop_at_the_platform_cap(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_fix_runs_per_day_platform", 4)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        scans = [await _a_scan(agency_id, cid) for _ in range(20)]

        got = await asyncio.gather(*(_fixes(client, sid) for sid in scans))

        assert sorted(r.status_code for r in got) == [201] * 4 + [503] * 16
        assert calls.fixes == 4
        assert await _cap_count(settings, "fix_runs") == 4

    async def test_the_second_regeneration_in_the_window_is_refused_and_makes_no_call(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))
        assert (await _fixes(client, sid)).status_code == 201

        with structlog.testing.capture_logs() as logs:
            again = await _fixes(client, sid)

        assert again.status_code == 429, again.text
        assert again.json()["type"] == "/problems/rate-limited"
        assert re.fullmatch(
            r"This scan's fixes were written again recently\. They can be written again "
            r"in about (a minute|\d+ minutes|\d+ hours)\.",
            again.json()["detail"],
        ), again.json()["detail"]
        assert 1 <= int(again.headers["Retry-After"]) <= settings.fix_regenerate_cooldown_seconds
        assert calls.fixes == 1
        assert await _cap_count(settings, "fix_runs") == 1  # the refusal took nothing more
        limited = [e for e in logs if e["event"] == "fixes.regenerate.rate_limited"]
        assert len(limited) == 1
        assert set(limited[0]) == {"event", "log_level", "scan_id", "agency_id", "retry_after_s"}
        assert OWNER not in repr(logs)

    async def test_after_the_window_it_is_allowed_again(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))
        assert (await _fixes(client, sid)).status_code == 201
        (key,) = await _keys(settings, "rl:fix_regenerate:*")
        redis = redis_module.get_redis(settings)
        # The cooldown is the setting, on the key itself.
        ttl = await redis.pttl(key)
        assert settings.fix_regenerate_cooldown_seconds * 1000 - 30_000 <= ttl
        assert ttl <= settings.fix_regenerate_cooldown_seconds * 1000

        await redis.pexpire(key, 50)
        await asyncio.sleep(0.2)

        assert (await _fixes(client, sid)).status_code == 201
        assert calls.fixes == 2

    async def test_a_different_scan_has_its_own_cooldown(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        one, two = await _a_scan(agency_id, cid), await _a_scan(agency_id, cid)
        assert (await _fixes(client, one)).status_code == 201
        assert (await _fixes(client, two)).status_code == 201

    @pytest.mark.parametrize("subscription", [None, "past_due", "canceled"])
    async def test_without_a_plan_it_is_a_402_and_nothing_is_spent(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace,
        subscription: str | None,
    ) -> None:
        agency_id = await _sign_up(client)
        await _subscribe(agency_id, subscription)
        sid = await _a_scan(agency_id, await _a_client(client))

        with structlog.testing.capture_logs() as logs:
            resp = await _fixes(client, sid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["type"] == "/problems/subscription-required"
        assert resp.json()["detail"] == FIXES_NEED_PLAN
        assert calls.fixes == 0
        assert await _all_claims(settings) == []
        refusal = [e for e in logs if e["event"] == "fixes.subscription_required"]
        assert len(refusal) == 1
        assert set(refusal[0]) == {"event", "log_level", "agency_id", "scan_id"}
        assert OWNER not in repr(logs)

    @pytest.mark.parametrize("role", ["admin", "member"])
    async def test_an_admin_or_a_member_is_told_to_ask_an_owner_and_nothing_is_spent(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace,
        role: str,
    ) -> None:
        """Neither can start a plan, so the owner's "Start one in Settings" is not said to them."""
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))
        await _become(client, agency_id, role)

        with structlog.testing.capture_logs() as logs:
            resp = await _fixes(client, sid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["type"] == "/problems/subscription-required"
        assert resp.json()["detail"] == FIXES_ASK_AN_OWNER
        assert "Settings" not in resp.text
        assert calls.fixes == 0
        assert await _all_claims(settings) == []
        refusal = [e for e in logs if e["event"] == "fixes.subscription_required"]
        assert len(refusal) == 1
        assert set(refusal[0]) == {"event", "log_level", "agency_id", "scan_id"}

    @pytest.mark.parametrize("who", ["active", "trialing", "operator", "billing-off"])
    async def test_a_paid_agency_the_operator_and_an_ungated_platform_are_allowed(
        self, client: AsyncClient, settings: Settings, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, who: str,
    ) -> None:
        if who == "billing-off":
            monkeypatch.setattr(settings, "stripe_secret_key", None)
            monkeypatch.setattr(settings, "stripe_price_id", None)
        else:
            monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
            monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")
        if who == "operator":
            monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        agency_id = await _sign_up(
            client, OPERATOR if who == "operator" else OWNER, google=who == "operator"
        )
        if who in ("active", "trialing"):
            await _subscribe(agency_id, who)
        sid = await _a_scan(agency_id, await _a_client(client))

        assert (await _fixes(client, sid)).status_code == 201
        assert calls.fixes == 1

    async def test_the_platform_cap_refuses_with_its_own_sentence_and_no_number(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_fix_runs_per_day_platform", 1)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        one, two = await _a_scan(agency_id, cid), await _a_scan(agency_id, cid)
        assert (await _fixes(client, one)).status_code == 201

        with structlog.testing.capture_logs() as logs:
            resp = await _fixes(client, two)

        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == FIX_CAP
        assert resp.json()["cap"] == "fix_runs"
        assert resp.json()["title"] == "Daily limit reached"
        _no_number(resp)
        assert calls.fixes == 1
        assert [e for e in logs if e["event"] == "ai.platform_cap_reached"] == [
            {"event": "ai.platform_cap_reached", "log_level": "warning",
             "cap": "fix_runs", "limit": 1, "agency_id": agency_id}
        ]

    @pytest.mark.parametrize("which", [rate_limit, spend_caps], ids=["cooldown", "platform-claim"])
    async def test_redis_down_is_a_503_and_nothing_is_written(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, which: Any,
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))

        async def down(*args: Any, **kwargs: Any) -> Any:
            raise OSError("SENTINEL-redis-host is down")

        monkeypatch.setattr(which, "_eval", down)
        resp = await _fixes(client, sid)

        assert resp.status_code == 503, resp.text
        assert resp.json()["type"] == "/problems/service-unavailable"
        assert "SENTINEL" not in resp.text
        assert calls.fixes == 0

    async def test_a_failed_scan_and_another_agencys_scan_spend_nothing(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        failed = await _a_scan(agency_id, cid)
        async with get_sessionmaker()() as db:
            await db.execute(update(Scan).where(Scan.id == failed).values(status=ScanStatus.FAILED))
            await db.commit()
        assert (await _fixes(client, failed)).status_code == 409
        assert (await _fixes(client, ids.new_id(ids.SCAN))).status_code == 404
        assert calls.fixes == 0
        assert await _all_claims(settings) == []

    def test_a_scans_own_fix_phase_is_not_gated_or_counted(self) -> None:
        """The scan executor and the generator call neither the plan gate nor a claim: only the
        route does. Read from the source, so a claim added to the generator fails here."""
        root = Path(avp_api.__file__).resolve().parent
        names = {"claim_fix_regeneration", "assert_may_write_fixes", "assert_plan_allows"}
        callers: set[str] = set()
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                used = (
                    node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name) else None
                )
                if used in names:
                    callers.add(path.relative_to(root).as_posix())
                if (
                    isinstance(node, ast.Constant) and node.value == "fix_runs"
                    and path.name not in {"spend_caps.py", "action_items.py"}
                ):
                    callers.add(path.relative_to(root).as_posix())
        assert callers <= {
            "routers/action_items.py", "services/rate_limit.py", "services/scan_allowance.py",
        }, callers
        executor = (root / "services" / "scan_executor.py").read_text(encoding="utf-8")
        assert "fix_runner.generate_for_scan" in executor  # the phase exists, and is ungated
        for name in names | {"spend_caps"}:
            assert name not in executor
            assert name not in (root / "services" / "fix_runner.py").read_text(encoding="utf-8")


# =========================================================================== (c) classification


class TestClassification:
    async def test_the_twenty_first_classification_of_the_day_is_refused_before_any_work(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        await _subscribe(agency_id, "active")  # a paid agency has the full day of 20
        for i in range(20):
            resp = await _create(client, f"site-{i}.com")
            assert resp.status_code == 201, resp.text
        assert calls.classify == 20
        assert await _client_rows(agency_id) == 20

        with structlog.testing.capture_logs() as logs:
            resp = await _create(client, "site-21.com")

        assert resp.status_code == 429, resp.text
        assert resp.json()["type"] == "/problems/rate-limited"
        assert resp.json()["detail"] == AGENCY_CLASSIFY_CAP
        assert 1 <= int(resp.headers["Retry-After"]) <= 86400
        assert calls.classify == 20  # no crawl, no model call
        assert await _client_rows(agency_id) == 20  # and no client written
        assert [e for e in logs if e["event"] == "ai.agency_cap_reached"] == [
            {"event": "ai.agency_cap_reached", "log_level": "warning",
             "cap": "classifications_agency", "limit": 20, "agency_id": agency_id}
        ]
        # An unclassified create is not counted, and still works.
        assert (await _a_client(client, "plain.com")) is not None

    async def test_twenty_parallel_creates_let_at_most_the_limit_reach_the_provider(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 2)
        agency_id = await _sign_up(client)

        got = await asyncio.gather(*(_create(client, f"burst-{i}.com") for i in range(20)))

        created = [r for r in got if r.status_code == 201]
        assert calls.classify == len(created) <= 2
        assert await _client_rows(agency_id) == len(created)
        assert all(r.status_code in (201, 429) for r in got)

    async def test_twenty_concurrent_claims_against_a_day_of_five_let_exactly_five_through(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The claim itself, with the browser slot out of the way (it serialises one agency)."""
        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 5)
        redis = redis_module.get_redis(settings)

        async def one() -> bool:
            try:
                await spend_caps.claim_for_agency(
                    redis, settings, kind="classifications", agency_id="agcy_burst"
                )
            except RateLimited:
                return False
            return True

        assert sum(await asyncio.gather(*(one() for _ in range(20)))) == 5
        assert await _cap_count(settings, "classifications") == 5  # refusals took no platform unit

    async def test_a_busy_browser_slot_spends_nothing(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        async with browser_slot.one_run_per_agency(agency_id):
            created = await _create(client, "busy.com")
            reclassified = await _reclassify(client, cid)
        assert created.status_code == reclassified.status_code == 429
        assert "already has a site being read" in created.json()["detail"]
        assert calls.classify == 0
        assert await _all_claims(settings) == []
        assert await _client_rows(agency_id) == 1

    async def test_the_claims_are_taken_after_the_slot_and_before_the_work(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At the moment the crawl would start, the slot is held and both units are taken."""
        agency_id = await _sign_up(client)
        seen: dict[str, Any] = {}

        async def spy(session, client_row, *, settings=None, max_pages=3):  # noqa: ANN001, ARG001
            seen["held"] = agency_id in browser_slot._agencies_with_a_run  # noqa: SLF001
            seen["agency"] = await _keys(settings_obj, "cap:classifications_agency:*")
            seen["platform"] = await _cap_count(settings_obj, "classifications")
            return client_row, None

        settings_obj = settings
        monkeypatch.setattr(intake, "run_classification", spy)
        assert (await _create(client, "ordered.com")).status_code == 201
        assert seen["held"] is True
        assert len(seen["agency"]) == 1 and seen["platform"] == 1

    async def test_the_platform_cap_refuses_a_create_with_its_own_sentence_and_writes_no_client(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_classifications_per_day_platform", 1)
        agency_id = await _sign_up(client)
        assert (await _create(client, "one.com")).status_code == 201

        with structlog.testing.capture_logs() as logs:
            resp = await _create(client, "two.com")

        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == CLASSIFY_CAP
        assert resp.json()["cap"] == "classifications"
        assert resp.json()["title"] == "Daily limit reached"
        _no_number(resp)
        assert calls.classify == 1
        assert await _client_rows(agency_id) == 1
        assert [e for e in logs if e["event"] == "ai.platform_cap_reached"] == [
            {"event": "ai.platform_cap_reached", "log_level": "warning",
             "cap": "classifications", "limit": 1, "agency_id": agency_id}
        ]
        # All or nothing: the platform's refusal left this agency's day at one.
        (agency_key,) = await _keys(settings, "cap:classifications_agency:*")
        assert await redis_module.get_redis(settings).get(agency_key) == "1"

    async def test_a_bad_url_and_a_duplicate_spend_no_unit_and_write_nothing(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        """Junk URLs never reach a crawl or a model, so they must not eat the day's units."""
        agency_id = await _sign_up(client)
        assert (await _a_client(client, "dup.com")) is not None
        rows = await _client_rows(agency_id)

        bad = await _create(client, "not a url at all")
        dup = await _create(client, "dup.com")

        assert bad.status_code == 409, bad.text
        assert bad.json()["type"] == "/problems/invalid-url"
        assert dup.status_code == 409, dup.text
        assert dup.json()["type"] == "/problems/conflict"
        assert await _keys(settings, "cap:classifications_agency:*") == []
        assert await _cap_count(settings, "classifications") == 0
        assert calls.classify == 0
        assert await _client_rows(agency_id) == rows

    async def test_reclassify_is_counted_and_refused_the_same_way(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 1)
        await _sign_up(client)
        cid = await _a_client(client)
        assert (await _reclassify(client, cid)).status_code == 200
        resp = await _reclassify(client, cid)
        assert resp.status_code == 429
        assert resp.json()["detail"] == AGENCY_CLASSIFY_CAP
        assert int(resp.headers["Retry-After"]) >= 1
        assert calls.classify == 1

        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 20)
        monkeypatch.setattr(settings, "max_classifications_per_day_platform", 1)
        platform = await _reclassify(client, cid)
        assert platform.status_code == 503
        assert platform.json()["detail"] == CLASSIFY_CAP
        assert calls.classify == 1

    async def test_another_agency_has_its_own_day(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 1)
        await _sign_up(client)
        assert (await _create(client, "mine.com")).status_code == 201
        assert (await _create(client, "mine-too.com")).status_code == 429
        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, "other@paid-calls.example", agency="Other Agency")
        assert (await _create(client, "theirs.com")).status_code == 201

    async def test_classify_false_and_another_agencys_client_take_nothing(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _a_client(client)
        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, "other@paid-calls.example", agency="Other Agency")
        assert (await _reclassify(client, cid)).status_code == 404
        assert calls.classify == 0
        assert await _all_claims(settings) == []

    async def test_it_needs_no_plan(
        self, client: AsyncClient, settings: Settings, billing_on: None, calls: SimpleNamespace
    ) -> None:
        """Classification is the first step of intake, before any plan: only counted."""
        await _sign_up(client)
        assert (await _create(client, "free.com")).status_code == 201

    async def test_redis_down_is_a_503_nothing_is_read_and_no_client_is_written(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _a_client(client)

        async def down(*args: Any, **kwargs: Any) -> Any:
            raise OSError("SENTINEL-redis-host is down")

        monkeypatch.setattr(spend_caps, "_eval", down)
        with structlog.testing.capture_logs() as logs:
            created = await _create(client, "down.com")
            reclassified = await _reclassify(client, cid)

        for resp in (created, reclassified):
            assert resp.status_code == 503, resp.text
            assert resp.json()["type"] == "/problems/service-unavailable"
            assert resp.json()["detail"] == UNAVAILABLE
            assert "SENTINEL" not in resp.text
        assert "SENTINEL" not in repr(logs)
        assert calls.classify == 0
        assert await _client_rows(agency_id) == 1


# =========================================================================== the module itself


class TestSpendCapsKinds:
    def test_each_new_kind_has_a_setting_a_default_and_its_own_sentence(self) -> None:
        defaults = {
            "prompt_runs": ("max_prompt_runs_per_day_platform", 300, PROMPT_CAP),
            "fix_runs": ("max_fix_runs_per_day_platform", 100, FIX_CAP),
            "classifications": ("max_classifications_per_day_platform", 200, CLASSIFY_CAP),
        }
        for kind, (setting, default, sentence) in defaults.items():
            assert spend_caps.KINDS[kind] == setting
            assert Settings.model_fields[setting].default == default
            assert spend_caps._SENTENCES[kind] == sentence  # noqa: SLF001
            assert not re.search(r"\d", sentence)
        assert Settings.model_fields["prompt_runs_per_client_per_hour"].default == 30
        assert Settings.model_fields["fix_regenerate_cooldown_seconds"].default == 3600
        assert Settings.model_fields["classifications_per_agency_per_day"].default == 20

    async def test_the_scan_kinds_keep_their_title_and_their_log_event(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 0)
        redis = redis_module.get_redis(settings)
        with structlog.testing.capture_logs() as logs, pytest.raises(PlatformCapReached) as scan:
            await spend_caps.claim(redis, settings, kinds=["scans"], agency_id="agcy_x")
        assert scan.value.title == "Daily scan limit reached"
        assert [e["event"] for e in logs] == ["scan.platform_cap_reached"]

    async def test_a_refusal_on_one_kind_increments_none_of_the_kinds_claimed_with_it(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "max_fix_runs_per_day_platform", 0)
        redis = redis_module.get_redis(settings)
        with pytest.raises(PlatformCapReached):
            await spend_caps.claim(
                redis, settings, kinds=["prompt_runs", "fix_runs"], agency_id="agcy_x"
            )
        assert await _cap_count(settings, "prompt_runs") == 0


# =========================================================================== security review

FREE_CLASSIFY_CAP = (
    "AVP has classified as many sites for free accounts as it allows in a day. "
    "Try again after midnight UTC."
)
AGENCY_ASK_CAP = (
    "This agency has asked as many questions as it can today. Try again after midnight UTC."
)
AGENCY_FIX_CAP = (
    "This agency has written fixes again as many times as it can today. "
    "Try again after midnight UTC."
)


async def _another_agency(client: AsyncClient, n: int, *, subscription: str | None = None) -> str:
    await client.post(f"{BASE}/auth/sign-out")
    agency_id = await _sign_up(client, f"agency{n}@paid-calls.example", agency=f"Agency {n}")
    if subscription:
        await _subscribe(agency_id, subscription)
    return agency_id


async def _values(settings: Settings, pattern: str) -> dict[str, int]:
    redis = redis_module.get_redis(settings)
    return {k: int(await redis.get(k) or 0) for k in await _keys(settings, pattern)}


class TestFreeClassificationsAreAShare:
    async def test_unpaid_agencies_cannot_spend_a_paid_agencys_classifications(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_free_classifications_per_day_platform", 2)
        await _sign_up(client)
        assert (await _create(client, "free-one.com")).status_code == 201
        await _another_agency(client, 2)
        assert (await _create(client, "free-two.com")).status_code == 201
        await _another_agency(client, 3)
        with structlog.testing.capture_logs() as logs:
            refused = await _create(client, "free-three.com")

        assert refused.status_code == 503, refused.text
        assert refused.json()["detail"] == FREE_CLASSIFY_CAP
        assert refused.json()["cap"] == "free_classifications"
        _no_number(refused)
        assert calls.classify == 2
        (event,) = [e for e in logs if e["event"] == "ai.platform_cap_reached"]
        assert event["cap"] == "free_classifications" and event["limit"] == 2

        # A paid agency, and the exempt operator, still classify.
        await _another_agency(client, 4, subscription="active")
        assert (await _create(client, "paid.com")).status_code == 201
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, OPERATOR, google=True, agency="Operator Agency")
        assert (await _create(client, "operator.com")).status_code == 201
        assert calls.classify == 4
        # Neither of those two drew on the free share.
        free = await _values(settings, "cap:free_classifications:*")
        assert list(free.values()) == [2]

    async def test_a_refusal_by_the_free_share_increments_neither_the_agency_nor_the_platform_day(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_free_classifications_per_day_platform", 1)
        await _sign_up(client)
        assert (await _create(client, "first.com")).status_code == 201
        before = {
            **await _values(settings, "cap:*"),
        }
        second = await _another_agency(client, 2)

        assert (await _create(client, "second.com")).status_code == 503

        after = await _values(settings, "cap:*")
        assert after == before  # not even a key for the second agency
        assert not any(second in k for k in after)

    def test_the_free_share_may_not_exceed_the_platform_cap_unless_that_is_zero(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="MAX_FREE_CLASSIFICATIONS_PER_DAY_PLATFORM"):
            Settings(
                _env_file=None,
                max_classifications_per_day_platform=10,
                max_free_classifications_per_day_platform=11,
            )
        Settings(
            _env_file=None,
            max_classifications_per_day_platform=10,
            max_free_classifications_per_day_platform=10,
        )
        Settings(
            _env_file=None,
            max_classifications_per_day_platform=0,
            max_free_classifications_per_day_platform=11,
        )
        assert Settings.model_fields["max_free_classifications_per_day_platform"].default == 60


class TestAnAgencysShareOfTheDay:
    async def test_one_agencys_questions_stop_at_its_share_while_another_still_asks(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "prompt_runs_per_agency_per_day", 3)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        for i in range(3):
            assert (await _ask(client, cid, f"q{i}")).status_code == 201

        with structlog.testing.capture_logs() as logs:
            refused = await _ask(client, cid, "q3")

        assert refused.status_code == 429, refused.text
        assert refused.json()["type"] == "/problems/rate-limited"
        assert refused.json()["detail"] == AGENCY_ASK_CAP
        assert 1 <= int(refused.headers["Retry-After"]) <= 86400
        assert calls.engines == 3
        assert await _cap_count(settings, "prompt_runs") == 3  # the refusal took no platform unit
        assert [e for e in logs if e["event"] == "ai.agency_cap_reached"] == [
            {"event": "ai.agency_cap_reached", "log_level": "warning",
             "cap": "prompt_runs_agency", "limit": 3, "agency_id": agency_id}
        ]
        await _another_agency(client, 2)
        other = await _a_client(client, "other-site.com")
        assert (await _ask(client, other)).status_code == 201

    async def test_the_default_share_of_questions_is_a_hundred_so_the_101st_is_refused(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert Settings.model_fields["prompt_runs_per_agency_per_day"].default == 100
        assert Settings.model_fields["fix_runs_per_agency_per_day"].default == 30
        monkeypatch.setattr(settings, "prompt_runs_per_client_per_hour", 1000)
        await _sign_up(client)
        cid = await _a_client(client)
        got = await asyncio.gather(*(_ask(client, cid, f"q{i}") for i in range(101)))
        assert sorted(r.status_code for r in got) == [201] * 100 + [429]
        assert calls.engines == 100

    async def test_a_platform_refusal_gives_the_clients_hourly_unit_back(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_prompt_runs_per_day_platform", 1)
        await _sign_up(client)
        cid = await _a_client(client)
        assert (await _ask(client, cid, "one")).status_code == 201
        assert (await _ask(client, cid, "two")).status_code == 503
        assert (await _ask(client, cid, "three")).status_code == 503
        assert list((await _values(settings, "rl:prompt_run:*")).values()) == [1]

    async def test_an_agency_refusal_gives_the_clients_hourly_unit_back(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "prompt_runs_per_agency_per_day", 1)
        await _sign_up(client)
        cid = await _a_client(client)
        assert (await _ask(client, cid, "one")).status_code == 201
        assert (await _ask(client, cid, "two")).status_code == 429
        assert list((await _values(settings, "rl:prompt_run:*")).values()) == [1]

    async def test_one_agencys_fix_regenerations_stop_at_its_share_while_another_still_writes(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "fix_runs_per_agency_per_day", 2)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        scans = [await _a_scan(agency_id, cid) for _ in range(3)]
        assert (await _fixes(client, scans[0])).status_code == 201
        assert (await _fixes(client, scans[1])).status_code == 201

        refused = await _fixes(client, scans[2])

        assert refused.status_code == 429, refused.text
        assert refused.json()["detail"] == AGENCY_FIX_CAP
        assert 1 <= int(refused.headers["Retry-After"]) <= 86400
        assert calls.fixes == 2
        assert await _cap_count(settings, "fix_runs") == 2
        # The refusal freed the third scan's cooldown: it can go the moment the share allows.
        assert len(await _keys(settings, "rl:fix_regenerate:*")) == 2
        monkeypatch.setattr(settings, "fix_runs_per_agency_per_day", 3)
        assert (await _fixes(client, scans[2])).status_code == 201
        other_agency = await _another_agency(client, 2)
        other = await _a_scan(other_agency, await _a_client(client, "other-site.com"))
        assert (await _fixes(client, other)).status_code == 201

    async def test_a_platform_refusal_leaves_the_scans_cooldown_free(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_fix_runs_per_day_platform", 1)
        agency_id = await _sign_up(client)
        cid = await _a_client(client)
        one, two = await _a_scan(agency_id, cid), await _a_scan(agency_id, cid)
        assert (await _fixes(client, one)).status_code == 201
        assert (await _fixes(client, two)).status_code == 503
        assert len(await _keys(settings, "rl:fix_regenerate:*")) == 1  # only the first scan's
        monkeypatch.setattr(settings, "max_fix_runs_per_day_platform", 5)
        assert (await _fixes(client, two)).status_code == 201

    async def test_a_scan_with_nothing_to_write_about_takes_no_unit_and_no_cooldown(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def none(session, scan):  # noqa: ANN001, ARG001
            return []

        monkeypatch.setattr(fix_runner, "build_candidates_for", none)
        agency_id = await _sign_up(client)
        sid = await _a_scan(agency_id, await _a_client(client))

        resp = await _fixes(client, sid)

        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "empty"
        assert resp.json()["reasonCode"] == "NO_CANDIDATES"
        assert calls.fixes == 0
        assert await _all_claims(settings) == []

    async def test_the_agency_share_of_classifications_and_every_day_key_in_one_script(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The agency day, the platform day and the free share are one all-or-nothing script."""
        monkeypatch.setattr(settings, "classifications_per_agency_per_day", 1)
        redis = redis_module.get_redis(settings)
        keys = await spend_caps.claim_for_agency(
            redis, settings, kind="classifications", agency_id="agcy_s", free=True
        )
        assert len(keys) == 3
        with pytest.raises(RateLimited):
            await spend_caps.claim_for_agency(
                redis, settings, kind="classifications", agency_id="agcy_s", free=True
            )
        assert set((await _values(settings, "cap:*")).values()) == {1}


class TestABusyBrowserSpendsNothing:
    @pytest.mark.parametrize("route", ["create", "reclassify"])
    async def test_the_counters_are_as_they_were_and_the_next_try_still_counts_once(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, route: str,
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _a_client(client)

        async def busy(session, client_row, *, settings=None, max_pages=3):  # noqa: ANN001, ARG001
            raise crawler_busy()

        real = intake.run_classification
        monkeypatch.setattr(intake, "run_classification", busy)
        resp = await _classify_via(client, route, cid)

        assert resp.status_code == 429, resp.text
        assert "crawler is busy" in resp.json()["detail"]
        counts = await _values(settings, "cap:*")
        assert len(counts) == 3 and set(counts.values()) == {0}, counts  # agency, platform, free
        assert await _client_rows(agency_id) == 1

        monkeypatch.setattr(intake, "run_classification", real)
        monkeypatch.setattr(intake, "run_classification", calls_classify(calls))
        ok = await _classify_via(client, route, cid)
        assert ok.status_code in (200, 201)
        assert set((await _values(settings, "cap:*")).values()) == {1}

    async def test_a_redis_error_while_giving_back_does_not_hide_the_busy_answer(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        cid = await _a_client(client)

        async def busy(session, client_row, *, settings=None, max_pages=3):  # noqa: ANN001, ARG001
            raise crawler_busy()

        class Down:
            def eval(self, *args: Any, **kwargs: Any) -> Any:  # noqa: A003
                raise OSError("SENTINEL-redis-host is down")

        monkeypatch.setattr(intake, "run_classification", busy)
        real_give_back = spend_caps.give_back

        async def flaky(redis: Any, keys: Any) -> None:
            await real_give_back(Down(), keys)

        monkeypatch.setattr(spend_caps, "give_back", flaky)
        resp = await _reclassify(client, cid)
        assert resp.status_code == 429
        assert "SENTINEL" not in resp.text


async def _classify_via(client: AsyncClient, route: str, cid: str) -> Any:
    if route == "create":
        return await _create(client, "busy-site.com")
    return await _reclassify(client, cid)


def calls_classify(calls: SimpleNamespace) -> Any:
    async def fake(session, client_row, *, settings=None, max_pages=3):  # noqa: ANN001, ARG001
        calls.classify += 1
        return client_row, None

    return fake


class TestUnpaidAgenciesGetASmallerDay:
    async def test_an_unpaid_agencys_sixth_classification_is_refused_and_a_paid_agencys_is_not(
        self, client: AsyncClient, settings: Settings, billing_off: None, calls: SimpleNamespace
    ) -> None:
        assert Settings.model_fields["classifications_per_unpaid_agency_per_day"].default == 5
        unpaid = await _sign_up(client)
        for i in range(5):
            assert (await _create(client, f"unpaid-{i}.com")).status_code == 201

        refused = await _create(client, "unpaid-5.com")

        assert refused.status_code == 429, refused.text
        assert refused.json()["detail"] == AGENCY_CLASSIFY_CAP
        assert calls.classify == 5
        assert await _client_rows(unpaid) == 5

        await _another_agency(client, 2, subscription="active")
        for i in range(6):
            assert (await _create(client, f"paid-{i}.com")).status_code == 201, i
        # The paid agency drew nothing from the free share.
        assert list((await _values(settings, "cap:free_classifications:*")).values()) == [5]

    def test_the_unpaid_day_may_not_exceed_the_paid_day(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="CLASSIFICATIONS_PER_UNPAID_AGENCY_PER_DAY"):
            Settings(
                _env_file=None,
                classifications_per_agency_per_day=3,
                classifications_per_unpaid_agency_per_day=4,
            )
        Settings(
            _env_file=None,
            classifications_per_agency_per_day=3,
            classifications_per_unpaid_agency_per_day=3,
        )


class TestTheCooldownIsReleasedOnlyByItsHolder:
    async def test_a_release_with_another_requests_token_leaves_the_key(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        redis = redis_module.get_redis(settings)
        mine = await rate_limit.claim_fix_regeneration(
            redis, settings, scan_id="scan_tok", agency_id="agcy_t"
        )
        (key,) = await _keys(settings, "rl:fix_regenerate:*")
        assert await redis.get(key) == mine

        await rate_limit.release_fix_regeneration(
            redis, settings, scan_id="scan_tok", token="somebody-elses"
        )
        assert await redis.get(key) == mine  # still taken

        await rate_limit.release_fix_regeneration(
            redis, settings, scan_id="scan_tok", token=mine
        )
        assert await _keys(settings, "rl:fix_regenerate:*") == []

    async def test_a_request_whose_cooldown_expired_does_not_free_the_next_ones(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        redis = redis_module.get_redis(settings)
        first = await rate_limit.claim_fix_regeneration(
            redis, settings, scan_id="scan_exp", agency_id="agcy_t"
        )
        (key,) = await _keys(settings, "rl:fix_regenerate:*")
        await redis.pexpire(key, 20)
        await asyncio.sleep(0.1)
        second = await rate_limit.claim_fix_regeneration(
            redis, settings, scan_id="scan_exp", agency_id="agcy_t"
        )
        await rate_limit.release_fix_regeneration(
            redis, settings, scan_id="scan_exp", token=first
        )
        assert await redis.get(key) == second
