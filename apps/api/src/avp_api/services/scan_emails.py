"""Email the user who started a scan when it finishes — F1, 2026-10-06.

OFF unless `EMAIL_SCAN_NOTIFICATIONS` is on, and then only while email is configured
(`Settings.scan_emails_available`): with either missing `sweep` returns at once and claims,
sends and logs nothing.

A SWEEP, NOT A HOOK. Four paths write a final status (`finalize_scan`, the deadline's finalise,
`_mark_failed`, the reaper), so none of them is hooked. `scan_email_watch.py` calls `sweep`
every minute; it finds scans that reached SUCCEEDED, PARTIAL or FAILED in the last `WINDOW`
with no `notified_at` and a requester, CLAIMS each with one conditional UPDATE (`notified_at`
set where it is null) and only then sends. Zero rows means another instance has it, so two
instances during a deploy send one email. The window is what stops a switch-on emailing every
past scan, and it still covers a scan that finished just before a restart.

AT MOST ONCE. The claim is committed before the send, and a failed send is logged and not
retried: a missed email is better than two.

WHO GETS NOTHING, checked when the email is sent: a requester who is gone, suspended, not in the
scan's agency or opted out (`users.scan_emails`), and a client or agency that is gone. Each is
one `scan_emails.skipped` line with the ids and a fixed reason. CANCELLED, QUEUED and RUNNING
scans are never selected.

WHAT IS SAID. Facts only: the client's name, counts of alerts by kind, and links built from
`PUBLIC_WEB_BASE_URL`. This module reads no score, no answer and no alert sentence; an alert is
read for its kind alone.

LOGS. Event, `user_id`, `scan_id` and a fixed reason. Never an address, a link or a name, and an
error is logged as its class only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..db import get_sessionmaker
from ..models import Agency, Alert, AlertKind, Client, Scan, ScanStatus, User, UserStatus
from . import email as email_service

logger = structlog.get_logger(__name__)

# A scan is emailed only if it ended within this long of the sweep.
WINDOW = timedelta(minutes=15)
# At most this many scans per sweep; the rest are found by the next one (still inside the window).
BATCH = 50
NOTIFIABLE = (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED)

Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Claimed:
    scan_id: str
    client_id: str
    agency_id: str
    requester_id: str
    status: ScanStatus


def client_label(name: str, brand_name: str | None, domain: str) -> str:
    """How an email names the client: what the agency typed, else the domain it entered.

    Intake copies the classifier's brand name, read off the scanned site's own copy, into
    the client's name column when nobody typed one. A client name equal to the brand name is
    that text, which the
    site controls, so it never reaches an email.
    """
    typed = name if brand_name is None or name != brand_name else ""
    return email_service.plain_name(typed, fallback=email_service.plain_name(domain))


async def claim(factory: Factory, scan_id: str, *, now: datetime) -> Claimed | None:
    """Claim a scan for notification: one conditional UPDATE, committed before anything is sent.

    None means somebody else has it (or it is no longer due). The conditions are the sweep's own,
    repeated, so a scan that is not due is never claimed.
    """
    async with factory() as session:
        row = (
            await session.execute(
                update(Scan)
                .where(
                    Scan.id == scan_id,
                    Scan.notified_at.is_(None),
                    Scan.status.in_(NOTIFIABLE),
                    Scan.finished_at >= now - WINDOW,
                    Scan.requested_by_user_id.is_not(None),
                )
                .values(notified_at=now)
                .returning(
                    Scan.client_id, Scan.agency_id, Scan.requested_by_user_id, Scan.status
                )
                .execution_options(synchronize_session=False)
            )
        ).first()
        await session.commit()
    if row is None:
        return None
    return Claimed(scan_id, row[0], row[1], row[2], row[3])


def _skip(claimed: Claimed, reason: str) -> None:
    logger.info(
        "scan_emails.skipped",
        scan_id=claimed.scan_id,
        user_id=claimed.requester_id,
        reason=reason,
    )


async def notify(factory: Factory, settings: Settings, claimed: Claimed) -> None:
    """Decide, at send time, whether the requester is to be emailed, and send what is due."""
    base = settings.public_web_base_url.rstrip("/")
    async with factory() as session:
        user = (
            await session.execute(
                select(
                    User.email, User.agency_id, User.status, User.deleted_at, User.scan_emails
                ).where(User.id == claimed.requester_id)
            )
        ).first()
        if user is None or user.deleted_at is not None:
            _skip(claimed, "requester_gone")
            return
        if user.agency_id != claimed.agency_id:
            _skip(claimed, "requester_other_agency")
            return
        if user.status is not UserStatus.ACTIVE:
            _skip(claimed, "requester_not_active")
            return
        if not user.scan_emails:
            _skip(claimed, "opted_out")
            return
        agency_alive = (
            await session.execute(
                select(Agency.id).where(Agency.id == claimed.agency_id, Agency.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if agency_alive is None:
            _skip(claimed, "agency_gone")
            return
        client_row = (
            await session.execute(
                select(Client.name, Client.brand_name, Client.domain).where(
                    Client.id == claimed.client_id,
                    Client.agency_id == claimed.agency_id,
                    Client.deleted_at.is_(None),
                )
            )
        ).first()
        if client_row is None:
            _skip(claimed, "client_gone")
            return
        client_name = client_label(*client_row)
        counts: dict[AlertKind, int] = {}
        if claimed.status is not ScanStatus.FAILED:
            # The kind of each alert and how many: never the row's own sentence.
            rows = (
                await session.execute(
                    select(Alert.kind, func.count())
                    .where(Alert.scan_id == claimed.scan_id, Alert.agency_id == claimed.agency_id)
                    .group_by(Alert.kind)
                )
            ).all()
            counts = {kind: n for kind, n in rows}
    # No connection is held while the provider is called.
    to, uid, sid = user.email, claimed.requester_id, claimed.scan_id
    settings_url = f"{base}/settings"
    if claimed.status is ScanStatus.FAILED:
        await email_service.send_scan_failed(
            to, user_id=uid, scan_id=sid, client_name=client_name,
            client_url=f"{base}/clients/{claimed.client_id}",
            settings_url=settings_url, settings=settings,
        )
        return
    await email_service.send_scan_ready(
        to, user_id=uid, scan_id=sid, client_name=client_name,
        report_url=f"{base}/scans/{sid}/report",
        settings_url=settings_url, partial=claimed.status is ScanStatus.PARTIAL,
        settings=settings,
    )
    if counts:
        await email_service.send_scan_alerts(
            to, user_id=uid, scan_id=sid, client_name=client_name, counts=counts,
            alerts_url=f"{base}/clients/{claimed.client_id}/alerts",
            settings_url=settings_url, settings=settings,
        )


async def sweep(
    settings: Settings, *, factory: Factory | None = None, now: datetime | None = None
) -> int:
    """One pass. Returns how many scans this call claimed. Inert unless the feature is available."""
    if not settings.scan_emails_available:
        return 0
    moment = now or datetime.now(UTC)
    sessions = factory or get_sessionmaker(settings)
    async with sessions() as session:
        due = list(
            (
                await session.execute(
                    select(Scan.id)
                    .where(
                        Scan.status.in_(NOTIFIABLE),
                        Scan.finished_at >= moment - WINDOW,
                        Scan.notified_at.is_(None),
                        Scan.requested_by_user_id.is_not(None),
                    )
                    .order_by(Scan.finished_at, Scan.id)
                    .limit(BATCH)
                )
            ).scalars()
        )
    claimed_count = 0
    for scan_id in due:
        claimed = await claim(sessions, scan_id, now=moment)
        if claimed is None:
            continue
        claimed_count += 1
        try:
            await notify(sessions, settings, claimed)
        except Exception as exc:  # noqa: BLE001 - one scan's failure is not the sweep's
            logger.warning(
                "scan_emails.failed",
                scan_id=claimed.scan_id,
                user_id=claimed.requester_id,
                error=type(exc).__name__,
            )
    return claimed_count
