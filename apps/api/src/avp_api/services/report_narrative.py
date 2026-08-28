"""The report's derived narrative, in Python — Epic 9.14.

WHY THIS EXISTS, AND WHY THAT IS UNCOMFORTABLE
----------------------------------------------
`apps/web/src/lib/report/derive.ts` computes the report's argument: the biggest
gap, the fix list, the points those fixes recover. Its own docstring explains
why it lives there and not in `scoring.py` — the gap arithmetic
`gap_i = weight_i x (100 - subscore_i) / 100` is already implemented and already
unit-tested inside the design system's `layoutLedger`, which is what draws the
unlit portion of each segment, and a second implementation risks "the chart
annotating one dimension while the headline names another."

The PDF endpoint is a Python process. It cannot call a TypeScript function. So
this module IS the second implementation that docstring warns about, and
pretending otherwise would be worse than saying it.

**What makes it safe is not care, it is a test.** `tests/test_report_narrative.py`
and `apps/web/src/lib/report/crossLanguage.test.ts` read the SAME checked-in
fixtures — `packages/shared-types/fixtures/report-cases.json`, six real and
degraded reports — and both assert against the SAME checked-in expected output,
`expected-narrative.json`, which was generated from the TypeScript
implementation. Change either derivation and one of the two suites fails. The
duplication is therefore visible and mechanically policed rather than trusted,
which is the honest form of a duplication that cannot be removed.

The alternatives were considered and are worse. A Node sidecar invoked per
request puts a second runtime in the request path (see `services/pdf.py` for the
same conclusion reached about React-PDF). Moving the derivation into the API
response would change `ReportOut` for every existing consumer and put authored
copy in a payload that `test_ip_safety.py` sweeps for prose-bearing fields.
Shipping a PDF WITHOUT the fix beat would make it a different document from the
one on screen, which is the one thing the brief rules out.

ip-safety.md #8: every string below is our own copy, ported character-for-
character from our own string table. Nothing here is adapted from another
product, and the cross-language test asserts the exact bytes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# --- knobs, all mirroring derive.ts ----------------------------------------

MAX_FIXES = 5
MAX_DIMENSION_FIXES = 3
MAX_CITATION_FIXES = 1
MIN_GAP_POINTS = 2

# Crawl-blocking checks sort above everything: nothing else on the list can
# take effect underneath a site that tells crawlers to stay out.
BLOCKING_FIX_IDS = ("audit:indexable", "audit:robots_txt_present")

DIMENSION_LABEL: dict[str, str] = {
    "mention_rate": "Mention Rate",
    "share_of_voice": "Share of Voice",
    "citation_strength": "Citation Strength",
    "sentiment": "Sentiment",
    "technical_foundation": "Technical Foundation",
}

EXCLUSION_REASON: dict[str, str] = {
    "NOT_YET_MEASURED": "Not yet checked",
    "NO_POPULATION": "Nothing to measure",
    "NO_COMPETITOR_SET": "No comparison was made",
    "NO_ANSWERED_RESULTS": "No answers came back",
}

ENGINE_LABEL: dict[str, str] = {
    "chatgpt": "ChatGPT",
    "perplexity": "Perplexity",
    "google_ai_overview": "Google AI Overviews",
    "gemini": "Gemini",
    "claude": "Claude",
    "claude_search": "Claude with web search",
    "copilot": "Copilot",
}

CHECK_LABEL: dict[str, str] = {
    "site_reachable": "Site reachable",
    "indexable": "Indexable",
    "robots_txt_present": "robots.txt present",
    "sitemap_present": "XML sitemap present",
    "canonical_present": "Canonical link present",
    "schema_present": "Structured data present",
    "schema_business_entity": "Organization / LocalBusiness schema",
    "schema_faq": "FAQ schema",
    "schema_product_or_service": "Product / Service schema",
    "meta_title": "Title element",
    "meta_description": "Meta description",
    "open_graph_tags": "Open Graph tags",
    "single_h1": "Single H1",
    "content_freshness": "Content freshness",
    "cwv_lcp": "Largest Contentful Paint",
    "cwv_cls": "Cumulative Layout Shift",
    "cwv_inp": "Interaction to Next Paint",
}

FIX_FOR_DIMENSION: dict[str, tuple[str, str, str]] = {
    "mention_rate": (
        "Publish pages that answer the questions buyers actually ask",
        "The brand is missing from answers to prompts in its own category. The lever is "
        "coverage: a page that directly answers a buying question is a page an engine can "
        "name you from.",
        "L",
    ),
    "share_of_voice": (
        "Compete on the comparisons where rivals currently appear alone",
        "Rivals are named more often than the brand in the same answers. Comparison and "
        "alternatives pages are the surfaces those answers are assembled from.",
        "L",
    ),
    "citation_strength": (
        "Make the site the source an answer cites, not just a name it mentions",
        "The brand is named more often than its own pages are cited. Citation follows "
        "structure: pages that state a claim plainly, mark it up, and are crawlable get "
        "quoted; pages that bury it in prose get paraphrased without attribution.",
        "M",
    ),
    "sentiment": (
        "Address how the brand is characterised where it is described unfavourably",
        "Where the brand is named, the surrounding characterisation is not consistently "
        "positive. The lever is the third-party material those answers draw on — "
        "reviews, comparisons and documentation.",
        "M",
    ),
    "technical_foundation": (
        "Fix the structural signals that make the site machine-readable",
        "Structured data, crawlability and freshness are the inputs this dimension "
        "measures, and each is a concrete change rather than a judgement call.",
        "M",
    ),
}

FIX_FOR_DETAIL_CODE: dict[str, tuple[str, str, str]] = {
    "ROBOTS_TXT_DISALLOW": (
        "Stop robots.txt from blocking the crawlers that build AI answers",
        "The site currently tells crawlers to stay out. Until that is lifted, nothing "
        "else on this list can take effect — an answer engine cannot cite a page it "
        "is not allowed to read.",
        "S",
    ),
    "META_ROBOTS_NOINDEX": (
        "Remove the noindex directive from pages that should be found",
        "A page marked noindex is excluded from the indexes these answers are drawn "
        "from, however good its content is.",
        "S",
    ),
    "NO_ROBOTS_TXT": (
        "Publish a robots.txt",
        "Crawlers currently get no guidance about the site. A robots.txt that names the "
        "sitemap is the cheapest way to give them one.",
        "S",
    ),
    "NO_SITEMAP_XML": (
        "Publish an XML sitemap and reference it from robots.txt",
        "A sitemap is how a crawler discovers pages that are not linked from the "
        "homepage. Without one, deeper pages may never be read.",
        "S",
    ),
    "NO_CANONICAL_LINK": (
        "Add canonical links so duplicate URLs consolidate",
        "Without a canonical, the same page reachable at several URLs splits its own "
        "signals between them.",
        "S",
    ),
    "NO_STRUCTURED_DATA": (
        "Add schema.org structured data to the main pages",
        "There is no structured data on the audited page. Structured data is how a "
        "machine reads what the business is and what it sells, rather than inferring it "
        "from prose.",
        "M",
    ),
    "NO_ORGANIZATION_OR_LOCALBUSINESS": (
        "Add Organization or LocalBusiness schema to the homepage",
        "Nothing on the page states, in machine-readable form, what this organisation "
        "is. This is the entity marker the rest of the structured data hangs off.",
        "S",
    ),
    "NO_FAQ_SCHEMA": (
        "Add FAQPage schema to the pages that answer buyer questions",
        "Buyers reach these engines by asking questions. FAQ markup pairs a question "
        "with its answer explicitly, which is the form an answer engine can quote and "
        "cite directly.",
        "M",
    ),
    "NO_PRODUCT_OR_SERVICE_SCHEMA": (
        "Add Product or Service schema to the offering pages",
        "Without it, what the business actually sells has to be inferred from page copy "
        "rather than read from the markup.",
        "M",
    ),
    "NO_TITLE": (
        "Give the page a title element",
        "The title is the shortest statement of what a page is. This one has none.",
        "S",
    ),
    "NO_META_DESCRIPTION": (
        "Add a meta description to the key pages",
        "A missing description leaves the summary of the page to be generated from "
        "whatever text happens to be near the top.",
        "S",
    ),
    "NO_OPEN_GRAPH_TAGS": (
        "Add Open Graph tags",
        "Open Graph tags are what most systems read when they need a page’s identity "
        "in a compact form.",
        "S",
    ),
    "NO_H1": (
        "Add a single H1 that states what the page is about",
        "The page has no top-level heading, so its subject has to be inferred.",
        "S",
    ),
    "MULTIPLE_H1": (
        "Reduce the page to one H1",
        "Several competing top-level headings leave the page’s actual subject "
        "ambiguous to anything parsing it.",
        "S",
    ),
    "CONTENT_STALE": (
        "Refresh the audited page and publish a visible updated date",
        "The most recent date signal on this page is old. Freshness is one of the few "
        "signals available for judging whether an answer is still current.",
        "M",
    ),
    "NO_DATE_SIGNAL_AVAILABLE": (
        "Publish machine-readable dates on the main pages",
        "No date could be found anywhere on the page, so its freshness cannot be "
        "established either way. A dateModified in the structured data fixes this.",
        "S",
    ),
}


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------


def _round(value: float, places: int) -> float:
    """JavaScript's `Math.round(x * 10**p) / 10**p`.

    Python's built-in `round` is banker's rounding — `round(2.5)` is 2 — and
    JavaScript's rounds half away from zero for positives. Every figure in this
    module is a non-negative score or point count, so half-up on positives is
    the whole of the difference, and getting it wrong would show up as a fix
    worth 19.2 points here and 19.3 points on screen.
    """
    factor: float = float(10**places)
    return float(math.floor(value * factor + 0.5)) / factor


def round1(value: float) -> float:
    return _round(value, 1)


def num(value: Any) -> float | None:
    """Decimals cross the wire as strings (scoring-spec.md rule 3)."""
    if value is None:
        return None
    if isinstance(value, Decimal | int | float):
        parsed = float(value)
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float) -> float:
    return min(100.0, max(0.0, value))


@dataclass(frozen=True)
class Segment:
    """One dimension's slice of the ledger. Mirrors `LedgerSegment`."""

    key: str
    label: str
    weight: float
    subscore: float
    gap: float
    is_biggest_gap: bool


