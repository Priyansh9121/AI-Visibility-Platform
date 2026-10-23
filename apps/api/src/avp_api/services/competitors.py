"""Competitor dedup, ranking and persistence — §5.4 step 2, final stage.

    "extract co-occurring brand/domain mentions -> dedupe -> rank top 3-5"

Ranking principle
-----------------
Competitors are ranked by **cross-signal corroboration**, not by how well they
match the client's industry label.

That is a deliberate consequence of `Client.industry` confidence being
uncalibrated (build-log Epic 2.6/2.8, Finding 2 — still open). Ranking by
industry match would launder a bad classification into a confident-looking
competitor set: every returned rival would "fit" the wrong industry, and nothing
downstream could tell. Ranking by agreement between two independent signals
means a mis-classified client produces a **visibly incoherent** set — one an
operator notices and corrects — rather than a plausible wrong one.

`CompetitorSet.detection_confidence` records that agreement so Epic 5 knows how
solid its comparison base is.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..config import Settings, get_settings
from ..models import (
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Scan,
    ScanStatus,
    ScanTrigger,
)
from . import cocitation as cocitation_service
from . import serp as serp_service
from .cocitation import CoCitationResult
from .intake import dns_form
from .serp import SerpResult, is_plausible_competitor

logger = structlog.get_logger(__name__)

# §7 Epic 3: "rank top 3-5".
MAX_COMPETITORS = 5
MIN_COMPETITORS_FOR_OK = 3

# Co-citation is weighted slightly above SERP because it is the signal closest
# to what this product actually measures: who an AI assistant names to a buyer.
# SERP is a proxy for the same question. The gap is small on purpose — treating
# either as authoritative would defeat the point of corroborating them.
SERP_WEIGHT = 1.0
CO_CITATION_WEIGHT = 1.2

# Multiplier for a candidate both signals surfaced independently. This is the
# single most important term in the ranking: agreement between two methods that
# fail differently is far stronger evidence than volume within either one.
CORROBORATION_BONUS = 1.6

# Minimum number of DISTINCT SERP queries an uncorroborated candidate must
# appear in before it may occupy a returned rank.
#
# Measured, not guessed. The 10-URL live verification (build-log Epic 3.4) found
# precision of 100% for `both` (22/22) and 100% for `co_citation` (16/16), but
# only 45% for SERP-only (5/11) — every false positive across fifty rows was
# SERP-only. The mechanism is explicable: search returns whatever *ranks* for a
# query, which includes listicles, review blogs and adjacent-market pages,
# whereas a model naming a brand asserts it is an option a buyer would consider.
#
# Two is the smallest threshold that expresses "more than one query agreed", and
# is deliberately the weakest form of the rule. It is within-signal
# corroboration, which is why it applies even when co-citation did not run:
# without a cross-signal check, an internal one matters more, not less.
MIN_SERP_QUERIES_FOR_UNCORROBORATED = 2

_SLUG = re.compile(r"[^a-z0-9]+")

# Suffixes stripped before name matching, so "Zendesk Inc." and "Zendesk" are
# one candidate rather than two.
_LEGAL_SUFFIXES = (
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co",
    "gmbh", "bv", "plc", "sa", "ag", "pty", "srl", "oy", "ab",
)


def slugify(value: str) -> str:
    """Comparison key for a brand name."""
    slug = _SLUG.sub("", value.lower().strip())
    for suffix in _LEGAL_SUFFIXES:
        if slug.endswith(suffix) and len(slug) > len(suffix) + 2:
            slug = slug[: -len(suffix)]
            break
    return slug


def domain_label(domain: str) -> str:
    """The registrable label of a domain: 'front.com' -> 'front'."""
    return slugify(domain.split(".")[0]) if domain else ""


def display_name_from_domain(domain: str) -> str:
    """Human-ish name for a SERP-only candidate that has no name attached."""
    label = domain.split(".")[0].replace("-", " ").strip()
    return label.title() if label else domain


@dataclass
class Candidate:
    """A merged competitor candidate across both signals."""

    domain: str | None = None
    name: str | None = None
    serp_positions: list[int] = field(default_factory=list)
    # The query text behind each SERP position, so "how many DISTINCT queries
    # surfaced this?" is answerable. A list rather than a set: sets have no
    # stable iteration order, and nothing in ranking may become
    # order-dependent on a hash seed.
    serp_queries: list[str] = field(default_factory=list)
    co_citation_positions: list[int] = field(default_factory=list)
    score: float = 0.0

    @property
    def serp_mentions(self) -> int:
        return len(self.serp_positions)

    @property
    def co_citation_mentions(self) -> int:
        return len(self.co_citation_positions)

    @property
    def distinct_serp_queries(self) -> int:
        return len(set(self.serp_queries))

    @property
    def corroborated(self) -> bool:
        return bool(self.serp_positions) and bool(self.co_citation_positions)

    def passes_serp_gate(self, minimum: int = MIN_SERP_QUERIES_FOR_UNCORROBORATED) -> bool:
        """Whether this candidate has earned a returned rank.

        Corroborated candidates and anything a model named are exempt: both
        measured at 100% precision. The gate applies only to SERP-only
        candidates, which measured 45%.
        """
        if self.corroborated or self.co_citation_positions:
            return True
        return self.distinct_serp_queries >= minimum

    @property
    def source(self) -> DetectionSource:
        if self.corroborated:
            return DetectionSource.BOTH
        if self.serp_positions:
            return DetectionSource.SERP
        return DetectionSource.CO_CITATION

    def resolved_name(self) -> str:
        if self.name:
            return self.name
        return display_name_from_domain(self.domain or "")


def _position_weight(position: int) -> float:
    """Diminishing weight by rank.

    1/sqrt(position): first place is worth ~2.2x tenth, which reflects that
    ordering carries real information without letting a single top-ranked
    listicle hit outweigh three independent mid-rank appearances.
    """
    return 1.0 / math.sqrt(max(1, position))


def merge_candidates(
    serp_results: list[SerpResult],
    co_citation_results: list[CoCitationResult],
    *,
    subject_domain: str,
    subject_name: str | None,
) -> list[Candidate]:
    """Dedupe hits from both signals into candidates.

    Matching is domain-first, name-second. A co-citation hit that carries a
    domain merges with a SERP hit on the same domain; one without a domain
    merges on a slugified name compared against the SERP domain's label
    ("Front" <-> front.com). Names are only compared after stripping legal
    suffixes, so "Zendesk Inc." and "Zendesk" do not split into two rivals.
    """
    by_domain: dict[str, Candidate] = {}
    by_name: dict[str, Candidate] = {}
    subject_slug = slugify(subject_name or "") or domain_label(subject_domain)

    def candidate_for(domain: str | None, name: str | None) -> Candidate | None:
        """Find or create the candidate for this (domain, name) pair."""
        name_slug = slugify(name or "")
        # Never let the subject compete with itself, under either identifier.
        if domain and domain == subject_domain:
            return None
        if name_slug and subject_slug and name_slug == subject_slug:
            return None

        if domain:
            existing = by_domain.get(domain)
            if existing is None:
                # A name-keyed candidate may already exist for this brand.
                existing = by_name.get(name_slug) if name_slug else None
                if existing is None and name_slug:
                    existing = by_name.get(domain_label(domain))
                if existing is None:
                    existing = Candidate(domain=domain, name=name)
                else:
                    existing.domain = existing.domain or domain
                by_domain[domain] = existing
            if name and not existing.name:
                existing.name = name
            if name_slug:
                by_name.setdefault(name_slug, existing)
            return existing

        if not name_slug:
            return None
        existing = by_name.get(name_slug)
        if existing is None:
            # Match a nameless SERP candidate whose domain label is this name.
            for dom, cand in by_domain.items():
                if domain_label(dom) == name_slug:
                    existing = cand
                    existing.name = existing.name or name
                    break
        if existing is None:
            existing = Candidate(name=name)
        by_name[name_slug] = existing
        return existing

    for result in serp_results:
        if not result.ok:
            continue
        for hit in result.hits:
            if not is_plausible_competitor(hit.domain, subject_domain=subject_domain):
                continue
            candidate = candidate_for(hit.domain, None)
            if candidate is not None:
                candidate.serp_positions.append(hit.position)
                candidate.serp_queries.append(hit.query)

    for result in co_citation_results:
        if not result.ok:
            continue
        for hit in result.hits:
            # Apply the same publisher/aggregator exclusion to model output —
            # models name G2 and Reddit as readily as search does.
            if hit.domain and not is_plausible_competitor(
                hit.domain, subject_domain=subject_domain
            ):
                continue
            candidate = candidate_for(hit.domain, hit.name)
            if candidate is not None:
                candidate.co_citation_positions.append(hit.position)

    # Deduplicate the two indexes into one list of distinct objects.
    seen: list[Candidate] = []
    for candidate in [*by_domain.values(), *by_name.values()]:
        if not any(candidate is existing for existing in seen):
            seen.append(candidate)
    return seen


def score_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Score and sort candidates, highest first.

    Each signal is normalised to 0-1 against its own maximum before the two are
    combined. Without that, whichever signal happened to return more hits would
    dominate — six SERP queries yield up to sixty hits, four seed prompts up to
    forty, and that difference is an artefact of configuration, not evidence.
    """
    raw_serp = {
        id(c): sum(_position_weight(p) for p in c.serp_positions) for c in candidates
    }
    raw_cocit = {
        id(c): sum(_position_weight(p) for p in c.co_citation_positions) for c in candidates
    }
    max_serp = max(raw_serp.values(), default=0.0) or 1.0
    max_cocit = max(raw_cocit.values(), default=0.0) or 1.0

    for candidate in candidates:
        serp_component = (raw_serp[id(candidate)] / max_serp) * SERP_WEIGHT
        cocit_component = (raw_cocit[id(candidate)] / max_cocit) * CO_CITATION_WEIGHT
        score = serp_component + cocit_component
        if candidate.corroborated:
            score *= CORROBORATION_BONUS
        candidate.score = round(score, 3)

    # Deterministic ordering: score desc, then corroborated first, then total
    # mentions, then name. Ties must never reshuffle between runs, or a client
    # comparing two reports sees competitors swap places for no reason.
    candidates.sort(
        key=lambda c: (
            -c.score,
            not c.corroborated,
            -(c.serp_mentions + c.co_citation_mentions),
            c.resolved_name().lower(),
        )
    )
    return candidates


