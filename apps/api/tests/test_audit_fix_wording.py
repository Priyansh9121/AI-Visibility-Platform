"""The audit fixes that said more than the audit read — launch checklist 3.49.

Four entries of `report_narrative.FIX_FOR_DETAIL_CODE` overstated their finding, in the
report's fix list and so in the PDF, which prints them:

  ROBOTS_TXT_DISALLOW              "nothing else on this list can take effect"
  NO_DATE_SIGNAL_AVAILABLE         "anywhere on the page"
  NO_ORGANIZATION_OR_LOCALBUSINESS "Nothing on the page states ... what this organisation is"
  CONTENT_STALE                    "publish a visible updated date" (round three)

The Technical screen already said the first three truly (3.39). This file holds the four
in three ways, because a sentence is only as true as the check behind it:

1. The wording is pinned, and the overstated phrases are gone from the table and from the
   fix a report derives from it (`test_report_pdf.py` holds the PDF).
2. What each sentence says the audit does is run against the audit's own code, so a change
   to the check that makes a sentence untrue fails here and sends someone to the copy.
3. The types the entity sentence lists are read back from `TRACKED_SCHEMA`, so widening the
   check (launch checklist 3.48) fails here until the copy follows.

It also holds the order the report's lead states (`ReportView.tsx`, `FixBeat`), which the PDF
prints without saying, and the one claim the Technical screen's "Not in this score" card makes
about the score (the weight of Content Freshness is shared), since both rest on this module.

The web table (`strings.ts`) is the same text. No shared fixture carries these codes:
`report-copy.json` has no detail-code table and `report-cases.json` holds only
`NO_FAQ_SCHEMA` and `NO_PRODUCT_OR_SERVICE_SCHEMA`. What holds the two together is
`auditFixWording.test.ts`, which reads this module's table back and compares it word for
word with `strings.ts`.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from avp_api.services import report_narrative as narrative
from avp_api.services import technical_audit as audit
from avp_api.services.ai_crawlers import crawl_policy
from avp_api.services.crawl import CRAWLER_TOKEN, Followed, RobotsFile
from avp_api.services.fix_generator import BANNED_CLAIMS

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
CASES: dict[str, Any] = json.loads((FIXTURES / "report-cases.json").read_text())

ROBOTS = "ROBOTS_TXT_DISALLOW"
DATE = "NO_DATE_SIGNAL_AVAILABLE"
ENTITY = "NO_ORGANIZATION_OR_LOCALBUSINESS"
STALE = "CONTENT_STALE"
CODES = [ROBOTS, DATE, ENTITY, STALE]

# The wording, character for character: (title, detail).
WORDING: dict[str, tuple[str, str]] = {
    ROBOTS: (
        "Remove Disallow: / from the User-agent: * rules in robots.txt",
        "The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt. "
        "That rule asks crawlers that have no rule of their own to stay out of the whole "
        "site, except where another rule lets them in. A crawler that obeys it does not "
        "read the pages it covers. Removing it, or narrowing it to the paths that should "
        "stay private, clears this finding.",
    ),
    DATE: (
        "Publish machine-readable dates on the main pages",
        "The audit read no date from the audited page’s Last-Modified header or from a "
        "datePublished or dateModified in its JSON-LD, so it cannot tell whether the page "
        "is fresh or stale. Dates printed on the page are not read. A dateModified in the "
        "JSON-LD (YYYY-MM-DD), or a Last-Modified header from the server, gives the audit "
        "a date to read.",
    ),
    ENTITY: (
        "Add Organization or LocalBusiness schema to the homepage",
        "The audit found none of Organization, Corporation, OnlineBusiness, LocalBusiness, "
        "Dentist, Restaurant, Store or MedicalBusiness in the audited page’s JSON-LD or "
        "microdata. Adding Organization or LocalBusiness markup to the homepage clears "
        "this finding.",
    ),
    STALE: (
        "Refresh the audited page and publish an updated dateModified in its JSON-LD",
        "The newest date the audit read from the audited page, in its Last-Modified header "
        "or in a datePublished or dateModified in its JSON-LD, is more than 180 days old. "
        "Dates printed on the page are not read. Once the page is refreshed, a dateModified "
        "from the last 180 days in the JSON-LD (YYYY-MM-DD), or a Last-Modified header that "
        "recent, clears this finding.",
    ),
}

# The effort each carries, unchanged by 3.49: a content refresh is more than a small job.
EFFORT = {ROBOTS: "S", DATE: "S", ENTITY: "S", STALE: "M"}

# The check each code rides on, and the status a report lists it with. A report lists
# warnings, failures and errors (`report._audit_out`). `content_freshness` is
# `not_applicable` when it carries NO_DATE_SIGNAL_AVAILABLE, so no real report holds that
# fix today; the table's wording is held anyway, and fed in here as a warning.
CHECK_FOR: dict[str, tuple[str, str]] = {
    ROBOTS: ("indexable", "fail"),
    DATE: ("content_freshness", "warn"),
    ENTITY: ("schema_business_entity", "fail"),
    STALE: ("content_freshness", "warn"),
}

# What each said before, which a report must not say again. Compared lower-cased.
OVERSTATED: dict[str, tuple[str, ...]] = {
    ROBOTS: (
        "nothing else on this list",
        "can take effect",
        "tells crawlers to stay out",
        "cannot cite",
        "stop robots.txt from blocking",
        "crawlers that build ai answers",
        "rules for all crawlers",
    ),
    DATE: (
        "anywhere on the page",
        "no date could be found",
        "cannot be established either way",
        "in the structured data fixes this",
    ),
    ENTITY: (
        "nothing on the page states",
        "what this organisation is",
        "entity marker",
        "hangs off",
        "gives the audit one to find",
    ),
    STALE: (
        "visible updated date",
        "date signal",
        "one of the few signals",
        "whether an answer is still current",
    ),
}


def _derived_fix(code: str) -> narrative.Fix:
    """The fix a report carries for `code`, through the derivation the PDF prints."""
    check_key, status = CHECK_FOR[code]
    case = json.loads(json.dumps(CASES["scored-no-generated-fixes"]))
    case["audit"]["findings"] = [
        {"id": "tchk_wording", "checkKey": check_key, "status": status, "value": None,
         "detailCode": code}
    ]
    return next(f for f in narrative.derive_narrative(case).fixes if f.id == f"audit:{check_key}")


# ---------------------------------------------------------------------------
# 1. the wording, and what it no longer says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_the_table_says_what_the_audit_read(code: str) -> None:
    title, detail, effort = narrative.FIX_FOR_DETAIL_CODE[code]
    assert (title, detail) == WORDING[code]
    assert effort == EFFORT[code]


@pytest.mark.parametrize("code", CODES)
def test_the_overstated_phrases_are_gone_from_the_fix_a_report_derives(code: str) -> None:
    fix = _derived_fix(code)
    assert (fix.title, fix.detail) == WORDING[code]
    for phrase in OVERSTATED[code]:
        assert phrase not in fix.title.lower(), phrase
        assert phrase not in fix.detail.lower(), phrase


@pytest.mark.parametrize("code", CODES)
def test_no_sentence_makes_a_claim_the_report_cannot_support(code: str) -> None:
    """The list `fix_generator` bars from generated copy, and the report's own test sweeps."""
    text = " ".join(WORDING[code]).lower()
    for banned in BANNED_CLAIMS:
        assert banned not in text, banned


