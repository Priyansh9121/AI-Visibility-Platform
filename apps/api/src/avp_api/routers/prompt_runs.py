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
which is what makes the comparison in `prompt_runs.RUNS_PER_CLIENT_PER_HOUR`
exact rather than approximate.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import NotFound, RateLimited, ValidationProblem
from ..models import Client
from ..schemas.prompt_run import PromptRunHistoryOut, PromptRunIn, PromptRunOut
from ..services import prompt_runs as service

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
    body: PromptRunIn,
    client_id: str = Path(alias="clientId"),
) -> PromptRunOut:
    """Ask one prompt of every engine and persist the facts."""
    client = await _load_client(db, client_id, principal.agency_id)

    # BEFORE anything is spent. The throttle is checked in the router rather
    # than inside `run_prompt` so that the decision to spend is visible at the
    # place the request arrives, not buried in the function that spends.
    try:
        await service.check_throttle(db, client.id)
        run = await service.run_prompt(
            db,
            client=client,
            agency_id=principal.agency_id,
            prompt_text=body.prompt,
            asked_by_user_id=principal.user_id,
        )
    except service.PromptRunError as exc:
        if exc.code == "PROMPT_RUN_RATE_LIMITED":
            raise RateLimited(detail=exc.detail) from exc
        raise ValidationProblem(detail=exc.detail) from exc

    await db.commit()
    await db.refresh(run)
    return PromptRunOut.model_validate(run)


@router.get("/clients/{clientId}/prompt-runs", response_model=PromptRunHistoryOut)
async def list_prompt_runs(
    db: DbDep,
    principal: PrincipalDep,
    client_id: str = Path(alias="clientId"),
) -> PromptRunHistoryOut:
    """This client's runs, newest first, with what the throttle has left."""
    client = await _load_client(db, client_id, principal.agency_id)
    runs = await service.history(db, client_id=client.id, limit=HISTORY_LIMIT)
    used = await service.runs_in_window(db, client.id)
    return PromptRunHistoryOut(
        data=[PromptRunOut.model_validate(r) for r in runs],
        runs_remaining=max(0, service.RUNS_PER_CLIENT_PER_HOUR - used),
        runs_per_hour=service.RUNS_PER_CLIENT_PER_HOUR,
        max_prompt_chars=service.MAX_PROMPT_CHARS,
    )
