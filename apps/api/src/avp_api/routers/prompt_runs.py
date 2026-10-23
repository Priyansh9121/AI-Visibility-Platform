"""Ad-hoc prompt endpoints — Epic 9.24.

Every endpoint here is recorded in docs/api-contracts.md.

    POST /clients/{clientId}/prompt-runs   run one prompt against every engine
    GET  /clients/{clientId}/prompt-runs   this client's run history

WHY THE RUN IS SYNCHRONOUS
---------------------------
A scan is backgrounded because it takes ~6 minutes and nobody waits for it. A
run is three concurrent engine calls bounded by `ENGINE_CALL_CEILING` (122s
worst case, ~23s median measured in Epic 9.8), and the operator asked a question
they are sitting there waiting to hear the answer to. Backgrounding it would
mean inventing a status to poll for a request that finishes inside the window a
browser will happily hold open — machinery with no beneficiary.

It is the same amount of work one `PROMPT_CONCURRENCY` slot does inside a scan,
which is what makes the comparison in `prompt_runs`' throttle note
exact rather than approximate.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, RedisDep, SettingsDep
from ..errors import NotFound, RateLimited, ValidationProblem
from ..models import Client
from ..schemas.prompt_run import PromptRunHistoryOut, PromptRunIn, PromptRunOut
from ..services import prompt_runs as service
from ..services import rate_limit, scan_allowance, spend_caps

router = APIRouter(tags=["prompt-runs"])

HISTORY_LIMIT = 50


async def _load_client(db: DbDep, client_id: str, agency_id: str) -> Client:
    client = (
        await db.execute(
            select(Client).where(
                Client.id == client_id,
                Client.agency_id == agency_id,
                Client.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if client is None:
        # 404 not 403 — confirming an id exists leaks across tenants. The same
        # call every other client-scoped router here makes.
        raise NotFound(detail="No client with that identifier.")
    return client


@router.post(
    "/clients/{clientId}/prompt-runs",
    response_model=PromptRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_run(
    db: DbDep,
    principal: PrincipalDep,
    redis: RedisDep,
    settings: SettingsDep,
    body: PromptRunIn,
    client_id: str = Path(alias="clientId"),
) -> PromptRunOut:
    """Ask one prompt of every engine and persist the facts.

    Every engine is a paid call, so before any is made (F3, 2026-10-10), in this order, each
    step refusing before the next is spent: **the plan** (an agency that is paid up, or the
    exempt operator, or any agency while billing is not configured), **this client's hour**
    (`PROMPT_RUNS_PER_CLIENT_PER_HOUR`, default 30: the run history first, then an atomic
    Redis count, so a burst cannot pass), **the platform's day**
    (`MAX_PROMPT_RUNS_PER_DAY_PLATFORM`, default 300) and **this agency's day**
    (`PROMPT_RUNS_PER_AGENCY_PER_DAY`, default 100), both or neither. A refused request spends
    none of the later steps, and a refusal by the agency's or the platform's day gives the
    client's hour back. Nothing taken is given back if the run then fails.

    **Errors:** `401`; `402 subscription-required` (to the owner, "Asking your own questions
    needs a plan. Start one in Settings, under Billing."; to an admin or a member, who cannot
    start a plan, "Asking your own questions needs a plan. Ask an owner to manage billing.");
    `404` (unknown client, or another agency's); `422` (blank or over-long prompt);
    `429 rate-limited` (with `Retry-After`): "This client
    has run N prompts in the last hour, which is the limit of N. ..."; `429
    rate-limited` (with `Retry-After` to UTC midnight): "This agency has asked as many
    questions as it can today. Try again after midnight UTC."; `503
    platform-cap-reached` (with `Retry-After` to UTC midnight): "AVP has answered as many
    questions as it allows in a day. Try again after midnight UTC."; `503
    service-unavailable` (Redis cannot say, so nothing is asked).
    """
    client = await _load_client(db, client_id, principal.agency_id)

    # The plan first, then the client's hour, then the platform's day, then the call: a
    # refusal spends nothing later in the line. The decision to spend is made here, where the
    # request arrives, and not in the function that spends.
    scan_allowance.assert_may_ask_prompts(
        settings, agency=principal.agency, user=principal.user, client_id=client.id
    )
    try:
        # A blank or over-long prompt is the caller's, and spends no claim.
        text = service.normalise_prompt(body.prompt)
        await service.check_throttle(
            db, client.id, limit=settings.prompt_runs_per_client_per_hour
        )
    except service.PromptRunError as exc:
        raise _problem(exc) from exc
    allowed, ms_left = await rate_limit.claim_prompt_run(
        redis, settings, client_id=client.id, agency_id=principal.agency_id
    )
    if not allowed:
        limit = settings.prompt_runs_per_client_per_hour
        raise RateLimited(
            detail=service.throttle_sentence(limit, limit),
            headers={"Retry-After": str(max(1, -(-ms_left // 1000)))},
        )
    try:
        await spend_caps.claim_for_agency(
            redis, settings, kind="prompt_runs", agency_id=principal.agency_id
        )
    except Exception:
        # Nothing was asked: the client's hour is not spent by the agency's or the platform's day.
        await rate_limit.release_prompt_run(redis, settings, client_id=client.id)
        raise
    try:
        run = await service.run_prompt(
            db,
            client=client,
            agency_id=principal.agency_id,
            prompt_text=text,
            asked_by_user_id=principal.user_id,
        )
    except service.PromptRunError as exc:
        raise _problem(exc) from exc

    await db.commit()
    await db.refresh(run)
    return PromptRunOut.model_validate(run)


def _problem(exc: service.PromptRunError) -> RateLimited | ValidationProblem:
    if exc.code == "PROMPT_RUN_RATE_LIMITED":
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return RateLimited(detail=exc.detail, headers=headers)
    return ValidationProblem(detail=exc.detail)


@router.get("/clients/{clientId}/prompt-runs", response_model=PromptRunHistoryOut)
async def list_prompt_runs(
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    client_id: str = Path(alias="clientId"),
) -> PromptRunHistoryOut:
    """This client's runs, newest first, with what the throttle has left."""
    client = await _load_client(db, client_id, principal.agency_id)
    runs = await service.history(
        db, client_id=client.id, agency_id=client.agency_id, limit=HISTORY_LIMIT
    )
    used = await service.runs_in_window(db, client.id)
    return PromptRunHistoryOut(
        data=[PromptRunOut.model_validate(r) for r in runs],
        runs_remaining=max(0, settings.prompt_runs_per_client_per_hour - used),
        runs_per_hour=settings.prompt_runs_per_client_per_hour,
        max_prompt_chars=service.MAX_PROMPT_CHARS,
    )
