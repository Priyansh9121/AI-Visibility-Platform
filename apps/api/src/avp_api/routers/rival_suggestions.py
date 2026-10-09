"""Rival suggestions — F2 (2026-10-09).

One read-only route: the probable rivals of a client's latest scan, offered to the agency as
rivals to add. A probable rival is a domain with the client's own restricted ending (edu.au for
rmit.edu.au) that the scan's answers named a brand for ("Deakin University"); the report's "Get
onto" skips it (`services/report.py`) and this is where it is suggested instead.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from ..deps import DbDep, PrincipalDep
from ..schemas.rival_suggestions import RivalSuggestionsOut
from ..services import rival_suggestions as service
from ..services.intake import get_client

router = APIRouter(prefix="/clients", tags=["competitors"])

ClientId = Annotated[str, Path(alias="clientId")]


@router.get("/{clientId}/rival-suggestions")
async def get_rival_suggestions(
    principal: PrincipalDep,
    db: DbDep,
    client_id: ClientId,
) -> RivalSuggestionsOut:
    """Domains the latest scan's answers named that the agency may want as rivals.

    **Reads. Writes nothing.** A suggestion changes nothing: a domain is a rival only once the
    agency adds it through `PUT /clients/{clientId}/competitors`. **Agency only**: the report, its
    PDF and CSV, and the shared link never carry a suggestion.

    A domain is a *probable rival* only if (a) it has the client's own restricted ending (`edu.au`
    for `rmit.edu.au`; `ac.uk`, `gov.au`, `edu` and the other registry-restricted endings of the
    14 markets for their clients; a client with none, a `.com`, has no probable rivals) AND (b) at
    least one answer of the scan named a brand whose name matches its label ("Deakin University"
    for `deakin.edu.au`). It is read from the client's newest finished scan that recorded the
    fact; a scan from before it was recorded is passed over, and a client with no such scan has no
    suggestions. A domain is suggested only if nobody holds it now: not the client's own domain or
    confirmed sites, not an active rival of the list the next scan will use, nor that rival's
    confirmed other sites. A rival the agency struck is suggested again.

    Each suggestion is `domain`, `name` (to add it with: the label in capitals for two to four
    letters, "UNSW", else capitalised, "Deakin"), `citations` (the scan's answered results citing
    it, the count the report's lists use) and `answersNaming` (how many answers named it), most
    cited first, then by domain.

    **Errors:** `401`; `404` (another agency's client is the same `404` as an unknown one).
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    return await service.build_rival_suggestions(db, client)
