"""GET and PUT /clients/{clientId}/questions: ask for new questions at the next scan (F6).

A rescan copies the client's last all-generated question set. This flag is the agency's way to
have the next scan write new ones instead. It is read from the database when that scan builds its
set, and cleared in the transaction that stores a fresh set the next rescan would copy
(`scan_runner.build_prompt_set`): a set topped up with templates, or cut to a prompt limit, leaves
it set. Monthly auto-rescans obey it too. Every endpoint here is recorded in
docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import Conflict
from ..models import Scan, ScanStatus
from ..schemas.questions import ClientQuestionsOut, NewQuestionsRequest
from ..services import question_view
from ..services.intake import get_client

router = APIRouter(prefix="/clients", tags=["questions"])

ClientId = Annotated[str, Path(alias="clientId")]


@router.get("/{clientId}/questions")
async def get_questions(
    principal: PrincipalDep,
    db: DbDep,
    client_id: ClientId,
) -> ClientQuestionsOut:
    """Whether new questions are asked for at the next scan, and the set the client keeps.

    `currentSet` is the set a rescan would copy (null until the client has one) with where and
    when it was written, since when it has been reused, the rivals it was written with and whether
    the client's rivals have changed since. Counts, dates and names; no question text. Any member
    of the agency; another agency's client and an unknown one are both the `404`.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    return await question_view.client_questions(db, client)


@router.put("/{clientId}/questions")
async def set_new_questions(
    payload: NewQuestionsRequest,
    principal: PrincipalDep,
    db: DbDep,
    client_id: ClientId,
) -> ClientQuestionsOut:
    """Set or take back "write new questions for the next scan". Any member of the agency.

    While a scan of the client is RUNNING the answer is `409` and nothing is written: a running
    scan may already have built its questions, and a save could not say whether it applies to it
    or to the next one. A queued or finished scan is no reason to refuse. Another agency's client
    and an unknown one are both the `404`.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    # Best-effort, not a lock: a scan claimed in the instant between this read and the commit below
    # may still read the old value. Only this agency's scans of the client count.
    running = (
        await db.execute(
            select(Scan.id)
            .where(
                Scan.client_id == client.id,
                Scan.agency_id == client.agency_id,
                Scan.status == ScanStatus.RUNNING,
            )
            .limit(1)
        )
    ).first()
    if running is not None:
        raise Conflict(
            detail="A scan of this client is running. Change its questions after it finishes."
        )
    client.new_questions_next_scan = payload.new_questions_next_scan
    await db.commit()
    return await question_view.client_questions(db, client)
