"""Automatic monthly rescans — F2, 2026-10-06 (`AUTO_RESCAN_ENABLED`, default off).

An owner or admin switches "Rescan every month" on for a client. Once that client's latest scan
started 30 or more days ago, the API starts a new scan, as the user who switched it on, through
the same start path as the Run button (`services/scan_start.py`): the engines, the reaper, the
allowance and the one-open-scan rule all apply. `auto_rescan_watch.py` is the loop that calls
`run_once`; this module is everything one wake does.

DUE. A client is due when it has the switch on, is not deleted (nor is its agency), has no scan
in flight, and its LATEST scan started 30 or more days ago (`INTERVAL`). A scan's start is its
submission, or its creation for a row nothing submitted; a detection-only QUEUED row (nothing
submitted it) is not a scan for this, and a scan that failed is a scan like any other, so a
failed automatic scan is not retried: the next is due 30 days after it. A client that has never
been scanned is not due.

ONE AT A TIME. A wake takes a transaction-level Postgres advisory lock for its whole length, so two
API instances awake at once (a deploy overlaps the old and the new) never both work; it starts
at most ONE scan; and it starts none while another automatic scan is queued or running
anywhere (after the reaper has had its say).

THE CLAIM. A due client is taken with ONE conditional UPDATE of its own row
(`auto_rescan_attempted_at`): it succeeds for one caller only, and the loser's WHERE no longer
holds once the winner commits. It holds for an hour (`CLAIM_HOLD`), so a crashed attempt is not
retried in a loop; and after a SKIP it holds until the first day of the next calendar month, UTC.
`uq_scans_one_open_per_client` is the backstop under it.

SKIPS. Each is recorded on the client (`auto_rescan_skip_reason`, and the time in
`auto_rescan_attempted_at`), shown on the client screen in plain words, and logged as
`rescan.skipped reason=...` with ids only:
* `AGENCY_NOT_PAID`: the agency is not paid up (its allowance would be the one free scan, or,
  with billing not configured, nothing to count it by). Skipped WITHOUT calling the start
  path. A listed operator (`is_exempt`) is not held to this.
* `ALLOWANCE_USED`: the start path refused with `402 scan-limit-reached` (the month's scans are
  used).
* `USER_NOT_ADMIN`: the user who switched it on is gone, no longer active, moved, or no longer an
  owner or admin.
* `COULD_NOT_START`: the start path refused or failed for any other reason (for example no engine
  key is set).
A skipped month is tried again on the first day of the next calendar month (UTC), when the
allowance resets, and never sooner: the claim says so, so there is no loop.

LOGS carry ids only: `rescan.started` (client, agency, scan, user) and `rescan.skipped`
(client, agency, reason). No name, address, domain or message goes in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, exists, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..errors import ProblemError, ScanLimitReached
from ..models import Agency, Client, Scan, ScanStatus, ScanTrigger, User, UserRole, UserStatus
from . import billing, scan_allowance
from .scan_executor import ScanExecutor, reap_stale_scans
from .scan_start import start_scan

logger = structlog.get_logger(__name__)

# A client is due once its latest scan started this long ago.
INTERVAL = timedelta(days=30)
# How long a claim holds when no skip is recorded: a crashed attempt is not retried sooner.
CLAIM_HOLD = timedelta(hours=1)
# How many due clients one wake looks at (skips cost no scan, so a wake may pass over several).
CANDIDATES_PER_WAKE = 25
# The wake's advisory lock (a transaction lock): a fixed number nothing else here takes.
WAKE_LOCK_KEY = 7_207_020_261_006

SKIP_NOT_PAID = "AGENCY_NOT_PAID"
SKIP_ALLOWANCE = "ALLOWANCE_USED"
SKIP_USER = "USER_NOT_ADMIN"
SKIP_COULD_NOT_START = "COULD_NOT_START"
SKIP_REASONS = frozenset({SKIP_NOT_PAID, SKIP_ALLOWANCE, SKIP_USER, SKIP_COULD_NOT_START})

_OPEN = (ScanStatus.QUEUED, ScanStatus.RUNNING)


def retries_at(skipped_at: datetime) -> datetime:
    """When a skip is tried again: the first instant of the next UTC calendar month."""
    return scan_allowance.period_bounds(skipped_at)[1]


def _claimable(now: datetime) -> list[Any]:
    """The conditions under which a client may be taken up now (see THE CLAIM)."""
    month_start = scan_allowance.period_bounds(now)[0]
    return [
        Client.auto_rescan.is_(True),
        Client.deleted_at.is_(None),
        or_(
            Client.auto_rescan_attempted_at.is_(None),
            and_(
                Client.auto_rescan_attempted_at < now - CLAIM_HOLD,
                or_(
                    Client.auto_rescan_skip_reason.is_(None),
                    Client.auto_rescan_attempted_at < month_start,
                ),
            ),
        ),
    ]


async def automatic_scan_open(db: AsyncSession) -> bool:
    """Whether an automatic scan is queued or running anywhere."""
    found = await db.execute(
        select(
            exists().where(Scan.trigger == ScanTrigger.SCHEDULED, Scan.status.in_(_OPEN))
        )
    )
    return bool(found.scalar_one())


async def due_client_ids(db: AsyncSession, now: datetime) -> list[str]:
    """The clients due and claimable now, the longest-waiting first, at most a wake's worth."""
    started = func.coalesce(Scan.submitted_at, Scan.created_at)
    latest = (
        select(Scan.client_id.label("client_id"), func.max(started).label("last_started"))
        # A QUEUED row nothing submitted is detection's open scan, not a scan that was started.
        .where(not_(and_(Scan.status == ScanStatus.QUEUED, Scan.submitted_at.is_(None))))
        .group_by(Scan.client_id)
        .subquery()
    )
    in_flight = exists().where(
        Scan.client_id == Client.id,
        or_(
            Scan.status == ScanStatus.RUNNING,
            and_(Scan.status == ScanStatus.QUEUED, Scan.submitted_at.is_not(None)),
        ),
    )
    rows = await db.execute(
        select(Client.id)
        .join(latest, latest.c.client_id == Client.id)
        .join(Agency, Agency.id == Client.agency_id)
        .where(
            *_claimable(now),
            Agency.deleted_at.is_(None),
            latest.c.last_started <= now - INTERVAL,
            not_(in_flight),
        )
        .order_by(latest.c.last_started, Client.id)
        .limit(CANDIDATES_PER_WAKE)
    )
    return list(rows.scalars().all())


