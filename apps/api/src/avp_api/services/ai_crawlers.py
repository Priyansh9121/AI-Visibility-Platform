"""AI crawler access policy, read from robots.txt — Epic F.

=============================================================================
WHAT THIS ANSWERS, AND WHAT IT DOES NOT
=============================================================================
This module answers **"can an AI crawler read this site"**. It does NOT answer
"is an AI crawler reading this site", which is what the roadmap's "Crawler
activity" line originally asked for.

That is a real weakening of the claim and it is deliberate, because the
stronger claim has no data source here. "Is AI touching our site" is a fact
about the CLIENT'S server logs. Nothing in this system ingests those: there is
no log drain, no CDN integration, no collector endpoint, and no model that
could hold a bot hit. The only user agent this product has ever sent is its own
(`crawl.py:USER_AGENT`) — that is us crawling the client, not a crawler
visiting them.

robots.txt is the one first-party, publicly-fetchable statement of that policy,
and `technical_audit._fetch_side_files` already fetches it on every scan. So
this reads what is genuinely readable and says so in those words. The screen
carries the same distinction; see `docs/build-log.md`, Epic F.

**A stated policy is not proof of compliance.** robots.txt is advisory. A
crawler may ignore it, and a block enforced at a CDN or WAF is invisible here.
`access` therefore describes what the site ASKS FOR, never what happened, and
no name in this module implies otherwise.

=============================================================================
WHY TRAINING AND SEARCH ARE SEPARATED
=============================================================================
The roster below classifies every agent by PURPOSE, and that split is the
finding this screen exists to surface rather than a taxonomy for its own sake.

Blocking a **training** crawler is a rights decision with no direct cost to
being cited — the model is trained either way or it is not, and a citation in
an answer does not come from the training corpus.

Blocking a **search** crawler is self-defeating *for this product's subject*:
an engine cannot cite a page its retrieval index was never allowed to fetch.
An agency that blanket-blocks "AI bots" to protect its content usually means
the first and has bought the second by accident.

Measured on the nine live client domains in `avp_dev`, that confusion is not
the common case — the common case is no policy at all. Not one of them names
GPTBot, ClaudeBot, PerplexityBot, CCBot or Google-Extended. Notion is the
sharpest example: it explicitly blocks five SEO and commerce crawlers
(AhrefsBot, Amazonbot, BLEXBot, SemrushBot, dotbot) and says nothing about any
AI crawler, so every one of them is allowed by the `*` group it does have.

=============================================================================
SILENCE IS AN ANSWER, WHICH MAKES THIS DIFFERENT FROM EPICS A, B AND E
=============================================================================
Those three all ended by REFUSING a claim the data could not support. This one
does not, and the reason is worth stating so the house rule is not misapplied:
a fetched robots.txt is a COMPLETE document. An agent that appears nowhere in
it is not unmeasured — it is allowed, by the file's own semantics (RFC 9309
§2.2.1). So `UNSPECIFIED` is a real verdict about a real policy, not a null.

The genuine unknown is narrower and is kept separate: robots.txt could not be
FETCHED (network error, non-200, non-text). That is `AccessVerdict.UNKNOWN`,
it is never merged with an allow, and the house rule applies to it exactly as
it does everywhere else.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "AGENTS",
    "AccessVerdict",
    "AgentAccess",
    "AiAgent",
    "AgentPurpose",
    "RuleSource",
    "evaluate_robots",
    "unknown_access",
]


class AgentPurpose(str, enum.Enum):
    """What the operator of this crawler does with what it fetches.

    The values are about CONSEQUENCE, not about the vendor. Two agents from the
    same company can sit in different rows here, which is the point — OpenAI's
    GPTBot and OAI-SearchBot cost a site completely different things when they
    are blocked.
    """

    # Builds a corpus a model is trained on. Blocking costs no citations.
    TRAINING = "training"
    # Builds the retrieval index an engine cites from. Blocking costs
    # citations directly, which is this product's entire subject.
    SEARCH = "search"
    # Fetches a page because a human asked the assistant about it, in the
    # moment. Blocking breaks "summarise this link" for that site.
    USER_ACTION = "user_action"


class AccessVerdict(str, enum.Enum):
    """What the site's robots.txt asks this agent to do.

    `UNSPECIFIED` is deliberately NOT `ALLOWED`, though its practical effect is
    the same. A site that has thought about GPTBot and allowed it has made a
    decision; a site that has never mentioned it has not. Collapsing the two
    would erase exactly the distinction an agency is being paid to spot, and
    the screen sorts on it.

    **There is deliberately no `PARTIAL`.** The first draft had one, for a
    group that permits the root while disallowing some paths, and it was cut
    after being measured: it fired on **seven of the nine** live client
    domains, on rules like Linear's `/api/` and `/cdn-cgi/` and Basecamp's
    `/demos/` and `/humans.txt`. Practically every site has housekeeping
    disallows, so a verdict that fires almost always carries as little
    information as Epic E's alert rules that fired never.

    Making it mean something would need to separate `/blog/` from `/wp-admin/`,
    and Epic B established there is no content inventory anywhere in this
    schema to judge that with. So path-level restriction is kept as
    `AgentAccess.disallow_rules` — a QUALIFIER an operator can act on — and the
    verdict axis stays the question that can actually be answered: can this
    agent fetch the site at all.
    """

    # The agent's applicable group permits the site root.
    ALLOWED = "allowed"
    # The applicable group forbids the site root — the whole site.
    BLOCKED = "blocked"
    # robots.txt was read and no group applies to this agent at all.
    UNSPECIFIED = "unspecified"
    # robots.txt could not be read. NOT an allow. See the module docstring.
    UNKNOWN = "unknown"


class RuleSource(str, enum.Enum):
    """WHICH group produced the verdict — the audit trail for it.

    Without this, `BLOCKED` cannot be argued with. An agency telling a client
    "you are blocking Perplexity" needs to be able to say whether the client
    typed `PerplexityBot` or whether it fell out of a blanket `User-agent: *`
    written years ago for a different reason. Those are different
    conversations and usually different fixes.
    """

    # The agent has its own named group in the file.
    EXPLICIT = "explicit"
    # No named group; the `User-agent: *` group applies.
    WILDCARD = "wildcard"
    # robots.txt exists but has neither a named group nor a `*` group.
    NONE = "none"
    # robots.txt was not readable.
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class AiAgent:
    """One crawler in the roster.

    `token` is the product token as it is matched in robots.txt, and matching
    is case-insensitive per RFC 9309 §2.2.1 — the casing here is the vendor's
    documented spelling, kept because it is what an operator will type.
    """

    token: str
    vendor: str
    purpose: AgentPurpose


# The roster. Each entry is a crawler that a site's robots.txt can plausibly
# name and whose behaviour matters to AI visibility. Ordered by vendor so the
# screen's default grouping needs no second sort.
#
# This list is OUR OWN, assembled from the crawler operators' published
# documentation of their own user agents (ip-safety.md constraint 1) — it is
# not lifted from any competitor product's feature list.
AGENTS: Final[tuple[AiAgent, ...]] = (
    # --- OpenAI ---
    AiAgent("GPTBot", "OpenAI", AgentPurpose.TRAINING),
    AiAgent("OAI-SearchBot", "OpenAI", AgentPurpose.SEARCH),
    AiAgent("ChatGPT-User", "OpenAI", AgentPurpose.USER_ACTION),
    # --- Anthropic ---
    AiAgent("ClaudeBot", "Anthropic", AgentPurpose.TRAINING),
    AiAgent("Claude-SearchBot", "Anthropic", AgentPurpose.SEARCH),
    AiAgent("Claude-User", "Anthropic", AgentPurpose.USER_ACTION),
    # --- Google ---
    # Google-Extended gates Gemini training and grounding ONLY. It does not
    # affect Googlebot or classic search indexing, and conflating the two is
    # the most common robots.txt mistake this screen can catch.
    AiAgent("Google-Extended", "Google", AgentPurpose.TRAINING),
    # --- Perplexity ---
    AiAgent("PerplexityBot", "Perplexity", AgentPurpose.SEARCH),
    AiAgent("Perplexity-User", "Perplexity", AgentPurpose.USER_ACTION),
    # --- Common Crawl ---
    # Not a model vendor, but its corpus feeds many of them, so a block here
    # reaches further than the name suggests.
    AiAgent("CCBot", "Common Crawl", AgentPurpose.TRAINING),
    # --- Apple ---
    AiAgent("Applebot-Extended", "Apple", AgentPurpose.TRAINING),
    # --- Meta ---
    AiAgent("meta-externalagent", "Meta", AgentPurpose.TRAINING),
    # --- Amazon ---
    AiAgent("Amazonbot", "Amazon", AgentPurpose.SEARCH),
    # --- ByteDance ---
    AiAgent("Bytespider", "ByteDance", AgentPurpose.TRAINING),
)


@dataclass(frozen=True, slots=True)
class AgentAccess:
    """One agent's verdict for one site."""

    agent: AiAgent
    verdict: AccessVerdict
    source: RuleSource
    # The group's user-agent token that decided it, lowercased, or None when
    # nothing applied. `'*'` for a wildcard group.
    matched_token: str | None = None
    # How many Disallow rules the deciding group carries — a COUNT, never the
    # paths themselves (ip-safety.md #7: a disallow path is a fragment of the
    # client's own site structure, and a count answers the question without
    # persisting it). Qualifies an ALLOWED verdict so an operator can see there
    # is a rule set worth reading, without this becoming a verdict of its own.
    # See `AccessVerdict` for why it is not one.
    disallow_rules: int = 0


