"""Competitor detection from the button is for paying agencies — launch checklist 3.61.

3.53 gave each client one `POST /clients/{clientId}/competitors/detect` run a day. Its
security review's Medium was that this caps a client, not an agency: an account can add
clients without a classification call and detect once on each, about six paid SerpApi
searches a time, out of a monthly quota the whole platform shares. The founder's decision
(2026-10-04): the route is for an agency whose subscription is active or trialing, and for
the platform operator; everyone else is refused in plain words. The per-client day stays for
those who are allowed, and a scan's own detection is unaffected.

What is pinned here, each by a test that fails without it:

* with billing configured, an agency with no subscription, or one whose subscription is
  anything but active or trialing, is refused with a 402 `subscription-required` and one
  sentence that names no status and no code, and nothing runs: no search, no scan opened;
* a refused press does not spend the client's day, and the plan is asked before the day, so
  a lapsed agency whose day is taken hears about its plan and not about the day;
* `active` and `trialing` run, whether the signed-in user has a Google identity or not, and
  are still held to one run per client per day;
* the operator (a listed address AND a linked Google identity) runs. The address alone, a
  teammate in the operator's agency, and a Google identity on an unlisted address do not;
* with billing not configured, or only half configured, the route is open as it was: the
  switch is the scan allowance's own;
* another agency's client is a 404 whatever the caller's plan: the client is found first;
* only that route is gated: a lapsed agency still reads and corrects the set it has;
* a scan's own detection runs for an agency that cannot press the button, and takes nothing
  of the client's day;
* the refusal's one log line carries the agency id and the client id and nothing else.

Nothing here reaches Stripe: the route reads the agency's stored subscription status and
never asks Stripe. SerpApi and the model calls are conftest's autouse fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.db import get_sessionmaker
from avp_api.models import Agency, CompetitorSet, Scan, User, UserRole
from avp_api.services import auth as auth_service
from avp_api.services import competitors as detection
from avp_api.services import scan_runner

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
# The agency's owner, whose address must never reach a log line.
OWNER = "owner@plan-gate.example"
# Not the founder's real address: the exempt list is empty by default and the live value is
# in the Render environment. Any address does here.
OPERATOR = "operator@example.com"
TEAMMATE = "teammate@plan-gate.example"

# What a refusal says, whatever the agency's status. The whole sentence is pinned, so a
# change to what a customer reads has to change this test.
REFUSED = (
    "Running competitor detection on its own needs an active subscription. "
    "A scan still finds a client's competitors the first time it runs."
)
REFUSAL_EVENT = "competitors.detect.subscription_required"

# Every status that is not paid up. The decision names two that run the route, `active` and
# `trialing`, and the scan allowance counts the same two as paid. Written out, not imported
# from `services.billing`, so editing the code cannot quietly edit the decision these pin.
# `on_hold` is no status Stripe has: a status nobody has decided about does not run it.
NOT_PAID_UP = [
    "past_due", "canceled", "incomplete", "unpaid", "paused", "incomplete_expired", "on_hold",
]
PAID_UP = ["active", "trialing"]


async def _sign_up(
    client: AsyncClient,
    email: str = OWNER,
    agency: str = "Plan Gate Agency",
    *,
    google: bool = True,
) -> str:
    """Sign an agency up through the real route; returns its id.

    Google-linked by default, so the account is one the free scan would start for and the
    one thing it lacks is a plan: the case the decision is about. `google=False` is the
    password-only account `POST /auth/sign-up` really makes. The link is written straight to
    the row, as `test_scan_allowance.py` writes it: the code asks only whether the column is
    set, and the OAuth round trip that sets it is `test_google_sign_in.py`'s to prove.
    """
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": agency,
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
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


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_subscription(agency_id: str, status: str | None) -> None:
    """What the Stripe webhook writes, and the only thing the route reads."""
    async with get_sessionmaker()() as db:
        agency = (await db.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        agency.subscription_status = status
        await db.commit()


async def _scan_rows(agency_id: str) -> int:
    """Every scan the agency has, in any status: "no scan was opened" means none."""
    async with get_sessionmaker()() as db:
        return int(
            (
                await db.execute(
                    select(func.count()).select_from(Scan).where(Scan.agency_id == agency_id)
                )
            ).scalar_one()
        )


async def _detect(client: AsyncClient, cid: str):  # noqa: ANN202
    return await client.post(f"{BASE}/clients/{cid}/competitors/detect")


async def _day_keys(settings: Settings) -> list[str]:
    """Every client's detection key in Redis."""
    redis = redis_module.get_redis(settings)
    return [key async for key in redis.scan_iter("rl:detect_competitors:*")]