async def claim(db: AsyncSession, client_id: str, now: datetime) -> bool:
    """Take this client up with one conditional UPDATE, committed. True for one caller only."""
    taken = await db.execute(
        update(Client)
        .where(Client.id == client_id, *_claimable(now))
        .values(auto_rescan_attempted_at=now, auto_rescan_skip_reason=None)
        .returning(Client.id)
    )
    won = taken.scalar_one_or_none() is not None
    await db.commit()
    return won


async def _record_skip(
    factory: async_sessionmaker[AsyncSession],
    client_id: str,
    agency_id: str,
    reason: str,
    now: datetime,
) -> None:
    async with factory() as db:
        await db.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(auto_rescan_skip_reason=reason, auto_rescan_attempted_at=now)
        )
        await db.commit()
    logger.info("rescan.skipped", reason=reason, client_id=client_id, agency_id=agency_id)


def _may_act_for_agency(user: User | None, agency_id: str) -> bool:
    return (
        user is not None
        and user.agency_id == agency_id
        and user.deleted_at is None
        and user.status is UserStatus.ACTIVE
        and user.role in (UserRole.OWNER, UserRole.ADMIN)
    )


async def _attempt(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    executor: ScanExecutor,
    client_id: str,
    now: datetime,
) -> str | None:
    """Start one claimed client's scan, or record why not. The scan's id when one was started."""
    async with factory() as db:
        client = await db.get(Client, client_id)
        if client is None or client.deleted_at is not None or not client.auto_rescan:
            return None
        agency_id = client.agency_id
        agency = await db.get(Agency, agency_id)
        if agency is None or agency.deleted_at is not None:
            return None
        user = (
            await db.get(User, client.auto_rescan_by_user_id)
            if client.auto_rescan_by_user_id
            else None
        )

        reason: str | None = None
        if not _may_act_for_agency(user, client.agency_id):
            reason = SKIP_USER
        elif not billing.is_active(agency) and not scan_allowance.is_exempt(settings, user):
            # Whether billing is configured or not: an agency that has not paid is not rescanned
            # unattended (no gate must not mean no limit for a switch nobody is watching).
            reason = SKIP_NOT_PAID
        if reason is None:
            assert user is not None
            try:
                scan = await start_scan(
                    db,
                    settings=settings,
                    executor=executor,
                    client=client,
                    user=user,
                    trigger=ScanTrigger.SCHEDULED,
                    now=now,
                )
            except ScanLimitReached:
                await db.rollback()
                reason = SKIP_ALLOWANCE
            except ProblemError:
                await db.rollback()
                reason = SKIP_COULD_NOT_START
            except Exception as exc:  # noqa: BLE001 - one client's failure must not stop the loop
                await db.rollback()
                logger.warning(
                    "rescan.failed", client_id=client_id, agency_id=agency_id,
                    error=type(exc).__name__,
                )
                reason = SKIP_COULD_NOT_START
            else:
                if scan.trigger is not ScanTrigger.SCHEDULED:
                    # A scan was already running (a person pressed Run between the list and
                    # now): nothing was started, and nothing is wrong with the client.
                    logger.info(
                        "rescan.skipped", reason="SCAN_ALREADY_RUNNING",
                        client_id=client.id, agency_id=client.agency_id,
                    )
                    return None
                await db.execute(
                    update(Client)
                    .where(Client.id == client.id)
                    .values(auto_rescan_skip_reason=None)
                )
                await db.commit()
                logger.info(
                    "rescan.started", client_id=client.id, agency_id=client.agency_id,
                    scan_id=scan.id, user_id=user.id,
                )
                return scan.id
    await _record_skip(factory, client_id, agency_id, reason, now)
    return None


async def run_once(
    settings: Settings,
    *,
    factory: async_sessionmaker[AsyncSession],
    executor: ScanExecutor,
    now: datetime | None = None,
) -> str | None:
    """One wake. The id of the scan it started, or None. Never raises for a client's failure."""
    moment = now or datetime.now(UTC)
    # The lock lives on this session's connection, which stays out of the pool until the session
    # closes: the end of the wake releases it, and so does a lost connection.
    async with factory() as lock_session:
        got = await lock_session.execute(select(func.pg_try_advisory_xact_lock(WAKE_LOCK_KEY)))
        if not got.scalar_one():
            return None
        try:
            async with factory() as db:
                if await reap_stale_scans(db, now=moment):
                    await db.commit()
                if await automatic_scan_open(db):
                    return None
                candidates = await due_client_ids(db, moment)
            for client_id in candidates:
                async with factory() as db:
                    won = await claim(db, client_id, moment)
                if not won:
                    continue
                scan_id = await _attempt(factory, settings, executor, client_id, moment)
                if scan_id is not None:
                    return scan_id
            return None
        finally:
            # A transaction lock: ending the transaction releases it, whatever happened above.
            await lock_session.rollback()
