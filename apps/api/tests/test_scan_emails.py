"""Email the user who started a scan when it finishes — F1, 2026-10-06.

OFF unless `EMAIL_SCAN_NOTIFICATIONS` is set, and then only while email is configured: with
either missing nothing is claimed, sent or logged. Switched on, a sweep
(`services/scan_emails.py`, run every minute by `scan_email_watch.py`) finds scans that reached a
final status in the last 15 minutes and were not notified, claims each with one conditional
UPDATE and then sends, through the send path invitations and resets use (`email.py`'s `_send`).
Resend is faked here at the HTTP transport, so nothing leaves the process.

Pinned here, each by a test that fails without it: who gets what (SUCCEEDED, PARTIAL, the alerts
email, FAILED; nothing for CANCELLED, QUEUED, RUNNING); who gets nothing (no requester,
suspended, deleted, another agency, opted out, client or agency gone, setting off, email not
configured); at most once (two sweeps, two instances, a second finalize, a late reaper, a failed
send not retried); the 15-minute window; and the bodies (plain text, our copy, the client's name
and counts only, never a share link, a score or an alert's own sentence).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from avp_api import db as db_module
from avp_api import ids
from avp_api.config import Settings
from avp_api.models import (
    Agency,
    Alert,
    AlertKind,
    Client,
    Scan,
    ScanStatus,
    Score,
    ScoreStatus,
    User,
    UserRole,
    UserStatus,
)
from avp_api.services import email as email_service
from avp_api.services import scan_emails, scan_runner
from avp_api.services.scan_executor import (
    _finalize_after_deadline,
    _mark_failed,
    reap_stale_scans,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
BASE_URL = "https://app.avp-pilot.example"
CLIENT_NAME = "Northwind Traders"
REQUESTER = "requester@agency.example"
OTHER = "someone.else@agency.example"

FINAL = (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED)

# The words a user reads, pinned here and not derived from the code under test.
FOOTER = (
    "You are getting this because you started this scan or turned on its monthly rescan. "
    f"To stop these emails, turn them off in Settings:\n{BASE_URL}/settings"
)


class Outbox:
    """What `email.py` posts to Resend, recorded. `status` is what the fake answers (500 is an
    outage); `raises` makes the request itself fail, as a dead connection does."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = 200
        self.raises = False

    @property
    def to(self) -> list[str]:
        return [address for message in self.sent for address in message["to"]]

    @property
    def subjects(self) -> list[str]:
        return [message["subject"] for message in self.sent]


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()

    def handler(request: httpx.Request) -> httpx.Response:
        if box.raises:
            raise httpx.ConnectError("no route to the provider")
        if box.status == 200:
            box.sent.append(json.loads(request.content))
        return httpx.Response(box.status, json={"id": "email_test"})

    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(email_service.httpx, "AsyncClient", factory)
    return box


@pytest.fixture
def on(settings: Settings) -> Settings:
    """The feature on, email configured, links pointed at a known address (with a stray slash)."""
    return settings.model_copy(
        update={
            "email_scan_notifications": True,
            "resend_api_key": SecretStr("re_test_key_never_sent"),
            "public_web_base_url": f"{BASE_URL}/",
        }
    )