@pytest.fixture
def billing_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Billing configured, so the plan applies. No Stripe call is made or can be."""
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """What detection actually did, counted at the two places a refused press must not reach.

    `searches` has one entry per run: a run is one batch of SerpApi queries, so one entry is
    six paid searches. `scans` has one entry per `get_or_create_scan`, the first thing the
    route does once it is let through. Both wrap the suite's own fakes (conftest's autouse
    `stub_chain_externals`), so nothing leaves the process.
    """
    seen = SimpleNamespace(searches=[], scans=[])
    search_many = detection.serp_service.search_many
    get_or_create_scan = detection.get_or_create_scan

    async def counting_search(queries, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.searches.append(list(queries))
        return await search_many(queries, **kwargs)

    async def counting_scan(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.scans.append(1)
        return await get_or_create_scan(*args, **kwargs)

    monkeypatch.setattr(detection.serp_service, "search_many", counting_search)
    monkeypatch.setattr(detection, "get_or_create_scan", counting_scan)
    return seen


@pytest.mark.usefixtures("billing_on")
class TestAnUnpaidAgencyIsRefused:
    async def test_no_subscription_is_a_402_in_plain_words_and_runs_nothing(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)

        resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        # Exactly the five members every problem has: no `code`, no counts, no id.
        assert resp.json() == {
            "type": "/problems/subscription-required",
            "title": "Subscription required",
            "status": 402,
            "detail": REFUSED,
            "instance": f"{BASE}/clients/{cid}/competitors/detect",
        }
        assert "retry-after" not in resp.headers
        # Refused before any work: no search, no scan opened or written, no day taken.
        assert runs.searches == []
        assert runs.scans == []
        assert await _scan_rows(agency_id) == 0
        assert await _day_keys(settings) == []

    @pytest.mark.parametrize("status", NOT_PAID_UP)
    async def test_a_subscription_that_is_not_paid_up_is_refused_with_the_same_sentence(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace, status: str
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)
        await _set_subscription(agency_id, status)

        resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["type"] == "/problems/subscription-required"
        # One sentence for every case: it does not say which status the agency is in.
        assert resp.json()["detail"] == REFUSED
        assert status not in resp.text
        assert runs.searches == []
        assert runs.scans == []
        assert await _day_keys(settings) == []

    async def test_a_refused_press_does_not_spend_the_clients_day(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)

        # Twice: the second is the plan again, not "already ran", so the first took no day.
        assert (await _detect(client, cid)).status_code == 402
        assert (await _detect(client, cid)).status_code == 402
        assert await _day_keys(settings) == []

        # Subscribes: the same client, the same day, runs.
        await _set_subscription(agency_id, "active")
        ran = await _detect(client, cid)
        assert ran.status_code == 201, ran.text
        assert len(runs.searches) == 1
        assert len(await _day_keys(settings)) == 1


@pytest.mark.usefixtures("billing_on")
class TestAPayingAgencyIsAllowed:
    @pytest.mark.parametrize("status", PAID_UP)
    async def test_it_runs_and_is_still_held_to_once_per_client_per_day(
        self, client: AsyncClient, runs: SimpleNamespace, status: str
    ) -> None:
        # Password-only on purpose: a payment is identity enough, so the plan is what the
        # route asks for and a linked Google identity is not.
        agency_id = await _sign_up(client, google=False)
        cid = await _make_client(client)
        await _set_subscription(agency_id, status)

        with structlog.testing.capture_logs() as logs:
            first = await _detect(client, cid)
        assert first.status_code == 201, first.text
        assert len(runs.searches) == 1
        # An allowed press says nothing about the plan.
        assert [e for e in logs if e["event"] == REFUSAL_EVENT] == []

        second = await _detect(client, cid)
        assert second.status_code == 429, second.text
        assert second.json()["type"].endswith("/rate-limited")
        assert len(runs.searches) == 1


@pytest.mark.usefixtures("billing_on")
class TestThePlatformOperator:
    async def test_the_listed_address_with_a_google_identity_runs_the_route(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        await _sign_up(client, OPERATOR)
        cid = await _make_client(client)

        first = await _detect(client, cid)
        assert first.status_code == 201, first.text
        assert len(runs.searches) == 1

        # Exempt from the plan, not from the day.
        second = await _detect(client, cid)
        assert second.status_code == 429, second.text
        assert len(runs.searches) == 1

    async def test_the_listed_address_alone_does_not(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An address is a string somebody typed; sign-up never proves it is theirs.

        Without a linked Google identity the listed address is gated like anyone else, so
        whoever registers it first on a fresh database gets nothing from it.
        """
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        await _sign_up(client, OPERATOR, google=False)
        cid = await _make_client(client)

        resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        assert resp.json()["detail"] == REFUSED
        assert runs.searches == []
        assert await _day_keys(settings) == []

    async def test_a_google_identity_on_an_unlisted_address_does_not(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Google vouching for an address is necessary, never sufficient."""
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        await _sign_up(client, "stranger@example.com")
        cid = await _make_client(client)

        resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        assert runs.searches == []

    async def test_a_teammate_in_the_operators_agency_is_gated_like_anyone_else(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per user, not per agency: the exemption is the operator's own sign-in."""
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [OPERATOR])
        agency_id = await _sign_up(client, OPERATOR)
        cid = await _make_client(client)
        async with get_sessionmaker()() as db:
            await auth_service.create_user_in_agency(
                db, agency_id=agency_id, email=TEAMMATE, full_name="Teammate",
                password=PASSWORD, role=UserRole.MEMBER, settings=settings,
            )
            await db.commit()
        signed_in = await client.post(
            f"{BASE}/auth/login", json={"email": TEAMMATE, "password": PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text

        resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        assert runs.searches == []
        assert await _day_keys(settings) == []


class TestWithoutBilling:
    """Billing not configured is a deployment that cannot sell a plan: the route stays open.

    The switch is the scan allowance's (`gate_enabled`): both `STRIPE_SECRET_KEY` and
    `STRIPE_PRICE_ID`, or no gate. Local development and CI are such deployments.
    """

    @pytest.mark.parametrize("status", [None, "canceled", "past_due"])
    async def test_an_agency_with_no_plan_runs_the_route_as_before(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        status: str | None,
    ) -> None:
        assert not (settings.stripe_secret_key and settings.stripe_price_id), "premise"
        agency_id = await _sign_up(client, google=False)
        cid = await _make_client(client)
        await _set_subscription(agency_id, status)

        resp = await _detect(client, cid)

        assert resp.status_code == 201, resp.text
        assert len(runs.searches) == 1

    @pytest.mark.parametrize("half", ["only the secret key", "only the price"])
    async def test_half_of_a_billing_setup_is_not_billing(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, half: str,
    ) -> None:
        only_key = half == "only the secret key"
        monkeypatch.setattr(
            settings, "stripe_secret_key", SecretStr("sk_" + "test_fake") if only_key else None
        )
        monkeypatch.setattr(settings, "stripe_price_id", None if only_key else "price_test_fake")
        await _sign_up(client, google=False)
        cid = await _make_client(client)

        resp = await _detect(client, cid)

        assert resp.status_code == 201, resp.text
        assert len(runs.searches) == 1


@pytest.mark.usefixtures("billing_on")
class TestTheOrderOfTheChecks:
    async def test_another_agencys_client_is_a_404_before_any_plan_check(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "intruder@plan-gate.example", "Other Agency", google=False)
            with structlog.testing.capture_logs() as logs:
                # Neither agency has a plan: were the plan asked first, both would be a 402.
                assert (await _detect(other, cid)).status_code == 404
                assert (await _detect(other, ids.new_id(ids.CLIENT))).status_code == 404

        assert [e for e in logs if e["event"].startswith("competitors.detect.")] == []
        assert runs.searches == []
        assert runs.scans == []
        assert await _day_keys(settings) == []
        # It is the owner's own client, so the owner is told about the plan.
        assert (await _detect(client, cid)).status_code == 402

    async def test_the_plan_is_asked_before_the_clients_day(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client, google=False)
        cid = await _make_client(client)
        await _set_subscription(agency_id, "active")
        assert (await _detect(client, cid)).status_code == 201
        [key] = await _day_keys(settings)
        redis = redis_module.get_redis(settings)
        left = await redis.pttl(key)

        # The plan lapses with the day taken.
        await _set_subscription(agency_id, "canceled")
        with structlog.testing.capture_logs() as logs:
            refused = await _detect(client, cid)

        # A plan refusal and not "already ran": the plan comes first, and the day is not
        # looked at, let alone touched.
        assert refused.status_code == 402, refused.text
        assert refused.json()["detail"] == REFUSED
        assert [e["event"] for e in logs if e["event"].startswith("competitors.detect.")] == [
            REFUSAL_EVENT
        ]
        assert len(runs.searches) == 1
        assert await _day_keys(settings) == [key]
        assert 0 < await redis.pttl(key) <= left

        # And the day was still there when the plan came back.
        await _set_subscription(agency_id, "active")
        assert (await _detect(client, cid)).status_code == 429
        assert len(runs.searches) == 1


@pytest.mark.usefixtures("billing_on")
class TestOnlyTheDetectRouteIsGated:
    """The decision is about the one route that spends searches, and about no other."""

    async def test_a_lapsed_agency_can_still_read_and_correct_the_set_it_has(
        self, client: AsyncClient, runs: SimpleNamespace
    ) -> None:
        agency_id = await _sign_up(client, google=False)
        cid = await _make_client(client)
        await _set_subscription(agency_id, "active")
        assert (await _detect(client, cid)).status_code == 201
        await _set_subscription(agency_id, "canceled")

        read = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert read.status_code == 200, read.text
        corrected = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}]},
        )
        assert corrected.status_code == 200, corrected.text
        assert [c["domain"] for c in corrected.json()["competitors"]] == ["zendesk.com"]

        # Neither made a search, and the button is still the plan's.
        assert len(runs.searches) == 1
        assert (await _detect(client, cid)).status_code == 402


