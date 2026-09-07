"""AI crawler access policy — what a site asks AI crawlers to do. Epic F.

WHAT THIS ANSWERS, AND THE CLAIM IT REFUSES TO MAKE
----------------------------------------------------
"**Can** an AI crawler read this site", read from robots.txt. NOT "is an AI
crawler reading this site" — that is a fact about the client's own server logs,
and nothing in this system ingests those (no log drain, no collector, no model
capable of holding a bot hit). The roadmap's "Crawler activity" line asked for
the second; only the first is answerable here, so only the first is claimed.

Every field name below is chosen to keep that line visible. There is no
`visits`, no `hits`, no `lastSeen`, and no field that could be rendered as
activity. `services/ai_crawlers.py` carries the full argument.

**A stated policy is not proof of compliance.** robots.txt is advisory: a
crawler may ignore it, and a block enforced at a CDN or WAF is invisible here.
`verdict` describes what the site ASKS FOR, never what happened.

THE FOUR VERDICTS, AND WHY `unspecified` IS NOT `allowed`
----------------------------------------------------------
Their practical effect is identical — an unnamed agent falls under `*` and
crawls. They are still different findings. A site that named GPTBot and let it
in DECIDED something; a site that never mentions it has not, and on the nine
live client domains in `avp_dev` the second is overwhelmingly the common case.
Collapsing them would erase the finding this screen exists to surface.

`unknown` is the genuinely absent measurement — robots.txt could not be
fetched at all — and is never merged with an allow. That is Epics A, B and E's
house rule applied unchanged: a state where the measurement was never taken is
never the same as a measurement that came back permissive.

IP-SAFETY (docs/ip-safety.md #7)
--------------------------------
Every field is a verdict enum, a count, an agent product token, a vendor name,
or OUR OWN classification of that agent's purpose. `matchedToken` is the
user-agent token that decided the verdict — a product name or the literal `*`.
No path, no rule body, and no part of the client's robots.txt file is carried,
and `disallowRules` is a COUNT precisely so the paths need not be.
"""

from __future__ import annotations

from datetime import datetime

from .common import ApiModel


class CrawlerAgentOut(ApiModel):
    """One AI crawler's verdict for one scan."""

    agent: str
    vendor: str
    # 'training' | 'search' | 'user_action'. OUR classification of what the
    # operator does with what it fetches, not the vendor's own wording.
    purpose: str
    # 'allowed' | 'blocked' | 'unspecified' | 'unknown'.
    verdict: str
    # 'explicit' | 'wildcard' | 'none' | 'unreadable'. WHICH group decided,
    # so a `blocked` verdict can be argued with: an agency telling a client
    # "you are blocking Perplexity" needs to say whether they typed the name
    # or inherited a blanket rule written years ago for a different reason.
    rule_source: str
    matched_token: str | None = None
    # Disallow rules on the deciding group. A count, never the paths.
    disallow_rules: int = 0


class CrawlerAccessSummaryOut(ApiModel):
    """Counts across the roster, for the screen's stat row.

    `searchBlocked` is separated from `blocked` because it is the finding with
    a cost attached. Blocking a TRAINING crawler is a rights decision that
    costs no citations. Blocking a SEARCH crawler removes the site from the
    retrieval index an engine cites from — which is this product's whole
    subject — and an agency that blanket-blocked "AI bots" to protect its
    content has usually bought that by accident.
    """

    total: int
    allowed: int
    blocked: int
    unspecified: int
    unknown: int
    # Of the blocked agents, how many are SEARCH-purpose. The costly subset.
    search_blocked: int
    # Agents whose verdict came from a group naming them directly. The measure
    # of whether this site has an AI crawler policy at all, as opposed to one
    # inherited from a `*` block written for SEO crawlers.
    explicit: int


class CrawlerAccessOut(ApiModel):
    """The AI crawler access policy read during one scan's technical audit."""

    client_id: str
    scan_id: str
    scanned_at: datetime | None = None
    # The origin whose robots.txt was read — the audited URL, so an operator
    # can check the claim against the file themselves.
    url_audited: str
    # False when robots.txt could not be fetched. Every verdict is then
    # `unknown`, and the screen says so rather than showing a permissive grid.
    robots_readable: bool
    agents: list[CrawlerAgentOut]
    summary: CrawlerAccessSummaryOut
    # So a scan picker needs no second call — the same contract answer-gaps
    # uses.
    available_scan_ids: list[str]


__all__ = ["CrawlerAccessOut", "CrawlerAccessSummaryOut", "CrawlerAgentOut"]