def layout_segments(dimensions: list[dict[str, Any]]) -> list[Segment]:
    """The gap arithmetic, mirroring `layoutLedger`.

    Weights are renormalised to 100 when they do not already sum to it, which is
    the case a report carrying an excluded dimension actually produces. The
    biggest-gap tie-break is lowest-index-wins with a 1e-9 epsilon, so equal
    gaps always resolve the same way and a re-render never reshuffles the
    headline.
    """
    if not dimensions:
        return []

    weight_sum = sum(d["weight"] for d in dimensions)
    scale = 100.0 / weight_sum if weight_sum > 0 else 0.0

    gaps = [
        (d["weight"] * scale * (100.0 - _clamp(d["subscore"]))) / 100.0 for d in dimensions
    ]

    biggest_index = -1
    biggest_value = -1.0
    for index, gap in enumerate(gaps):
        if gap > biggest_value + 1e-9:
            biggest_value = gap
            biggest_index = index
    # A perfect score has no gap worth naming.
    if biggest_value <= 1e-9:
        biggest_index = -1

    return [
        Segment(
            key=d["key"],
            label=d["label"],
            weight=d["weight"],
            subscore=_clamp(d["subscore"]),
            gap=_round(gaps[index], 3),
            is_biggest_gap=index == biggest_index,
        )
        for index, d in enumerate(dimensions)
    ]