@dataclass
class DetectionOutcome:
    """What a detection run produced. Persisted by `persist_detection`."""

    candidates: list[Candidate]
    status: DetectionStatus
    detection_confidence: Decimal | None
    serp_queries_run: int
    co_citation_prompts_run: int
    candidates_considered: int
    used_industry_seed: bool


def decide_detection(
    candidates: list[Candidate],
    *,
    serp_ok: int,
    co_citation_ok: int,
    used_industry_seed: bool,
    limit: int = MAX_COMPETITORS,
) -> DetectionOutcome:
    """Turn scored candidates into a persistable outcome.

    Split out from the I/O so the confidence policy is unit-testable without a
    network call — the branch that matters is the one that refuses to report
    agreement it never actually observed.
    """
    considered = len(candidates)

    # Gate BEFORE truncating to the limit, so a filtered-out candidate frees its
    # slot for the next eligible one rather than shortening the set. Filtering
    # is a pure predicate over already-scored candidates, so relative ordering
    # among survivors — and therefore determinism — is unchanged.
    eligible = [c for c in candidates if c.passes_serp_gate()]
    gated = [c for c in candidates if not c.passes_serp_gate()]

    # Floor: the gate must not be able to starve a set it could otherwise fill.
    #
    # Epic 3.5's live run never hit this, but TestSerpGateRecallImpact proved it
    # reachable: one query's worth of SERP data with no co-citation gates every
    # candidate, turning a 3-competitor WEAK_SIGNAL set into NO_SIGNAL. Under a
    # partial SerpApi outage or in an obscure market, showing an operator three
    # weakly-evidenced rivals they can correct beats showing them nothing.
    #
    # Backfill takes the highest-scoring gated candidates, and they are appended
    # AFTER the eligible ones rather than merged by score. Ranking is a claim
    # about evidence, and a candidate that failed the gate has weaker evidence
    # than one that passed it regardless of its raw score. Both lists arrive
    # already sorted by the deterministic key, so appending preserves
    # determinism.
    backfilled = 0
    if len(eligible) < MIN_COMPETITORS_FOR_OK and gated:
        shortfall = MIN_COMPETITORS_FOR_OK - len(eligible)
        backfill = gated[:shortfall]
        backfilled = len(backfill)
        eligible = [*eligible, *backfill]

    gated_out = considered - len(eligible)
    top = eligible[:limit]
    both_signals_ran = serp_ok > 0 and co_citation_ok > 0

    if gated_out or backfilled:
        logger.info(
            "competitors.serp_gate_applied",
            gated_out=gated_out,
            backfilled=backfilled,
            eligible=len(eligible),
            considered=considered,
        )

    if not top:
        return DetectionOutcome(
            candidates=[],
            status=DetectionStatus.NO_SIGNAL,
            detection_confidence=None,
            serp_queries_run=serp_ok,
            co_citation_prompts_run=co_citation_ok,
            candidates_considered=considered,
            used_industry_seed=used_industry_seed,
        )

    if not both_signals_ran:
        # Corroboration is *unmeasurable*, not zero. Reporting 0.0 here would
        # read as "two signals looked and disagreed", when in fact only one
        # signal ever ran. Same discipline as Score's INSUFFICIENT_DATA:
        # undetermined must be representable as undetermined.
        return DetectionOutcome(
            candidates=top,
            status=DetectionStatus.WEAK_SIGNAL,
            detection_confidence=None,
            serp_queries_run=serp_ok,
            co_citation_prompts_run=co_citation_ok,
            candidates_considered=considered,
            used_industry_seed=used_industry_seed,
        )

    corroborated = sum(1 for c in top if c.corroborated)
    confidence = Decimal(corroborated) / Decimal(len(top))
    confidence = confidence.quantize(Decimal("0.001"))

    status = DetectionStatus.OK
    if len(top) < MIN_COMPETITORS_FOR_OK or corroborated == 0 or backfilled:
        # Too few rivals to compare against, two independent signals agreeing on
        # nothing, or a set only reaching the floor because gated candidates were
        # backfilled — each means the set needs an operator's eye. A backfilled
        # set is never OK: it contains rows that failed the evidence bar, and
        # WEAK_SIGNAL is exactly the existing flag for that.
        status = DetectionStatus.WEAK_SIGNAL

    return DetectionOutcome(
        candidates=top,
        status=status,
        detection_confidence=confidence,
        serp_queries_run=serp_ok,
        co_citation_prompts_run=co_citation_ok,
        candidates_considered=considered,
        used_industry_seed=used_industry_seed,
    )


