"""A brand's other sites — schemas (M1, task 1.1).

Facts only: a domain, a count, and the name and domain of the brand a domain is proposed or
confirmed for. A domain is the form a cited domain is stored in (lower case, no `www.`, the
registrable domain). Nothing here can carry a page's or an answer's words.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .common import ApiModel

# Whose other site a domain is: the client's own, or one of the rivals' on the list the client's
# next scan will use.
BrandKind = Literal["client", "rival"]

# The longest hostname DNS carries (`clients.domain` and `competitors.domain` are 253 too). A bound
# on what can be typed into the request, not a rule about domains: `intake.rival_domain` is that.
MAX_DOMAIN_LENGTH = 253
Domain = Annotated[str, Field(max_length=MAX_DOMAIN_LENGTH)]

# The most rivals a client's list can hold (`ReplaceCompetitorsRequest.competitors`). A request
# that names more can only name some that are not on the list, so it is refused at the door.
MAX_RIVALS = 10

# The most domains one list of a request may carry, whatever the caps: a bound on the work a
# request can ask for, as every list in a request is bounded. The caps a person meets are the
# service's own (20 sites for a brand, 200 dismissed), refused with a sentence naming a domain.
MAX_NAMED_PER_LIST = 1000


class ConfirmedSiteOut(ApiModel):
    """A domain the agency confirmed as a brand's own other site."""

    domain: str
    brand_kind: BrandKind
    # The client's name (its brand name, else its name), or the rival's name on the list.
    brand_name: str
    brand_domain: str


class ProposedSiteOut(ApiModel):
    """A domain the latest finished scan cited that carries a brand's name under another suffix.

    A proposal, not a finding: it counts as the brand's own only once it is confirmed, and
    asking about it changes nothing.
    """

    domain: str
    # How many times the scan's answered results cited it.
    citations: int
    brand_kind: BrandKind
    brand_name: str
    brand_domain: str


class OtherSitesOut(ApiModel):
    """What the agency has confirmed and dismissed, and what the latest scan suggests."""

    # The client's confirmed sites, then those of each active rival of the list the client's
    # next scan will use, in rank order.
    confirmed: list[ConfirmedSiteOut]
    # Most-cited first, then by domain. Empty until the client has a finished scan.
    proposals: list[ProposedSiteOut]
    # Domains the agency said are nobody's own, for the client and its rivals alike.
    dismissed: list[str]


class RivalSitesIn(ApiModel):
    """The other sites confirmed for one rival, named by the rival's own domain."""

    rival_domain: Domain
    sites: list[Domain] = Field(max_length=MAX_NAMED_PER_LIST)


class ReplaceOtherSitesRequest(ApiModel):
    """All three lists, replaced: the client's confirmed sites, the rivals', and the dismissed.

    Every list is named, so a request that leaves one out is refused rather than read as
    "clear it". A rival left out of `rivals` has its sites cleared, as a site left out of a list
    is removed. How many sites a brand may have, and which domains may be on which list, is
    checked in `services/other_sites.py`, which says why in a sentence naming the domain.
    """

    client: list[Domain] = Field(max_length=MAX_NAMED_PER_LIST)
    rivals: list[RivalSitesIn] = Field(max_length=MAX_RIVALS)
    dismissed: list[Domain] = Field(max_length=MAX_NAMED_PER_LIST)
