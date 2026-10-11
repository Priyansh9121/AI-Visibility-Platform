"""A brand's other sites — M1, task 1.1.

A domain with the brand's name under another public suffix (rmit.edu.vn for rmit.edu.au) is
PROPOSED as the brand's other site, and counts as the brand's own only once the agency CONFIRMS
it. These two routes read what is confirmed, dismissed and proposed, and replace the lists. They
change nothing else themselves: a CONFIRMED site is read once by each scan and prompt run when it
starts (task 1.2) and is stored with its citations; nothing downstream reads the lists again, and a
proposal changes nothing. The lists hold
domains in the DNS form and every comparison is made in it (`services/other_sites.py`).

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Path

from ..deps import DbDep, PrincipalDep
from ..errors import Conflict
from ..models import ScanStatus
from ..schemas.other_sites import OtherSitesOut, ReplaceOtherSitesRequest
from ..services import competitors as detection
from ..services import other_sites as service
from ..services.intake import get_client

# The helper `PUT /clients/{clientId}/competitors` writes the client's next list with, reused so
# the two writers of that list (launch checklist 3.78/3.79) make and lock it one way.
from .competitors import next_list_to_replace

router = APIRouter(prefix="/clients", tags=["other-sites"])
logger = structlog.get_logger(__name__)

ClientId = Annotated[str, Path(alias="clientId")]


@router.get("/{clientId}/other-sites")
async def get_other_sites(
    principal: PrincipalDep,
    db: DbDep,
    client_id: ClientId,
) -> OtherSitesOut:
    """What is confirmed, dismissed and proposed as this brand's other sites.

    **Reads. Writes nothing.** A proposal changes nothing: a domain counts as a brand's own only
    once it is on a confirmed list.

    `confirmed` is the client's own confirmed sites, then those of each active rival of the list
    the client's NEXT scan will use (its next list if it has one, else the latest scan's set), in
    rank order. `proposals` come from the client's latest FINISHED scan (succeeded or partial,
    with answered results): each domain its answered results cited that carries the client's name,
    or the first rival's in rank order, under another public suffix, counted per domain, most
    cited first. The client's and the rivals' own domains and anything already confirmed or
    dismissed are not proposed, whichever way each is spelled (`bücher.de` and `xn--bcher-kva.de`
    are one host). `dismissed` is one list per client, for the client and its rivals alike. A
    client with no finished scan has no proposals, and one with no rivals list has only its own.

    `confirmed` and `dismissed` are returned as they are stored, in the DNS form (lower case
    ASCII: `xn--bcher-kva.at` for `bücher.at`). A proposal keeps the spelling the engine
    cited, which is the domain it cited.

    **Errors:** `401`; `404` (another agency's client is the same `404` as an unknown one).
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    return await service.build_other_sites(db, client)


@router.put("/{clientId}/other-sites")
async def replace_other_sites(
    payload: ReplaceOtherSitesRequest,
    principal: PrincipalDep,
    db: DbDep,
    client_id: ClientId,
) -> OtherSitesOut:
    """Replace the confirmed sites of the client and its rivals, and the dismissed domains.

    All three lists are replaced and all three are named: a rival left out of `rivals` has its
    sites cleared. The answer is the body of `GET`.

    Every domain is stored in the DNS form (lower case ASCII, an internationalised label
    IDNA-encoded: `bücher.at` is stored as `xn--bcher-kva.at`) and compared in it, so
    `bücher.de` and `xn--bcher-kva.de` are one host: it is listed once, it is a brand's own
    domain, and it cannot be both confirmed and dismissed, whichever way each is spelled, and
    saving a host again in the other spelling changes nothing. A rival is named by its
    `rivalDomain` in either spelling. The answer is what is stored.

    The client's confirmed sites and the dismissed list are the client's own. A rival's sites are
    written to its row on the client's NEXT LIST, never to a scan's own set: the list the next
    scan will adopt is edited in place, or made as a copy of the latest scan's set exactly as
    `PUT /clients/{clientId}/competitors` does when there is none. A scan that has started keeps
    the set it started with. A save that changes no rival's sites makes no list.

    While the client's most recent scan is RUNNING the answer is `409` and nothing is written, as
    for the competitors list: a running scan may or may not have read the lists already.

    **Errors:** `401`; `404`; `409` ("A scan of this client is running."); `422`. The request schema
    cuts a list off first, with nothing read: more than 50 entries for the client or for a rival's
    `sites`, more than 250 `dismissed` or more than 10 `rivals` is a `422` of type `too_long`, in
    pydantic's own words. Every other `422` has one plain sentence in `detail` that names the
    domain as it was typed, the same sentence in `errors[0].message`, and the place and a code in
    `errors[0].field` and `errors[0].type`.
    The first thing wrong is the one reported, in this order: a `rivalDomain` that is not a
    bare domain (`invalid_domain`, which quotes nothing typed); more than 20 entries for a
    brand (`too_many_sites`) or 200 dismissed (`too_many_dismissed`), counted as sent, repeats
    included, and checked before any site is read; a site or dismissed domain that is not a
    bare domain (`invalid_domain`); a rival that is named twice (`repeated_rival`) or is not on
    the list (`unknown_rival`); a site that is a brand's own domain (`a_brands_own_domain`), is
    not the brand's name under another public suffix (`not_the_same_name`) or is confirmed for
    two brands (`one_brand_only`); and a domain both confirmed and dismissed
    (`confirmed_and_dismissed`). Nothing is written when any is refused.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    latest_scan = await detection.latest_scan_for_client(db, client.id, client.agency_id)
    if latest_scan is not None and latest_scan.status is ScanStatus.RUNNING:
        # The competitors list's own rule, for the same reason. A save never reaches a scan's own
        # set, so this is not about the rivals' rows: a running scan reads the client's confirmed
        # sites once, as it starts (task 1.2), and the person cannot be told whether it has
        # yet. Queued and finished scans are no reason to refuse.
        raise Conflict(
            detail="A scan of this client is running. Change its other sites after it finishes."
        )

    current = await detection.current_list_for_client(db, client)
    _name, client_domain = service.client_brand(client)
    replacement = service.checked_replacement(
        client_domain=client_domain,
        rival_domains=[row.domain for row in service.active_rivals(current) if row.domain],
        client_sites=payload.client,
        rival_sites=[(item.rival_domain, item.sites) for item in payload.rivals],
        dismissed=payload.dismissed,
    )

    if service.rival_sites_differ(current, replacement.rivals):
        # The next list is the only set a save writes: locked until the commit, or made as a copy
        # of the latest scan's set. There is a list to copy, because a rival has sites to change.
        next_list = await next_list_to_replace(db, client, naming_rivals=True)
        service.write_rival_sites(next_list, replacement.rivals)
    client.other_sites = replacement.client
    client.dismissed_other_sites = replacement.dismissed
    await db.commit()

    logger.info(
        "other_sites.saved",
        client_id=client.id,
        client_sites=len(replacement.client),
        rival_sites=sum(len(sites) for sites in replacement.rivals.values()),
        dismissed=len(replacement.dismissed),
    )
    return await service.build_other_sites(db, client)
