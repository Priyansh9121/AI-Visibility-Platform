"""Report schemas — Epic 7.

One aggregating projection behind `GET /scans/{scanId}/report`, assembled from
rows Epics 3-6 already persisted. **This epic computes nothing new.** Every
number here was written by the epic that owns it; the report re-reads them.

Why one endpoint rather than four frontend calls
------------------------------------------------
The proof beat needs aggregates the raw endpoints do not expose — which domains
cite the subject and how often, which competitors outrank it and by how much,
how each engine covered the prompt set. Deriving those in the browser means
paging `/scans/{id}/results` (48 rows and ~400 citations on a full scan) and
re-aggregating on every render. Server-side it is one query set.

It also keeps the facts-only projection in ONE place. `tests/test_ip_safety.py`
asserts over this module; four separate client-side derivations would have four
places for a snippet to slip in.

ip-safety.md #7 — what may appear here
--------------------------------------
Every field below is a name, a domain, a URL, a count, an ordinal, an enum
member, a machine code, or a number. There is deliberately **no field capable
of carrying an engine's answer text or a competitor's marketing copy**:

  * competitors are `name` + `domain` + provenance counts — no description,
    tagline, or positioning field exists anywhere in the chain
  * citations are `domain` + `url` + type + count — never the cited page's text
  * audit findings are `check_key` + `status` + `detail_code` — machine codes,
    resolved to human copy by OUR OWN frontend string table
  * `prompt` on an evidence row is OUR generated question, the same deliberate
    exception `PromptOut.text` already makes

The narrative itself is NOT authored here. Beat headings and the biggest-gap
claim are derived on the client from these figures via the design system's
`layoutLedger` — see docs/build-log.md Epic 7 for why the derivation lives with
the visualisation rather than being duplicated in Python.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models.engine_result import Engine
from ..models.technical_audit import AuditStatus
from .action_item import ActionItemOut
from .audit import AuditCheckOut
from .common import ApiModel
from .competitor import CompetitorOut
from .score import ScoreDetailOut


class ReportAgencyOut(ApiModel):
    """The white-label surface.

    Name and slug only. Logo, custom domain and brand colours are §7 line 2 and
    are deferred to Epic 7.1 — they need new columns AND a written policy on
    which tokens an agency may override, because the visibility ramp is
    load-bearing: an agency free to recolour it changes what the score means.
    """

    id: str
    name: str
    slug: str


class ReportSubjectOut(ApiModel):
    """Who the report is about. Facts from Client (Epic 2)."""

    client_id: str
    name: str
    domain: str
    # Null when classification was inconclusive. The UI renders null as
    # "industry not determined", never as a blank industry.
    industry: str | None = None
    industry_niche: str | None = None
    brand_name: str | None = None


class ReportDimensionOut(ApiModel):
    """One §6 dimension as the report needs it.

    `key` is a stable machine key; the human label lives in the frontend string
    table, matching how `detail_code` is handled (api-contracts.md, Epic 6).

    An EXCLUDED dimension carries `included: false`, a null `subscore`, and the
    reason. It is never emitted as `subscore: 0` — the whole point of
    `excluded_dimensions` is that "nothing to measure" and "measured, scored
    zero" are different claims about a client.
    """

    key: str
    # Effective weight after redistribution, so the included set sums to 100.
    weight: Decimal
    # Null exactly when `included` is false.
    subscore: Decimal | None = None
    included: bool
    # NOT_YET_MEASURED | NO_POPULATION | NO_COMPETITOR_SET | NO_ANSWERED_RESULTS
    exclusion_reason: str | None = None


class ReportCompetitorOut(CompetitorOut):
    """A detected competitor plus its comparable sub-scores.

    **No composite, deliberately.** Sentiment is classified toward the subject
    only and Technical Foundation is the subject's own site, so 25% of the
    weight has no per-competitor input. A "competitor composite" over the
    remaining 75% would not be comparable to the subject's — it would read as a
    competitor scoring lower than they do. The report compares per-dimension
    instead, on the three dimensions that genuinely have both sides.

    Null sub-scores mean the scan produced no comparison for this competitor.
    """

    mention_rate: Decimal | None = None
    share_of_voice: Decimal | None = None
    citation_strength: Decimal | None = None


class ReportCompetitorSetOut(ApiModel):
    status: str
    # FINDING 3 (api-contracts.md, Open findings): describes only the rows
    # detection found. On a set carrying manual overrides it is presented for
    # more rows than it covers, and a reader cannot tell from this field alone.
    detection_confidence: Decimal | None = None
    # How many of the rows below came from DETECTION rather than an operator —
    # i.e. how many of them `detectionConfidence` is a statement about.
    #
    # The figure measures agreement between two automated signals. Rows an
    # operator set by hand were corroborated by neither, so on a mixed set a
    # lone confidence number is presented for more rows than it describes.
    # That was Finding 3. Publishing the scope alongside it lets a reader see
    # the gap instead of guessing at it: "0.80 across 4 of these 6" is honest
    # where a bare "0.80" is not. Equals the row count when nothing was
    # overridden.
    #
    # It is NOT a proxy for `detectionConfidence` being present. A set where
    # only one signal ran carries a null confidence and a full complement of
    # detected rows, and a set where re-detection re-found nothing but rivals
    # the operator had already named carries a real confidence and zero of
    # them. Read the two fields together; neither implies the other.
    #
    # Not quite the figure's own denominator, and deliberately not claimed to
    # be: detection computes it over every candidate it ranked, including any
    # the operator had already named or struck by hand, which are then held
    # back from the set. So this is the count of rows the reader can actually
    # see it apply to, which is the number the report needs.
    confidence_covers: int = 0
    competitors: list[ReportCompetitorOut]


class EngineCoverageOut(ApiModel):
    """How one engine covered the prompt set. Counts only."""

    engine: Engine
    prompts_run: int
    # Returned an answer at all (as opposed to erroring or being filtered).
    answered: int
    # Answered AND named the subject.
    mentioned: int


class CitedDomainOut(ApiModel):
    """A domain an engine cited, and how often.

    Domain + URL + count. api-contracts.md and ip-safety.md #7 both permit
    cited domains and URLs explicitly; the cited PAGE's text is never read,
    stored, or shown. `sample_url` is a link out to the source — the sanctioned
    alternative to quoting it.
    """

    domain: str
    citations: int
    cites_subject: bool
    # Set when the citation was attributed to a detected competitor.
    competitor_name: str | None = None
    sample_url: str | None = None


class MentionShareOut(ApiModel):
    """One brand's share of the mentions in this scan.

    Entity NAMES are facts and are explicitly permitted. `outranks_subject` is
    arithmetic over stored ordinals, not a judgement.
    """

    entity_name: str
    entity_domain: str | None = None
    is_subject: bool
    appearances: int
    best_position: int | None = None
    outranks_subject: bool


class ReportProofOut(ApiModel):
    """The evidence beat's raw material — all of it aggregated counts.

    This is the surface ip-safety.md #7 is most exposed on, because it is the
    first time collected facts are RENDERED rather than stored. There is no
    field here that could hold an engine's prose, and there is nothing upstream
    to fill one with: `engine_results` has no text-bearing column at all.
    """

    prompts_run: int
    engine_results: int
    answered_results: int
    results_mentioning_subject: int
    engine_coverage: list[EngineCoverageOut]

    total_citations: int
    subject_citations: int
    subject_cited_domains: list[CitedDomainOut]
    competitor_cited_domains: list[CitedDomainOut]
    mention_shares: list[MentionShareOut]


class ReportAuditFindingOut(AuditCheckOut):
    """An audit check the report can act on.

    Same shape as `AuditCheckOut` — a machine `detail_code`, never prose. The
    fix beat maps the code to concrete copy from our own string table.
    """


class ReportAuditOut(ApiModel):
    status: AuditStatus
    error_code: str | None = None
    url_audited: str | None = None
    technical_foundation: Decimal | None = None
    audited_at: datetime | None = None

    passed: int
    warned: int
    failed: int
    not_applicable: int
    # Only the checks that warn or fail — what the fix beat is built from.
    findings: list[ReportAuditFindingOut]


class ReportOut(ApiModel):
    """Everything the five narrative beats need, in one response."""

    scan_id: str
    scan_status: str
    generated_at: datetime
    scanned_at: datetime | None = None

    agency: ReportAgencyOut
    subject: ReportSubjectOut

    # Null when the scan has never been scored. Distinct from a score whose
    # STATUS is insufficient_data — "never run" and "ran, unscoreable" are
    # different things to tell a viewer.
    score: ScoreDetailOut | None
    # Ordered heaviest-weight first, matching the ledger's bottom-up stacking.
    dimensions: list[ReportDimensionOut]

    # Null when detection never ran for this scan.
    competitor_set: ReportCompetitorSetOut | None
    proof: ReportProofOut
    # Null when the scan has never been audited.
    audit: ReportAuditOut | None

    # Epic 8's generated fix list. Empty until generation has run, and the fix
    # beat renders its deterministic derivation in that case — these ENRICH the
    # client's list by key, they do not replace it, so an empty list is a
    # weaker report rather than a broken one.
    #
    # ActionItemOut is declared in schemas/action_item.py, not here, because
    # this module is swept for prose-bearing field names and `title` is on that
    # list. See that module's docstring for why the exception is sound.
    action_items: list[ActionItemOut] = Field(default_factory=list)