# ---------------------------------------------------------------------------
# 2. ROBOTS_TXT_DISALLOW: what the finding is, run against the audit
# ---------------------------------------------------------------------------


class _Gate:
    """One robots.txt body and nothing else: no network, no sitemap."""

    def __init__(self, body: str) -> None:
        self._file = RobotsFile(status=200, content_type="text/plain", body=body)

    async def file(self, origin: str) -> RobotsFile:
        return self._file

    async def readable(self, origin: str) -> bool:
        return True

    async def follow(self, method: str, url: str, *, max_chars: int = 0) -> Followed:
        return Followed(url, None)


@pytest.mark.parametrize(
    ("body", "found", "our_crawler_may_load_the_home_page"),
    [
        # "a Disallow: / rule under User-agent: *"
        ("User-agent: *\nDisallow: /\n", True, False),
        # "except where another rule lets them in": an Allow lets AVPScanBot in, and the
        # finding stands, because the check reads the rule and not the Allow beside it
        ("User-agent: *\nDisallow: /\nAllow: /\n", True, True),
        ("User-agent: *\nDisallow: /\nAllow: /public/\n", True, False),
        # a crawler's own group is not read: an AI crawler let in by name does not clear
        # it, which is why the title does not say the AI crawlers are blocked
        ("User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n", True, False),
        (f"User-agent: *\nDisallow: /\n\nUser-agent: {CRAWLER_TOKEN}\nAllow: /\n", True, True),
        # "Removing it, or narrowing it to the paths that should stay private, clears this
        # finding", and a rule for one named crawler is not the `*` rule the title names
        ("User-agent: *\nDisallow: /private/\n", False, True),
        ("User-agent: *\nAllow: /\n", False, True),
        ("User-agent: GPTBot\nDisallow: /\n", False, True),
    ],
)
async def test_the_robots_finding_is_the_user_agent_star_rule_and_nothing_beside_it(
    body: str, found: bool, our_crawler_may_load_the_home_page: bool
) -> None:
    side = await audit._fetch_side_files(_Gate(body), "https://example.test")
    assert (not side.robots_allows_crawl) is found
    # The two reads differ on purpose (technical_audit._fetch_side_files): the finding is
    # the blanket rule, and what the crawler obeys is the RFC's group, Allow lines and all.
    policy = crawl_policy(200, body, token=CRAWLER_TOKEN)
    assert policy.allows("/") is our_crawler_may_load_the_home_page


