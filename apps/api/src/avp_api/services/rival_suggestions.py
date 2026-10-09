"""Rival suggestions: a probable rival, offered to the agency as a rival (F2, 2026-10-09).

THE RULE (the founder's)
------------------------
A domain is a PROBABLE RIVAL only if (a) it has the client's own restricted ending (edu.au for
rmit.edu.au) AND (b) the scan's answers named a brand whose name matches its label ("Deakin
University" for deakin.edu.au). The report skips a probable rival as a "Get onto" target
(`report._proof`); this module is the other half, what the agency is asked instead: "deakin.edu.au:
cited 6 times, named in 4 answers. Add it as a rival?", on the client's Competitors page, never in
the client-facing report.

WHAT IT READS
-------------
The fact the scan recorded (`Scan.named_domains`, `probable_rivals.named_domains`): for each cited
domain with the client's ending, how many answers named its label. Which scan: the client's NEWEST
FINISHED scan (succeeded or partial, with an answered result) that recorded it. A scan from before
the column, or one whose measuring failed, holds NULL, which is "not recorded", never "none": it is
passed over and the last measurement stands. A scan that measured and found nothing named holds
`{}`, which is a measurement, and it replaces an earlier scan's.

A domain is suggested when at least one answer named it and it is NOT CURRENTLY CLAIMED: not the
client's own domain, not one of its confirmed other sites, not an active rival of the list the
client's NEXT scan will use (its next list if it has one, else the latest scan's set), and not a
confirmed other site of such a rival. A rival the agency struck is a tombstone and claims nothing.
Every comparison is on the DNS form (`intake.dns_form`), as `other_sites` makes its own.

`citations` is the scan's count for the domain, per registrable domain over its answered results:
the count `other_sites.cited_domain_counts` gives and the report's own unclaimed list uses. A domain
that no answered result of the scan cited is not offered ("cited 0 times" says nothing a person
could act on).

Reads; writes nothing and takes no lock. Nothing is logged: a domain is the client's business.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Client, Competitor, Scan
from ..models.engine_result import ANSWERED_STATUSES, EngineResult
from ..schemas.rival_suggestions import RivalSuggestionOut, RivalSuggestionsOut
from . import competitors as competitors_service
from . import other_sites, probable_rivals
from .client_history import PLOTTABLE
from .intake import dns_form


async def latest_scan_with_named_domains(session: AsyncSession, client: Client) -> Scan | None:
    """The client's newest finished scan that recorded `named_domains`, or None.

    Finished as `other_sites.latest_finished_scan` says it (`PLOTTABLE` with at least one answered
    result), and newest by id, which is creation order (ULIDs). A scan that recorded nothing
    (SQL NULL) is not it. The client's agency is named as well as its id, as there.
    """
    answered = (
        select(EngineResult.id)
        .where(EngineResult.scan_id == Scan.id, EngineResult.status.in_(ANSWERED_STATUSES))
        .exists()
    )
    return (
        await session.execute(
            select(Scan)
            .where(
                Scan.client_id == client.id,
                Scan.agency_id == client.agency_id,
                Scan.status.in_(PLOTTABLE),
                Scan.named_domains.is_not(None),
                answered,
            )
            .order_by(Scan.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def claimed_hosts(client: Client, rivals: list[Competitor]) -> frozenset[str]:
    """Every host somebody already holds, in DNS form: the client's own domain and confirmed
    sites, and each active rival's domain and confirmed sites."""
    _name, own_domain = other_sites.client_brand(client)
    hosts = {dns_form(own_domain), *(dns_form(site) for site in client.other_sites)}
    for row in rivals:
        if row.domain:
            hosts.add(dns_form(row.domain))
        hosts.update(dns_form(site) for site in row.other_sites)
    return frozenset(hosts)


async def build_rival_suggestions(session: AsyncSession, client: Client) -> RivalSuggestionsOut:
    """The probable rivals of the client's latest recorded scan that nobody holds, suggested."""
    scan = await latest_scan_with_named_domains(session, client)
    if scan is None:
        return RivalSuggestionsOut(suggestions=[])
    named = scan.named_domains
    probable = probable_rivals.domains_named_in_answers(named)
    if not probable or not isinstance(named, dict):
        return RivalSuggestionsOut(suggestions=[])

    current = await competitors_service.current_list_for_client(session, client)
    rivals = other_sites.active_rivals(current)
    claimed = claimed_hosts(client, rivals)
    cited = {
        dns_form(domain): count
        for domain, count in (await other_sites.cited_domain_counts(session, scan.id)).items()
    }

    found: list[RivalSuggestionOut] = []
    for domain in probable:
        host = dns_form(domain)
        if host in claimed or cited.get(host, 0) < 1:
            continue
        found.append(
            RivalSuggestionOut(
                domain=domain,
                name=probable_rivals.rival_name(domain),
                citations=cited[host],
                answers_naming=named[domain],
            )
        )
    found.sort(key=lambda s: (-s.citations, s.domain))
    return RivalSuggestionsOut(suggestions=found)


__all__ = ["build_rival_suggestions", "claimed_hosts", "latest_scan_with_named_domains"]
