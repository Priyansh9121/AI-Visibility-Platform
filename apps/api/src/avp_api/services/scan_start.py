"""The one way a scan is started: the Run button's route and the monthly rescan both call it.

`POST /clients/{clientId}/scans` used to hold this body itself. Automatic rescans (F2) start
scans too, and a second copy of the start path would be a second place for the allowance, the
reaper or the one-open-scan rule to drift. So the body lives here, and there is one start path:

1. the engines are checked (a `422`, before anything is created);
2. stranded scans are reaped, so a lost executor's row is not adopted and re-run;
3. a scan already RUNNING is returned as it is, and nothing is started;
4. the allowance is checked under a lock on the agency row (`402 scan-limit-reached`);
5. the open scan is adopted or a new one created and stamped with its submission by one
   conditional UPDATE (`submitted_at IS NULL`). Only the request that wins it claims; a request
   that finds the row already submitted claims nothing: an automatic start returns it as it is,
   and a person's own start restamps it and hands it to the executor again (the recovery of a
   job lost with its executor; the executor still runs the row once);
6. the platform's daily caps are claimed by that winner (`503 platform-cap-reached`,
   `services/spend_caps`): `scans`, plus `free_scans` when the agency is not paid up, in one
   all-or-nothing script. After the allowance, so an allowance refusal spends no platform unit;
   before any provider is called. A refusal rolls back the submission and any new row and
   releases the lock. The exempt operator counts toward `scans` only. Applies whether or not
   billing is on. Running scans are never re-checked; the commit releases the lock;
7. the job is handed to the executor.

It raises the same problems the route always raised and returns the scan as committed (QUEUED,
or the RUNNING one it found), as the route always returned it. The tenancy check (`get_client`)
stays with each caller, because each reaches the client its own way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import ProblemError, ValidationProblem
from ..models import Client, Scan, ScanStatus, ScanTrigger
from ..models.engine_result import Engine
from ..models.tenancy import Agency, User
from ..redis_client import get_redis
from . import billing, scan_allowance, spend_caps
from . import competitors as detection
from .engines import ENGINE_REGISTRY, configured_engines, keyed_engines
from .scan_executor import ScanExecutor, ScanJob, reap_stale_scans


async def start_scan(
    db: AsyncSession,
    *,
    settings: Settings,
    executor: ScanExecutor,
    client: Client,
    user: User,
    engines: Sequence[Engine] | None = None,
    prompt_limit: int | None = None,
    trigger: ScanTrigger = ScanTrigger.MANUAL,
    now: datetime | None = None,
    redis: Redis | None = None,
) -> Scan:
    """Queue a scan for `client` as `user`; the work happens out of band. See the module docstring.

    `trigger` is how the scan was started. A new scan carries it, and so does a detection row
    this adopts when it is not MANUAL (the row is being started by that trigger now). A MANUAL
    start leaves an adopted row as it is, as it always did.

    `now` is the clock the reaper, the allowance and the submission stamp all read; None is the
    real one, which is what the route passes. The monthly rescan passes its wake's, so a test can
    set the month it asks the allowance about.
    """
    moment = now or datetime.now(UTC)
    # Checked BEFORE the reaper runs and before a scan row is created or
    # adopted: a request that cannot run should leave nothing behind. The
    # schema has already collapsed duplicates and validated each value against
    # the Engine enum; this is the second check, against the engines that have
    # an adapter and a key. `ask_all` subscripts ENGINE_REGISTRY bare, so an
    # enum-only engine such as `perplexity` used to fail INSIDE the executor —
    # after competitor detection had been paid for — and land the scan at
    # FAILED / EXECUTION_FAILED. Now it is a 422 and no scan exists.
    # The default is the DEFAULT engines with a KEY, not the five with an
    # adapter — Epic 21. An engine nobody has provisioned would run as 20
    # failures a scan and read as "Gemini never named you". Naming one
    # explicitly is refused with the variable to set, the same 422 an
    # adapter-less engine gets, and for the same reason: it cannot answer.
    # Naming a REGISTERED, KEYED engine that is not a default is allowed —
    # that is how `claude_search` runs since it left the default set on
    # 2026-09-12 — so the second check below asks `keyed_engines`, not the
    # default set. (Before that date the two sets were identical and the
    # check read the default set; the suite caught the difference.)
    live = configured_engines(settings)
    keyed = keyed_engines(settings)
    chosen = tuple(engines) if engines else live
    unsupported = [e.value for e in chosen if e not in ENGINE_REGISTRY]
    if unsupported:
        raise ValidationProblem(
            detail=(
                f"No adapter for engine(s): {', '.join(unsupported)}. "
                f"Supported: {', '.join(e.value for e in ENGINE_REGISTRY)}."
            )
        )
    unkeyed = [e for e in chosen if e not in keyed]
    if unkeyed:
        raise ValidationProblem(
            detail=(
                "No key configured for engine(s): "
                + ", ".join(
                    f"{e.value} ({ENGINE_REGISTRY[e].key_setting.upper()})" for e in unkeyed
                )
                + ". Keyed: "
                + (", ".join(e.value for e in ENGINE_REGISTRY if e in keyed) or "none")
                + "."
            )
        )

    # Reap before reusing. A scan stranded at RUNNING by a lost executor would
    # otherwise be picked up by `get_or_create_scan` and re-run, which dies on
    # `prompt_sets`' unique constraint as a 500. This is the path where a
    # stranded row does real harm, so it is the path that clears it.
    if await reap_stale_scans(db, now=moment):
        await db.commit()

    # A scan already in flight is reported, not re-started — and not gated:
    # nothing is being started. Read before the lock so the common case pays
    # for no lock at all.
    existing = await detection.open_scan(db, client.id, agency_id=client.agency_id)
    if existing is not None and existing.status is ScanStatus.RUNNING:
        return existing
    if trigger is not ScanTrigger.MANUAL and existing is not None and existing.submitted_at:
        return existing

    # The allowance, under a row lock on the agency that the COMMIT below
    # releases. Gate off when billing is not configured — see the service.
    if scan_allowance.gate_enabled(settings):
        await scan_allowance.assert_can_start(
            db,
            client.agency_id,
            settings,
            exclude_scan_id=existing.id if existing is not None else None,
            now=moment,
            user=user,
        )

    # The platform's daily caps (D2), after the allowance and before any provider call. Counted
    # whether or not billing is on: cost safety does not depend on billing.
    scan = await detection.get_or_create_scan(db, client, user_id=user.id)
    if scan.status is not ScanStatus.QUEUED:
        # Raced to RUNNING: nothing to hand off, nothing to claim. The rollback expires every
        # loaded object, so the row is read again before anyone reads it.
        await db.rollback()
        await db.refresh(scan)
        return scan

    # Submit the row only if nobody has: ONE conditional UPDATE decides who is first, whatever
    # this session loaded earlier (`scan.submitted_at` may be stale; the row lock is not). The
    # request that wins is the only one that claims platform units: the executor runs a row once
    # (its QUEUED→RUNNING claim), so a start that finds the row already submitted buys nothing
    # and spends nothing. A scan somebody else submitted keeps its owner and label.
    values: dict[str, object] = {"submitted_at": moment, "requested_by_user_id": user.id}
    if trigger is not ScanTrigger.MANUAL:
        values["trigger"] = trigger
    won = (
        await db.execute(
            update(Scan)
            .where(Scan.id == scan.id, Scan.submitted_at.is_(None))
            .values(**values)
            .returning(Scan.id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    if won is None:
        if trigger is not ScanTrigger.MANUAL:
            # An automatic start leaves a submitted scan alone: no relabel, no second hand-off.
            await db.rollback()  # releases the allowance lock
            await db.refresh(scan)
            return scan
        # A person's own start re-hands a submitted QUEUED scan to the executor, as it always
        # did: a job lost with its executor (a restart) is recovered by pressing Run again, and
        # the executor still runs the row once. It restamps the submission and claims NOTHING:
        # only the first submission spends a platform unit.
        await db.execute(
            update(Scan)
            .where(Scan.id == scan.id, Scan.status == ScanStatus.QUEUED)
            .values(submitted_at=moment)
            .execution_options(synchronize_session=False)
        )
    else:
        await _claim_platform_units(db, client, user, settings, redis, moment)

    # Commit BEFORE handing off. The executor loads the scan on its own session
    # and would not find it otherwise. This commit also releases the lock.
    await db.commit()
    await db.refresh(scan)

    if scan.status is not ScanStatus.QUEUED:
        return scan

    await executor.submit(
        ScanJob(
            scan_id=scan.id,
            client_id=client.id,
            engines=chosen,
            prompt_limit=prompt_limit,
        ),
        settings=settings,
    )
    # Deliberately returns the row as committed — QUEUED — and does not re-read
    # after handing off. The `202` reports what was accepted, not how far it has
    # since got; an executor that happens to run synchronously must not change
    # this endpoint's contract. Completion is observed through
    # `GET /scans/{scanId}`, which is the one place that answers it.
    return scan


async def _claim_platform_units(
    db: AsyncSession,
    client: Client,
    user: User,
    settings: Settings,
    redis: Redis | None,
    moment: datetime,
) -> None:
    """Claim the platform's daily units (D2) for the request that submitted the scan.

    Every scan counts toward `scans`; one by an agency that is not paid up, and not the exempt
    operator's, also toward `free_scans`. One script checks both and increments neither when
    either is full. A refusal rolls back the submission, any new row and the agency lock.
    """
    agency = await db.get(Agency, client.agency_id)
    paid = agency is not None and billing.is_active(agency)
    kinds = ["scans"]
    if not paid and not scan_allowance.is_exempt(settings, user):
        kinds.append("free_scans")
    try:
        counter = redis if redis is not None else get_redis(settings)
        await spend_caps.claim(
            counter, settings, kinds=kinds, agency_id=client.agency_id, now=moment
        )
    except ProblemError:
        await db.rollback()  # the submission, any new row and the agency lock all go
        raise