def to_ledger_dimensions(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Included dimensions only, in the order the API supplied.

    An excluded dimension is DROPPED, never passed as zero. Passing it as zero
    would draw a full-height unlit segment reading as a total failure on that
    dimension — asserting exactly what the scoring engine refused to assert.
    """
    return [
        {
            "key": d["key"],
            "label": DIMENSION_LABEL.get(d["key"], d["key"]),
            "weight": num(d.get("weight")) or 0.0,
            "subscore": num(d.get("subscore")) or 0.0,
        }
        for d in dimensions
        if d.get("included")
    ]


# ---------------------------------------------------------------------------
# the fix list
# ---------------------------------------------------------------------------


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _list_domains(rows: list[dict[str, Any]]) -> str:
    names = [str(r["domain"]) for r in rows]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def fix_for_unclaimed_domains(
    domains: list[dict[str, Any]], subject_citations: int
) -> tuple[str, str, str] | None:
    """The one recommendation that names a specific page to go and get.

    A function rather than a table entry because the whole value of it is that
    it NAMES the domain. Everything interpolated is a fact — a domain, a count,
    and the subject's own citation count. Nothing describes what is ON the
    domain, which would be republishing someone else's content (ip-safety.md
    #7); we have never read it in any case.
    """
    if not domains:
        return None
    heaviest = domains[0]
    rest = domains[1:]

    others = f" The same is true of {_list_domains(rest)}." if rest else ""
    if subject_citations == 0:
        standing = "Nothing on your own site was cited at all."
    elif subject_citations < heaviest["citations"]:
        standing = (
            f"Your own pages were cited {subject_citations} "
            f"{_plural(subject_citations, 'time')} across the same answers."
        )
    else:
        standing = ""

    return (
        f"Get onto {heaviest['domain']} — the source these answers keep citing",
        f"{heaviest['domain']} was cited {heaviest['citations']} "
        f"{_plural(heaviest['citations'], 'time')} "
        f"and belongs to neither you nor any rival in this scan.{others} "
        f"{standing} A page on a source an engine already trusts is the shortest route "
        f"into the answer, because the engine does not have to start trusting a new "
        f"domain to use it.",
        "M",
    )


@dataclass
class Fix:
    id: str
    title: str
    detail: str
    priority: str
    effort: str
    points_upside: float | None
    source: str
    generated: bool = False


def derive_fixes(
    segments: list[Segment],
    findings: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    unclaimed: list[dict[str, Any]],
    subject_citations: int,
) -> list[Fix]:
    """The fix list, mirroring `deriveFixes` step for step."""
    fixes: list[Fix] = []

    citation_copy = fix_for_unclaimed_domains(unclaimed[:3], subject_citations)

    ranked = sorted(
        (s for s in segments if s.gap >= MIN_GAP_POINTS),
        key=lambda s: (-s.gap, s.key),
    )[:MAX_DIMENSION_FIXES]

    for segment in ranked:
        copy = FIX_FOR_DIMENSION.get(segment.key)
        if copy is None:
            continue
        title, detail, effort = copy
        fixes.append(
            Fix(
                id=f"gap:{segment.key}",
                title=title,
                detail=detail,
                priority=(
                    "high"
                    if segment.is_biggest_gap
                    else "medium"
                    if segment.gap >= 8
                    else "low"
                ),
                effort=effort,
                points_upside=round1(segment.gap),
                source="gap",
            )
        )

    for finding in findings:
        code = finding.get("detailCode")
        if not code:
            continue
        copy = FIX_FOR_DETAIL_CODE.get(code)
        if copy is None:
            continue
        title, detail, effort = copy
        fixes.append(
            Fix(
                id=f"audit:{finding['checkKey']}",
                title=title,
                detail=detail,
                priority="high" if finding.get("status") in ("fail", "error") else "medium",
                effort=effort,
                points_upside=None,
                source="audit",
            )
        )

    if citation_copy is not None and unclaimed:
        title, detail, effort = citation_copy
        fixes.append(
            Fix(
                id=f"citation:{unclaimed[0]['domain']}",
                title=title,
                detail=detail,
                # Never 'high'. A dimension gap worth 19 points is a bigger
                # claim than one domain, and this fix has no point value to
                # defend a top slot with.
                priority="medium",
                effort=effort,
                points_upside=None,
                source="citation",
            )
        )

    # A dimension excluded because WE have not measured it yet is our gap, not
    # the client's. It must never produce a fix telling them to change anything.
    not_their_problem = {
        f"gap:{e['key']}" for e in exclusions if e.get("reason") == "NOT_YET_MEASURED"
    }

    ordered = [f for f in fixes if f.id not in not_their_problem]
    ordered.sort(
        key=lambda f: (
            0 if f.id in BLOCKING_FIX_IDS else 1,
            -(f.points_upside or 0),
            f.id,
        )
    )

    # The citation fix gets its own slot ON TOP of MAX_FIXES rather than taking
    # one from the measured list — see derive.ts for why both alternatives were
    # worse.
    citation_fixes = [f for f in ordered if f.source == "citation"][:MAX_CITATION_FIXES]
    measured = [f for f in ordered if f.source != "citation"][:MAX_FIXES]
    kept = sorted([*measured, *citation_fixes], key=ordered.index)

    return _enrich(kept, generated)


def _enrich(fixes: list[Fix], generated: list[dict[str, Any]]) -> list[Fix]:
    """Overlay Epic 8's generated wording onto the list just derived.

    Only four fields move: title, detail, priority and effort. Which fixes
    exist, what order they are in, and what each is worth stay as derived,
    because those are arithmetic and the generator was never shown enough to
    re-decide them. `points_upside` in particular is never taken from a
    generated item — an audit fix has no measurable point value, and a model
    asked for one would supply a plausible number rather than no number.

    A miss is silent by design: the two lists disagreeing costs wording, never
    a claim.
    """
    if not generated:
        return fixes

    by_key = {f"{item.get('source')}:{item.get('sourceKey')}": item for item in generated}

    out: list[Fix] = []
    for fix in fixes:
        item = by_key.get(fix.id)
        if item is None or not item.get("title"):
            out.append(fix)
            continue
        out.append(
            Fix(
                id=fix.id,
                title=item["title"],
                detail=item.get("detail") or fix.detail,
                priority=item["priority"],
                effort=item["effort"],
                points_upside=fix.points_upside,
                source=fix.source,
                generated=True,
            )
        )
    return out


# ---------------------------------------------------------------------------
# the whole narrative
# ---------------------------------------------------------------------------


@dataclass
class Narrative:
    composite: float | None
    status: str
    segments: list[Segment]
    exclusions: list[dict[str, Any]]
    biggest_gap: Segment | None
    fixes: list[Fix]
    recoverable_points: float
    potential_composite: float | None
    ahead_on_dimensions: list[dict[str, Any]]


COMPARABLE_DIMENSIONS = (
    ("mention_rate", "mentionRate"),
    ("share_of_voice", "shareOfVoice"),
    ("citation_strength", "citationStrength"),
)


def derive_narrative(report: dict[str, Any]) -> Narrative:
    """Derive the whole narrative from one report payload (camelCase JSON)."""
    raw_dimensions = report.get("dimensions") or []
    dimensions = to_ledger_dimensions(raw_dimensions)
    exclusions = [
        {
            "key": d["key"],
            "label": DIMENSION_LABEL.get(d["key"], d["key"]),
            "reason": d.get("exclusionReason") or "NOT_YET_MEASURED",
            "weight": num(d.get("weight")) or 0.0,
        }
        for d in raw_dimensions
        if not d.get("included")
    ]

    segments = layout_segments(dimensions)
    biggest_gap = next((s for s in segments if s.is_biggest_gap), None)

    score = report.get("score")
    if score is None:
        status = "not_scored"
    elif score.get("status") == "insufficient_data":
        status = "insufficient_data"
    else:
        status = "scored"

    # The STORED composite is authoritative, never the recomputed one —
    # scoring-spec.md rule 2 fixes rounding once, at storage.
    composite = num(score.get("composite")) if status == "scored" and score else None

    proof = report.get("proof") or {}
    audit = report.get("audit") or {}
    fixes = derive_fixes(
        segments,
        audit.get("findings") or [],
        exclusions,
        report.get("actionItems") or [],
        proof.get("unclaimedCitedDomains") or [],
        proof.get("subjectCitations") or 0,
    )

    recoverable_points = round1(sum(f.points_upside or 0.0 for f in fixes))
    potential_composite = (
        None if composite is None else min(100.0, round1(composite + recoverable_points))
    )

    return Narrative(
        composite=composite,
        status=status,
        segments=segments,
        exclusions=exclusions,
        biggest_gap=biggest_gap,
        fixes=fixes,
        recoverable_points=recoverable_points,
        potential_composite=potential_composite,
        ahead_on_dimensions=ahead_on_dimensions(report),
    )


def ahead_on_dimensions(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Which rivals are ahead, and on what. Per-dimension only.

    There is deliberately no competitor composite to compare against —
    comparing the subject's five-dimension composite to a rival's
    three-dimension one would understate every rival by construction.
    """
    subject = report.get("score")
    if not subject:
        return []

    out: list[dict[str, Any]] = []
    competitor_set = report.get("competitorSet") or {}
    for competitor in competitor_set.get("competitors") or []:
        for key, camel in COMPARABLE_DIMENSIONS:
            theirs = num(competitor.get(camel))
            ours = num(subject.get(camel))
            if theirs is None or ours is None:
                continue
            if theirs > ours:
                out.append(
                    {
                        "competitorName": competitor["name"],
                        "dimensionKey": key,
                        "delta": round1(theirs - ours),
                    }
                )
    out.sort(key=lambda r: (-r["delta"], r["competitorName"], r["dimensionKey"]))
    return out


def score_heading(narrative: Narrative, subject_name: str) -> str:
    """The score beat's heading — a claim, never a category label."""
    if narrative.status == "not_scored":
        return f"{subject_name} has not been scored yet."
    if narrative.status == "insufficient_data" or narrative.composite is None:
        return f"There is not enough data yet to score {subject_name}."
    score = _round(narrative.composite, 0)
    if score >= 80:
        return f"{subject_name} is one of the names these answers reach for."
    if score >= 60:
        return f"{subject_name} shows up, but not reliably enough to be the default."
    if score >= 35:
        return f"{subject_name} is present in some answers and absent from many."
    if score > 0:
        return f"{subject_name} is close to invisible when buyers ask."
    return f"{subject_name} did not appear in any answer we measured."


def gap_heading(narrative: Narrative) -> str:
    """The gap beat's heading. Names the dimension the arithmetic picked."""
    gap = narrative.biggest_gap
    if gap is None:
        return (
            "There is no single dimension left to recover."
            if narrative.status == "scored"
            else "No gap can be measured until there is a score."
        )
    return f"{gap.label} is costing the most — {gap.gap:.1f} points."
