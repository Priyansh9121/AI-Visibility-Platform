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
copy in a payload that `test_facts_only.py` sweeps for prose-bearing fields.
Shipping a PDF WITHOUT the fix beat would make it a different document from the
one on screen, which is the one thing the brief rules out.

The no-borrowed-copy rule: every string below is our own copy, ported character-for-
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

# Crawl and indexing checks sort first, ahead of the points ranking: a finding on the
# `indexable` or the `robots_txt_present` check (`derive.ts` has the same rule).
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
    "NO_AWARENESS_POPULATION": "Nothing unprompted to measure",
    "NO_AUTHORITY_DATA": "No authority source",
    "CRAWL_DISALLOWED": "Blocked by robots.txt",
    "CRAWL_ADDRESS_NOT_PUBLIC": "Address not public",
    "CRAWL_POLICY_UNREADABLE": "Could not read robots.txt",
    "AUDIT_FAILED": "Technical check failed",
}

# The sentence under each label — `strings.ts`'s `EXCLUSION_REASON[code].detail`,
# word for word, so the PDF's "Left out of the score, and why" says why. Until
# 2026-09-08 the PDF printed the label alone, which for a permanent, structural
# exclusion (NO_AUTHORITY_DATA) would leave a reader with two words.
EXCLUSION_DETAIL: dict[str, str] = {
    "NOT_YET_MEASURED": (
        "This dimension has not been measured for this scan. It is left out of the "
        "score rather than counted as zero, so nothing here is held against the site "
        "for a check that never ran."
    ),
    "NO_POPULATION": (
        "There was no data to score this on — the brand was not named often enough "
        "for this measurement to mean anything. An absence is not a bad result, so it "
        "is excluded rather than scored zero."
    ),
    "NO_COMPETITOR_SET": (
        "This scan\u2019s competitor list was missing or had no rivals on it, so there is "
        "no rival to measure a share of voice or a citation comparison against. The "
        "dimension is excluded and its weight spread across the others, rather than "
        "awarding points for a comparison that could not be made."
    ),
    "NO_AWARENESS_POPULATION": (
        "Mention Rate counts only questions that did not name the brand — the ones "
        "that can show whether a buyer would discover it. This prompt set had none, "
        "so there was nothing to measure discovery with. It is left out and its "
        "weight spread across the others rather than scored as an absence the brand "
        "did not earn."
    ),
    "NO_ANSWERED_RESULTS": (
        "No engine returned an answer for this scan, so there is nothing to measure. "
        "This is a scan that did not run, not a brand that scored badly."
    ),
    # v3 — 2026-09-27. The dimension is measured now, so this fires only when a
    # scan's citations are all unclassified. The second tier's limitation is
    # stated here, to the reader, and not only in a docstring.
    # 2026-10-03: the first tier's list says what `domain_authority`'s curated lists
    # hold — suffixes for government, military and education bodies, and the named
    # domains — and `tests/test_report_copy.py` fails when the two disagree.
    "NO_AUTHORITY_DATA": (
        "Citation Strength weighs the sources an answer cites when it names the brand, "
        "on two tiers: a short structural list — government, military and education "
        "domains; Wikipedia and Wikidata; the AP and Reuters wire services; and the "
        "cross-industry review platforms (Better Business Bureau, Trustpilot, G2, "
        "Capterra) — and the domains engines cite across at least three client "
        "industries and five clients on this platform, leaving out domains that are "
        "themselves brands in a scanned market. "
        "Nothing cited in this scan’s unprompted answers is on either tier, so there is no "
        "authority to measure and the dimension is left out with its weight spread "
        "across the others, rather than scored as zero. The second tier counts "
        "industries through an automatic classification whose confidence score is "
        "uncalibrated, so it is a coarse proxy that cannot screen out a mis-classified "
        "client; that is said here so the tier is not read as more precise than it is. "
        "The citations themselves are still shown in the evidence below."
    ),
    # 2026-09-29. The crawler obeys robots.txt, and a site that forbids its
    # pages to crawlers like ours leaves Technical Foundation unmeasured.
    "CRAWL_DISALLOWED": (
        "This site’s robots.txt asks crawlers like ours not to load its pages, so its "
        "technical foundation wasn’t measured."
    ),
    "CRAWL_ADDRESS_NOT_PUBLIC": (
        "The site’s address, or an address the site sent the crawler to, is on a private or "
        "reserved network, so the crawler did not read the site. Technical Foundation is "
        "left out of the score."
    ),
    # 2026-10-03 (launch checklist 3.41). True for every way a robots.txt cannot be
    # read (no answer, an error, redirects that never reach a file) and for whichever
    # host's it was (the site's, a redirect's, where the browser landed), so it says
    # "did not get a usable answer" and not "did not answer", and "of this site, or of
    # an address the site sent it to"; and it says nothing of what the site asks of
    # crawlers: it was never read.
    "CRAWL_POLICY_UNREADABLE": (
        "The crawler tried to read the robots.txt of this site, or of an address the site "
        "sent it to, and did not get a usable answer. It obeys robots.txt, so without that "
        "answer it did not read the site. Technical Foundation is left out of the score."
    ),
    # 2026-10-05 (launch checklist 3.87). Any failed audit the three above do not name:
    # a timeout, a browser that was busy (the check never started) or broke, a page that
    # did not respond, an error status (the page did answer). The words claim only what
    # is true of all of them, and blame no one: the check could not be completed.
    "AUDIT_FAILED": (
        "The technical check of the site could not be completed for this scan. Technical "
        "Foundation is left out of the score."
    ),
}