def test_the_robots_finding_reaches_a_report_whether_or_not_the_page_was_read() -> None:
    """The sentences never say whether our crawler got in, because both happen.

    A read audit carries the finding on `indexable` when the crawler was let in despite the
    rule; an audit robots.txt kept off the page carries it too (`_robots_refused_checks`).
    """
    read = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=True, http_status=200,
        is_indexable=False, robots_allows_crawl=False,
    )
    refused = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=False,
        error_code=audit.ROBOTS_DISALLOWED, is_indexable=False, robots_allows_crawl=False,
    )
    for signals in (read, refused):
        indexable = next(c for c in audit.build_checks(signals) if c.key == "indexable")
        assert (indexable.status, indexable.detail_code) == ("fail", ROBOTS)


# ---------------------------------------------------------------------------
# 3. NO_DATE_SIGNAL_AVAILABLE: the two dates the audit reads, and nothing else
# ---------------------------------------------------------------------------


def test_a_dated_json_ld_or_a_last_modified_header_is_a_date_the_audit_reads() -> None:
    # "A dateModified in the JSON-LD (YYYY-MM-DD), or a Last-Modified header from the server"
    assert audit._extract_structured_date('{"@type":"WebPage","dateModified":"2026-10-03"}')
    assert audit._extract_structured_date('{"datePublished": "2026-10-03T10:00:00+10:00"}')
    assert audit._parse_http_date("Sat, 03 Oct 2026 01:02:03 GMT") is not None
    # ...and a date in any other form is not one.
    assert audit._extract_structured_date('{"dateModified": "3 October 2026"}') is None
    printed = '<time datetime="2026-10-03">3 October 2026</time>'
    assert audit._extract_structured_date(printed) is None
    assert audit._parse_http_date("yesterday") is None


def test_no_date_from_either_is_what_leaves_freshness_unmeasured() -> None:
    signals = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=True, http_status=200,
    )
    assert signals.last_modified is None and signals.structured_date is None
    assert signals.content_age_days is None
    freshness = next(c for c in audit.build_checks(signals) if c.key == "content_freshness")
    assert (freshness.status, freshness.detail_code) == ("not_applicable", DATE)


def test_the_page_read_takes_no_date_from_what_is_printed_on_the_page() -> None:
    """The sentence "Dates printed on the page are not read" is true of what `audit_site` reads.

    The script that reads the page returns the structured data's text, the microdata types,
    the title, description, canonical and Open Graph presence, the robots meta, the heading
    and word counts and the vitals. If it ever reads a date, the sentence is untrue.
    """
    source = inspect.getsource(audit.audit_site)
    script = source.split("page.evaluate(", 1)[1].split('}"""', 1)[0].lower()
    for token in ("datetime", "<time", "modified", "published", "article:"):
        assert token not in script, (
            f"the audit now reads {token!r} from the page: 'Dates printed on the page are not "
            "read' (launch checklist 3.49) may no longer be true"
        )
    # It does read the JSON-LD (where the two dates are) and the microdata types.
    assert "application/ld+json" in script and "[itemtype]" in script


# ---------------------------------------------------------------------------
# 4. NO_ORGANIZATION_OR_LOCALBUSINESS: the eight names, read back from the check
# ---------------------------------------------------------------------------