@pytest.fixture
def wired(engine: Any, monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    """The module-level session factory the real scan writers use, pointed at the test database."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_sessionmaker", factory)
    return factory


class World:
    """Rows built straight into the test database. One per test; the tables start empty."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    async def _add(self, row: Any) -> Any:
        self.session.add(row)
        await self.session.commit()
        return row

    async def agency(self, name: str = "Meridian Search Partners") -> Agency:
        return await self._add(
            Agency(id=ids.new_id(ids.AGENCY), name=name, slug=f"agency-{self._next()}")
        )

    async def user(
        self,
        agency: Agency,
        email: str = REQUESTER,
        *,
        status: UserStatus = UserStatus.ACTIVE,
        scan_emails: bool = True,
        deleted: bool = False,
    ) -> User:
        return await self._add(
            User(
                id=ids.new_id(ids.USER),
                agency_id=agency.id,
                email=email,
                full_name="Casey Requester",
                role=UserRole.MEMBER,
                status=status,
                password_hash="not-a-real-hash",
                scan_emails=scan_emails,
                deleted_at=NOW if deleted else None,
            )
        )

    async def client(
        self,
        agency: Agency,
        name: str = CLIENT_NAME,
        *,
        deleted: bool = False,
        brand_name: str | None = None,
    ) -> Client:
        return await self._add(
            Client(
                id=ids.new_id(ids.CLIENT),
                agency_id=agency.id,
                name=name,
                brand_name=brand_name,
                domain=f"client-{self._next()}.example",
                deleted_at=NOW if deleted else None,
            )
        )

    async def scan(
        self,
        client: Client,
        requester: User | None,
        status: ScanStatus,
        *,
        ago: timedelta = timedelta(minutes=2),
        notified_at: datetime | None = None,
        share_token: str | None = None,
    ) -> Scan:
        """A scan that reached `status` `ago` before NOW (one not finished has no end)."""
        return await self._add(
            Scan(
                id=ids.new_id(ids.SCAN),
                client_id=client.id,
                agency_id=client.agency_id,
                requested_by_user_id=None if requester is None else requester.id,
                status=status,
                started_at=NOW - timedelta(minutes=30),
                finished_at=NOW - ago if status in FINAL else None,
                notified_at=notified_at,
                share_token=share_token,
                share_expires_at=None if share_token is None else NOW + timedelta(days=7),
            )
        )

    async def alerts(self, scan: Scan, *kinds: AlertKind, detail: str = "An alert.") -> None:
        """One alert of each kind, against an older scan of the same client as the baseline."""
        owner = await self.session.get(Client, scan.client_id)
        assert owner is not None
        baseline = await self.scan(
            owner, None, ScanStatus.SUCCEEDED, ago=timedelta(days=3), notified_at=NOW
        )
        for kind in kinds:
            self.session.add(
                Alert(
                    id=ids.new_id(ids.ALERT),
                    client_id=scan.client_id,
                    agency_id=scan.agency_id,
                    kind=kind,
                    scan_id=scan.id,
                    baseline_scan_id=baseline.id,
                    detail=detail,
                )
            )
        await self.session.commit()

    async def notified_at(self, scan: Scan) -> datetime | None:
        scan_id = scan.id
        self.session.expire_all()
        return (
            await self.session.execute(select(Scan.notified_at).where(Scan.id == scan_id))
        ).scalar_one()


@pytest.fixture
def world(session: AsyncSession) -> World:
    return World(session)


async def sweep(
    on: Settings, wired: async_sessionmaker[AsyncSession], *, now: datetime | None = NOW
) -> int:
    return await scan_emails.sweep(on, factory=wired, now=now)


async def a_finished_scan(
    world: World, status: ScanStatus = ScanStatus.SUCCEEDED
) -> tuple[Agency, User, Client, Scan]:
    agency = await world.agency()
    requester = await world.user(agency)
    client = await world.client(agency)
    return agency, requester, client, await world.scan(client, requester, status)


def events(logs: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"].startswith(prefix)]


class TestTheReadyEmail:
    async def test_a_succeeded_scan_tells_the_requester_the_report_is_ready(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)

        assert await sweep(on, wired) == 1

        (message,) = outbox.sent
        assert message["to"] == [REQUESTER]
        assert message["from"] == on.email_from
        assert message["subject"] == f"Your report for {CLIENT_NAME} is ready"
        assert message["text"] == (
            f"Your report for {CLIENT_NAME} is ready.\n\n"
            f"Open it here:\n{BASE_URL}/scans/{scan.id}/report\n\n" + FOOTER
        )
        assert set(message) == {"from", "to", "subject", "text"}

    async def test_a_partial_scan_adds_one_sentence_and_nothing_else(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world, ScanStatus.PARTIAL)

        await sweep(on, wired)

        (message,) = outbox.sent
        assert message["subject"] == f"Your report for {CLIENT_NAME} is ready"
        assert message["text"] == (
            f"Your report for {CLIENT_NAME} is ready.\n\n"
            "Some answers are missing from this scan; the report shows how many each engine "
            "returned.\n\n"
            f"Open it here:\n{BASE_URL}/scans/{scan.id}/report\n\n" + FOOTER
        )

    async def test_a_succeeded_scan_says_nothing_about_missing_answers(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await a_finished_scan(world, ScanStatus.SUCCEEDED)
        await sweep(on, wired)
        assert "missing" not in outbox.sent[0]["text"]

    async def test_the_link_is_the_signed_in_report_and_never_the_share_link(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        scan = await world.scan(
            client, requester, ScanStatus.SUCCEEDED, share_token="SHARETOKENSENTINEL0123456789"
        )

        await sweep(on, wired)

        text = outbox.sent[0]["text"]
        assert f"{BASE_URL}/scans/{scan.id}/report" in text
        assert "SHARETOKENSENTINEL" not in text
        assert "/share" not in text
        assert "#" not in text, "a fragment is how a token link travels"

    async def test_no_score_is_in_the_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        session.add(
            Score(
                id=ids.new_id(ids.SCORE),
                scan_id=scan.id,
                status=ScoreStatus.SCORED,
                composite=Decimal("73.40"),
                mention_rate=Decimal("61.25"),
            )
        )
        await session.commit()

        await sweep(on, wired)

        everything = json.dumps(outbox.sent)
        for number in ("73.4", "61.25", "61.2"):
            assert number not in everything

    async def test_the_links_come_from_the_configured_address_with_one_slash(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await a_finished_scan(world)
        await sweep(on, wired)
        links = [ln for ln in outbox.sent[0]["text"].splitlines() if ln.startswith("http")]
        assert links
        assert all(link.startswith(f"{BASE_URL}/") and "//" not in link[8:] for link in links)


class TestTheFailedEmail:
    async def test_a_failed_scan_gets_one_plain_did_not_finish_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, client, scan = await a_finished_scan(world, ScanStatus.FAILED)

        await sweep(on, wired)

        (message,) = outbox.sent
        assert message["to"] == [REQUESTER]
        assert message["subject"] == f"Your scan for {CLIENT_NAME} did not finish"
        assert message["text"] == (
            f"Your scan for {CLIENT_NAME} did not finish.\n\n"
            f"You can run it again from the client's page:\n{BASE_URL}/clients/{client.id}\n\n"
            + FOOTER
        )
        assert f"/scans/{scan.id}" not in message["text"]
        assert "ready" not in message["text"]

    async def test_a_failed_scan_carries_nothing_of_the_scan_but_the_clients_name(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        _, _, _, scan = await a_finished_scan(world, ScanStatus.FAILED)
        scan.error_code = "ALL_ENGINE_CALLS_FAILED"
        scan.error_detail = "ScanDeadlineError DETAILSENTINEL"
        await session.commit()

        await sweep(on, wired)

        everything = json.dumps(outbox.sent)
        assert "ALL_ENGINE_CALLS_FAILED" not in everything
        assert "DETAILSENTINEL" not in everything

    async def test_a_failed_scan_that_raised_alerts_still_gets_only_the_one_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world, ScanStatus.FAILED)
        await world.alerts(scan, AlertKind.SENTIMENT_DECLINE)

        await sweep(on, wired)

        assert outbox.subjects == [f"Your scan for {CLIENT_NAME} did not finish"]


class TestNothingForAScanThatIsNotDone:
    @pytest.mark.parametrize(
        "status", [ScanStatus.CANCELLED, ScanStatus.QUEUED, ScanStatus.RUNNING]
    )
    async def test_cancelled_queued_and_running_scans_get_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, status: ScanStatus
    ) -> None:
        _, _, _, scan = await a_finished_scan(world, status)

        assert await sweep(on, wired) == 0

        assert outbox.sent == []
        assert await world.notified_at(scan) is None


class TestTheAlertsEmail:
    async def test_a_scan_that_raised_alerts_gets_a_second_email_counting_each_kind(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, client, scan = await a_finished_scan(world)
        await world.alerts(
            scan,
            AlertKind.SENTIMENT_DECLINE,
            AlertKind.VISIBILITY_DROP,
            AlertKind.SENTIMENT_DECLINE,
        )

        await sweep(on, wired)

        assert outbox.to == [REQUESTER, REQUESTER]
        ready, alerts = outbox.sent
        assert ready["subject"] == f"Your report for {CLIENT_NAME} is ready"
        assert alerts["subject"] == f"Your scan for {CLIENT_NAME} raised 3 alerts"
        assert alerts["text"] == (
            f"Your scan for {CLIENT_NAME} raised 3 alerts:\n\n"
            "- Visibility fell: 1\n"
            "- Tone declined: 2\n\n"
            f"See them here:\n{BASE_URL}/clients/{client.id}/alerts\n\n" + FOOTER
        )

    async def test_one_alert_is_said_in_the_singular(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        await world.alerts(scan, AlertKind.OWNED_CITATION_LOST)

        await sweep(on, wired)

        alerts = outbox.sent[1]
        assert alerts["subject"] == f"Your scan for {CLIENT_NAME} raised 1 alert"
        assert "raised 1 alert:\n\n- Own citation lost: 1\n" in alerts["text"]

    async def test_only_the_kinds_that_were_raised_are_listed(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)

        await sweep(on, wired)

        text = outbox.sent[1]["text"]
        assert "Visibility fell: 1" in text
        assert "Tone declined" not in text and "Own citation lost" not in text

    async def test_a_partial_scan_that_raised_alerts_gets_both(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world, ScanStatus.PARTIAL)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)

        await sweep(on, wired)

        assert outbox.subjects == [
            f"Your report for {CLIENT_NAME} is ready",
            f"Your scan for {CLIENT_NAME} raised 1 alert",
        ]
        assert "Some answers are missing" in outbox.sent[0]["text"]
        assert "Some answers are missing" not in outbox.sent[1]["text"]

    async def test_a_scan_with_no_alerts_gets_no_second_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await a_finished_scan(world)
        await sweep(on, wired)
        assert len(outbox.sent) == 1

    async def test_only_this_scans_alerts_are_counted(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, requester, client, scan = await a_finished_scan(world)
        earlier = await world.scan(
            client, requester, ScanStatus.SUCCEEDED, ago=timedelta(days=2), notified_at=NOW
        )
        await world.alerts(earlier, AlertKind.VISIBILITY_DROP, AlertKind.SENTIMENT_DECLINE)
        await world.alerts(scan, AlertKind.OWNED_CITATION_LOST)

        await sweep(on, wired)

        assert outbox.subjects[1] == f"Your scan for {CLIENT_NAME} raised 1 alert"

    async def test_an_alerts_own_sentence_never_reaches_the_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        """An alert's `detail` quotes the scan's score; the email takes the row's kind alone."""
        _, _, _, scan = await a_finished_scan(world)
        await world.alerts(
            scan,
            AlertKind.VISIBILITY_DROP,
            detail="Visibility fell 5.8%, from 52.0 to 49.0. DETAILSENTINEL",
        )

        await sweep(on, wired)

        everything = json.dumps(outbox.sent)
        for fragment in ("5.8", "52.0", "49.0", "DETAILSENTINEL"):
            assert fragment not in everything

    def test_every_kind_of_alert_has_words(self) -> None:
        assert set(email_service.ALERT_KIND_WORDS) == set(AlertKind)
        assert all(words.strip() for words in email_service.ALERT_KIND_WORDS.values())

    def test_the_words_are_the_alerts_screens(self) -> None:
        assert email_service.ALERT_KIND_WORDS == {
            AlertKind.VISIBILITY_DROP: "Visibility fell",
            AlertKind.SENTIMENT_DECLINE: "Tone declined",
            AlertKind.OWNED_CITATION_LOST: "Own citation lost",
        }


class TestNothingForTheWrongPerson:
    async def test_a_scan_with_no_requester_gets_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        client = await world.client(agency)
        scan = await world.scan(client, None, ScanStatus.SUCCEEDED)

        assert await sweep(on, wired) == 0

        assert outbox.sent == []
        assert await world.notified_at(scan) is None

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"status": UserStatus.SUSPENDED}, "requester_not_active"),
            ({"status": UserStatus.INVITED}, "requester_not_active"),
            ({"deleted": True}, "requester_gone"),
            ({"scan_emails": False}, "opted_out"),
        ],
        ids=["suspended", "invited", "deleted", "opted-out"],
    )
    async def test_a_requester_who_cannot_or_will_not_be_emailed_gets_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        kwargs: dict[str, Any], reason: str,
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency, **kwargs)
        client = await world.client(agency)
        scan = await world.scan(client, requester, ScanStatus.SUCCEEDED)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)

        with capture_logs() as logs:
            await sweep(on, wired)

        assert outbox.sent == [], "neither the ready email nor the alerts email"
        (skipped,) = events(logs, "scan_emails.skipped")
        assert skipped["scan_id"] == scan.id
        assert skipped["user_id"] == requester.id
        assert skipped["reason"] == reason
        assert set(skipped) == {"event", "log_level", "scan_id", "user_id", "reason"}

    async def test_a_requester_who_is_not_in_the_scans_agency_gets_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency("Meridian Search Partners")
        elsewhere = await world.agency("Other Agency")
        stranger = await world.user(elsewhere, OTHER)
        client = await world.client(agency)
        scan = await world.scan(client, stranger, ScanStatus.SUCCEEDED)

        with capture_logs() as logs:
            await sweep(on, wired)

        assert outbox.sent == []
        (skipped,) = events(logs, "scan_emails.skipped")
        assert skipped["reason"] == "requester_other_agency"
        assert await world.notified_at(scan) is not None, "examined once, never again"

    async def test_the_agency_is_checked_per_scan_not_once(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        elsewhere = await world.agency("Other Agency")
        insider = await world.user(agency, REQUESTER)
        stranger = await world.user(elsewhere, OTHER)
        client = await world.client(agency)
        await world.scan(client, stranger, ScanStatus.SUCCEEDED, ago=timedelta(minutes=5))
        await world.scan(client, insider, ScanStatus.SUCCEEDED, ago=timedelta(minutes=1))

        await sweep(on, wired)

        assert outbox.to == [REQUESTER]

    async def test_a_client_that_has_been_deleted_gets_no_email_about_it(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency, deleted=True)
        await world.scan(client, requester, ScanStatus.SUCCEEDED)

        with capture_logs() as logs:
            await sweep(on, wired)

        assert outbox.sent == []
        assert events(logs, "scan_emails.skipped")[0]["reason"] == "client_gone"

    async def test_an_agency_that_has_been_deleted_gets_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        agency, _, _, _ = await a_finished_scan(world)
        agency.deleted_at = NOW
        await session.commit()

        with capture_logs() as logs:
            await sweep(on, wired)

        assert outbox.sent == []
        assert events(logs, "scan_emails.skipped")[0]["reason"] == "agency_gone"

    async def test_the_choice_is_per_user(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        quiet = await world.user(agency, OTHER, scan_emails=False)
        loud = await world.user(agency, REQUESTER, scan_emails=True)
        client = await world.client(agency)
        await world.scan(client, quiet, ScanStatus.SUCCEEDED, ago=timedelta(minutes=4))
        await world.scan(client, loud, ScanStatus.SUCCEEDED, ago=timedelta(minutes=3))

        await sweep(on, wired)

        assert outbox.to == [REQUESTER]


class TestOffUnlessTheSettingIsOnAndEmailIsConfigured:
    async def test_with_the_setting_off_nothing_is_claimed_sent_or_logged(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        off = on.model_copy(update={"email_scan_notifications": False})

        with capture_logs() as logs:
            assert await sweep(off, wired) == 0

        assert outbox.sent == []
        assert logs == []
        assert await world.notified_at(scan) is None

    async def test_with_email_not_configured_nothing_is_claimed_sent_or_logged(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        unconfigured = on.model_copy(update={"resend_api_key": None})

        with capture_logs() as logs:
            assert await sweep(unconfigured, wired) == 0

        assert outbox.sent == []
        assert logs == []
        assert await world.notified_at(scan) is None

    @pytest.mark.parametrize(
        ("setting", "key", "available"),
        [(False, None, False), (True, None, False), (False, "k", False), (True, "k", True)],
        ids=["neither", "setting only", "email only", "both"],
    )
    def test_it_is_available_only_with_both(
        self, settings: Settings, setting: bool, key: str | None, available: bool
    ) -> None:
        copy = settings.model_copy(
            update={
                "email_scan_notifications": setting,
                "resend_api_key": None if key is None else SecretStr(key),
            }
        )
        assert copy.scan_emails_available is available

    def test_the_default_is_off(self, settings: Settings) -> None:
        assert settings.email_scan_notifications is False
        assert settings.scan_emails_available is False


class TestAtMostOnce:
    async def test_a_second_sweep_sends_nothing_more(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)

        assert await sweep(on, wired) == 1
        assert await sweep(on, wired) == 0
        assert await sweep(on, wired, now=NOW + timedelta(minutes=5)) == 0

        assert len(outbox.sent) == 2, "one ready and one alerts email, once"
        assert await world.notified_at(scan) == NOW

    async def test_two_claims_on_one_scan_one_wins(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)

        results = await asyncio.gather(
            scan_emails.claim(wired, scan.id, now=NOW),
            scan_emails.claim(wired, scan.id, now=NOW),
        )

        assert sorted(result is not None for result in results) == [False, True]

    async def test_two_instances_that_both_found_the_scan_due_send_one_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both sweeps read the same due list and are held at the claim until both have arrived."""
        _, _, _, scan = await a_finished_scan(world)
        gate = asyncio.Barrier(2)
        real_claim = scan_emails.claim

        async def held(*args: Any, **kwargs: Any) -> Any:
            await asyncio.wait_for(gate.wait(), timeout=10)
            return await real_claim(*args, **kwargs)

        monkeypatch.setattr(scan_emails, "claim", held)

        counts = await asyncio.gather(sweep(on, wired), sweep(on, wired))

        assert sorted(counts) == [0, 1]
        assert len(outbox.sent) == 1
        assert await world.notified_at(scan) == NOW

    async def test_instances_sweeping_together_never_email_a_scan_twice(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        for minutes in range(1, 7):
            await world.scan(
                client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=minutes)
            )

        counts = await asyncio.gather(*(sweep(on, wired) for _ in range(3)))

        assert sum(counts) == 6
        assert len(outbox.sent) == 6

    async def test_a_scan_already_notified_is_never_emailed(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        await world.scan(
            client, requester, ScanStatus.SUCCEEDED, notified_at=NOW - timedelta(minutes=1)
        )

        assert await sweep(on, wired) == 0
        assert outbox.sent == []

    async def test_a_failed_send_is_logged_and_not_retried(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, requester, _, scan = await a_finished_scan(world)
        outbox.status = 500

        with capture_logs() as logs:
            assert await sweep(on, wired) == 1
            outbox.status = 200
            assert await sweep(on, wired) == 0
            assert await sweep(on, wired, now=NOW + timedelta(minutes=10)) == 0

        assert outbox.sent == [], "the provider was down for the one attempt"
        (failed,) = events(logs, "email.scan_ready.failed")
        assert failed["user_id"] == requester.id
        assert failed["scan_id"] == scan.id
        assert failed["error"] == "HTTPStatusError"
        assert await world.notified_at(scan) == NOW

    async def test_a_send_that_cannot_connect_is_not_retried_either(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await a_finished_scan(world)
        outbox.raises = True

        with capture_logs() as logs:
            await sweep(on, wired)
            outbox.raises = False
            await sweep(on, wired)

        assert outbox.sent == []
        assert [e["event"] for e in events(logs, "email.")] == ["email.scan_ready.failed"]

    async def test_a_failed_ready_email_does_not_stop_the_alerts_email_being_tried_once(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _, _, scan = await a_finished_scan(world)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)
        calls: list[str] = []
        real_send = email_service._send

        async def flaky(**kwargs: Any) -> None:
            calls.append(kwargs["log_event"])
            outbox.status = 500 if kwargs["log_event"] == "email.scan_ready" else 200
            await real_send(**kwargs)

        monkeypatch.setattr(email_service, "_send", flaky)

        await sweep(on, wired)
        await sweep(on, wired)

        assert calls == ["email.scan_ready", "email.scan_alerts"]
        assert outbox.subjects == [f"Your scan for {CLIENT_NAME} raised 1 alert"]

    async def test_a_sweep_that_cannot_read_one_scan_goes_on_to_the_next(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        first = await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=9))
        second = await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=1))
        real = scan_emails.notify

        async def explode_once(factory: Any, settings: Settings, claimed: Any) -> None:
            if claimed.scan_id == first.id:
                raise RuntimeError(f"the database said {CLIENT_NAME} REQUESTER-SENTINEL")
            await real(factory, settings, claimed)

        monkeypatch.setattr(scan_emails, "notify", explode_once)

        with capture_logs() as logs:
            assert await sweep(on, wired) == 2

        assert len(outbox.sent) == 1 and f"/scans/{second.id}/report" in outbox.sent[0]["text"]
        (failed,) = events(logs, "scan_emails.failed")
        assert failed["scan_id"] == first.id
        assert failed["error"] == "RuntimeError"
        assert "SENTINEL" not in json.dumps(logs, default=str), "an error's text is never logged"


class TestTheRealWritersOfAFinalStatus:
    """The sweep reads the table, so every path that ends a scan is covered, each the code's own."""

    async def _running(
        self, world: World, *, lease_ends_in: timedelta = timedelta(minutes=3)
    ) -> Scan:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        scan = await world.scan(client, requester, ScanStatus.RUNNING)
        scan.lease_expires_at = datetime.now(UTC) + lease_ends_in
        await world.session.commit()
        return scan

    async def test_finalize_scan_then_a_second_finalize_sends_one_email(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        scan = await self._running(world)

        first = await scan_runner.finalize_scan(session, scan)
        stamped_at = first.finished_at
        second = await scan_runner.finalize_scan(session, scan)

        assert first.status is ScanStatus.SUCCEEDED
        assert second.finished_at == stamped_at, "a second stamp would move the end"
        assert await scan_emails.sweep(on, factory=wired) == 1
        assert await scan_emails.sweep(on, factory=wired) == 0
        assert outbox.subjects == [f"Your report for {CLIENT_NAME} is ready"]

    async def test_a_reaper_that_arrives_after_the_stamp_changes_nothing_and_sends_nothing(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        await self._running(world, lease_ends_in=-timedelta(minutes=1))
        assert await reap_stale_scans(session) == 1
        await session.commit()
        assert await scan_emails.sweep(on, factory=wired) == 1
        assert await scan_emails.sweep(on, factory=wired) == 0
        assert await reap_stale_scans(session) == 0
        await session.commit()
        assert await scan_emails.sweep(on, factory=wired) == 0

        assert outbox.subjects == [f"Your scan for {CLIENT_NAME} did not finish"]

    async def test_the_deadlines_finalise_is_emailed_like_any_other(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        scan = await self._running(world)

        await _finalize_after_deadline(scan.id, scan_runner.EnginePhaseProgress(), settings=on)

        assert await scan_emails.sweep(on, factory=wired) == 1
        assert outbox.subjects == [f"Your scan for {CLIENT_NAME} did not finish"]

    async def test_a_scan_marked_failed_after_a_crash_is_emailed(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        scan = await self._running(world)

        await _mark_failed(scan.id, RuntimeError("boom"), claimed=True, settings=on)

        assert await scan_emails.sweep(on, factory=wired) == 1
        assert outbox.subjects == [f"Your scan for {CLIENT_NAME} did not finish"]

    async def test_a_scan_that_is_still_running_is_not_emailed_until_it_ends(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, session: AsyncSession
    ) -> None:
        scan = await self._running(world)
        assert await scan_emails.sweep(on, factory=wired) == 0
        assert outbox.sent == []

        await scan_runner.finalize_scan(session, scan)

        assert await scan_emails.sweep(on, factory=wired) == 1
        assert len(outbox.sent) == 1


class TestTheWindow:
    def test_it_is_fifteen_minutes(self) -> None:
        assert timedelta(minutes=15) == scan_emails.WINDOW

    async def test_a_scan_that_finished_just_inside_the_window_is_emailed(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        await world.scan(
            client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=14, seconds=59)
        )

        assert await sweep(on, wired) == 1
        assert len(outbox.sent) == 1

    async def test_a_scan_that_finished_just_outside_it_is_not_and_is_left_unclaimed(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        scan = await world.scan(
            client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=15, seconds=1)
        )

        assert await sweep(on, wired) == 0

        assert outbox.sent == []
        assert await world.notified_at(scan) is None

    async def test_switching_it_on_does_not_email_every_past_scan(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        for days in range(1, 8):
            for status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED):
                await world.scan(client, requester, status, ago=timedelta(days=days))

        assert await sweep(on, wired) == 0
        assert outbox.sent == []

    async def test_a_scan_that_finished_while_the_process_was_restarting_is_still_caught(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=10))

        assert await sweep(on, wired) == 1


class TestTheBatch:
    async def test_a_sweep_takes_a_bounded_number_and_the_next_takes_the_rest(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scan_emails, "BATCH", 3)
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        for minutes in range(1, 8):
            await world.scan(
                client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=minutes)
            )

        assert [await sweep(on, wired) for _ in range(4)] == [3, 3, 1, 0]
        assert len(outbox.sent) == 7

    async def test_the_oldest_finish_goes_first(
        self, world: World, on: Settings, wired: Any, outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scan_emails, "BATCH", 1)
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency)
        old = await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=9))
        await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=1))

        await sweep(on, wired)

        assert f"/scans/{old.id}/report" in outbox.sent[0]["text"]


class TestTheClientsName:
    @pytest.mark.parametrize(
        ("typed", "said"),
        [
            ("Acme\r\nBcc: attacker@evil.example", "Acme Bcc: attacker@evil.example"),
            ("  Spaced \t Out  ", "Spaced Out"),
            ("Zero​Width‮Bidi", "ZeroWidthBidi"),
            ("\x07\x1b", "DOMAIN"),
            ("   ", "DOMAIN"),
        ],
        ids=["header injection", "whitespace", "invisible controls", "only controls", "blank"],
    )
    async def test_the_name_is_one_plain_line_in_the_subject_and_the_body(
        self, world: World, on: Settings, wired: Any, outbox: Outbox, typed: str, said: str
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency, name=typed)
        await world.scan(client, requester, ScanStatus.SUCCEEDED)

        await sweep(on, wired)

        said = said.replace("DOMAIN", client.domain)
        (message,) = outbox.sent
        assert message["subject"] == f"Your report for {said} is ready"
        assert message["text"].startswith(f"Your report for {said} is ready.\n\n")
        assert "\n" not in message["subject"] and "\r" not in message["subject"]


class TestOnlyWhatTheAgencyTypedNamesTheClient:
    """`intake` copies the classifier's brand name (read off the client's own site) into
    `Client.name` when nobody typed one. That is text the scanned site controls, so an email never
    carries it: the client is named by the domain the agency entered."""

    PROMOTED = "Security notice: your account is locked, sign in at evil.example"

    async def _scan_for(self, world: World, **client_kwargs: Any) -> Client:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency, **client_kwargs)
        await world.scan(client, requester, ScanStatus.SUCCEEDED)
        return client

    async def test_a_promoted_brand_name_is_never_in_the_subject_or_the_body(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        client = await self._scan_for(world, name=self.PROMOTED, brand_name=self.PROMOTED)

        await sweep(on, wired)

        (message,) = outbox.sent
        assert message["subject"] == f"Your report for {client.domain} is ready"
        assert "Security notice" not in json.dumps(message)
        assert "evil.example" not in json.dumps(message)

    async def test_it_holds_for_the_failed_and_alerts_emails_too(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        agency = await world.agency()
        requester = await world.user(agency)
        client = await world.client(agency, name=self.PROMOTED, brand_name=self.PROMOTED)
        ok = await world.scan(client, requester, ScanStatus.SUCCEEDED, ago=timedelta(minutes=3))
        await world.scan(client, requester, ScanStatus.FAILED, ago=timedelta(minutes=2))
        await world.alerts(ok, AlertKind.VISIBILITY_DROP)

        await sweep(on, wired)

        assert len(outbox.sent) == 3
        for message in outbox.sent:
            assert client.domain in message["subject"]
            assert "Security notice" not in json.dumps(message)

    async def test_a_name_the_agency_typed_is_used(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await self._scan_for(world, name="Our Own Label", brand_name=self.PROMOTED)

        await sweep(on, wired)

        assert outbox.subjects == ["Your report for Our Own Label is ready"]

    async def test_a_client_with_no_brand_name_keeps_its_typed_name(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await self._scan_for(world, name="Typed Name", brand_name=None)

        await sweep(on, wired)

        assert outbox.subjects == ["Your report for Typed Name is ready"]

    async def test_a_long_typed_name_is_cut_to_sixty_characters(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await self._scan_for(world, name="N" * 50 + " " + "M" * 49)

        await sweep(on, wired)

        said = outbox.subjects[0].removeprefix("Your report for ").removesuffix(" is ready")
        # Cut at a word boundary, with an ellipsis, inside sixty characters.
        assert said == "N" * 50 + "…" and len(said) <= 60

    def test_the_cap_is_sixty(self) -> None:
        assert email_service.NAME_MAX == 60
        assert len(email_service.plain_name("x" * 500)) == 60
        assert email_service.plain_name("x" * 500).endswith("…")
        assert email_service.plain_name("a" * 60) == "a" * 60, "sixty exactly is not cut"

    @pytest.mark.parametrize(
        "ch",
        ["\u3164", "\u115f", "\u1160", "\uffa0", "\u2800", "\u034f", "\ufe0f", "\ufe00",
         "\U000e0100", "\U000e01ef", "\u200b", "\u202e"],
    )
    def test_invisible_filler_characters_are_dropped(self, ch: str) -> None:
        assert email_service.plain_name(f"A{ch}B") == "AB"
        blank = email_service.plain_name(ch * 3, fallback="the-domain.example")
        assert blank == "the-domain.example"

    def test_an_encoded_word_cannot_be_decoded_back_into_hidden_characters(self) -> None:
        said = email_service.plain_name("=?utf-8?b?4oCu?= Re: =?UTF-8?Q?x?=")
        assert "=?" not in said

    def test_a_name_empty_after_cleaning_falls_back_to_the_given_one_then_to_a_client(
        self) -> None:
        assert email_service.plain_name("\u200b", fallback="x.example") == "x.example"
        assert email_service.plain_name("\u200b") == "a client"


class TestEverySendIsLoggedByIdOnly:
    async def test_a_sent_email_logs_the_event_the_user_and_the_scan_and_nothing_else(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        _, requester, _, scan = await a_finished_scan(world)
        await world.alerts(scan, AlertKind.VISIBILITY_DROP)

        with capture_logs() as logs:
            await sweep(on, wired)

        sent = events(logs, "email.")
        assert [e["event"] for e in sent] == ["email.scan_ready.sent", "email.scan_alerts.sent"]
        for line in sent:
            assert set(line) == {"event", "log_level", "user_id", "scan_id"}
            assert line["user_id"] == requester.id and line["scan_id"] == scan.id

    async def test_a_failed_scans_email_is_named_for_what_it_is(
        self, world: World, on: Settings, wired: Any, outbox: Outbox
    ) -> None:
        await a_finished_scan(world, ScanStatus.FAILED)

        with capture_logs() as logs:
            await sweep(on, wired)

        assert [e["event"] for e in events(logs, "email.")] == ["email.scan_failed.sent"]