# What a MEASURED dimension is measured from, where a reader needs telling —
# `strings.ts`'s `DIMENSION_NOTE`, word for word. Only Citation Strength has
# one: the other four say what they are in their label. Printed under the
# score table in the PDF and under the score on screen, so a three-tier proxy
# is never presented as more precise than it is. At most 45 words since
# 2026-09-29; the tier list lives in EXCLUSION_DETAIL and scoring-spec.md.
DIMENSION_NOTE: dict[str, str] = {
    "citation_strength": (
        "When AI names the brand, how often does it back it with a trusted source? "
        "It counts only answers to questions that did not name the brand. Some sources "
        "qualify as trusted through uncalibrated industry labels — a coarse proxy that a "
        "mis-classified client can skew."
    ),
}

# Average position — reported, not scored (v4). `strings.ts`'s `POSITION_COPY`,
# word for word. `note` is the one line the report prints beside the figure;
# `not_named` is the cell for a brand never named on an unprompted question,
# which is NEVER rendered as 0 (ahead of first) or as a large number (a
# position it did not hold).
POSITION_COPY: dict[str, str] = {
    "heading": "Average position",
    "not_named": "Not named",
    "note": (
        "Average position is where the brand appeared among the brands an answer "
        "named, averaged over unprompted answers that named it, with the number of "
        "answers that is. It is shown for comparison and does not enter the score: "
        "a mean over a handful of answers moves a lot between runs — on re-runs of "
        "the same scan, rivals named in under twenty-five answers moved by up to "
        "six-tenths of a position — so it is a figure to read beside the score, not "
        "part of it."
    ),
}


def position_cell(average: Any, count: Any) -> str:
    """`2.4 across 9 answers`, or the not-named cell — the same on both sides."""
    parsed = num(average)
    n = int(count or 0)
    if parsed is None or n == 0:
        return POSITION_COPY["not_named"]
    return f"{parsed:.1f} across {n} {'answer' if n == 1 else 'answers'}"