def _retrieved(task: asyncio.Task[int | None]) -> None:
    """Retrieve a finished task's outcome, so that asyncio can never report it as unseen.

    A task that ends with an exception nobody asked for is reported when it is collected:
    `Task exception was never retrieved`, the traceback and the exception's text, on stderr.
    The exception of `serp.quota_left` is an httpx error's, whose text can carry the request's
    URL, and the URL carries the API key. `quota_left` never raises, so this is for the day it
    does, and for the ways out of `detect_from_facts` that never reach its `await`: the model's
    call failed, or the detection was cancelled, after the read had begun. A read that is
    cancelled while it is in flight can still end with an exception of its own, after
    `cancel()` was called, and nothing else asks for that one. A cancelled task has no
    exception to retrieve (asking raises).
    """
    if not task.cancelled():
        task.exception()


async def detect_from_facts(
    *,
    brand_name: str | None,
    domain: str,
    industry: str | None = None,
    niche: str | None = None,
    name: str | None = None,
    client_id: str | None = None,
    settings: Settings | None = None,
) -> DetectionOutcome:
    """Run both discovery signals over a subject's facts and rank the results.

    The whole of detection, taking the six scalars it actually needs rather
    than a `Client` row. It makes no database call — it never did; the ORM
    object was only ever an awkward way to pass six strings.

    Extracted in Epic 3.11 to close Finding 5. `scripts/verify_competitors.py`
    is the live check for §7 Epic 3's acceptance criterion and had no database
    connection by design, so it could not call `detect_for_client` and had
    reassembled the pipeline itself instead — measuring a copy. The two were
    verified equivalent at the time, so the 80% figure it reported was sound,
    but nothing would have caught them drifting apart. Now there is one
    implementation and the script calls it.

    Both signals run in the client's own market: the search in the country its
    domain's TLD names (`serp.search_country`), and the model told the domain as
    well as the brand.

    It also reads how many SerpApi searches are left, once, for the log
    (`serp.quota_left`; see the call below for when, and why there).

    `client_id` is for the log line only and is optional for exactly that
    reason: a caller with no Client row still gets a real detection.
    """
    settings = settings or get_settings()

    queries = serp_service.build_queries(
        brand_name=brand_name,
        domain=domain,
        industry=industry,
        niche=niche,
    )
    prompts = cocitation_service.build_seed_prompts(
        brand_name=brand_name,
        domain=domain,
        industry=industry,
        niche=niche,
    )
    # The market a detection runs in (2026-10-03; the search side is measured in serp.py,
    # above `DEFAULT_SEARCH_COUNTRY`). Production's new .in client, an Indian dessert-cafe
    # chain, came out at `confidence=0.000`. Reproduced through this path: searched as
    # gl=us, its 3 queries returned 27 result domains and none was a brand the model had
    # named. And the model was given only the brand name: its two brand-anchored seed
    # prompts named US packaged-waffle brands, where with the brand and its domain as the
    # subject all four prompts named Indian dessert chains. So the search runs
    # in the country the domain's TLD names, and the subject is the brand WITH its
    # domain, or the domain alone when no name is known. The queries and seed prompts
    # are as they were, and so are the SERP gate, the confidence and `FORMULA_VERSION`:
    # detection's inputs change, not its arithmetic.
    country = serp_service.search_country(domain)
    brand = brand_name or name
    subject_brand = f"{brand} ({domain})" if brand else domain

    serp_results = await serp_service.search_many(queries, settings=settings, country=country)
    # How many searches are left (U2, 2026-10-04): ONE read of SerpApi's account endpoint for the
    # detection, AFTER the searches so that the figure is what they left (SerpApi's count lags a
    # minute or two, so it can still miss them: `serp.py` says how far), and BESIDE the model's
    # calls, which take tens of seconds, so that it costs the detection nothing it was not already
    # waiting for. It runs no longer than `serp.QUOTA_TIMEOUT_S` (5 s, far under the model's
    # `CO_CITATION_BOUND` ceiling, so the worst case `test_scan_executor.py` adds up does not
    # move), it never raises, and a model call that fails or a detection that is cancelled takes
    # it down with them. Read before the searches it would cost nothing either (they take longer
    # than it does), but it would report the month as it stood before this detection spent
    # anything. However it ends, its outcome is retrieved (`_retrieved`), so that a read that
    # ever raised is never reported by asyncio as an exception nobody asked about.
    quota = asyncio.create_task(serp_service.quota_left(settings), name="serp.quota_left")
    quota.add_done_callback(_retrieved)
    try:
        co_citation_results = await cocitation_service.run_seed_prompts(
            prompts, subject_brand=subject_brand, settings=settings
        )
    except BaseException:
        quota.cancel()
        raise
    # `quota_left` never raises; this is for the day it does. A line about the quota is not worth
    # a competitor set.
    with contextlib.suppress(Exception):
        await quota

    for result in serp_results:
        if not result.ok:
            logger.warning("competitors.serp_failed", **result.redacted())
    for result in co_citation_results:
        if not result.ok:
            logger.warning("competitors.cocitation_failed", **result.redacted())
    # One line per detection, a count and the client: the domains are model-authored and stay out.
    dropped = sum(r.domains_dropped for r in co_citation_results)
    if dropped:
        logger.info(
            "competitors.cocitation_domains_dropped", client_id=client_id, dropped=dropped
        )

    candidates = merge_candidates(
        serp_results,
        co_citation_results,
        subject_domain=domain,
        subject_name=brand_name or name,
    )
    score_candidates(candidates)

    outcome = decide_detection(
        candidates,
        serp_ok=sum(1 for r in serp_results if r.ok),
        co_citation_ok=sum(1 for r in co_citation_results if r.ok),
        used_industry_seed=bool(industry or niche),
    )
    logger.info(
        "competitors.detected",
        client_id=client_id,
        domain=domain,
        status=outcome.status.value,
        confidence=str(outcome.detection_confidence),
        returned=len(outcome.candidates),
        considered=outcome.candidates_considered,
        country=country,
    )
    return outcome