def test_the_entity_sentence_names_exactly_the_types_the_check_matches() -> None:
    tracked = [*audit.TRACKED_SCHEMA["organization"], *audit.TRACKED_SCHEMA["localbusiness"]]
    assert len(tracked) == 8
    detail = narrative.FIX_FOR_DETAIL_CODE[ENTITY][1]
    listed = re.search(r"found none of (.+?) in the audited page", detail)
    assert listed is not None
    assert re.split(r", | or ", listed.group(1)) == tracked


def test_the_entity_remedy_clears_the_finding() -> None:
    """"Adding Organization or LocalBusiness markup to the homepage clears this finding."

    The page the audit reads is the homepage (`audit_runner.run_audit`), and the check passes,
    with no code, once either type is among the page's types.
    """
    page = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=True, http_status=200,
        schema_types=["WebSite"],
    )
    entity = next(c for c in audit.build_checks(page) if c.key == "schema_business_entity")
    assert (entity.status, entity.detail_code) == ("fail", ENTITY)
    for markup in ("Organization", "LocalBusiness"):
        types = ["WebSite", markup]
        flags = audit._classify_schema(types)
        added = audit.AuditSignals(
            url="https://example.test", domain="example.test", ok=True, http_status=200,
            schema_types=types, has_organization_schema=flags["organization"],
            has_localbusiness_schema=flags["localbusiness"],
        )
        entity = next(c for c in audit.build_checks(added) if c.key == "schema_business_entity")
        assert (entity.status, entity.detail_code) == ("pass", None), markup


# ---------------------------------------------------------------------------
# 5. CONTENT_STALE: what makes a page stale, which page, and what clears it
# ---------------------------------------------------------------------------


def _freshness(age_days: int | None) -> audit.CheckVerdict:
    signals = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=True, http_status=200,
        content_age_days=age_days,
    )
    return next(c for c in audit.build_checks(signals) if c.key == "content_freshness")


@pytest.mark.parametrize(
    ("age_days", "status", "code"),
    [
        (0, "pass", None),
        (180, "pass", None),
        # "is more than 180 days old"
        (181, "warn", STALE),
        (365, "warn", STALE),
        (366, "fail", STALE),
    ],
)
def test_a_page_is_stale_when_its_newest_date_is_more_than_180_days_old(
    age_days: int, status: str, code: str | None
) -> None:
    freshness = _freshness(age_days)
    assert (freshness.status, freshness.detail_code) == (status, code)


def test_the_newest_date_the_audit_read_is_the_one_that_counts() -> None:
    """"The newest date the audit read ..., in its Last-Modified header or in a datePublished
    or dateModified in its JSON-LD": the newest of the JSON-LD's dates, then the newer of that
    and the header, and the age from it (`audit_site`)."""
    several = (
        '{"datePublished":"2020-01-01","dateModified":"2026-05-06",'
        '"x":{"dateModified":"2024-02-03"}}'
    )
    assert audit._extract_structured_date(several) == datetime(2026, 5, 6, tzinfo=UTC)
    source = " ".join(inspect.getsource(audit.audit_site).split())
    for fragment in (
        "freshest = max( [d for d in (signals.last_modified, signals.structured_date) if d]",
        "signals.content_age_days = max( 0, (datetime.now(UTC) - freshest).days )",
    ):
        assert fragment in source, (
            f"`audit_site` no longer computes the age as {fragment!r}: the sentence 'The newest "
            "date the audit read ... is more than 180 days old' (launch checklist 3.49) may "
            "no longer be true"
        )


@pytest.mark.parametrize(
    ("days_ago", "stale"), [(0, False), (179, False), (180, False), (181, True)]
)
def test_a_date_from_the_last_180_days_clears_the_finding(days_ago: int, stale: bool) -> None:
    """"a dateModified from the last 180 days in the JSON-LD (YYYY-MM-DD) ... clears this
    finding", through the audit's own date reading and age arithmetic."""
    now = datetime.now(UTC)
    printed = (now - timedelta(days=days_ago)).date().isoformat()
    newest = audit._extract_structured_date(f'{{"dateModified": "{printed}"}}')
    assert newest is not None
    freshness = _freshness(max(0, (now - newest).days))
    assert (freshness.status, freshness.detail_code) == (
        ("warn", STALE) if stale else ("pass", None)
    )


# ---------------------------------------------------------------------------
# 6. The Technical screen's card: the weight of Content Freshness is shared
# ---------------------------------------------------------------------------