@dataclass(slots=True)
class _Group:
    """One robots.txt group: its agent tokens and its path rules."""

    tokens: list[str]
    # (path, allowed) in file order.
    rules: list[tuple[str, bool]]


def _parse_groups(body: str) -> list[_Group]:
    """Split a robots.txt body into groups, per RFC 9309 §2.2.

    A group is one or more consecutive `user-agent` lines followed by its
    rules. A `user-agent` line that FOLLOWS a rule line starts a new group;
    one that follows another user-agent line joins the same group. Getting
    that boundary wrong merges every group in the file into one, which is why
    it is parsed rather than pattern-matched.
    """
    groups: list[_Group] = []
    current: _Group | None = None
    # True once the current group has seen a rule, so the next user-agent line
    # is known to open a new group rather than extend this one.
    seen_rule = False

    for raw in body.splitlines():
        # Comments run to end of line and may follow a value (RFC 9309 §2.2).
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field in ("user-agent", "useragent"):
            if current is None or seen_rule:
                current = _Group(tokens=[], rules=[])
                groups.append(current)
                seen_rule = False
            current.tokens.append(value.lower())
        elif field in ("allow", "disallow"):
            if current is None:
                # Rules before any user-agent line belong to no group and are
                # ignored, rather than being silently attributed to `*`.
                continue
            seen_rule = True
            current.rules.append((value, field == "allow"))
        # Every other field (sitemap, crawl-delay, host, ...) is not a rule and
        # must not end the group's rule run.

    return groups