async def detect_for_client(
    client: Client, *, settings: Settings | None = None
) -> DetectionOutcome:
    """Run detection for a client. A thin unpack over `detect_from_facts`.

    Kept as the service's entry point so every existing caller is unchanged,
    and deliberately holding no logic of its own — the moment it does, the
    script that calls `detect_from_facts` stops verifying what the API runs,
    which is the whole defect Finding 5 recorded.
    """
    return await detect_from_facts(
        brand_name=client.brand_name,
        domain=client.domain,
        industry=client.industry,
        niche=client.industry_niche,
        name=client.name,
        client_id=client.id,
        settings=settings,
    )


async def open_scan(session: AsyncSession, client_id: str, *, agency_id: str) -> Scan | None:
    """The client's open (queued or running) scan, if it has one.

    Factored out because `get_or_create_scan` asks the same question twice —
    once optimistically, and once after losing a race.

    The client's agency is named as well as its id, so a scan row that names this client under
    another agency's id is never the open scan (`latest_scan_for_client` takes both the same way).
    `agency_id` is keyword-only, so every call spells out which agency's scans it asks for.
    """
    return (
        await session.execute(
            select(Scan)
            .where(
                Scan.client_id == client_id,
                Scan.agency_id == agency_id,
                Scan.status.in_([ScanStatus.QUEUED, ScanStatus.RUNNING]),
            )
            .order_by(Scan.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_or_create_scan(
    session: AsyncSession, client: Client, *, user_id: str | None = None
) -> Scan:
    """The open scan a detection run leaves for a later scan to reuse, and the scan a run executes.

    Detection used to hang its set off this scan (§5.3 nests CompetitorSet under Scan). It no
    longer does: the button's detection writes the client's NEXT LIST, which the scan adopts when
    it starts, so this scan carries no set until then. It is still opened (and counted by the scan
    allowance as it always was), because a detect-only run has always left an open QUEUED row for
    the next scan to reuse and nothing here changes that. Epic 3 reuses
    the most recent non-terminal scan rather than creating a new one per
    detection run, so repeated detection while tuning does not litter the
    client with empty scans.

    **Race-safe since Epic 9.6.** Looking and then inserting is a check-then-act:
    two concurrent requests can both look, both find nothing, and both insert —
    two scans, and two scans' worth of paid model calls. Epic 9.5 narrowed that
    window from ~303s to milliseconds by committing early; it could not close
    it. `uq_scans_one_open_per_client` closes it, and the loser is handled here
    rather than surfacing as a 500: a losing INSERT blocks until the winner
    commits, then raises, and both callers end up pointing at the same scan.

    The INSERT runs inside a SAVEPOINT so a lost race rolls back only its own
    statement. A plain rollback would discard whatever else the caller had
    pending in the surrounding transaction.
    """
    existing = await open_scan(session, client.id, agency_id=client.agency_id)
    if existing is not None:
        return existing

    # QUEUED, which is the model's own default (models/scan.py) and was the
    # original intent — Epic 9.5. It used to be created RUNNING with a
    # started_at, which claimed work had begun before anything had picked it up.
    # `run_scan` sets RUNNING and stamps started_at when an executor actually
    # starts, and the stale-scan reaper keys off exactly that: RUNNING means
    # claimed, QUEUED means open. Detection also lands here, and a detect-only
    # run correctly leaves an open QUEUED row for a later scan to reuse.
    scan = Scan(
        id=ids.new_id(ids.SCAN),
        client_id=client.id,
        agency_id=client.agency_id,
        requested_by_user_id=user_id,
        status=ScanStatus.QUEUED,
        trigger=ScanTrigger.MANUAL,
    )
    try:
        async with session.begin_nested():
            session.add(scan)
            await session.flush()
    except IntegrityError as exc:
        # Someone else opened a scan for this client between our SELECT and our
        # INSERT. Expected under concurrency, not an error: adopt their row.
        if scan in session:
            session.expunge(scan)
        winner = await open_scan(session, client.id, agency_id=client.agency_id)
        if winner is None:
            # Not the race — a genuine constraint failure worth surfacing. When it is the
            # one-open-scan index, an open scan of this client exists that this agency's lookup
            # cannot see: a row under another agency's id, which the app never writes. It is
            # not adopted. The constraint's name (asyncpg's own error, under SQLAlchemy's) tells
            # that case from any other. Ids only, and only this client's own: the other agency's
            # is not named.
            logger.warning(
                "scan.create.insert_refused",
                client_id=client.id,
                agency_id=client.agency_id,
                constraint=getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None),
            )
            raise
        logger.info("scan.create.lost_race", client_id=client.id, adopted=winner.id)
        return winner
    return scan


def _identity(name: str) -> str:
    """The key that decides whether two submissions mean the same rival.

    `slugify(name)`, which is not a new concept: `apply_override` already used
    exactly this to work out which rows had been STRUCK, and `persist_detection`
    already used it to decide which candidates a manual override blocks. Epic
    3.9 reuses it to decide which row to KEEP, so no new notion of competitor
    identity enters the codebase.
    """
    return slugify(name)


def _host(domain: str | None) -> str | None:
    """A rival's domain as a host, for comparing: its DNS form, or None for no domain."""
    return None if domain is None else dns_form(domain)


def _forget_other_sites_if_the_domain_moved(row: Competitor, new_domain: str | None) -> None:
    """Clear a rival's confirmed other sites when its HOST is about to change (M1 task 1.1).

    The agency confirmed them as sites of the brand at the OLD domain (`rmit.edu.vn` for
    `rmit.edu.au`); at another host they are claims about a brand nobody confirmed. A row reused
    at the host it had keeps them, and the host is compared in DNS form (`intake.dns_form`): a
    rival saved again as `xn--bcher-kva.com` where it was `bücher.com` is the same host, spelled
    the other way, and keeps its sites. A row that has no domain yet (one just made, whose
    `other_sites` is unset until it is flushed) starts with none. Everything else about the row
    is the caller's to write.
    """
    if _host(row.domain) != _host(new_domain):
        row.other_sites = []


def _apply_manual(row: Competitor, name: str, domain: str | None, rank: int) -> None:
    """Write an operator-supplied competitor onto a row.

    Shared between the reuse and the insert path so the two cannot drift — the
    same discipline `scoring_runner._apply` and `audit_runner._apply` use. Every
    field an operator's submission determines is written here, which is what
    makes reusing a row indistinguishable from recreating it except for the id.

    The one thing a submission does not determine is `other_sites`: the domains the agency
    confirmed as the rival's other sites stay on a reused row, and are cleared only when the
    submission moves the row to another HOST, the same host in another spelling being the same
    host (M1 task 1.1).
    """
    row.name = name.strip()[:200]
    new_domain = (domain or "").strip().lower() or None
    _forget_other_sites_if_the_domain_moved(row, new_domain)
    row.domain = new_domain
    row.rank = rank
    row.detection_source = DetectionSource.MANUAL
    row.signal_count = 0
    row.serp_mentions = 0
    row.co_citation_mentions = 0
    row.corroborated = False
    row.score = None
    row.is_manual_override = True
    row.is_suppressed = False


def _apply_detected(row: Competitor, candidate: Candidate, name: str, rank: int) -> None:
    """Write a detection candidate onto a row. Same discipline as above.

    `other_sites` are kept on a row detection finds again at its host (in either spelling of
    it), and cleared when detection gives the same rival (matched by name) another host: they
    were confirmed against the old one. A row detection makes new starts with none.
    """
    row.name = name[:200]
    _forget_other_sites_if_the_domain_moved(row, candidate.domain)
    row.domain = candidate.domain
    row.rank = rank
    row.detection_source = candidate.source
    row.signal_count = candidate.serp_mentions + candidate.co_citation_mentions
    row.serp_mentions = candidate.serp_mentions
    row.co_citation_mentions = candidate.co_citation_mentions
    row.corroborated = candidate.corroborated
    row.score = Decimal(str(candidate.score))


def sites_a_save_would_carry(
    competitor_set: CompetitorSet | None, items: list[tuple[str, str | None]]
) -> list[list[str]]:
    """The other sites each of `items` would hold after `apply_override`: one list per item.

    Read from the rows `competitor_set` holds now, matched exactly as `apply_override` matches
    them (a rival's identity, the first item to claim a row getting it) and carried exactly as
    `_forget_other_sites_if_the_domain_moved` carries them: kept while the HOST is unchanged,
    its spelling being no change, and none for an item that gets a new row. Writes nothing, so a
    save can be checked before anything is written. Struck rows are matched too, as they are
    when a struck rival is named again.
    """
    by_identity: dict[str, Competitor] = {}
    for held in competitor_set.competitors if competitor_set is not None else []:
        by_identity.setdefault(_identity(held.name), held)
    claimed: set[str] = set()
    carried: list[list[str]] = []
    for name, domain in items:
        key = _identity(name)
        row = by_identity.get(key) if key not in claimed else None
        claimed.add(key)
        new_domain = (domain or "").strip().lower() or None
        keeps = row is not None and _host(row.domain) == _host(new_domain)
        carried.append(list(row.other_sites) if row is not None and keeps else [])
    return carried


async def apply_override(
    session: AsyncSession,
    competitor_set: CompetitorSet,
    items: list[tuple[str, str | None]],
) -> CompetitorSet:
    """Replace a competitor set with an operator's corrected one.

    Lives here rather than in the router because it is the write half of the
    same contract `persist_detection` reads: the two have to agree about what
    an override means, and a rule enforced in a request handler is a rule the
    verification scripts and the workers cannot reach. Epic 3.6's live
    verification was initially written against a COPY of this logic and passed
    against its own copy while the real endpoint was still wrong — which is
    exactly the self-consistent measurement Epic 4.0 warns about.

    `items` is (name, domain) in the operator's intended rank order.

    Replaces wholesale rather than patching rows: an operator correcting a bad
    detection is asserting "these are the rivals", and reconciling that against
    auto-detected rows one at a time invites a half-applied state.

    Anything currently in the set and NOT in `items` has been STRUCK, and is
    kept as a suppressed tombstone rather than deleted. Epic 3 deleted it,
    which recorded nothing: the next detection run surfaced the same rival,
    matched no override, and reinstated it, so "remove" was the one correction
    that did not survive a re-run.

    `detection_confidence` is cleared. It measures agreement between two
    automated signals and neither produced this set, so keeping the value would
    attach a corroboration claim to rows nothing corroborated.
    """
    # Rows are REUSED, not recreated. Citation.competitor_id and
    # BrandMention.competitor_id are ON DELETE SET NULL, so deleting a row and
    # inserting an identical one silently nulled every attribution pointing at
    # it — Finding 4, live in avp_dev for two epics. Keeping the id keeps the
    # foreign keys, and since `_apply_manual` overwrites every field an
    # operator's submission determines, the resulting row is indistinguishable
    # from a freshly-inserted one in everything except that id.
    #
    # This does not weaken "replace wholesale". That phrase is about the
    # resulting STATE — no partial merge, every submitted row ends up MANUAL
    # with its signals zeroed — and that is unchanged. Only the mechanism used
    # to reach it changed.
    #
    # It also removes a same-flush delete-then-insert of the same
    # (competitor_set_id, name), which SQLAlchemy orders INSERT-first and which
    # therefore raised UniqueViolationError. See persist_detection.
    by_identity: dict[str, Competitor] = {}
    for row in competitor_set.competitors:
        by_identity.setdefault(_identity(row.name), row)

    claimed: set[str] = set()
    for rank, (name, domain) in enumerate(items, start=1):
        key = _identity(name)
        row = by_identity.get(key) if key not in claimed else None
        if row is None:
            row = Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=competitor_set.id
            )
            # Appended through the relationship rather than session.add(): the
            # collection cascades delete-orphan, so a row attached only by
            # foreign key would be deleted as an orphan on flush.
            competitor_set.competitors.append(row)
        _apply_manual(row, name, domain, rank)
        claimed.add(key)

    # Anything left is STRUCK. It becomes a tombstone IN PLACE rather than
    # being deleted and replaced by a new one, for the same reason: the row's
    # id is what its citations and mentions point at. `active_competitors`
    # filters tombstones out before anything reads the set, so a citation
    # attributed to a struck rival resolves to no name and presents as third
    # party — the operator's correction is respected without the underlying
    # fact being destroyed.
    #
    # The domain is kept rather than nulled, which the previous tombstone did
    # not do. `persist_detection` blocks a re-offered candidate by name OR
    # domain, so keeping it makes the strike harder to defeat, not easier.
    tail = len(items) + 1
    for row in competitor_set.competitors:
        if _identity(row.name) in claimed:
            continue
        row.rank = tail
        row.detection_source = DetectionSource.MANUAL
        row.signal_count = 0
        row.serp_mentions = 0
        row.co_citation_mentions = 0
        row.corroborated = False
        row.score = None
        row.is_manual_override = True
        row.is_suppressed = True
        tail += 1

    competitor_set.detection_confidence = None
    competitor_set.status = DetectionStatus.OK if items else DetectionStatus.NO_SIGNAL
    await session.flush()
    return competitor_set


async def persist_detection(
    session: AsyncSession, scan: Scan, outcome: DetectionOutcome
) -> CompetitorSet:
    """Write a detection outcome, preserving operator overrides.

    Competitors an operator added or kept by hand survive re-detection. That is
    the whole point of the override: an operator who has corrected a bad set
    must not have their correction silently undone by the next run.

    Epic 3.6 extended that to removals. A struck rival is kept as a SUPPRESSED
    row rather than deleted, because a deleted row records nothing — the next
    run surfaced the same rival, matched no override, and reinstated it, so
    "remove" was the one correction that did not stick. Suppressed rows are
    manual overrides too, so they land in `manual` below and their keys block
    the candidate from being re-offered. They are excluded from every read path
    via `CompetitorSet.active_competitors`.
    """
    competitor_set = (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan.id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()

    if competitor_set is None:
        # `competitors=[]` is required, not cosmetic. Without it the collection
        # is unloaded after flush(), and the next attribute access triggers a
        # lazy load — which raises MissingGreenlet under the async session.
        # Passing an empty list marks the relationship as already loaded.
        competitor_set = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, competitors=[]
        )
        session.add(competitor_set)
        await session.flush()

    # The scan's client in the scan's own agency: a scan row that names another agency's client
    # finds none, so nothing of that client's (its confirmed other sites, its list) is used.
    client = (
        await session.execute(
            select(Client).where(Client.id == scan.client_id, Client.agency_id == scan.agency_id)
        )
    ).scalar_one_or_none()
    confirmed = await _confirmed_other_sites(session, client) if client is not None else set()
    return await _write_detection(
        session, competitor_set, outcome, client_id=scan.client_id, confirmed_sites=confirmed
    )


async def persist_detection_for_client(
    session: AsyncSession, client: Client, outcome: DetectionOutcome
) -> CompetitorSet:
    """Write a detection outcome to the client's NEXT LIST: what the button's detection run does.

    A scan's own set never changes once the scan has started, so a detection run no longer writes
    one (it used to write the open scan's, which a running scan had already read). It writes the
    list the next scan will adopt. If the client has none, it is created as a copy of the client's
    latest set (rows, hand edits and struck rivals included) and detection merges into the copy,
    so what an operator named or struck survives a re-detection exactly as it does within one set.
    A client with no set at all starts from an empty list.

    The row is locked from here to the caller's commit, so a scan adopting the list at the same
    moment either takes it before this writes (and this makes a new list from what is now that
    scan's set) or waits for it.
    """
    next_list = await next_list_for_client(session, client, lock=True)
    if next_list is None:
        source = await latest_set_for_client(session, client)
        next_list = await create_next_list(session, client, source=source)
    return await _write_detection(
        session, next_list, outcome, client_id=client.id,
        confirmed_sites=await _confirmed_other_sites(session, client),
    )


async def _confirmed_other_sites(session: AsyncSession, client: Client) -> set[str]:
    """Every domain, in DNS form, the agency confirmed as an other site of the client or of a
    rival on the client's current list (`current_list_for_client`: its next list, else its latest
    set; struck rivals hold none). Detection does not propose these as rivals.
    """
    current = await current_list_for_client(session, client)
    held = [*client.other_sites]
    for row in current.active_competitors if current is not None else []:
        held.extend(row.other_sites)
    return {dns_form(domain) for domain in held}


async def _write_detection(
    session: AsyncSession,
    competitor_set: CompetitorSet,
    outcome: DetectionOutcome,
    *,
    client_id: str,
    confirmed_sites: set[str],
) -> CompetitorSet:
    """Merge a detection outcome into `competitor_set`, preserving operator overrides.

    The body of `persist_detection`, shared with `persist_detection_for_client`, which differ
    only in which set they hand it. `competitor_set.competitors` must be loaded.

    A candidate whose domain is one the agency confirmed as an other site of the client or of a
    rival (`confirmed_sites`, in DNS form) is not written, and one `competitors.skipped` line is
    logged for it.
    """
    manual = [c for c in competitor_set.competitors if c.is_manual_override]
    manual_keys = {slugify(c.name) for c in manual} | {c.domain for c in manual if c.domain}

    # Auto-detected rows are REUSED where detection finds the same rival again,
    # for two reasons that Epic 3.9 found together.
    #
    # Attribution: Citation.competitor_id and BrandMention.competitor_id are
    # ON DELETE SET NULL, so deleting every non-manual row on each run nulled
    # the attribution for competitors the very same run went on to re-find.
    # Finding 4 named apply_override; this path had it too.
    #
    # Correctness: deleting `Keep` and inserting `Keep` in one flush is a
    # same-table delete-then-insert on a unique key, and SQLAlchemy's unit of
    # work orders INSERTs before DELETEs — so re-detecting a scan that re-found
    # any rival raised UniqueViolationError on uq_competitors_set_name and the
    # endpoint returned 500. That has been true since Epic 3; no test caught it
    # because none re-detected with an overlapping result set. Reuse removes
    # the delete/insert pair entirely, so the collision cannot arise.
    auto_by_identity: dict[str, Competitor] = {}
    auto_by_domain: dict[str, Competitor] = {}
    for competitor in competitor_set.competitors:
        if competitor.is_manual_override:
            continue
        auto_by_identity.setdefault(slugify(competitor.name), competitor)
        if competitor.domain:
            auto_by_domain.setdefault(competitor.domain, competitor)

    competitor_set.status = outcome.status
    # Finding 3, closed in Epic 3.11. This figure measures agreement between
    # two automated signals across the rows DETECTION found; manual overrides
    # preserved above are not in that calculation, so on a mixed set it used to
    # be published for more rows than it described.
    #
    # The fix is scope, not arithmetic: the value stored here still means what
    # it always meant, and both projections now publish `confidenceCovers`
    # beside it — the number of rows in the set that detection produced. What
    # the figure should MEAN on a part-hand-set list is still a scoring
    # question and still open; what it covers is now stated rather than
    # implied, which is the part that was misleading.
    competitor_set.detection_confidence = outcome.detection_confidence
    competitor_set.serp_queries_run = outcome.serp_queries_run
    competitor_set.co_citation_prompts_run = outcome.co_citation_prompts_run
    competitor_set.candidates_considered = outcome.candidates_considered
    competitor_set.used_industry_seed = outcome.used_industry_seed
    competitor_set.detected_at = datetime.now(UTC)

    # Suppressed rows are tombstones, not competitors: they must not consume a
    # rank or push detected rivals down the list.
    rank = sum(1 for c in manual if not c.is_suppressed) + 1
    reused: set[str] = set()
    for candidate in outcome.candidates:
        if candidate.domain and _host(candidate.domain) in confirmed_sites:
            # Confirmed by the agency as another brand's other site (M1 task 1.1): not a rival.
            # A domain is not a secret; no name or address of a person is logged.
            logger.info(
                "competitors.skipped", reason="confirmed_other_site", client_id=client_id,
                domain=candidate.domain,
            )
            continue
        name = candidate.resolved_name()
        if slugify(name) in manual_keys or (candidate.domain and candidate.domain in manual_keys):
            continue
        key = slugify(name)
        row = auto_by_identity.get(key) or (
            auto_by_domain.get(candidate.domain) if candidate.domain else None
        )
        if row is not None and row.id in reused:
            row = None
        if row is None:
            row = Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=competitor_set.id
            )
            # Appended through the relationship, NOT session.add(): the
            # relationship cascades delete-orphan, so a Competitor attached
            # only by foreign key would be seen as an orphan and deleted on
            # flush, silently producing an empty competitor set.
            competitor_set.competitors.append(row)
        _apply_detected(row, candidate, name, rank)
        reused.add(row.id)
        rank += 1

    # An auto-detected row this run did NOT re-find is genuinely gone from the
    # set, so it is deleted and its attribution nulled with it. That is the
    # correct outcome — the rival is no longer a rival — and it is the only
    # case in which attribution is now lost, where previously every run lost
    # all of it.
    for competitor in list(competitor_set.competitors):
        if competitor.is_manual_override or competitor.id in reused:
            continue
        await session.delete(competitor)
        competitor_set.competitors.remove(competitor)

    await session.flush()
    return competitor_set

