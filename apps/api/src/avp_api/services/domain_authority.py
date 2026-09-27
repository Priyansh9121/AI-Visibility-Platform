"""Citing-domain authority — the input Citation Strength lacked until v3.

scoring-spec.md §6 asks Citation Strength to weigh "number AND authority of
domains citing the brand", and from Epic 5 to v2.1 there was no authority
input at all: no Domain Authority feed, no backlink source, nothing upstream
that produced one. v2.1 excluded the dimension for exactly that reason. This
module is the input. It is deliberately modest about what it knows, and it
comes in two tiers that are honest about their limits in different ways.

=============================================================================
TIER A — CURATED, STRUCTURAL
=============================================================================
A short, version-controlled list, and NOT an editorial ranking of "trustworthy
sources". Which news outlets are credible is contested ground a scoring
product should not stand on, so nothing here is on the list because someone
judged it reliable. Everything is on it because of a STRUCTURAL fact:

* Government and education domains, matched by suffix — `.gov`, `.edu`,
  `.mil`, and their country forms (`.gov.uk`, `.ac.uk`, `.gov.au`, `.gc.ca`,
  `.go.jp`, `.gouv.fr`, ...). Registering one requires being the kind of
  institution the suffix names.
* Wikipedia and Wikidata, by name. Reference works, not publishers.
* The two major wire services, AP and Reuters. Included because most other
  outlets draw from them — a fact about the structure of news distribution,
  not a claim about their trustworthiness relative to anyone else's.
* Cross-industry trust and review infrastructure that exists specifically to
  aggregate signals about businesses: Better Business Bureau, Trustpilot, G2,
  Capterra.

Deliberately EXCLUDED: any general "credible news" list; Reddit and forums;
and industry-specific authorities — WebMD, IEEE, a bar association. A generic
list cannot do the last group well, and Tier B is what is meant to catch them.

=============================================================================
TIER B — PLATFORM FREQUENCY, COMPUTED FROM DATA ALREADY STORED
=============================================================================
A domain qualifies when scans have cited it across at least
`MIN_PLATFORM_INDUSTRIES` distinct client industries AND at least
`MIN_PLATFORM_CLIENTS` distinct clients, with `Client.industry` as the
grouping key. The idea: a source engines reach for across unrelated markets
is a source engines treat as general reference, whatever it is called.

**Both thresholds are first guesses, not measured ones.** There is no scan
volume yet to derive them from — 27 clients across 12 industry labels on the
day this was written — and they should be revisited once there is. What the
data does already show is recorded in the build log: on those 27 clients, 70
domains clear the thresholds, and a good number of them are vendors in one
market (help-desk software) that the fine-grained industry labels —
"customer service software", "customer support software", "help desk
software", "customer support helpdesk software" — count as several industries.

**The honest limitation, stated in the report copy and not only here.**
`Client.industry`'s classification confidence score was found uncalibrated
(build-log Epic 2.6/2.8, Finding 2 — five real sites scored 0.97, 0.97, 0.97,
0.97, 0.96, effectively one value) and remains unfixed, so there is no
reliable way to screen a mis-classified client's industry out of this count.
The label itself is more workable — the earlier over-generalisation problem
(Finding 1) was addressed with prompt tuning — but industry-spread is a
coarse proxy that cannot currently filter out a bad classification, and
`report_narrative.EXCLUSION_DETAIL` and `strings.ts` say so to the reader.

Everything else is UNCLASSIFIED: no tier, zero authority weight, the same as
today's absence. The tier VALUES live in `scoring.AUTHORITY_VALUES`, beside
WEIGHTS and SENTIMENT_VALUES, where the reasoning for the numbers is.

=============================================================================
WHY A LIVE QUERY AND NOT A MATERIALISED TABLE
=============================================================================
Measured before deciding, not guessed. On 2026-09-27 `avp_dev` held 5,787
citation rows over 1,552 distinct domains, from 395 engine results. The
grouping join below — citations → engine_results → scans → clients — plans
in 7 ms and executes in 15 ms, with the citations side served from
`ix_citations_result_domain` as an index-only scan. A scan is scored once at
the end of its chain and once per explicit re-score; a 15 ms aggregate on
that path is nothing, and a materialised table would add a refresh schedule,
a staleness window, and a migration to keep a number the database already
produces on demand. Revisit when the citation table is two orders of
magnitude larger; at that size an index on `(client_id)` through `scans` is
the first thing to try, before any table.

The aggregate is a PLATFORM statistic across every agency's clients, by
design: the tier says "engines cite this domain across many markets", which
is a fact about the domain. It emits domains only — no client, agency or
scan identity leaves this function.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.client import Client
from ..models.engine_result import Citation, EngineResult
from ..models.scan import Scan


class Tier(str, enum.Enum):
    """Which kind of evidence says a domain carries authority."""

    CURATED = "curated"  # Tier A — structural, version-controlled list
    PLATFORM = "platform"  # Tier B — cited across industries and clients


# --- Tier B thresholds — FIRST GUESSES, see the module docstring ------------
#
# Three industries: two could be one market under two labels (the data shows
# exactly that for help-desk software); three is the smallest number that
# starts to look like "unrelated markets" rather than "one market, split".
# Five clients: enough that a single client's unusually source-heavy scan
# cannot qualify a domain alone, while still reachable at today's volume.
# Neither number was derived from data, because there is not yet enough data
# to derive it from. Revisit once real scan volume exists.
MIN_PLATFORM_INDUSTRIES = 3
MIN_PLATFORM_CLIENTS = 5

# --- Tier A — the curated list ---------------------------------------------

# Top-level suffixes whose registration is restricted to the institution kind
# the suffix names. US-style, no country code.
_CURATED_TLDS = frozenset({"gov", "edu", "mil"})

# Second-level labels that play the same role under a two-letter country code:
# example.gov.uk, example.ac.uk, example.edu.au, example.gc.ca, example.go.jp,
# example.gouv.fr, example.gob.mx, example.govt.nz. Matched only when the last
# label is a two-letter country code and there is a name in front, so
# `go.com` is not caught and `gov.uk` alone (no institution) is not either.
_CURATED_CC_LABELS = frozenset({"gov", "edu", "ac", "mil", "gouv", "gob", "govt", "gc", "go"})

# Named domains, matched exactly or as a subdomain (en.wikipedia.org,
# uk.trustpilot.com). The list is the whole of Tier A's editorial content and
# is meant to be read as short.
_CURATED_NAMES = frozenset(
    {
        # reference works
        "wikipedia.org",
        "wikidata.org",
        # wire services — structural: most other outlets draw from them
        "apnews.com",
        "ap.org",
        "reuters.com",
        # cross-industry trust / review infrastructure
        "bbb.org",
        "trustpilot.com",
        "g2.com",
        "capterra.com",
    }
)

# Capterra publishes country editions on their own registrable domains
# (capterra.co.uk, capterra.ca, capterra.com.de were all cited in real scans),
# so it is matched on its first label with a public-suffix-shaped tail behind
# it — one label, or `co`/`com` plus a country code — rather than enumerated.
# `capterra.example.com` does not match: the tail is not a public suffix.
# Nothing else on the list is published that way.
_CURATED_FIRST_LABELS = frozenset({"capterra"})
_COUNTRY_EDITION_SECOND_LABELS = frozenset({"co", "com"})


def normalise_domain(domain: str) -> str:
    """Lower-case, no scheme, no leading `www.` — the shape `Citation` stores."""
    d = domain.strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def curated_tier(domain: str) -> Tier | None:
    """Tier A membership for one domain, or None. Pure; no data needed."""
    d = normalise_domain(domain)
    if not d or "." not in d:
        return None
    labels = d.split(".")
    if any(not label for label in labels):
        return None

    if labels[-1] in _CURATED_TLDS and len(labels) >= 2:
        return Tier.CURATED
    if (
        len(labels) >= 3
        and len(labels[-1]) == 2
        and labels[-1].isalpha()
        and labels[-2] in _CURATED_CC_LABELS
    ):
        return Tier.CURATED
    for name in _CURATED_NAMES:
        if d == name or d.endswith("." + name):
            return Tier.CURATED
    if labels[0] in _CURATED_FIRST_LABELS:
        country_edition = len(labels) == 2 or (
            len(labels) == 3
            and labels[1] in _COUNTRY_EDITION_SECOND_LABELS
            and len(labels[2]) == 2
        )
        if country_edition:
            return Tier.CURATED
    return None


@dataclass(frozen=True, slots=True)
class DomainAuthority:
    """The lookup `scoring.citation_strength` reads tiers from.

    A frozen value object so the scoring functions stay pure: Tier A is
    computed from the domain string alone, Tier B is the set the loader below
    produced. Tier A takes precedence when a domain is in both, so the
    curated fact is never downgraded by a frequency count.

    The default — no platform domains — is NOT "no authority": Tier A still
    applies. It is "no platform tier loaded", which is what a unit test wants
    and what `scoring_runner.score_scan` must never pass.
    """

    platform_domains: frozenset[str] = frozenset()

    def tier_of(self, domain: str) -> Tier | None:
        curated = curated_tier(domain)
        if curated is not None:
            return curated
        if normalise_domain(domain) in self.platform_domains:
            return Tier.PLATFORM
        return None

    def tiers_for(self, domains: Iterable[str]) -> dict[str, Tier]:
        """Tier per domain, for the domains that HAVE one. Sorted keys."""
        out: dict[str, Tier] = {}
        for domain in sorted(set(domains)):
            tier = self.tier_of(domain)
            if tier is not None:
                out[domain] = tier
        return out


# The curated tier on its own. What unit tests and callers without a session
# use; `load_domain_authority` is the production path.
CURATED_ONLY = DomainAuthority()


async def load_platform_domains(
    session: AsyncSession,
    *,
    min_industries: int = MIN_PLATFORM_INDUSTRIES,
    min_clients: int = MIN_PLATFORM_CLIENTS,
) -> frozenset[str]:
    """Tier B — every domain cited across enough industries and clients.

    One aggregate over stored `Citation` rows joined to `Client.industry`
    through the scan. Clients without an industry (never classified, or
    classified as ambiguous — `industry` is NULL by constraint) contribute
    nothing: a domain cannot be "across industries" on a scan whose industry
    is unknown. Soft-deleted clients are left out for the same reason a
    deleted prospect is left out of everything else.

    Cost is in the module docstring: 15 ms on 5,787 rows, measured.
    """
    stmt = (
        select(Citation.source_domain)
        .join(EngineResult, EngineResult.id == Citation.engine_result_id)
        .join(Scan, Scan.id == EngineResult.scan_id)
        .join(Client, Client.id == Scan.client_id)
        .where(Client.industry.is_not(None), Client.deleted_at.is_(None))
        .group_by(Citation.source_domain)
        .having(
            func.count(distinct(Client.industry)) >= min_industries,
            func.count(distinct(Client.id)) >= min_clients,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return frozenset(normalise_domain(d) for d in rows)


async def load_domain_authority(session: AsyncSession) -> DomainAuthority:
    """The production lookup: Tier A from the list, Tier B from the database."""
    return DomainAuthority(platform_domains=await load_platform_domains(session))


def describe(tiers: Mapping[str, Tier]) -> dict[str, str]:
    """`{domain: tier value}` in sorted order — the shape the inputs digest records."""
    return {domain: tiers[domain].value for domain in sorted(tiers)}