def _rule_to_regex(path: str) -> re.Pattern[str]:
    """Compile a robots.txt path pattern.

    Supports the two wildcards the spec defines: `*` for any run of characters
    and `$` for end-of-URL. Everything else is matched literally, so a path
    containing regex metacharacters (`+`, `(`, `?`) cannot change the meaning
    of the pattern — which is the bug a naive `re.compile(path)` would ship.
    """
    out = ["^"]
    for index, char in enumerate(path):
        if char == "*":
            out.append(".*")
        elif char == "$" and index == len(path) - 1:
            out.append("$")
        else:
            out.append(re.escape(char))
    return re.compile("".join(out))


def _allows(group: _Group, target: str) -> bool:
    """Whether `group` permits `target`, by longest-match with Allow winning ties.

    RFC 9309 §2.2.2: the most specific (longest) matching rule decides, and
    where an Allow and a Disallow of equal length both match, the Allow wins.
    An empty `Disallow:` is not a rule about any path — it is the documented
    way to say "nothing is forbidden" — so it never matches.
    """
    best_length = -1
    best_allowed = True

    for path, allowed in group.rules:
        if not path:
            # `Disallow:` with an empty value permits everything; `Allow:` with
            # one is meaningless. Neither constrains a path.
            continue
        if not _rule_to_regex(path).match(target):
            continue
        # Wildcards make the pattern's own length the specificity measure, which
        # is what every major implementation uses.
        length = len(path)
        if length > best_length or (length == best_length and allowed):
            best_length = length
            best_allowed = allowed

    return best_allowed


def _select_group(groups: list[_Group], token: str) -> tuple[_Group | None, str | None]:
    """Pick the group that applies to `token`, and say which token matched.

    RFC 9309 §2.2.1: a crawler obeys the group matching its own product token
    and, if one exists, IGNORES the `*` group entirely. Falling through to `*`
    after finding a named group is the classic misreading, and it would report
    Notion as blocking every AI crawler it never mentions.
    """
    wanted = token.lower()
    wildcard: _Group | None = None

    for group in groups:
        for candidate in group.tokens:
            if candidate == wanted:
                return group, candidate
            if candidate == "*" and wildcard is None:
                wildcard = group

    if wildcard is not None:
        return wildcard, "*"
    return None, None


def evaluate_robots(body: str) -> list[AgentAccess]:
    """Verdicts for every agent in `AGENTS` against one robots.txt body.

    Pure: no network, no database, no clock. The whole point of the split is
    that the semantics above can be tested against real files without either.
    """
    groups = _parse_groups(body)
    results: list[AgentAccess] = []

    for agent in AGENTS:
        group, matched = _select_group(groups, agent.token)

        if group is None:
            # The file was read and simply has nothing that applies. Allowed by
            # the file's own semantics, and recorded as the distinct fact that
            # nobody decided it. See the module docstring.
            results.append(
                AgentAccess(agent=agent, verdict=AccessVerdict.UNSPECIFIED, source=RuleSource.NONE)
            )
            continue

        source = RuleSource.EXPLICIT if matched != "*" else RuleSource.WILDCARD
        disallows = sum(1 for path, allowed in group.rules if path and not allowed)
        verdict = (
            AccessVerdict.ALLOWED if _allows(group, "/") else AccessVerdict.BLOCKED
        )

        results.append(
            AgentAccess(
                agent=agent,
                verdict=verdict,
                source=source,
                matched_token=matched,
                disallow_rules=disallows,
            )
        )

    return results


def unknown_access() -> list[AgentAccess]:
    """Verdicts for a site whose robots.txt could not be read.

    A separate constructor rather than `evaluate_robots("")`, because an empty
    STRING is a valid robots.txt that allows everything, and an unreadable one
    is not a statement at all. Those must not collapse into the same rows.
    """
    return [
        AgentAccess(agent=agent, verdict=AccessVerdict.UNKNOWN, source=RuleSource.UNREADABLE)
        for agent in AGENTS
    ]
