"""Fix generation — Epic 8.

    §7: "LLM cross-references audit + scan gaps into named, specific
    recommendations" / "Priority + effort estimation per fix"

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
=============================================================================
Every value this module interpolates into a prompt is a fact: a brand name, a
registrable domain, an industry LABEL, a weight, a sub-score, a count, an
ordinal, a schema.org TYPE NAME, or a machine-readable check code. No page
copy, no engine answer, no competitor marketing text, and no citation title
reaches `build_fix_prompt` — there is no field on `FixFacts` capable of
carrying any of them.

Unlike Epics 2 and 4, where third-party text is deliberately handed to the
model and the guard sits on the OUTPUT schema, this module's boundary is on the
INPUT. That is what the guards in test_ip_safety.py's Epic 8 block check, and
they are the first prompt-input guards in this repo.
=============================================================================

What Epic 7 already decided, and this module must not re-decide
--------------------------------------------------------------
Which dimensions have a gap worth naming, which audit findings warrant a fix,
how many of each the list carries, and what order they go in — all of that is
Epic 7's arithmetic, implemented in `apps/web/src/lib/report/derive.ts` and
mirrored by `build_candidates` below under the same constant names. The model
is handed a fixed, closed candidate list and told it may not add to it, drop
from it, or reorder it. A generated fix that does not key back to a candidate
is discarded rather than persisted.

What the model actually contributes is the thing a lookup table cannot do:
wording a correctly-identified gap as a specific instruction, and judging real
priority and effort from the whole picture at once — the citation gap read
alongside the missing FAQ schema, rather than each in isolation. Epic 7's
priority was `isBiggestGap ? high : gap >= 8 ? medium : low` and its effort was
a fixed per-code lookup; neither had any view of the rest of the scan.

Why two implementations of the candidate rules are tolerable here
-----------------------------------------------------------------
build-log Epic 7 refused to reimplement the gap formula in Python because two
sources for one number eventually disagree, and the failure mode was the chart
annotating one dimension while the headline named another. That reasoning still
holds for anything RENDERED. It does not bite here, because nothing this module
computes is ever rendered as a number: the client keeps deriving the fix list,
its ordering and its point figures exactly as before, and merges generated copy
onto it BY KEY. If the two candidate sets ever diverge, the unmatched generated
row simply fails to merge and the deterministic Epic 7 copy renders in its
place. The degradation is weaker wording, never a wrong claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import anthropic
import structlog
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings, get_settings
from ..models.action_item import ActionItemSource, Effort, Priority
from .call_bounds import CallBound

logger = structlog.get_logger(__name__)

FIX_MODEL = "claude-opus-5"
# Medium, not low: this is a judgement task over the whole scan at once, and
# the whole point of the epic is that the reasoning is real. Not high — the
# input is a few dozen facts, not a corpus.
FIX_EFFORT = "medium"
# Headroom is for thinking tokens, which count toward max_tokens. Opus 5 has
# thinking on by default.
FIX_MAX_TOKENS = 8_000

# --- The call's bound (API key discipline audit, 2026-09-07) -----------------
# This was the one site outside `engines.py` that bounded anything: 120s per
# attempt, with a comment naming what it did not bound — `max_retries`, so the
# SDK's default two retries made the real ceiling 3 x 120s plus backoff. The
# 120s is kept. It is six times the measured call (20.3s on
# `POST /scans/{id}/fixes` in Epic 9.4's table; a 21s phase in Epic 9.17's
# chained run), and medium effort over an 8,000-token budget is the most
# expensive single generation the pipeline makes. One retry, `engines.py`'s,
# and 242s enforced by the outer deadline. The largest single ceiling of the
# five, deliberately: this is the last phase, nothing waits behind it but
# `finalize_scan`, and a timeout degrades to Epic 7's deterministic fix list
# rather than failing the scan.
FIX_BOUND = CallBound(timeout=120.0, max_retries=1)  # ceiling: 242.0s

# --------------------------------------------------------------------------
# Epic 7's selection rules, mirrored. Same names, same values, same reasons —
# see apps/web/src/lib/report/derive.ts:34-55. Changing one without the other
# is what `test_candidate_rules_match_the_client` exists to catch.
# --------------------------------------------------------------------------
MAX_FIXES = 5
MAX_DIMENSION_FIXES = 3
MIN_GAP_POINTS = Decimal("2")
# A site telling crawlers to stay out is promoted above everything else:
# nothing else on the list can take effect underneath it.
BLOCKING_CHECK_KEYS = ("indexable", "robots_txt_present")

MAX_TITLE_CHARS = 200
MAX_DETAIL_CHARS = 600

# Claims this product cannot support from its own measurements. The rendered
# report is swept for these already (ReportView.test.tsx "makes no claim it
# cannot support"); enforcing the same list here means generated copy cannot
# introduce one, rather than being caught by a test that only runs on a
# fixture the generator never touches. Matched as substrings on lower-cased
# text, identically to that sweep, so the two cannot disagree about what
# counts as a violation.
BANNED_CLAIMS = (
    "revenue",
    "roi",
    "traffic",
    "leads",
    "conversion",
    "guarantee",
    "million",
    "x more",
    "skyrocket",
    "dominate",
    "act now",
    "limited time",
)


# --------------------------------------------------------------------------
# transient inputs — none of these is a database model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class FixCandidate:
    """One gap or finding Epic 7 already decided is worth a line.

    `key` is the business key the report client mints for the same candidate
    (`gap:citation_strength`, `audit:schema_faq`), and is what a generated fix
    must quote back to be accepted.
    """

    source: ActionItemSource
    source_key: str
    dimension_key: str | None
    points_upside: Decimal | None
    rank: int
    # The audit verdict, for audit candidates: "warn" / "fail" / "error".
    status: str | None = None
    # Machine-readable cause, e.g. NO_FAQ_SCHEMA. Never prose.
    detail_code: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source.value}:{self.source_key}"


@dataclass(slots=True)
class DimensionFact:
    key: str
    weight: Decimal
    subscore: Decimal
    gap: Decimal


@dataclass(slots=True)
class FixFacts:
    """Everything the generator is told about the scan.

    Every field is a name, a domain, a label, a code, a count or a number.
    There is deliberately no field that can hold page copy, an engine answer,
    a citation title or a competitor description — the constraint is enforced
    by the type, not by a reviewer noticing.
    """

    domain: str
    brand_name: str | None = None
    industry: str | None = None
    niche: str | None = None

    composite: Decimal | None = None
    formula_version: str | None = None
    degradation_flags: list[str] = field(default_factory=list)
    dimensions: list[DimensionFact] = field(default_factory=list)

    # Entity names and domains only (ip-safety.md #7: "names of entities
    # mentioned"), in detection rank order.
    competitor_names: list[tuple[str, str]] = field(default_factory=list)

    answers_analysed: int = 0
    answers_naming_subject: int = 0
    citations_total: int = 0
    citations_to_subject: int = 0
    # (domain, count) — "URLs and domains that were cited", never their content.
    top_cited_domains: list[tuple[str, int]] = field(default_factory=list)

    # Structural signals only: schema.org TYPE NAMES, counts, booleans.
    schema_types: list[str] = field(default_factory=list)
    word_count: int | None = None
    h1_count: int | None = None
    indexable: bool | None = None
    has_sitemap: bool | None = None

    def redacted(self) -> dict[str, object]:
        """Log-safe view. Counts only — never the entity names."""
        return {
            "domain": self.domain,
            "dimensions": len(self.dimensions),
            "competitors": len(self.competitor_names),
            "cited_domains": len(self.top_cited_domains),
            "schema_types": len(self.schema_types),
        }


# --------------------------------------------------------------------------
# structured output
# --------------------------------------------------------------------------


class GeneratedFix(BaseModel):
    """One model-authored recommendation for one candidate.

    `priority_reason` and `effort_reason` are the working, not the product.
    They are read at debug level while tuning and are deliberately absent from
    `PersistableFix` and from every response schema — the same discipline as
    `IndustryClassification.rationale` in Epic 2. Asking for them is not
    ceremony: a model that has to state why a fix is Large before saying so
    picks the letter less arbitrarily than one that only emits the letter.
    """

    candidate_id: str = Field(
        max_length=120,
        description=(
            "The candidate this fix is for, copied EXACTLY from the candidate "
            "list, e.g. 'gap:citation_strength' or 'audit:schema_faq'."
        ),
    )
    title: str = Field(
        max_length=MAX_TITLE_CHARS,
        description=(
            "The change, written as an instruction someone can be handed and "
            "act on. Name the specific artefact — the page type, the schema "
            "type, the file. 'Add FAQPage schema to the pages that answer "
            "buyer questions', not 'improve structured data'. "
            f"HARD LIMIT {MAX_TITLE_CHARS} characters — one short sentence, no "
            "clauses stacked with semicolons or 'and'. Anything longer is "
            "refused and the whole fix list is lost."
        ),
    )
    detail: str = Field(
        max_length=MAX_DETAIL_CHARS,
        description=(
            "One or two sentences on what this changes about the measurement, "
            "referring to this scan's actual numbers. State only what the "
            f"supplied facts support. HARD LIMIT {MAX_DETAIL_CHARS} characters."
        ),
    )
    priority: Priority = Field(
        description=(
            "How urgent this is relative to the OTHER candidates in this list, "
            "judged from the whole scan at once: points at stake, whether it "
            "blocks anything else, and whether it is concrete or diffuse."
        )
    )
    effort: Effort = Field(
        description=(
            "Real implementation cost for the client's team. S: a config or "
            "markup change measured in hours. M: focused work over days. "
            "L: sustained work over weeks, or work that depends on third "
            "parties."
        )
    )
    priority_reason: str = Field(
        max_length=240,
        description="Why that priority, referring to the other candidates.",
    )
    effort_reason: str = Field(
        max_length=240, description="Why that effort, in terms of the work itself."
    )


class GeneratedFixSet(BaseModel):
    fixes: list[GeneratedFix] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You turn a completed AI-visibility measurement into a fix list an agency can \
hand to a client's team on Monday morning.

You are given a CLOSED list of candidates. Each one has already been \
identified, ranked and justified by the measurement itself.

Rules:
- Write exactly one fix per candidate. Never invent a candidate, never drop \
one, and never merge two. Copy each candidate id back exactly.
- Name the specific artefact. "Add FAQPage schema to the pages that answer \
buyer questions" is a fix; "improve your structured data" is not, because \
nobody can be handed it and know what to open.
- Cross-reference. The candidates describe one site, and a fix that accounts \
for the others is worth more than five written in isolation. Where a schema \
gap and a citation gap have the same underlying cause, say so in the detail.
- Judge priority RELATIVE to the other candidates in this list, not on an \
absolute scale. Something that blocks the others outranks something worth \
more points on its own.
- Judge effort as the client's team would experience it. Publishing a file is \
not the same size of job as earning citations from sites you do not control.
- Claim only what the supplied facts support. You have counts, scores, \
domains and check codes. You do not have revenue, rankings, or any figure \
about what a change will earn — never imply one.
- Never quote an internal identifier back. Check names like `schema_faq` and \
codes like NO_FAQ_SCHEMA are how this system talks to itself; the client sees \
what they MEAN. Write "the site declares no FAQ markup", not "the schema_faq \
check returned NO_FAQ_SCHEMA".
- Plain professional English. No marketing language, no superlatives, no \
urgency, no promises about outcomes.
- LENGTH IS A HARD LIMIT, not a preference. A title is ONE short sentence of \
at most 200 characters; a detail is at most 600. Do not stack clauses to fit \
more in. A single title over the limit invalidates the entire response and \
the client gets no fix list at all."""