# ---------------------------------------------------------------------------
# Guaranteeing a scan has a competitor set — Epic 9.17
# ---------------------------------------------------------------------------


async def latest_set_for_client(
    session: AsyncSession, client: Client, *, excluding_scan_id: str | None = None
) -> CompetitorSet | None:
    """The set of the client's most recent scan that has one. Its next list is not one.

    **Ordered by the scan, not by when the set was minted.** An adopted next list keeps the id it
    was minted with, so a set id can be older than the id of an earlier scan's own set (that scan's
    detection finishing after the list was created). Ordering by set id would then put the earlier
    scan's raw detection ahead of the later scan's adopted list, and the operator's names and
    strikes would silently be gone. Scan ids are creation-ordered ULIDs and a client has one open
    scan at a time, so scan order is run order (`latest_scan_for_client` orders the same way).

    The inner join to `scans` is what leaves a next list out: it has no scan. The query names the
    client's agency as well as its id, which only stops a scan row whose agency disagrees with the
    client's. Tenancy is the caller's: each one resolves the client under the signed-in agency first
    (`get_client`, `_load_client`), or takes it from a scan of that client.
    A scan carries this set forward when there is no next list to adopt, and a save or a detection
    run copies it when the client has no next list yet. `current_list_for_client` answers "what is
    the client's list now", with the next list ahead of this one; a second copy of that question
    is a second answer waiting to disagree with the first.
    """
    stmt = (
        select(CompetitorSet)
        .join(Scan, Scan.id == CompetitorSet.scan_id)
        .where(Scan.client_id == client.id, Scan.agency_id == client.agency_id)
        .order_by(Scan.id.desc(), CompetitorSet.id.desc())
        .limit(1)
        .options(selectinload(CompetitorSet.competitors))
    )
    if excluding_scan_id is not None:
        stmt = stmt.where(CompetitorSet.scan_id != excluding_scan_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_scan_for_client(
    session: AsyncSession, client_id: str, agency_id: str
) -> Scan | None:
    """The client's most recent scan of any status: what a save is checked against.

    No scan at all is a 404 for `PUT /clients/{id}/competitors`, and a RUNNING one is a 409. The
    scan is not where the saved list goes (that is the client's next list).

    Scoped to the client's own agency as well, so a scan row that does not belong to it can
    never be chosen.
    """
    return (
        await session.execute(
            select(Scan)
            .where(Scan.client_id == client_id, Scan.agency_id == agency_id)
            .order_by(Scan.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def next_list_for_client(
    session: AsyncSession, client: Client, *, lock: bool = False
) -> CompetitorSet | None:
    """The client's next list, if it has one: the set a save and a detection run write.

    Reachable only through the client: the query names the client's id and its agency. That
    agency is the client object's own, so tenancy is the caller's: each one resolves the client
    under the signed-in agency first (`get_client`, `_load_client`), or takes it from a scan of
    that client.

    `lock=True` takes the row `FOR UPDATE` and reads it as the database holds it now. Every
    writer takes the lock, and so does the adoption by a scan. That is the whole protocol that
    keeps a scan's set from changing after the scan has started: a writer that finds the list
    while a scan is adopting it waits for the adoption to commit, and when it does the row is
    the scan's and no longer matches `scan_id IS NULL`, so the writer finds nothing here and
    makes a new next list instead of writing the adopted one.
    """
    stmt = (
        select(CompetitorSet)
        .where(
            CompetitorSet.client_id == client.id,
            CompetitorSet.scan_id.is_(None),
            CompetitorSet.client_id.in_(
                select(Client.id).where(
                    Client.id == client.id, Client.agency_id == client.agency_id
                )
            ),
        )
        .options(selectinload(CompetitorSet.competitors))
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def current_list_for_client(
    session: AsyncSession, client: Client
) -> CompetitorSet | None:
    """The client's list as it stands: its next list if it has one, else its latest set.

    What `GET /clients/{id}/competitors` returns, and what an ad hoc prompt run looks for. The
    next list is ahead of the latest set because it is the one later scans will use.
    """
    next_list = await next_list_for_client(session, client)
    if next_list is not None:
        return next_list
    return await latest_set_for_client(session, client)


async def create_next_list(
    session: AsyncSession, client: Client, *, source: CompetitorSet | None = None
) -> CompetitorSet:
    """Make the client's next list, as a copy of `source` (a set of one of its scans) or empty.

    The copy is the one `carry_forward_set` makes (rows, hand edits and struck rivals, provenance
    verbatim), held for the client instead of hung off a scan. A client with no set at all gets
    an empty list, which a hand edit then fills (`apply_override`) and no detection has touched.

    If a next list appeared between the caller's look and this insert (two first saves, or a
    save and a detection run), `uq_competitor_sets_next_list_per_client` refuses the second, and
    that one is returned instead of failing. The losing INSERT blocks until the winner commits,
    so the winner's list is there to find, and is locked for the caller to write.

    **If the winner's list is gone by the re-read** (a scan adopted it between the winner's commit
    and this look, so it is that scan's set and no longer a next list), the insert is tried once
    more from the client's latest set, which is now the adopted one. Raising there would be a 500,
    and on a detection run the paid outcome would be thrown away. A second refusal is a constraint
    that is genuinely wrong, and is raised.
    """
    for attempt in (1, 2):
        created = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET),
            client_id=client.id,
            competitors=[],
            **(_provenance(source) if source is not None else {}),
        )
        try:
            async with session.begin_nested():
                session.add(created)
                await session.flush()
        except IntegrityError:
            if created in session:
                session.expunge(created)
            winner = await next_list_for_client(session, client, lock=True)
            if winner is not None:
                logger.info(
                    "competitors.next_list.lost_race", client_id=client.id, adopted=winner.id
                )
                return winner
            if attempt == 2:
                raise
            source = await latest_set_for_client(session, client)
            logger.info(
                "competitors.next_list.retried_after_adoption",
                client_id=client.id,
                source_set_id=source.id if source is not None else None,
            )
            continue
        break
    if source is not None:
        _copy_rows(created, source)
        await session.flush()
    logger.info(
        "competitors.next_list.created",
        client_id=client.id,
        source_set_id=source.id if source is not None else None,
        competitors=len(created.competitors),
    )
    return created


async def adopt_next_list(
    session: AsyncSession, scan: Scan, client: Client
) -> CompetitorSet | None:
    """Make the client's next list `scan`'s own set, whole, if the client has one.

    The set is not copied: its `scan_id` becomes the scan's and its `client_id` NULL, in one
    UPDATE, so it is no longer the client's next list and a later save starts another. From that
    moment it is the scan's set and nothing writes it again.

    The row is locked first (`next_list_for_client`), so a save that is writing it finishes
    before this takes it, and one that arrives after waits and then finds it gone.

    `scan` must be `client`'s own: a next list is reachable only through its client, and handing
    it to another client's scan would be a way across that line.
    """
    # The scan's own client, in the scan's own agency: a scan row that names this client under
    # another agency's id takes nothing of the client's.
    if scan.client_id != client.id or scan.agency_id != client.agency_id:
        raise ValueError("a scan adopts only its own client's next list")
    next_list = await next_list_for_client(session, client, lock=True)
    if next_list is None:
        return None
    next_list.scan_id = scan.id
    next_list.client_id = None
    await session.flush()
    logger.info(
        "competitors.adopted",
        scan_id=scan.id,
        set_id=next_list.id,
        competitors=len(next_list.active_competitors),
    )
    return next_list


def _provenance(source: CompetitorSet) -> dict[str, Any]:
    """How `source` was made, to be recorded on a copy of it verbatim.

    Provenance is copied rather than re-stamped: it records what detection found when it ran, and
    a `detected_at` of now would claim a detection run that did not happen.
    """
    return {
        "status": source.status,
        "detection_confidence": source.detection_confidence,
        "serp_queries_run": source.serp_queries_run,
        "co_citation_prompts_run": source.co_citation_prompts_run,
        "candidates_considered": source.candidates_considered,
        "used_industry_seed": source.used_industry_seed,
        "detected_at": source.detected_at,
    }


def _copy_rows(copy: CompetitorSet, source: CompetitorSet) -> None:
    """Every row of `source` as a new row of `copy`, struck rivals' tombstones included.

    A rival's confirmed `other_sites` are copied with it (M1 task 1.1): the set a scan ran with
    is a frozen copy of the client's list, so they are carried to each new list from the one
    before.

    Appended through the relationship, not `session.add()`: the collection cascades delete-orphan,
    so a row attached only by foreign key would be deleted as an orphan on flush.
    """
    for row in source.competitors:
        copy.competitors.append(
            Competitor(
                id=ids.new_id(ids.COMPETITOR),
                competitor_set_id=copy.id,
                name=row.name,
                domain=row.domain,
                rank=row.rank,
                detection_source=row.detection_source,
                signal_count=row.signal_count,
                serp_mentions=row.serp_mentions,
                co_citation_mentions=row.co_citation_mentions,
                corroborated=row.corroborated,
                score=row.score,
                is_manual_override=row.is_manual_override,
                is_suppressed=row.is_suppressed,
                # A list of its own: the confirmed sites travel with the rival, and the copy's
                # list is not the source's, which a scan's set must never see change.
                other_sites=list(row.other_sites or []),
            )
        )


async def carry_forward_set(
    session: AsyncSession, scan: Scan, source: CompetitorSet
) -> CompetitorSet:
    """Copy a client's existing competitor set onto a new scan.

    WHY THIS EXISTS AT ALL, AND WHY IT IS NOT A CACHE
    -------------------------------------------------
    `CompetitorSet` hangs off a SCAN (`models/competitor.py`, `scan_id`), and
    `scan_runner.load_competitors` looks it up by `scan_id` and nothing else.
    So a client's second scan starts with no rivals unless something puts them
    there — and `scan_runner` is not defensive about that: it runs to
    SUCCEEDED with a set of one, reporting the subject at position 1 of 1 in
    every answer, with no competitor to compare against and no flag saying so.

    The two obvious alternatives are both worse:

    * **Skip detection because "the client already has a set"** — the shape the
      brief assumed. It leaves the new scan with nothing, because the set it
      is skipping on account of belongs to a different scan.
    * **Re-detect on every scan** — which spends six SerpApi searches against a
      250/month quota (≈41 scans a month platform-wide if every scan detected,
      250 ÷ 6; north-star.md §5.1 counts the same quota in new clients), and,
      worse, DISCARDS THE OPERATOR'S CORRECTION. `persist_detection` preserves
      manual overrides only within the set it is writing; a brand-new set on a
      brand-new scan has no manual rows to preserve, so the hand-corrected list
      would silently revert to whatever detection found this time.

    Copying is the only option that leaves a second scan measuring the same
    rivals as the first, which is also what makes two scans of one client
    comparable at all.

    **Suppressed tombstones are copied too.** They are how `persist_detection`
    knows not to re-offer a rival an operator struck; dropping them here would
    make the strike last exactly one scan.

    Provenance is copied verbatim rather than re-stamped: these rows record
    what detection found when it ran, and a `detected_at` of now would claim a
    detection run that did not happen.
    """
    copy = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET),
        scan_id=scan.id,
        competitors=[],
        **_provenance(source),
    )
    session.add(copy)
    await session.flush()
    _copy_rows(copy, source)
    await session.flush()
    logger.info(
        "competitors.carried_forward",
        scan_id=scan.id,
        source_set_id=source.id,
        competitors=len(copy.competitors),
    )
    return copy


async def ensure_set_for_scan(
    session: AsyncSession, scan: Scan, client: Client, *, settings: Settings | None = None
) -> tuple[CompetitorSet | None, str]:
    """Guarantee this scan has a competitor set before it is run.

    Returns `(set, how)` where `how` is one of `existing`, `adopted`, `carried_forward`
    or `detected` — so a caller can say which path a scan took, and in particular whether
    it spent SerpApi quota. In the order tried: the scan's own set, else the client's NEXT LIST
    (what a save or the button's detection wrote since the last scan, adopted whole:
    `adopt_next_list`), else a copy of the client's latest set, else detection.

    **From here on the set is the scan's, and never changes.** Everything the scan shows reads it
    by `scan_id`, and nothing writes a scan's set after this phase: a save and a detection run
    from the button write the next list, which a later scan takes.

    **Ordering: this must run BEFORE `scan_runner.run_scan`, not after.**
    `run_scan` reads the competitor list at its top and feeds it into two
    places that cannot be corrected afterwards — prompt generation is seeded
    with the rival names, and fact extraction scopes brand detection to them.
    A set written after the loop would be a set nothing ever used.

    Detection is the only branch that costs money, and it runs once per client
    rather than once per scan.
    """
    existing = (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan.id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()
    if existing is not None:
        # A scan that already has its own set (a scan run again, or one whose phase had already
        # run) keeps it, and the client's next list is left for the scan after.
        return existing, "existing"

    adopted = await adopt_next_list(session, scan, client)
    if adopted is not None:
        return adopted, "adopted"

    previous = await latest_set_for_client(session, client, excluding_scan_id=scan.id)
    if previous is not None:
        return await carry_forward_set(session, scan, previous), "carried_forward"

    outcome = await detect_for_client(client, settings=settings)
    return await persist_detection(session, scan, outcome), "detected"