@pytest.mark.usefixtures("billing_on")
class TestAScanStillDetects:
    """The refusal's second sentence is true: a scan's detection is not the button's.

    A scan's own detection goes through `ensure_set_for_scan`, never the route, so the plan
    does not reach it. (Whether the scan may START is the scan allowance's question, and its
    answer has not moved: an agency's first scan is free.)
    """

    async def test_an_unpaid_agencys_first_scan_runs_detection_though_the_button_is_refused(
        self, client: AsyncClient, session: AsyncSession, settings: Settings,
        runs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)
        assert (await _detect(client, cid)).status_code == 402
        assert runs.searches == []

        async def no_engines(*args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG001
            # The competitor-set phase is the one under test and it comes first; the
            # engine loop would otherwise call the real providers.
            return None

        monkeypatch.setattr(scan_runner, "run_scan", no_engines)
        started = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert started.status_code == 202, started.text

        assert len(runs.searches) == 1, "the scan did not run detection"
        sets = (
            await session.execute(
                select(CompetitorSet)
                .join(Scan, Scan.id == CompetitorSet.scan_id)
                .where(Scan.client_id == cid)
            )
        ).scalars().all()
        assert len(sets) == 1, "the scan has no competitor set"
        assert await _scan_rows(agency_id) == 1
        # It took nothing of the client's day, and the button is still the plan's.
        assert await _day_keys(settings) == []
        assert (await _detect(client, cid)).status_code == 402


@pytest.mark.usefixtures("billing_on")
class TestWhatIsLogged:
    async def test_a_refusal_logs_the_agency_and_the_client_and_nothing_else(
        self, client: AsyncClient
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)

        with structlog.testing.capture_logs() as logs:
            resp = await _detect(client, cid)

        assert resp.status_code == 402, resp.text
        refusals = [e for e in logs if e["event"] == REFUSAL_EVENT]
        assert refusals == [
            {
                "event": REFUSAL_EVENT,
                "log_level": "info",
                "client_id": cid,
                "agency_id": agency_id,
            }
        ]
        # The route's only line, and no address anywhere in what was written.
        assert [e for e in logs if e["event"].startswith("competitors.detect.")] == refusals
        assert OWNER not in repr(logs)
