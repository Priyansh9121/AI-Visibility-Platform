"""A brand's other sites: the rule, the proposals, and the lists (M1, task 1.1).

THE PROBLEM
-----------
The report counts a cited domain as the client's own only on EXACT equality
(`extraction.classify_citation`). RMIT University (rmit.edu.au) had rmit.edu.vn, RMIT Vietnam's
site, cited four times as an independent third-party source, and because an `edu` domain is Tier A
(`domain_authority`), RMIT's own Vietnamese site raised RMIT's own Citation Strength.

THE PLAN (the founder's)
------------------------
A domain with the brand's name under ANOTHER public suffix is PROPOSED as the brand's other site.
It counts as the brand's own only once the AGENCY CONFIRMS it. **A proposal changes nothing**:
nothing in this module writes a list, and nothing that scores or types a citation reads one yet
(task 1.2 makes extraction and scoring use the confirmed lists; task 1.3 the report).

WHAT IS HERE
------------
* `same_brand_elsewhere`: the rule, over the Public Suffix List's own split.
* `proposals`: one scan's cited domains turned into what the agency is asked about. Pure.
* `build_other_sites`: the GET body (confirmed, proposals, dismissed). Reads only.
* `checked_replacement` and the helpers beside it: the PUT's validation, which refuses with one
  plain sentence that names the domain. Pure, and it writes nothing.

ONE HOST, TWO SPELLINGS
-----------------------
A cited domain keeps the spelling the engine gave (`bücher.de` or `xn--bcher-kva.de`) and
`intake.rival_domain` keeps the spelling typed, so one host can arrive in both. EVERY COMPARISON
IN THIS MODULE IS MADE ON THE DNS FORM (`intake.dns_form`: `encode_hostname`, as `PUT
/clients/{clientId}/competitors` compares a rival's domain with the client's): the rule, whether
a domain is a brand's own, confirmed, dismissed or already listed, whether another brand claims
it, and which rival's row a request names. For a client at `пример.рф`, `пример.xn--p1ai` is
the same host and not its other site.

THE THREE LISTS HOLD THE DNS FORM. A domain the agency confirms or dismisses is written as
`dns_form` of what `rival_domain` accepted: `bücher.at` is stored as `xn--bcher-kva.at`. So a
list holds one string per host, a save that respells what is stored changes nothing, and the
lists are lower case ASCII in any database encoding (a JSONB column will not hold a non-ASCII
character in a database that is not UTF8). `GET` returns them as stored. A PROPOSAL is the
other way round: it is a domain an engine cited, and keeps the spelling the engine gave. A
client's and a rival's own `domain` keep the spelling they were typed in.

WHERE THE LISTS LIVE
--------------------
The client's confirmed sites and the dismissed list are on `clients` (`other_sites`,
`dismissed_other_sites`; the dismissed list is one per client, for the client and its rivals
alike). A rival's confirmed sites are on ITS ROW (`competitors.other_sites`), because a scan's
competitor set is a frozen copy of the client's list: they are frozen with it, and the client's
next list is the one set a save writes (`routers/other_sites.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ValidationProblem
from ..models import Client, Competitor, CompetitorSet, Scan
from ..models.engine_result import ANSWERED_STATUSES, Citation, EngineResult
from ..schemas.other_sites import (
    ConfirmedSiteOut,
    OtherSitesOut,
    ProposedSiteOut,
)
from . import competitors as competitors_service
from .client_history import PLOTTABLE
from .crawl import name_and_icann_suffix, name_and_suffix
from .intake import InvalidUrl, checked_domain, dns_form, rival_domain

# How many other sites one brand may have confirmed, and how many domains the agency may dismiss
# for one client (the brief of task 1.1): bounded so a list cannot grow without end.
MAX_SITES_PER_BRAND = 20
MAX_DISMISSED = 200

BrandKind = Literal["client", "rival"]


# --- the rule -----------------------------------------------------------------------------------


def _split(domain: str) -> tuple[str, str]:
    """`domain` split into (name, suffix) on its DNS form: `bücher.at` is `xn--bcher-kva`, `at`.

    Only under a suffix of the Public Suffix List's ICANN section (`crawl.name_and_icann_suffix`):
    an address under a private suffix (`rmit.github.io`) has no split, so the rule never holds
    for it, whichever side it is on.
    """
    return name_and_icann_suffix(dns_form(domain))


def _elsewhere(split: tuple[str, str], brand_split: tuple[str, str]) -> bool:
    """The rule over two splits: the same name, not empty, under a different suffix."""
    name, suffix = split
    brand_name, brand_suffix = brand_split
    return bool(name) and name == brand_name and suffix != brand_suffix


def same_brand_elsewhere(domain: str, brand_domain: str) -> bool:
    """Whether `domain` is the brand's name under a DIFFERENT public suffix.

    The label left of each domain's public suffix is equal (and not empty), and the suffixes
    differ: `rmit.edu.vn` for `rmit.edu.au` (True), `monash.edu.my` for `monash.edu` (True),
    `rmit-online.com` (False: another name), `pineapple.com.vn` for `apple.com.au` (False:
    another name that contains it). The same suffix is not "elsewhere": `rmit.edu.au` for
    `rmit.edu.au` is False. An empty string, an IP address and `localhost` have no public
    suffix, and a bare suffix has no name, so they are never anyone's other site.

    Both domains are put in DNS form (`intake.dns_form`) before they are split, so the rule does
    not depend on the spelling either was given. `bücher.at` for `xn--bcher-kva.de` is True, and
    `пример.xn--p1ai` for `пример.рф` is False: `рф` and `xn--p1ai` are one suffix, so it is the
    same host spelled two ways and not another site of it.

    Only under the Public Suffix List's ICANN section (the founder, 2026-10-09). A domain on a
    service anyone can sign up to (`rmit.github.io`, `rmit.blogspot.com`, `rmit.vercel.app`) is
    never proposed for `rmit.edu.au`, and a brand whose own domain is under a private suffix
    (`acme.github.io`) has no other sites: the rule is False when EITHER domain's public suffix
    comes from the private section. `rmit.edu.vn` and `monash.edu.my` are still True.

    The split is `crawl.name_and_icann_suffix`, the extractor every cited domain was reduced
    with (`crawl.name_and_suffix`), refusing a private suffix.
    """
    return _elsewhere(_split(domain), _split(brand_domain))


# --- proposals ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal:
    """A cited domain, and the brand whose other site it may be."""

    domain: str
    citations: int
    brand_kind: BrandKind
    brand_name: str
    brand_domain: str


def proposals(
    cited: dict[str, int],
    client_domain: str,
    rivals: list[tuple[str, str]],
    confirmed: set[str],
    dismissed: set[str],
    *,
    client_name: str = "",
) -> list[Proposal]:
    """What one scan's cited domains ask the agency, most-cited first. Pure.

    `cited` is domain -> citation count from ONE scan. `rivals` is (name, domain) in rank order,
    rivals with no domain left out by the caller. `confirmed` is every domain the agency has
    confirmed, for any brand, and `dismissed` every one it has dismissed. `client_name` is what a
    proposal for the client calls it; the client's domain stands in for it when none is given.

    Domains are compared on the DNS form (`intake.dns_form`), so a host is the same host in
    either of its spellings: a cited `пример.xn--p1ai` is the client `пример.рф` itself, and a
    cited `bücher.at` is the confirmed `xn--bcher-kva.at` (the lists hold the DNS form, and
    `confirmed` and `dismissed` are matched in whatever spelling they come). A proposal keeps
    the spelling the domain was CITED in, which is the domain the engines cited, and names the
    brand by its domain as `clients.domain` or `competitors.domain` holds it.

    A domain is proposed for the CLIENT if `same_brand_elsewhere` holds for it; otherwise for the
    FIRST rival, in rank order, for which it holds, so a domain two rivals could each have is
    asked about once. Never the client's own domain or a rival's own domain (a brand's own
    domain is not another site of any brand, whichever brand's name it carries), and never one
    already confirmed or dismissed. Sorted by citations (most first), then domain.

    Each brand is split once, before the cited domains are read: the brands do not change while
    they are.
    """
    brands: list[tuple[BrandKind, str, str]] = [
        ("client", client_name or client_domain, client_domain),
        *(("rival", name, domain) for name, domain in rivals),
    ]
    brand_splits = [_split(brand_domain) for _kind, _name, brand_domain in brands]
    own = {dns_form(domain) for _kind, _name, domain in brands}
    confirmed_hosts = {dns_form(domain) for domain in confirmed}
    dismissed_hosts = {dns_form(domain) for domain in dismissed}
    found: list[Proposal] = []
    for domain, citations in cited.items():
        host = dns_form(domain)
        if host in own or host in confirmed_hosts or host in dismissed_hosts:
            continue
        split = name_and_icann_suffix(host)
        for (kind, name, brand_domain), brand_split in zip(brands, brand_splits, strict=True):
            if _elsewhere(split, brand_split):
                found.append(Proposal(domain, citations, kind, name, brand_domain))
                break
    found.sort(key=lambda proposal: (-proposal.citations, proposal.domain))
    return found


# --- what the latest scan cited ----------------------------------------------------------------


async def latest_finished_scan(session: AsyncSession, client: Client) -> Scan | None:
    """The client's newest scan that finished with answers: the one the proposals come from.

    `client_history.PLOTTABLE` (succeeded or partial: a running or queued scan has measured
    nothing yet, a failed or cancelled one never will) with at least one answered result. Newest
    by id, as `competitors.latest_scan_for_client` orders (ULIDs, so id order is creation order).
    The client's agency is named as well as its id, which only stops a scan row whose agency
    disagrees with the client's: tenancy is the caller's (`intake.get_client`).
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
                answered,
            )
            .order_by(Scan.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def cited_domain_counts(session: AsyncSession, scan_id: str) -> dict[str, int]:
    """Domain -> how many citations the scan's ANSWERED results hold for it.

    Grouped in the database, over the rows `client_history._domain_counts` reads, and only those
    of an engine that answered: one that timed out cited no one.
    """
    rows = await session.execute(
        select(Citation.source_domain, func.count())
        .join(EngineResult, EngineResult.id == Citation.engine_result_id)
        .where(EngineResult.scan_id == scan_id, EngineResult.status.in_(ANSWERED_STATUSES))
        .group_by(Citation.source_domain)
    )
    return {domain: count for domain, count in rows}


# --- the GET body ------------------------------------------------------------------------------


def client_brand(client: Client) -> tuple[str, str]:
    """The client's (name, domain) as a proposal and a confirmed site call them.

    The name is the brand name when classification found one, as the history calls it. The
    domain is in its checked form: a client stored before launch checklist 3.67 as `www.acme.com`
    is `acme.com`, so its own domain is compared as the cited domains are written.
    """
    return client.brand_name or client.name, checked_domain(client.domain) or client.domain


def active_rivals(current: CompetitorSet | None) -> list[Competitor]:
    """The rows of a list that are rivals with a domain, in rank order.

    Struck rows are tombstones and a rival with no domain has no brand to carry sites of
    (`CompetitorSet.active_competitors` is the one definition of the first).
    """
    if current is None:
        return []
    return [row for row in current.active_competitors if row.domain]


async def build_other_sites(session: AsyncSession, client: Client) -> OtherSitesOut:
    """What the agency has confirmed and dismissed, and what the latest finished scan proposes.

    Reads; writes nothing and takes no lock. The rivals are those of the list the client's NEXT
    scan will use (`competitors.current_list_for_client`: its next list if it has one, else the
    latest scan's set), so a save of the rivals is ahead of the scan that ran. The proposals are
    counted over the latest FINISHED scan's answered results and asked of that same rival list.

    `confirmed` and `dismissed` are what is stored, in the DNS form; a `proposal` keeps the
    spelling the engine cited. A host the lists hold twice, in two spellings (a list written
    by hand, or by a save from before the DNS form was what is stored), is listed once.
    """
    name, domain = client_brand(client)
    rivals = active_rivals(await competitors_service.current_list_for_client(session, client))

    brands: list[tuple[BrandKind, str, str, list[str]]] = [
        ("client", name, domain, client.other_sites),
        *(("rival", row.name, row.domain or "", row.other_sites) for row in rivals),
    ]
    confirmed: list[ConfirmedSiteOut] = []
    # The hosts listed so far, on the DNS form: a host confirmed in two spellings (for two brands,
    # or twice for one) is listed once, as the first brand to hold it spelled it.
    seen: set[str] = set()
    for brand_kind, brand_name, brand_domain, sites in brands:
        for site in sites:
            host = dns_form(site)
            if host not in seen:
                seen.add(host)
                confirmed.append(
                    ConfirmedSiteOut(
                        domain=site, brand_kind=brand_kind, brand_name=brand_name,
                        brand_domain=brand_domain,
                    )
                )

    scan = await latest_finished_scan(session, client)
    cited = await cited_domain_counts(session, scan.id) if scan is not None else {}
    found = proposals(
        cited, domain, [(row.name, row.domain or "") for row in rivals], seen,
        set(client.dismissed_other_sites), client_name=name,
    )
    return OtherSitesOut(
        confirmed=confirmed,
        proposals=[
            ProposedSiteOut(
                domain=p.domain, citations=p.citations, brand_kind=p.brand_kind,
                brand_name=p.brand_name, brand_domain=p.brand_domain,
            )
            for p in found
        ],
        dismissed=list(client.dismissed_other_sites),
    )


# --- the PUT's validation ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Replacement:
    """A request that passed `checked_replacement`: every domain in the DNS form, each host once.

    This is what is stored (`intake.dns_form`): lower case ASCII, so `bücher.at` and
    `xn--bcher-kva.at` come back as the one string `xn--bcher-kva.at`, in whatever order and
    spelling they were sent. `rivals` is keyed by the rival's own domain in the DNS form too, in
    the order they were named, and holds every rival named, one with no sites included. A rival
    named in the other spelling from its row's is still that row: `write_rival_sites` finds it
    on the DNS form, and the row keeps the domain it had.
    """

    client: list[str]
    rivals: dict[str, list[str]]
    dismissed: list[str]


def _refuse(field: str, kind: str, sentence: str) -> ValidationProblem:
    """The 422: `detail` and the one error say the same plain sentence, which names the domain.

    The same shape as every other refusal (`errors[].field/message/type`), with the sentence in
    `detail` too, so a screen that shows only the problem's `detail` shows what is wrong.
    """
    return ValidationProblem(
        detail=sentence, errors=[{"field": field, "message": sentence, "type": kind}]
    )


def _normalised(raw: str, field: str) -> str:
    """`raw` as a bare domain in the spelling typed, or a 422 that does not quote it.

    `intake.rival_domain` is the check, as for a rival typed into the competitors list (launch
    checklist 3.81): lower case, one trailing dot and one leading `www.` dropped, and what is
    left must be its own registrable domain with a valid hostname. A page, a path, a port or a
    subdomain is refused and never reduced, because reducing one names some other site. Its
    sentence quotes nothing that was typed (an address can carry a user name and password), so
    `field` says which one.
    """
    try:
        return rival_domain(raw)
    except InvalidUrl as exc:
        raise _refuse(field, "invalid_domain", exc.detail) from exc


def _each_once(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(field, domain) pairs with a repeated host dropped, the first of each kept.

    A host is repeated if its DNS form is: `bücher.at` and `xn--bcher-kva.at` are one host. The
    first is kept as it was typed, because a later sentence names it; what is stored is its
    DNS form either way.
    """
    seen: set[str] = set()
    kept: list[tuple[str, str]] = []
    for field, domain in pairs:
        host = dns_form(domain)
        if host not in seen:
            seen.add(host)
            kept.append((field, domain))
    return kept


def checked_replacement(
    *,
    client_domain: str,
    rival_domains: list[str],
    client_sites: list[str],
    rival_sites: list[tuple[str, list[str]]],
    dismissed: list[str],
) -> Replacement:
    """Validate a replacement of all three lists, or raise a 422 naming the first thing wrong.

    `rival_domains` are those of the active rivals of the list the client's next scan will use.
    The checks, in the order they are made (the first to fail is the one reported, so the answer
    for one request is always the same):

    1. every `rivalDomain` is a bare domain (`_normalised`). There are at most ten (the
       schema's `MAX_RIVALS`), and they are read first because the sentence for a rival's list
       names the rival;
    2. at most `MAX_SITES_PER_BRAND` sites for a brand and `MAX_DISMISSED` dismissed. These are
       the lengths of the lists AS SENT, checked before any site or dismissed domain is read, so
       what a refusal costs is bounded by the caps and not by what the schema lets a request
       carry: at most 10 + 20 + 10 x 20 + 200 = 430 domains are read, whatever is sent. Entries
       are counted as sent, not as distinct: a list of 21 with one domain repeated is refused,
       though it names 20;
    3. every site and every dismissed domain is a bare domain. A host named twice in one list is
       one host, stored once;
    4. every `rivalDomain` is the domain of one of `rival_domains`, and is named once;
    5. no site is a brand's own domain (the client's or a rival's), is not the brand's name under
       another public suffix (`same_brand_elsewhere`; a site under a private suffix, which is
       `shared_hosting`), or is confirmed for two brands;
    6. nothing is both confirmed and dismissed.

    "The same host" in 3 to 6 is on the DNS form (`intake.dns_form`): `bücher.de` and
    `xn--bcher-kva.de` are one host, a rival named in the other spelling from its row's is that
    rival, and `пример.xn--p1ai` for a client at `пример.рф` is the client's own domain. The
    sentence of a refusal names the domain as the person typed it (lower case, `www.` and a root
    dot dropped). What comes back is in the DNS form, which is what is stored (`Replacement`):
    `bücher.at` is returned as `xn--bcher-kva.at`.

    Pure: it reads nothing and writes nothing.
    """
    rivals = [
        _normalised(raw, f"rivals.{i}.rivalDomain") for i, (raw, _sites) in enumerate(rival_sites)
    ]

    if len(client_sites) > MAX_SITES_PER_BRAND:
        raise _refuse(
            "client", "too_many_sites",
            f"{client_domain} can have at most {MAX_SITES_PER_BRAND} other sites.",
        )
    for i, (rival, (_raw, sites)) in enumerate(zip(rivals, rival_sites, strict=True)):
        if len(sites) > MAX_SITES_PER_BRAND:
            raise _refuse(
                f"rivals.{i}.sites", "too_many_sites",
                f"{rival} can have at most {MAX_SITES_PER_BRAND} other sites.",
            )
    if len(dismissed) > MAX_DISMISSED:
        raise _refuse(
            "dismissed", "too_many_dismissed",
            f"At most {MAX_DISMISSED} domains can be dismissed.",
        )

    client_pairs = _each_once(
        [(f"client.{i}", _normalised(raw, f"client.{i}")) for i, raw in enumerate(client_sites)]
    )
    named: list[tuple[str, list[tuple[str, str]]]] = [
        (
            rival,
            _each_once(
                [
                    (f"rivals.{i}.sites.{j}", _normalised(raw, f"rivals.{i}.sites.{j}"))
                    for j, raw in enumerate(sites)
                ]
            ),
        )
        for i, (rival, (_raw, sites)) in enumerate(zip(rivals, rival_sites, strict=True))
    ]
    dismissed_pairs = _each_once(
        [(f"dismissed.{i}", _normalised(raw, f"dismissed.{i}")) for i, raw in enumerate(dismissed)]
    )

    on_the_list = {dns_form(domain) for domain in rival_domains}
    once: set[str] = set()
    for i, (rival, _pairs) in enumerate(named):
        host = dns_form(rival)
        if host in once:
            raise _refuse(
                f"rivals.{i}.rivalDomain", "repeated_rival", f"{rival} is listed more than once."
            )
        once.add(host)
        if host not in on_the_list:
            raise _refuse(
                f"rivals.{i}.rivalDomain", "unknown_rival",
                f"{rival} is not one of this client's rivals.",
            )

    own_domains = {dns_form(client_domain), *on_the_list}
    claimed_by: dict[str, str] = {}
    for brand_domain, pairs in [(client_domain, client_pairs), *named]:
        brand = dns_form(brand_domain)
        brand_split = name_and_icann_suffix(brand)
        for field, site in pairs:
            host = dns_form(site)
            if host in own_domains:
                raise _refuse(
                    field, "a_brands_own_domain",
                    f"{site} is a brand's own domain here, so it cannot be confirmed as "
                    "another site.",
                )
            if not _elsewhere(name_and_icann_suffix(host), brand_split):
                if name_and_suffix(host) != ("", "") and name_and_icann_suffix(host) == ("", ""):
                    # The site's own suffix is a private one: the rule refuses it for that
                    # reason, which is the sentence it gets.
                    raise _refuse(
                        field, "shared_hosting",
                        f"{site} is an address on a service anyone can sign up to, so it "
                        f"cannot be confirmed as one of {brand_domain}'s other sites.",
                    )
                raise _refuse(
                    field, "not_the_same_name",
                    f"{site} is not the same name as {brand_domain} with a different ending, "
                    "so it cannot be confirmed as one of its other sites.",
                )
            if claimed_by.setdefault(host, brand) != brand:
                raise _refuse(
                    field, "one_brand_only", f"{site} can be confirmed for one brand only."
                )
    for field, domain in dismissed_pairs:
        if dns_form(domain) in claimed_by:
            raise _refuse(
                field, "confirmed_and_dismissed",
                f"{domain} cannot be both confirmed and dismissed.",
            )

    return Replacement(
        client=[dns_form(domain) for _field, domain in client_pairs],
        rivals={
            dns_form(rival): [dns_form(site) for _field, site in pairs] for rival, pairs in named
        },
        dismissed=[dns_form(domain) for _field, domain in dismissed_pairs],
    )


# --- the competitor editor and a confirmed site ------------------------------------------------


def confirmed_site_clashes(
    client: Client, current: CompetitorSet | None, items: list[tuple[str, str | None]]
) -> list[dict[str, str]]:
    """The errors for a rival saved at a domain already confirmed as another brand's other site.

    `items` is the (name, domain) of each rival the save names, in order. A rival is refused if
    its domain, in DNS form, is in the client's `other_sites`, or in the `other_sites` of ANOTHER
    rival the save keeps, with the sites that rival would carry (`competitors.sites_a_save_would_
    carry`: kept while its host stays, none for a new one or one moved to another host, so a rival
    struck by the save, or moved by it, holds nothing). Writes nothing and reads nothing but its
    arguments, so the editor can refuse before it writes.

    One error per offending rival, `field` `competitors.<i>.domain` (i is the item's index),
    `type` `confirmed_other_site`. The message names the domain as sent and the brand that holds
    it: the client by its brand name (as `client_brand` calls it) or the other rival by name. A
    domain the client holds is reported for the client before any rival's claim to it.
    """
    # Who holds each host: the client first (index None), then each rival in the save's order. A
    # rival's own entry is not a reason to refuse that same rival.
    holders: list[tuple[str, int | None, str]] = [
        (dns_form(site), None, client_brand(client)[0]) for site in client.other_sites
    ]
    carried = competitors_service.sites_a_save_would_carry(current, items)
    for index, ((name, _domain), sites) in enumerate(zip(items, carried, strict=True)):
        holders.extend((dns_form(site), index, name.strip()[:200]) for site in sites)
    errors: list[dict[str, str]] = []
    for index, (_name, domain) in enumerate(items):
        if not domain:
            continue
        host = dns_form(domain)
        holder = next((who for h, i, who in holders if h == host and i != index), None)
        if holder is not None:
            errors.append(
                {
                    "field": f"competitors.{index}.domain",
                    "message": (
                        f"{domain} is confirmed as {holder}'s other site. Remove it there first."
                    ),
                    "type": "confirmed_other_site",
                }
            )
    return errors


# --- writing a rival's sites onto a list -------------------------------------------------------


def rival_sites_differ(current: CompetitorSet | None, wanted: dict[str, list[str]]) -> bool:
    """Whether any active rival of `current` holds other sites than `wanted` gives it.

    A rival `wanted` does not name wants none. A rival is matched to its row on the DNS form, so
    one named in the other spelling from its row's is that row. `wanted` holds the DNS form
    (`Replacement`), which is what a row holds, so the lists are compared as they stand and a
    site respelled is no difference: it is the same stored string. A row that holds a site in a
    spelling that is no longer stored (a list written by hand, or before the DNS form was what
    is stored) does differ, and is rewritten. If nothing differs there is no row to write, and
    a save that only touches the client's own lists must not make a next list for nothing.
    """
    by_host = {dns_form(rival): sites for rival, sites in wanted.items()}
    return any(
        list(row.other_sites) != by_host.get(dns_form(row.domain or ""), [])
        for row in active_rivals(current)
    )


def write_rival_sites(competitor_set: CompetitorSet, wanted: dict[str, list[str]]) -> None:
    """Set the other sites of every active rival of `competitor_set` to what `wanted` gives it.

    A rival `wanted` does not name is cleared: the request is the whole state. Struck rows are
    left as they are. `wanted` holds the DNS form (`Replacement`), which is what is stored. A
    rival is matched to its row on the DNS form (`rival_sites_differ`), and the row keeps the
    domain it had. Every rival `wanted` names must be on the set, which
    `checked_replacement` checked against the list as it was read; this is the set as it is now,
    locked, so a rival struck in between is a 422 and nothing is written.
    """
    rows = active_rivals(competitor_set)
    on_the_set = {dns_form(row.domain or "") for row in rows}
    for i, rival in enumerate(wanted):
        if dns_form(rival) not in on_the_set:
            raise _refuse(
                f"rivals.{i}.rivalDomain", "unknown_rival",
                f"{rival} is not one of this client's rivals.",
            )
    by_host = {dns_form(rival): sites for rival, sites in wanted.items()}
    for row in rows:
        row.other_sites = list(by_host.get(dns_form(row.domain or ""), []))