# --------------------------------------------------------------------------
# candidate selection — Epic 7's rules, not new ones
# --------------------------------------------------------------------------


def build_candidates(
    dimensions: list[DimensionFact],
    findings: list[tuple[str, str, str | None]],
    excluded: dict[str, str] | None = None,
) -> list[FixCandidate]:
    """The fix candidates for a scan, in Epic 7's order.

    `findings` is (check_key, status, detail_code) for every check that did not
    pass, already in the severity order the report projection applies.

    A mirror of `deriveFixes` in apps/web/src/lib/report/derive.ts. It exists
    so the generator knows what to write about; the client keeps deriving the
    rendered list itself, so a divergence degrades wording rather than
    changing a claim. See the module docstring.
    """
    excluded = excluded or {}
    candidates: list[FixCandidate] = []

    ranked = sorted(
        (d for d in dimensions if d.gap >= MIN_GAP_POINTS),
        key=lambda d: (-d.gap, d.key),
    )[:MAX_DIMENSION_FIXES]

    for dim in ranked:
        # A dimension excluded because we have not measured it yet is OUR gap,
        # not the client's. It must never generate a fix telling them to
        # change something.
        if excluded.get(dim.key) == "NOT_YET_MEASURED":
            continue
        candidates.append(
            FixCandidate(
                source=ActionItemSource.GAP,
                source_key=dim.key,
                dimension_key=dim.key,
                points_upside=dim.gap,
                rank=0,
            )
        )

    for check_key, status, detail_code in findings:
        if not detail_code:
            continue
        candidates.append(
            FixCandidate(
                source=ActionItemSource.AUDIT,
                source_key=check_key,
                # An audit fix moves Technical Foundation, but this system does
                # not measure by how much — hence a dimension with no
                # points_upside rather than an invented number.
                dimension_key="technical_foundation",
                points_upside=None,
                rank=0,
                status=status,
                detail_code=detail_code,
            )
        )

    def order(candidate: FixCandidate) -> tuple[int, Decimal, str]:
        blocking = 0 if candidate.source_key in BLOCKING_CHECK_KEYS else 1
        if candidate.source is not ActionItemSource.AUDIT:
            blocking = 1
        return (blocking, -(candidate.points_upside or Decimal(0)), candidate.key)

    candidates.sort(key=order)
    chosen = candidates[:MAX_FIXES]
    for position, candidate in enumerate(chosen, start=1):
        candidate.rank = position
    return chosen