# Why a number is lower or rougher than it would otherwise be — `strings.ts`'s
# `DEGRADATION_FLAG`, word for word. Lived in `report_pdf.py` until 2026-09-27
# with two codes missing (NO_AWARENESS_POPULATION, NO_SENTIMENT_POPULATION), so
# the PDF printed those identifiers raw while the screen said a sentence.
# Moved here, beside the other mirrored tables, and covered by the
# cross-language copy test.
DEGRADATION_FLAG: dict[str, str] = {
    # Carried only by scores computed before v2.1. The sentence describes what
    # that formula actually did; its predecessor claimed a comparison against
    # the best-cited brand that the arithmetic never made.
    "NO_AUTHORITY_DATA": (
        "Citation Strength was measured as whether the brand’s own domain was cited, "
        "against every domain the engines cited, because this system has no external "
        "authority source. That measure cannot rise above about a point for any site, "
        "and later scores leave the dimension out instead."
    ),
    "WEAK_COMPETITOR_SET": (
        "The competitor set was only weakly corroborated — some rivals were surfaced by a "
        "single signal. Share of Voice should be read as indicative rather than exact."
    ),
    "NO_CITATIONS_IN_SCAN": "No engine cited any source in this scan, for any brand.",
    "NO_COMPETITOR_SET": "No competitors were on the list, so no share comparison was possible.",
    "TECHNICAL_FOUNDATION_NOT_MEASURED": (
        "The site had not been audited when this score was computed."
    ),
    "NO_ANSWERED_RESULTS": "No engine returned an answer for this scan.",
    "NO_AWARENESS_POPULATION": (
        "This prompt set contained no unprompted questions, which are the only ones "
        "Mention Rate and Share of Voice count. Both were left out of the score rather "
        "than measured on questions that named the brand."
    ),
    "NO_SENTIMENT_POPULATION": (
        "The brand was not named often enough for sentiment to mean anything, so that "
        "dimension was left out of the score rather than counted as a bad result."
    ),
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
    # v3: the dimension is the authority of the sources cited alongside the
    # brand, so the lever is presence on the sources engines already treat as
    # authorities — not the brand's own pages, which was the retired reading.
    "citation_strength": (
        "Get onto the sources that answers already cite as authorities",
        "Answers that name the brand draw on sources with less authority than those "
        "that name the best-placed rival. The lever is presence on the review platforms "
        "and reference sources engines already cite — a current listing on G2, Capterra "
        "and Trustpilot, a Better Business Bureau profile, a Wikipedia entry where one is "
        "warranted — and coverage by the sources engines reach for across every industry.",
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

# Four entries below say only what the audit read — launch checklist 3.49, 2026-10-03.
# They used to say more, and each was untrue of a case that carries its code:
#
#   ROBOTS_TXT_DISALLOW: "nothing else on this list can take effect", and a title that
#     named "the crawlers that build AI answers". The check reads one robots.txt group,
#     `*`, for a `Disallow: /` line (`technical_audit._fetch_side_files`); it ignores
#     Allow lines and every named crawler's group, so a crawler may be let in, or an AI
#     crawler may have a group of its own, and the finding still stands. The code reaches
#     this list on a read audit, where the crawler was let in despite the rule, and on
#     one where robots.txt kept the crawler off the page (`_robots_refused_checks`), so
#     the sentences say what the rule asks and never whether our crawler got in. The
#     title names the `User-agent: *` group and not "all crawlers": a crawler with a group
#     of its own ignores the `*` group (RFC 9309), so "the rules for all crawlers" was not
#     strictly true either.
#   NO_DATE_SIGNAL_AVAILABLE: "anywhere on the page". The audit reads the page's
#     Last-Modified header and the datePublished and dateModified in its JSON-LD, and no
#     date printed on the page.
#   CONTENT_STALE: "publish a visible updated date", and "Freshness is one of the few
#     signals available for judging whether an answer is still current". The audit takes
#     the newest of the page's Last-Modified header and the datePublished and dateModified
#     in its JSON-LD (`audit_site`), and calls the page stale when that is more than 180
#     days old (`build_checks`: a warning to 365 days, a failure after). A date printed on
#     the page is not read, so publishing one would not clear the finding; and nothing the
#     audit measures supports a claim about which signals are available for judging an
#     answer.
#   NO_ORGANIZATION_OR_LOCALBUSINESS: "Nothing on the page states … what this
#     organisation is". The check matches eight schema.org type names
#     (`technical_audit.TRACKED_SCHEMA`), so a page marked up as `ProfessionalService`
#     does state what it is and still fails. The sentence that said what the markup is
#     for went too: nothing the audit measures supports it.
#
# `strings.ts` carries the same four, word for word. No shared fixture holds these
# codes (`report-copy.json` has no detail-code table and `report-cases.json` carries
# only NO_FAQ_SCHEMA and NO_PRODUCT_OR_SERVICE_SCHEMA), so what holds the two together
# is `test_audit_fix_wording.py` here, which pins the text and runs each sentence's
# claims against the audit, and `auditFixWording.test.ts` there, which reads this table
# back and compares it with `strings.ts`.
FIX_FOR_DETAIL_CODE: dict[str, tuple[str, str, str]] = {
    "ROBOTS_TXT_DISALLOW": (
        "Remove Disallow: / from the User-agent: * rules in robots.txt",
        "The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt. "
        "That rule asks crawlers that have no rule of their own to stay out of the whole "
        "site, except where another rule lets them in. A crawler that obeys it does not "
        "read the pages it covers. Removing it, or narrowing it to the paths that should "
        "stay private, clears this finding.",
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
        "The audit found none of Organization, Corporation, OnlineBusiness, LocalBusiness, "
        "Dentist, Restaurant, Store or MedicalBusiness in the audited page’s JSON-LD or "
        "microdata. Adding Organization or LocalBusiness markup to the homepage clears "
        "this finding.",
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
        "Refresh the audited page and publish an updated dateModified in its JSON-LD",
        "The newest date the audit read from the audited page, in its Last-Modified header "
        "or in a datePublished or dateModified in its JSON-LD, is more than 180 days old. "
        "Dates printed on the page are not read. Once the page is refreshed, a dateModified "
        "from the last 180 days in the JSON-LD (YYYY-MM-DD), or a Last-Modified header that "
        "recent, clears this finding.",
        "M",
    ),
    "NO_DATE_SIGNAL_AVAILABLE": (
        "Publish machine-readable dates on the main pages",
        "The audit read no date from the audited page’s Last-Modified header or from a "
        "datePublished or dateModified in its JSON-LD, so it cannot tell whether the page "
        "is fresh or stale. Dates printed on the page are not read. A dateModified in the "
        "JSON-LD (YYYY-MM-DD), or a Last-Modified header from the server, gives the audit "
        "a date to read.",
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
    domain, which would be republishing someone else's content (the facts-only
    rule); we have never read it in any case.
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
        f"gap:{e['key']}"
        for e in exclusions
        if e.get("reason") in ("NOT_YET_MEASURED", "AUDIT_FAILED")
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
    if report.get("scanStatus") == "failed":
        # A FAILED scan's narrative has status "failed" and no composite (3.24), even
        # if a payload still carried a score. That is all this guards: the score and
        # dimensions are blanked by the API (`build_report`), not here.
        status = "failed"
        score = None
    elif score is None:
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

    # Every total is the sum of the figures printed above it, so a reader can re-add
    # each one. The points are the fixes' own printed (1dp) figures summed; the ceiling
    # is the printed composite plus those points, rounded to 2dp only to clear float
    # noise (a 2dp figure plus a 1dp one is exact at 2dp). Rounding the ceiling again,
    # to 1dp, printed 72.48 + 25.9 as 98.40 (RMIT University, 2026-10-08).
    #
    # THE ONE EXCEPTION is the cap. When the listed fixes cover every gap that is left,
    # their 1dp points can add up to slightly more than 100 minus the composite (each
    # rounds by up to 0.05), and the ceiling is held at 100: 70.53 + 29.6 prints as
    # 100.00. The 100 is true; it is the rounded points that overshoot. How the page
    # words that case is the founder's (launch checklist 3.100).
    recoverable_points = round1(sum(f.points_upside or 0.0 for f in fixes))
    potential_composite = (
        None if composite is None else min(100.0, _round(composite + recoverable_points, 2))
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
    if narrative.status == "failed":
        return f"{subject_name} has no score: this scan failed."
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