def test_no_date_leaves_content_freshness_out_and_shares_its_weight() -> None:
    """"... so the weight of Content Freshness was shared across the components that could be
    measured" (`EXCLUSION_COPY.NO_DATE_SIGNAL_AVAILABLE`, `apps/web/src/lib/client/technical.ts`;
    the name "Content Freshness" is that screen's `COMPONENT_LABEL`)."""
    signals = audit.AuditSignals(
        url="https://example.test", domain="example.test", ok=True, http_status=200,
        is_indexable=True, has_sitemap=True, canonical_present=True,
        schema_types=["Organization", "WebSite", "WebPage"], has_organization_schema=True,
    )
    outcome = audit.score_audit(signals)
    assert outcome.excluded_components == {"content_freshness": DATE}
    assert set(outcome.components) == {"indexation", "schema_presence", "structured_data"}
    shared = audit.effective_weights(outcome.components)
    assert set(shared) == set(outcome.components)
    assert sum(shared.values()) == Decimal("100.00")
    for key, weight in shared.items():
        assert weight > audit.COMPONENT_WEIGHTS[key], key


# ---------------------------------------------------------------------------
# 7. The order the report's lead states, which the PDF prints without saying
# ---------------------------------------------------------------------------

LEAD_PROMOTED = {
    # "a noindex tag, a Disallow: / rule in robots.txt or a missing robots.txt"
    "META_ROBOTS_NOINDEX": "noindex",
    ROBOTS: "Disallow: /",
    "NO_ROBOTS_TXT": "Publish a robots.txt",
}


def test_the_fixes_that_come_first_are_the_three_the_lead_names() -> None:
    """`ReportView.tsx` says a fix for a noindex tag, a Disallow: / rule in robots.txt or a
    missing robots.txt comes first. Those are the codes the two promoted checks can carry, and
    no other crawlability check is promoted."""
    assert narrative.BLOCKING_FIX_IDS == ("audit:indexable", "audit:robots_txt_present")

    def code_on(key: str, **facts: Any) -> str | None:
        signals = audit.AuditSignals(
            url="https://example.test", domain="example.test", ok=True, http_status=200, **facts
        )
        return next(c for c in audit.build_checks(signals) if c.key == key).detail_code

    noindex = code_on("indexable", is_indexable=False, meta_robots_noindex=True)
    assert noindex == "META_ROBOTS_NOINDEX"
    assert code_on("indexable", is_indexable=False, robots_allows_crawl=False) == ROBOTS
    assert code_on("robots_txt_present", robots_txt_present=False) == "NO_ROBOTS_TXT"
    # The ones that are not promoted: a sitemap and a canonical link are about indexing too.
    assert code_on("sitemap_present") == "NO_SITEMAP_XML"
    assert code_on("canonical_present") == "NO_CANONICAL_LINK"
    for code, words in LEAD_PROMOTED.items():
        title = narrative.FIX_FOR_DETAIL_CODE[code][0]
        assert words in title, (code, title)


def _order(base: str, findings: list[tuple[str, str, str]]) -> list[str]:
    case = json.loads(json.dumps(CASES[base]))
    case["audit"]["findings"] = [
        {"id": f"tchk_{key}", "checkKey": key, "status": status, "value": None, "detailCode": code}
        for key, status, code in findings
    ]
    return [f.id for f in narrative.derive_narrative(case).fixes]


def test_the_fix_list_is_ordered_as_the_lead_says() -> None:
    """By points, except that the promoted fixes come first; after them, the changes with a
    point figure come largest first, then those without one (the audit's, by check, then the
    citation fix). The same order `derive.ts` gives the screen: `auditFixWording.test.ts`
    and `derive.test.ts` hold the same lists."""
    assert _order(
        "scored-no-generated-fixes",
        [
            ("sitemap_present", "warn", "NO_SITEMAP_XML"),
            ("indexable", "fail", "META_ROBOTS_NOINDEX"),
        ],
    ) == [
        "audit:indexable",
        "gap:citation_strength",
        "gap:share_of_voice",
        "gap:sentiment",
        "audit:sitemap_present",
        "citation:eesel.ai",
    ]
    # Promotion beats the alphabet: `audit:canonical_present` sorts before both promoted ids.
    assert _order(
        "unscored",
        [
            ("canonical_present", "warn", "NO_CANONICAL_LINK"),
            ("robots_txt_present", "warn", "NO_ROBOTS_TXT"),
            ("indexable", "fail", ROBOTS),
        ],
    ) == [
        "audit:indexable",
        "audit:robots_txt_present",
        "audit:canonical_present",
        "citation:eesel.ai",
    ]