# --------------------------------------------------------------------------
# prompt construction — FACTS ONLY. See the module banner.
# --------------------------------------------------------------------------


def build_fix_prompt(facts: FixFacts, candidates: list[FixCandidate]) -> str:
    """Assemble the generator's input from measured facts and closed candidates.

    Every line is `Label: <facts>` or `- <facts>`, and every interpolated value
    is a name, a domain, a label, a code or a number. The line labels are
    whitelisted by `test_fix_prompt_lines_are_all_whitelisted_facts`, so adding
    a line that carries anything else fails a test rather than shipping.
    """
    lines = [
        f"Subject brand: {facts.brand_name or facts.domain}",
        f"Website: {facts.domain}",
    ]
    if facts.industry:
        lines.append(f"Industry (classified automatically, may be imprecise): {facts.industry}")
    if facts.niche:
        lines.append(f"Niche: {facts.niche}")

    if facts.composite is not None:
        version = f" (formula {facts.formula_version})" if facts.formula_version else ""
        lines.append(f"Visibility score: {facts.composite} out of 100{version}")
    if facts.degradation_flags:
        lines.append("Measurement caveats: " + ", ".join(sorted(facts.degradation_flags)))

    if facts.dimensions:
        lines.append("")
        lines.append("Dimension scores (weight, score out of 100, points not yet recovered):")
        for dim in facts.dimensions:
            lines.append(
                f"- {dim.key}: weight {dim.weight}, score {dim.subscore}, gap {dim.gap} points"
            )

    if facts.competitor_names:
        lines.append("")
        lines.append("Competitors detected, in detection rank order (name and domain only):")
        for position, (name, competitor_domain) in enumerate(facts.competitor_names, start=1):
            lines.append(f"- {position}. {name} ({competitor_domain})")

    if facts.answers_analysed:
        lines.append("")
        lines.append(
            f"Answer coverage: {facts.answers_naming_subject} of "
            f"{facts.answers_analysed} AI answers named the subject"
        )
        lines.append(
            f"Citation coverage: {facts.citations_to_subject} of "
            f"{facts.citations_total} citations pointed at the subject's own domain"
        )
    if facts.top_cited_domains:
        lines.append(
            "Most-cited domains: " + ", ".join(f"{d} ({n})" for d, n in facts.top_cited_domains)
        )

    structure: list[str] = []
    if facts.schema_types:
        structure.append("schema.org types present: " + ", ".join(facts.schema_types))
    else:
        structure.append("schema.org types present: none")
    if facts.word_count is not None:
        structure.append(f"word count {facts.word_count}")
    if facts.h1_count is not None:
        structure.append(f"h1 count {facts.h1_count}")
    if facts.indexable is not None:
        structure.append(f"indexable: {'yes' if facts.indexable else 'no'}")
    if facts.has_sitemap is not None:
        structure.append(f"sitemap: {'yes' if facts.has_sitemap else 'no'}")
    if structure:
        lines.append("")
        # Scoped, because the model will otherwise scope it for you: the third
        # dry run's regenerated copy read "1,221 words of indexable content
        # overall" about a figure the audit measured on one page — the number
        # was right, and the reader's own site would have shown "overall" to
        # be wrong in one click (build-log, third pilot dry run, 2026-09-08).
        lines.append(
            "Audited page signals (the home page, one URL, not the whole site): "
            + "; ".join(structure)
        )

    lines.append("")
    lines.append(f"Write one fix for each of these {len(candidates)} candidates, and no others.")
    for candidate in candidates:
        if candidate.source is ActionItemSource.GAP:
            lines.append(
                f"- {candidate.key} — dimension {candidate.source_key}, "
                f"{candidate.points_upside} points recoverable"
            )
        else:
            lines.append(
                f"- {candidate.key} — audit check {candidate.source_key} "
                f"returned {candidate.status} ({candidate.detail_code}), "
                f"point value not separately measured"
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class PersistableFix:
    """What the caller writes to `action_items`. No model reasoning here."""

    source: ActionItemSource
    source_key: str
    dimension_key: str | None
    points_upside: Decimal | None
    rank: int
    title: str
    detail: str
    priority: Priority
    effort: Effort


@dataclass(slots=True)
class FixOutcome:
    """Never raises for a failed generation — the report degrades instead."""

    status: str  # "generated" | "empty" | "failed"
    fixes: list[PersistableFix] = field(default_factory=list)
    reason_code: str | None = None
    model: str | None = None
    # Candidates the model answered for but whose copy was rejected. Reported
    # so a silent quality regression is visible rather than inferred from a
    # short list.
    rejected: list[str] = field(default_factory=list)


def banned_claim_in(text: str) -> str | None:
    """The first unsupportable claim in `text`, or None.

    Substring matching on lower-cased text, deliberately identical to the
    rendered-page sweep it backstops. Coarse — "roi" matches inside "android" —
    and that is the correct trade: a false positive costs one item its
    generated wording and falls back to Epic 7's, while a false negative puts
    a claim this product cannot support in front of a client.
    """
    lowered = text.lower()
    for phrase in BANNED_CLAIMS:
        if phrase in lowered:
            return phrase
    return None


def machine_code_in(text: str, candidate: FixCandidate) -> str | None:
    """The first internal identifier `text` quotes back, or None.

    Epic 7 established that a finding is resolved through our own words, never
    the stored code — `ReportView.test.tsx` asserts the page contains
    "FAQPage schema" and never "NO_FAQ_SCHEMA". Epic 8 hands the generator
    those codes so it knows what happened, which makes it the first thing in
    the system that could put one back on the page.

    Checked against the underscored/upper-case machine forms only, so ordinary
    prose about citation strength or FAQ schema passes untouched. Found by a
    rendered-page test on genuinely generated copy, not by review: the first
    live generation wrote "the schema_faq check returned NO_FAQ_SCHEMA".
    """
    identifiers = [candidate.detail_code, candidate.source_key]
    for identifier in identifiers:
        if identifier and identifier in text:
            return identifier
    return None


def accept(
    parsed: GeneratedFixSet, candidates: list[FixCandidate]
) -> tuple[list[PersistableFix], list[str]]:
    """Match generated fixes back onto candidates, dropping anything unsound.

    Split out from `generate_fixes` so the acceptance policy is unit-testable
    without a network call — the branches that matter most are the ones that
    refuse to store something.
    """
    by_key = {candidate.key: candidate for candidate in candidates}
    accepted: list[PersistableFix] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for generated in parsed.fixes:
        key = generated.candidate_id.strip()
        candidate = by_key.get(key)
        if candidate is None:
            # A fix for something nobody measured. The whole design is that
            # the model words findings rather than producing them.
            rejected.append(key or "<empty>")
            continue
        if key in seen:
            rejected.append(key)
            continue
        title = generated.title.strip()
        detail = generated.detail.strip()
        if not title:
            rejected.append(key)
            continue
        banned = banned_claim_in(f"{title} {detail}")
        if banned is not None:
            logger.warning("fixes.claim_rejected", candidate=key, phrase=banned)
            rejected.append(key)
            continue
        leaked = machine_code_in(f"{title} {detail}", candidate)
        if leaked is not None:
            logger.warning("fixes.code_rejected", candidate=key, identifier=leaked)
            rejected.append(key)
            continue

        seen.add(key)
        accepted.append(
            PersistableFix(
                source=candidate.source,
                source_key=candidate.source_key,
                dimension_key=candidate.dimension_key,
                points_upside=candidate.points_upside,
                # Epic 7's rank, never the model's. The order is arithmetic.
                rank=candidate.rank,
                title=title[:MAX_TITLE_CHARS],
                detail=detail[:MAX_DETAIL_CHARS],
                priority=generated.priority,
                effort=generated.effort,
            )
        )

    accepted.sort(key=lambda f: f.rank)
    return accepted, rejected


async def generate_fixes(
    facts: FixFacts,
    candidates: list[FixCandidate],
    *,
    settings: Settings | None = None,
) -> FixOutcome:
    """Generate the fix list. Never raises — a provider outage degrades the report.

    When this returns anything other than "generated", no rows are written and
    the report renders Epic 7's deterministic fix list unchanged. That is why
    there is no Python copy of the string table: the deterministic floor
    already exists on the client and duplicating it here would create the
    second source of truth Epic 7 deliberately refused.
    """
    settings = settings or get_settings()

    if not candidates:
        return FixOutcome(status="empty", reason_code="NO_CANDIDATES")

    client = FIX_BOUND.client(settings)

    try:
        async with FIX_BOUND.deadline():
            response = await client.messages.parse(
                model=FIX_MODEL,
                max_tokens=FIX_MAX_TOKENS,
                output_config={"effort": FIX_EFFORT},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_fix_prompt(facts, candidates)}],
                output_format=GeneratedFixSet,
            )
    except anthropic.AuthenticationError:
        logger.error("fixes.auth_failed")
        return FixOutcome(status="failed", reason_code="PROVIDER_AUTH_FAILED")
    except anthropic.RateLimitError:
        logger.warning("fixes.rate_limited")
        return FixOutcome(status="failed", reason_code="PROVIDER_RATE_LIMITED")
    except anthropic.BadRequestError as exc:
        # 400 is also what an exhausted credit balance returns, which is a
        # billing problem rather than a malformed request. Matching the message
        # is the only available signal — type and status are identical.
        message = str(exc)
        if "credit balance" in message.lower():
            logger.error("fixes.quota_exhausted")
            return FixOutcome(status="failed", reason_code="PROVIDER_QUOTA_EXHAUSTED")
        logger.error("fixes.bad_request", error=message[:200])
        return FixOutcome(status="failed", reason_code="PROVIDER_BAD_REQUEST")
    except (anthropic.APITimeoutError, TimeoutError):
        # Before APIConnectionError, which APITimeoutError subclasses; the
        # builtin TimeoutError is the outer deadline (`CallBound.deadline`).
        logger.warning("fixes.timeout")
        return FixOutcome(status="failed", reason_code="TIMEOUT")
    except anthropic.APIConnectionError:
        logger.warning("fixes.connection_error")
        return FixOutcome(status="failed", reason_code="PROVIDER_UNREACHABLE")
    except anthropic.APIError as exc:
        logger.error("fixes.api_error", status=getattr(exc, "status_code", None))
        return FixOutcome(status="failed", reason_code="PROVIDER_ERROR")
    except ValidationError as exc:
        # THE MODEL ANSWERED, AND ITS ANSWER DID NOT FIT THE SCHEMA.
        #
        # `messages.parse` validates the whole response against
        # `GeneratedFixSet` INSIDE the SDK — `TypeAdapter(...).validate_json`
        # in `anthropic/lib/_parse/_response.py` — and a violation raises
        # `pydantic.ValidationError`, which is not an `anthropic.APIError` and
        # was therefore caught by none of the five clauses above. It escaped
        # `generate_fixes` entirely: a crash inside the scan chain, and a raw
        # 500 through `POST /scans/{id}/fixes`. The whole careful ladder was
        # bypassed by the one failure the model itself causes.
        #
        # Observed live three times on the same scan, always the same shape:
        #
        #     1 validation error for GeneratedFixSet
        #     fixes.0.title
        #       String should have at most 200 characters
        #
        # WHOLE-RESPONSE, NOT PER-ITEM, AND THAT IS THE SDK'S DOING.
        # `parse_text` validates the entire document in one call and the
        # exception escapes before any `ParsedMessage` is constructed, so
        # neither the valid fixes nor the raw JSON are recoverable here —
        # `ValidationError` carries the offending value, never the document
        # around it. Keeping the four good fixes and dropping the fifth would
        # mean abandoning `messages.parse` for `messages.create` plus a
        # hand-rolled parse, which is a larger change to how this call is made
        # than the bug warrants. So it degrades as a whole, exactly like every
        # other failure above.
        #
        # The length rule is now stated in words in the system prompt and in
        # both field descriptions as well as in the JSON schema, because
        # `maxLength` alone was the only signal the model had and it did not
        # hold. That reduces how often this fires; it is not why it is safe.
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "type": err.get("type"),
                # The LENGTH of the offending value, never the value: it is
                # model-authored copy and this is a log line.
                "length": len(v) if isinstance(v := err.get("input"), str) else None,
            }
            for err in exc.errors()[:5]
        ]
        logger.error("fixes.schema_violation", errors=errors)
        return FixOutcome(status="failed", reason_code="PROVIDER_SCHEMA_VIOLATION")

    if response.stop_reason == "refusal":
        logger.warning("fixes.refused")
        return FixOutcome(status="failed", reason_code="PROVIDER_REFUSED")

    parsed = response.parsed_output
    if parsed is None:
        return FixOutcome(status="failed", reason_code="PROVIDER_NO_OUTPUT")

    accepted, rejected = accept(parsed, candidates)

    # Debug-only. Model-authored reasoning, never persisted, never returned.
    for generated in parsed.fixes:
        logger.debug(
            "fixes.reasoning",
            candidate=generated.candidate_id,
            priority=generated.priority.value,
            effort=generated.effort.value,
            priority_reason=generated.priority_reason,
            effort_reason=generated.effort_reason,
        )

    if not accepted:
        return FixOutcome(status="failed", reason_code="NO_USABLE_FIXES", rejected=rejected)

    logger.info(
        "fixes.generated",
        accepted=len(accepted),
        rejected=len(rejected),
        **facts.redacted(),
    )
    return FixOutcome(status="generated", fixes=accepted, model=FIX_MODEL, rejected=rejected)
