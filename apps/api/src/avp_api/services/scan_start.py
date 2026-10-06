"""The one way a scan is started: the Run button's route and the monthly rescan both call it.

`POST /clients/{clientId}/scans` used to hold this body itself. Automatic rescans (F2) start
scans too, and a second copy of the start path would be a second place for the allowance, the
reaper or the one-open-scan rule to drift. So the body lives here, and there is one start path:

1. the engines are checked (a `422`, before anything is created);
2. stranded scans are reaped, so a lost executor's row is not adopted and re-run;
3. a scan already RUNNING is returned as it is, and nothing is started;
4. the allowance is checked under a lock on the agency row (`402 scan-limit-reached`);
5. the open scan is adopted or a new one created, stamped with its submission, and committed
   (which releases the lock);
6. the job is handed to the executor.

It raises the same problems the route always raised and returns the scan as committed (QUEUED,
or the RUNNING one it found), as the route always returned it. The tenancy check (`get_client`)
stays with each caller, because each reaches the client its own way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import ValidationProblem
from ..models import Client, Scan, ScanStatus, ScanTrigger
from ..models.engine_result import Engine
from ..models.tenancy import User
from . import competitors as detection
from . import scan_allowance
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
    existing = await detection.open_scan(db, client.id)
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

    scan = await detection.get_or_create_scan(db, client, user_id=user.id)

    # Read BEFORE stamping: a scan somebody already submitted is theirs, and an automatic start
    # must leave it alone (no new owner, no relabel, no second hand-off). A person's own start
    # restamps as it always did. Rolling back releases the allowance lock.
    was_submitted = scan.submitted_at is not None
    if trigger is not ScanTrigger.MANUAL and was_submitted:
        await db.rollback()
        return scan

    # Stamp the submission BEFORE the commit that releases the allowance lock,
    # so a concurrent request's count sees this scan holding its slot from now,
    # not from the (possibly days-old) detection row it adopted. Detection
    # never sets this. Skipped for RUNNING: nothing is handed off.
    if scan.status is ScanStatus.QUEUED:
        scan.submitted_at = moment
        if not was_submitted:
            # An unsubmitted detection row is adopted by whoever starts it: they are the person
            # the scan is for (and the one told when it finishes). A scan somebody else already
            # submitted keeps its owner and its label.
            scan.requested_by_user_id = user.id
            if trigger is not ScanTrigger.MANUAL:
                scan.trigger = trigger

    # Commit BEFORE handing off. The executor loads the scan on its own session
    # and would not find it otherwise. This commit also releases the lock.
    await db.commit()
    await db.refresh(scan)

    if scan.status is ScanStatus.RUNNING:
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
