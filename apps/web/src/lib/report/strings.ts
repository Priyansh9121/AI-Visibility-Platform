/**
 * The report's string table — Epic 7.
 *
 * OUR OWN COPY, every word of it. The API returns machine codes
 * (`NO_FAQ_SCHEMA`, `NOT_YET_MEASURED`, `WEAK_SIGNAL`); this file is where they
 * become English. That split is deliberate and predates this epic: Epic 6 chose
 * to store `detail_code` rather than a sentence precisely so the human wording
 * would live in one place we control, be translatable, and be white-labelable.
 *
 * The no-borrowed-copy rule — no verbatim competitor marketing copy anywhere, including
 * microcopy. Nothing here is adapted from another product's UI. The wording is
 * written from the data model: each string says what the measurement found and
 * what changing it would do, in the plainest terms that are still accurate.
 *
 * Two rules the copy follows throughout:
 *
 *  1. **Absence is never scored as failure.** "We have not checked this yet"
 *     and "there was nothing to measure" are different sentences, because they
 *     are different facts about a client. Collapsing them would be a lie the
 *     scoring engine went out of its way not to tell.
 *  2. **No number is asserted that the data cannot support.** There is no
 *     revenue estimate, no traffic projection and no urgency language, because
 *     nothing upstream measures any of those.
 */

/** §6 dimension labels. Keys match `Score`'s DIMENSION_KEYS. */
export const DIMENSION_LABEL: Record<string, string> = {
  mention_rate: 'Mention Rate',
  share_of_voice: 'Share of Voice',
  citation_strength: 'Citation Strength',
  sentiment: 'Sentiment',
  technical_foundation: 'Technical Foundation',
};

/** What each dimension actually measures, in one line. */
export const DIMENSION_MEANING: Record<string, string> = {
  // Scoring v2: counts UNPROMPTED questions only. "named at all when a buyer
  // asks" described the retired definition, which also counted questions that
  // named the brand themselves — see scoring-spec.md's population section.
  mention_rate: 'How often the brand is named when a buyer asks without naming it first.',
  share_of_voice:
    'How much of the conversation the brand holds against its rivals, in those same unprompted answers.',
  // v3: authority of the sources cited alongside the brand — not "the brand's
  // own pages", which described the retired 1/N stand-in. v5: per answer that
  // names the brand, so how often it is named no longer raises it.
  citation_strength:
    'When answers name the brand, how well they back it with a trusted source, against the best-backed brand in the scan.',
  sentiment: 'How favourably the brand is described when it is named.',
  technical_foundation: 'How readable the site is to the systems that build those answers.',
};

/**
 * Why a dimension was left out, worded so a viewer can tell the reasons apart.
 *
 * api-contracts.md fixes these meanings; this is the client-facing rendering of
 * them. Epic 5's build log records why they are distinct codes rather than one
 * "excluded" flag: a capability we have not built yet is our gap, not the
 * client's, and must not read as a finding about them.
 */
/**
 * The audit's error code when robots.txt kept the crawler off the page —
 * 2026-09-29. `ROBOTS_DISALLOWED` in the API's `services/crawl.py`, which a
 * test reads back. Not a failure: the crawler obeys the file. The report and
 * the technical screen say so instead of "could not be read".
 */
export const ROBOTS_DISALLOWED = 'ROBOTS_DISALLOWED';

/**
 * The same, when robots.txt could not be read at all — 2026-10-03 (launch
 * checklist 3.41). `ROBOTS_UNREACHABLE` in the API's `services/crawl.py`, which a
 * test reads back. No answer, a name that does not resolve, a timeout, a 5xx, a
 * 429 or redirects that never reach a file: the crawler fetches nothing then, as
 * for a file that forbids it, but the SITE forbade nothing, so the screens must
 * not say it asks crawlers to stay out. Until this code every one of those cases
 * was stored as `ROBOTS_DISALLOWED`, and older rows still are.
 */
export const ROBOTS_UNREACHABLE = 'ROBOTS_UNREACHABLE';

/**
 * What happened when robots.txt could not be read, in one place for the intake
 * card and for `EXCLUSION_REASON.CRAWL_POLICY_UNREADABLE`, so they cannot word it
 * two ways. True of every case the code covers, which is why it says "did not get
 * a usable answer" and not "did not answer" (a 503 and a 429 are answers; so is a
 * redirect that never ends), why it says "of this site, or of an address the site
 * sent it to" (the file that could not be read can be a redirect's, or the one the
 * browser landed on), and why it says nothing of what the site asks of crawlers: the
 * file was never read. It blames nobody. Mirrored word for word in
 * `report_narrative.EXCLUSION_DETAIL`.
 */
export const ROBOTS_UNREACHABLE_SENTENCE =
  'The crawler tried to read the robots.txt of this site, or of an address the site sent it to, and did not get a usable answer. It obeys robots.txt, so without that answer it did not read the site.';

/**
 * What the person who just added the site can do about it: only the intake card says
 * this, because only there is someone who typed the address.
 */
export const ROBOTS_UNREACHABLE_ADVICE =
  'Check that the address is right and the site opens in a browser, then try again.';

/**
 * The reason code when the site's address is not a public one — a client's
 * `classificationReasonCode` and a failed audit's `errorCode` alike.
 * `ADDRESS_NOT_PUBLIC` in the API's `services/egress.py`. Not a robots.txt
 * matter and not an unreachable site: the crawler's egress guard refused it.
 */
export const ADDRESS_NOT_PUBLIC = 'ADDRESS_NOT_PUBLIC';

/** The client-facing sentence for `ADDRESS_NOT_PUBLIC` on a client's classification. */
export const ADDRESS_NOT_PUBLIC_SENTENCE =
  'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site.';

/**
 * One plain sentence per way the technical audit can fail, in place of the code
 * (2026-10-02). The codes are `services/technical_audit.py`'s: `BROWSER_ERROR`
 * (the browser failed, or the egress guard could not verify a connection),
 * `FETCH_FAILED` (the home page returned no response), `TIMEOUT` (the browser run
 * was cut at its time cap because a step never returned; a slow page load is
 * `BROWSER_ERROR`, not this), `BROWSER_BUSY` (the crawler's one browser was reading
 * another page for the whole wait, so a scan's audit never started; only a scan
 * records it, a request is refused instead), and `HTTP_<status>` (the home page
 * answered 400 or above). Each says what happened and makes no guess at why, and
 * none blames the site. The raw code is never shown; `ROBOTS_DISALLOWED`,
 * `ROBOTS_UNREACHABLE` and `ADDRESS_NOT_PUBLIC` have their own copy.
 */
export const AUDIT_FAILURE_SENTENCE: Record<string, string> = {
  BROWSER_ERROR: 'The crawler’s browser stopped before it could read the home page.',
  FETCH_FAILED: 'The crawler asked for the home page and got no response.',
  TIMEOUT: 'The crawler could not finish reading the home page in the time it allows.',
  BROWSER_BUSY: 'The crawler was busy with another page and could not start on this one in time.',
};

/** Any code this list does not know: no code, no guess at a cause. */
export const AUDIT_FAILURE_GENERIC = 'The crawler could not read the home page.';

/** The sentence for a failed audit's `errorCode`; never contains the code. */
export function auditFailureSentence(errorCode: string | null | undefined): string {
  if (errorCode && Object.hasOwn(AUDIT_FAILURE_SENTENCE, errorCode)) {
    return AUDIT_FAILURE_SENTENCE[errorCode]!;
  }
  const status = errorCode?.match(/^HTTP_([45]\d\d)$/)?.[1];
  if (status) {
    return `The site answered with an error (HTTP ${status}) when the crawler asked for its home page.`;
  }
  return AUDIT_FAILURE_GENERIC;
}

/** The intake card's heading when the crawler could not read the site. */
export const CLASSIFICATION_READ_HEADING = 'We could not read this site.';

/**
 * Its heading for everything else, which includes the model step failing: the crawler
 * had read the site then, so "could not read" would be untrue. "Classify" is true of
 * every way a client ends up `unclassifiable`.
 */
export const CLASSIFICATION_CLASSIFY_HEADING = 'We could not classify this site.';

/** What three of the model provider's refusals share: a problem on our side, and no more than that. */
const OUR_SIDE_SENTENCE =
  'We could not use the model that classifies sites because of a problem on our side, so nothing was classified.';

/**
 * One plain sentence per way a client is recorded `unclassifiable`, in place of the
 * code and of "This usually means the site did not load" (launch checklist 3.39). The
 * codes are the crawl's (`error_code` in `services/crawl.py`) and the classifier's own
 * (`reason_code` in `services/classify.py`); a test reads both files back, so a code
 * added there without wording here fails.
 *
 * Each sentence is true of every case that records its code, says what happened, and
 * blames nobody. A code the crawl records says what the crawler did; a code the model
 * step records says what that step did, and none of those says the site did not load.
 * Only `HTTP_<status>` says the site answered with an error, because it did.
 *
 * `TIMEOUT` is two failures under one code: the crawl's run cap, and the classifier's
 * own deadline on its call. Its sentence names both, so it is true of either.
 *
 * The audit's `TIMEOUT`, `BROWSER_ERROR` and `FETCH_FAILED` sentences are not reused
 * here, because each says something that is not true of every intake crawl. They speak
 * of "the home page" and of "no response", which holds for an audit (one page, read to
 * the end). The intake crawl reads up to three pages, so its run cap and its browser can
 * fail after the home page was read, its `BROWSER_ERROR` is also what a connection the
 * browser made but the crawler could not verify is recorded as (nothing is kept then),
 * and its `FETCH_FAILED` also covers a home page that answered but could not be read.
 * The sentences below say "the site" and "could not complete a read" instead. The
 * `HTTP_<status>` sentence is the audit's, as it describes the same thing: the home
 * page's answer.
 *
 * `PROVIDER_AUTH_FAILED`, `PROVIDER_QUOTA_EXHAUSTED` and `PROVIDER_BAD_REQUEST` share one
 * sentence. Each is a problem on our side (a key the provider refused, an account with no
 * credit or quota, a request it rejected), and which of the three it was is not something
 * the person who typed the address can act on or needs to read. They keep an entry each so
 * a test can read the API's codes back against them.
 *
 * `BROWSER_BUSY` has no entry on purpose. Since M1 a read that never got the crawler's
 * one browser writes nothing and answers 429 (`intake.run_classification` raises before
 * `classify` or `apply_outcome`), so no client carries it as its reason; wording it here
 * would be copy nothing can reach. If one ever did, it gets the generic sentence below,
 * which says nothing about the site.
 *
 * `ROBOTS_UNREACHABLE` and `ADDRESS_NOT_PUBLIC` keep the sentences they already had.
 * Older clients whose robots.txt could not be read are still stored as
 * `ROBOTS_DISALLOWED` (launch checklist 3.41), and read as that here too.
 */
const CLASSIFICATION_FAILURE: Record<string, { heading: string; sentence: string }> = {
  [ADDRESS_NOT_PUBLIC]: {
    heading: CLASSIFICATION_READ_HEADING,
    sentence: ADDRESS_NOT_PUBLIC_SENTENCE,
  },
  [ROBOTS_UNREACHABLE]: {
    heading: CLASSIFICATION_READ_HEADING,
    sentence: ROBOTS_UNREACHABLE_SENTENCE,
  },
  [ROBOTS_DISALLOWED]: {
    heading: CLASSIFICATION_READ_HEADING,
    sentence:
      'The robots.txt of this site, or of an address the site sent the crawler to, asks crawlers like ours not to load its home page. The crawler obeys robots.txt, so it did not read the site.',
  },
  BROWSER_ERROR: {
    heading: CLASSIFICATION_READ_HEADING,
    sentence: 'The crawler’s browser could not complete a read of the site.',
  },
  FETCH_FAILED: {
    heading: CLASSIFICATION_READ_HEADING,
    sentence: 'The crawler asked for the site’s home page and did not get a page it could read.',
  },
  TIMEOUT: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence:
      'Reading and classifying the site did not finish in the time we allow, so nothing was classified.',
  },
  INSUFFICIENT_CONTENT: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence:
      'The crawler read too little text from this site to tell what business it is, so nothing was classified.',
  },
  NOT_A_BUSINESS_SITE: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence:
      'The model found no operating business it could classify in what the crawler read, so nothing was classified.',
  },
  PROVIDER_AUTH_FAILED: { heading: CLASSIFICATION_CLASSIFY_HEADING, sentence: OUR_SIDE_SENTENCE },
  PROVIDER_RATE_LIMITED: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'The model’s provider asked us to slow down, so nothing was classified.',
  },
  PROVIDER_QUOTA_EXHAUSTED: { heading: CLASSIFICATION_CLASSIFY_HEADING, sentence: OUR_SIDE_SENTENCE },
  PROVIDER_BAD_REQUEST: { heading: CLASSIFICATION_CLASSIFY_HEADING, sentence: OUR_SIDE_SENTENCE },
  PROVIDER_UNREACHABLE: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'We could not reach the model’s provider, so nothing was classified.',
  },
  PROVIDER_ERROR: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'The request to the model’s provider failed, so nothing was classified.',
  },
  PROVIDER_SCHEMA_VIOLATION: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'The model answered, but not in the form we asked for, so nothing was classified.',
  },
  PROVIDER_REFUSED: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'The model declined to answer, so nothing was classified.',
  },
  PROVIDER_NO_OUTPUT: {
    heading: CLASSIFICATION_CLASSIFY_HEADING,
    sentence: 'The model did not return an answer we could read, so nothing was classified.',
  },
};

/** The codes `CLASSIFICATION_FAILURE` words, for the test that reads the API's source back. */
export const CLASSIFICATION_FAILURE_CODES: readonly string[] = Object.keys(CLASSIFICATION_FAILURE);

/** Any code this list does not know, or none: no code, no guess at a cause. */
export const CLASSIFICATION_FAILURE_GENERIC =
  'Nothing was classified, and we have no more specific reason to give.';

/**
 * What the intake card says of an `unclassifiable` client's reason code: a heading and
 * one sentence, neither containing the code. `HTTP_<status>` is the audit's sentence
 * (the crawler asked for the home page and the site answered with an error).
 */
export function classificationFailure(reasonCode: string | null | undefined): {
  heading: string;
  sentence: string;
} {
  if (reasonCode && Object.hasOwn(CLASSIFICATION_FAILURE, reasonCode)) {
    return CLASSIFICATION_FAILURE[reasonCode]!;
  }
  if (reasonCode && /^HTTP_[45]\d\d$/.test(reasonCode)) {
    return { heading: CLASSIFICATION_READ_HEADING, sentence: auditFailureSentence(reasonCode) };
  }
  return { heading: CLASSIFICATION_CLASSIFY_HEADING, sentence: CLASSIFICATION_FAILURE_GENERIC };
}

/**
 * How a `partial` technical audit is worded, on the report and on the client's
 * Technical screen alike: one place, so the two cannot drift.
 */
export const PARTIAL_AUDIT_LABEL = 'Partial audit';
export const PARTIAL_AUDIT_NOTE =
  'Some signals were unavailable, so the sub-score was computed from what could be measured.';

export const EXCLUSION_REASON: Record<string, { label: string; detail: string }> = {
  NOT_YET_MEASURED: {
    label: 'Not yet checked',
    detail:
      'This dimension has not been measured for this scan. It is left out of the score rather than counted as zero, so nothing here is held against the site for a check that never ran.',
  },
  NO_POPULATION: {
    label: 'Nothing to measure',
    detail:
      'There was no data to score this on — the brand was not named often enough for this measurement to mean anything. An absence is not a bad result, so it is excluded rather than scored zero.',
  },
  NO_COMPETITOR_SET: {
    label: 'No comparison was made',
    detail:
      'This scan’s competitor list was missing or had no rivals on it, so there is no rival to measure a share of voice or a citation comparison against. The dimension is excluded and its weight spread across the others, rather than awarding points for a comparison that could not be made.',
  },
  NO_AWARENESS_POPULATION: {
    label: 'Nothing unprompted to measure',
    detail:
      'Mention Rate counts only questions that did not name the brand — the ones that can show whether a buyer would discover it. This prompt set had none, so there was nothing to measure discovery with. It is left out and its weight spread across the others rather than scored as an absence the brand did not earn.',
  },
  NO_ANSWERED_RESULTS: {
    label: 'No answers came back',
    detail:
      'No engine returned an answer for this scan, so there is nothing to measure. This is a scan that did not run, not a brand that scored badly.',
  },
  // v2.1 — 2026-09-08. Not NOT_YET_MEASURED: that says a capability is on its
  // way; this says an input does not exist. Mirrored word for word in
  // `report_narrative.EXCLUSION_DETAIL`, so the PDF says the same.
  //
  // v3 — 2026-09-27. The dimension is measured now, so this fires only when a
  // scan's citations are all unclassified. The second tier's limitation is
  // stated here, to the reader, and not only in a docstring.
  //
  // 2026-10-03: the first tier's list says what `domain_authority`'s curated lists
  // hold — suffixes for government, military and education bodies, and the named
  // domains. The API's `tests/test_report_copy.py` fails when the two disagree, and
  // reaches this sentence through the shared fixture (`copyContract.test.ts`).
  NO_AUTHORITY_DATA: {
    label: 'No authority source',
    detail:
      'Citation Strength weighs the sources an answer cites when it names the brand, on two tiers: a short structural list — government, military and education domains; Wikipedia and Wikidata; the AP and Reuters wire services; and the cross-industry review platforms (Better Business Bureau, Trustpilot, G2, Capterra) — and the domains engines cite across at least three client industries and five clients on this platform, leaving out domains that are themselves brands in a scanned market. Nothing cited in this scan’s unprompted answers is on either tier, so there is no authority to measure and the dimension is left out with its weight spread across the others, rather than scored as zero. The second tier counts industries through an automatic classification whose confidence score is uncalibrated, so it is a coarse proxy that cannot screen out a mis-classified client; that is said here so the tier is not read as more precise than it is. The citations themselves are still shown in the evidence below.',
  },
  // 2026-09-29. The crawler obeys robots.txt now, and a site that forbids its
  // pages to crawlers like ours leaves Technical Foundation unmeasured. Its own
  // code, not NOT_YET_MEASURED: the audit ran and read robots.txt, and the
  // reason is the site's choice rather than our gap. Mirrored word for word in
  // `report_narrative.EXCLUSION_DETAIL`.
  CRAWL_DISALLOWED: {
    label: 'Blocked by robots.txt',
    detail:
      'This site’s robots.txt asks crawlers like ours not to load its pages, so its technical foundation wasn’t measured.',
  },
  // The crawler's egress guard refused the site's address. Mirrored word for
  // word in `report_narrative.EXCLUSION_LABEL` / `EXCLUSION_DETAIL`.
  CRAWL_ADDRESS_NOT_PUBLIC: {
    label: 'Address not public',
    detail:
      'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site. Technical Foundation is left out of the score.',
  },
  // 2026-10-03 (launch checklist 3.41). robots.txt could not be read, so the
  // crawler read nothing — and the site forbade nothing, which is why this is not
  // CRAWL_DISALLOWED. Mirrored word for word in `report_narrative`.
  CRAWL_POLICY_UNREADABLE: {
    label: 'Could not read robots.txt',
    detail: `${ROBOTS_UNREACHABLE_SENTENCE} Technical Foundation is left out of the score.`,
  },
  // 2026-10-05 (launch checklist 3.87). Any failed audit the three above do not name: a
  // timeout, a browser that was busy (the check never started) or broke, a page that did
  // not respond, an error status (the page did answer). The words claim only what is true
  // of all of them, and blame no one: the check could not be completed. Not
  // NOT_YET_MEASURED: an audit exists and failed. Mirrored word for word in
  // `report_narrative.EXCLUSION_LABEL` / `EXCLUSION_DETAIL`.
  AUDIT_FAILED: {
    label: 'Technical check failed',
    detail:
      'The technical check of the site could not be completed for this scan. Technical Foundation is left out of the score.',
  },
};

/**
 * What a MEASURED dimension is measured from, where a reader needs telling.
 *
 * Only Citation Strength has one: the other four say what they are in their
 * label. Shown under the score on screen and under the score table in the
 * PDF (`report_narrative.DIMENSION_NOTE`, word for word), so a three-tier
 * proxy is never presented as more precise than it is — the second tier is
 * counted through an industry classification whose confidence score is
 * uncalibrated (build-log Epic 2.6/2.8, Finding 2, still open), and the
 * sentence says so.
 *
 * At most 45 words since 2026-09-29, so a client can read it at a glance:
 * the question the dimension answers, which answers it counts, and the
 * proxy's limit. The tier list and the market-participant rule are in
 * `EXCLUSION_REASON.NO_AUTHORITY_DATA` and in scoring-spec.md.
 */
export const DIMENSION_NOTE: Record<string, string> = {
  citation_strength:
    'When AI names the brand, how well does it back it with a trusted source? It counts only answers to questions that did not name the brand. Some sources qualify as trusted through uncalibrated industry labels — a coarse proxy that a mis-classified client can skew.',
};

/**
 * Findings about the SUBJECT, kept apart from the degradation flags below.
 *
 * Those say why a NUMBER is rougher than it would otherwise be. These say what
 * a scan found. Rendering them in one list would repeat the category error this
 * project already refused when it kept `NOT_YET_MEASURED` apart from
 * `NO_POPULATION`.
 *
 * **Worded so a narrow positive cannot read as a broad one.** "Appears when
 * asked about by name" is not visibility, and the obvious framing —
 * "answerable, just not discoverable" — would claim recognition the data does
 * not support: a mention is a text match, and an engine answering "I have no
 * knowledge of this brand" is recorded as naming it. So the copy says only
 * where the mentions came from.
 */
export const VISIBILITY_FLAG: Record<string, string> = {
  NAMED_ONLY_WHEN_PROMPTED:
    'Every answer that named this brand was answering a question that named it first. No question asked without the name produced a mention. A buyer who already knows the name gets a reply; a buyer who does not is never shown the brand — and being named back by a question that supplied the name is not evidence an engine knows it.',
};

/**
 * Average position — reported, not scored (v4). Mirrored word for word in
 * `report_narrative.POSITION_COPY`. `note` is the one line the report prints
 * beside the figure; `not_named` is the cell for a brand never named on an
 * unprompted question, which is NEVER rendered as 0 (ahead of first) or as a
 * large number (a position it did not hold).
 */
export const POSITION_COPY: Record<string, string> = {
  heading: 'Average position',
  not_named: 'Not named',
  note:
    'Average position is where the brand appeared among the brands an answer named, averaged over unprompted answers that named it, with the number of answers that is. It is shown for comparison and does not enter the score: a mean over a handful of answers moves a lot between runs — on re-runs of the same scan, rivals named in under twenty-five answers moved by up to six-tenths of a position — so it is a figure to read beside the score, not part of it.',
};

/** `2.4 across 9 answers`, or the not-named cell — the same on both sides. */
export function positionCell(average: string | null | undefined, count: number | undefined): string {
  const parsed = average === null || average === undefined ? NaN : Number.parseFloat(average);
  const n = count ?? 0;
  if (!Number.isFinite(parsed) || n === 0) return POSITION_COPY['not_named']!;
  return `${parsed.toFixed(1)} across ${n} ${n === 1 ? 'answer' : 'answers'}`;
}

/** Why a number is lower or rougher than it would otherwise be. */
export const DEGRADATION_FLAG: Record<string, string> = {
  // Carried only by scores computed before v2.1. The sentence now describes
  // what that formula actually did; its predecessor claimed a comparison
  // against the best-cited brand that the arithmetic never made.
  NO_AUTHORITY_DATA:
    'Citation Strength was measured as whether the brand’s own domain was cited, against every domain the engines cited, because this system has no external authority source. That measure cannot rise above about a point for any site, and later scores leave the dimension out instead.',
  WEAK_COMPETITOR_SET:
    'The competitor set was only weakly corroborated — some rivals were surfaced by a single signal. Share of Voice should be read as indicative rather than exact.',
  NO_CITATIONS_IN_SCAN: 'No engine cited any source in this scan, for any brand.',
  NO_COMPETITOR_SET: 'No competitors were on the list, so no share comparison was possible.',
  TECHNICAL_FOUNDATION_NOT_MEASURED:
    'The site had not been audited when this score was computed.',
  NO_ANSWERED_RESULTS: 'No engine returned an answer for this scan.',
  NO_AWARENESS_POPULATION:
    'This prompt set contained no unprompted questions, which are the only ones Mention Rate and Share of Voice count. Both were left out of the score rather than measured on questions that named the brand.',
  // Emitted by scoring.py alongside the `NO_POPULATION` exclusion, and missing
  // from this table until 2026-09-08 — so a scan where the brand was never
  // named rendered the raw code to a client. Found by the coverage test below.
  NO_SENTIMENT_POPULATION:
    'The brand was not named often enough for sentiment to mean anything, so that dimension was left out of the score rather than counted as a bad result.',
};

export interface DetectionCopy {
  label: string;
  detail: string;
}

/**
 * Competitor-detection quality, stated plainly rather than as a status code.
 *
 * Keyed by the exact `DetectionStatus` members rather than by `string`, so
 * `DETECTION_STATUS.no_signal` is a known value and a status the API adds later
 * is a compile error here rather than an undefined at render time.
 */
export const DETECTION_STATUS: Record<'ok' | 'weak_signal' | 'no_signal', DetectionCopy> = {
  ok: {
    label: 'Corroborated',
    detail: 'Search results and AI answers independently surfaced this set of rivals.',
  },
  weak_signal: {
    label: 'Weakly corroborated',
    detail:
      'Few candidates surfaced and the two detection signals largely disagreed. Treat this rival set as a starting point.',
  },
  // True of every empty list: detection that found nothing, and a list emptied by hand through
  // the report's editor, are both stored as `no_signal` and nothing tells them apart.
  no_signal: {
    label: 'No rivals on the list',
    detail:
      'This scan’s competitor list has no rivals, so no comparison is shown. Either search results and AI answers surfaced no usable set, or the list was emptied by hand. Neither is a finding about the brand.',
  },
};

/**
 * What the report's competitor editor says. Each sentence is true of the code (launch checklist
 * 3.78, 3.79): a save is written to the client's NEXT LIST, which the next scan that starts adopts
 * (`ensure_set_for_scan`), and a scan that has started keeps the list it started with, so a save
 * moves nothing the scan on screen shows: its list, its score, its figures, its client-history
 * point, an explicit re-score. A scan that has not started yet (queued) takes the saved list when
 * it runs; a save is refused while one is running (`409`). A list saved empty is carried forward
 * by later scans like any other.
 *
 * The `...Running` and `...Finished` forms differ only for a scan still queued or running, which
 * a saved list can still reach: for a finished scan the plain sentence "this scan's list and
 * score do not change" is the true one. It says "score", not "figures": a rival's figures are
 * recomputed on read with today's platform tiers, and a rival named in a next list is a market
 * participant for them, so one of those can move.
 *
 * A save is refused while a scan of the CLIENT is running (`409`), whichever scan's report is on
 * screen, so every form says so, and the forms for a scan still queued or running lead with it.
 * The `...Saved` forms are what the closed editor says under the status line a save just printed,
 * so that "does not change" is said once.
 */
const SCAN_UNCHANGED = 'This scan’s list and score do not change.';
const NOT_WHILE_RUNNING = 'A list cannot be saved while a scan of this client is running.';
const NAMING_FOR_LATER = 'Rivals you name here are saved for later scans, which compare against them.';
const CORRECT_FOR_LATER =
  'A list you correct here is saved for later scans, and a rival you add or keep by hand stays in place on them.';

export const COMPETITOR_EDITOR_COPY = {
  openButton: 'Name the rivals',
  openHeading: 'Name the competitors',
  missingLead: 'No competitor list is recorded for this scan.',
  emptyLead:
    'This scan’s competitor list has no rivals: none were found, or the list was emptied by hand.',
  // After the lead, for a list with no rivals or none at all, on a scan that has started.
  namingTail: `${NAMING_FOR_LATER} ${SCAN_UNCHANGED} ${NOT_WHILE_RUNNING}`,
  // The same, under the status line a save just printed (which says the scan does not change).
  namingTailSaved: `${NAMING_FOR_LATER} ${NOT_WHILE_RUNNING}`,
  // The same, for a scan still queued or running.
  namingTailRunning: `${NOT_WHILE_RUNNING} Rivals you name here are saved as the client’s list, and later scans compare against them. A scan that has not started yet uses the list when it runs.`,
  // Under a list that has rivals.
  correctLead: 'Detection is automated and imperfect.',
  correctTail: `${CORRECT_FOR_LATER} ${SCAN_UNCHANGED} ${NOT_WHILE_RUNNING}`,
  correctTailSaved: `${CORRECT_FOR_LATER} ${NOT_WHILE_RUNNING}`,
  correctTailRunning: `${NOT_WHILE_RUNNING} A list you correct here is saved as the client’s list, and a rival you add or keep by hand stays in place on later scans. A scan that has not started yet uses the list when it runs.`,
  reading: 'Reading the client’s current list…',
  differsFromScan:
    'The client’s current list, which later scans use, differs from this scan’s. Saving replaces the current list.',
  noneYet: 'No competitors named yet. Add the first below.',
  emptiedStaysEmpty:
    'No competitors on the list. A list saved empty stays empty: later scans carry it forward, and compare against no rivals until some are named here.',
  // In the open editor: what saving does to the list, then what it does to this scan.
  openNote:
    'Saving replaces the client’s whole list and marks every entry on it as set by hand, including ones you did not change. The corroboration figure is cleared with it, because it measures agreement between two automated signals and neither produced this list.',
  openNoteFinished: `The list is saved for later scans. ${SCAN_UNCHANGED} ${NOT_WHILE_RUNNING}`,
  openNoteRunning: `${NOT_WHILE_RUNNING} The list is saved as the client’s list. A scan that has not started yet uses it when it runs.`,
  // After a save, in the editor's status line. The scan's own card does not change.
  saved: `Saved for later scans. ${SCAN_UNCHANGED}`,
  savedRunning: 'Saved as the client’s list. A scan that has not started yet uses it when it runs.',
  // Beside the sentence for a save refused because the list changed after the editor opened
  // (`lib/competitorListChange`): it puts the list as it stands in the editor, in place of the
  // editor's own edits.
  loadCurrent: 'Load the current list',
  // Said with that sentence, in the same alert, before the control is pressed: the button's label
  // stays short (it does not wrap), so the warning is the text beside it.
  loadReplaces: 'Loading the current list replaces your edits.',
  // Said, in a status region that is already on the page, once the control has put the list in.
  loadedCurrent: 'Showing the current list.',
} as const;

/** A scan with no competitor set at all, which is not the same as an empty one. */
export const NO_COMPETITOR_LIST: DetectionCopy = {
  label: 'No competitor list for this scan',
  detail:
    'This scan has no competitor list, so no comparison is shown. That is a limit of the data, not a finding about the brand.',
};

export const ENGINE_LABEL: Record<string, string> = {
  chatgpt: 'ChatGPT',
  perplexity: 'Perplexity',
  google_ai_overview: 'Google AI Overviews',
  gemini: 'Gemini',
  claude: 'Claude',
  claude_search: 'Claude with web search',
  copilot: 'Copilot',
};

export interface FixCopy {
  /** The change, stated as an instruction. */
  title: string;
  /** What it does for the measurement, in one sentence. */
  detail: string;
  effort: 'S' | 'M' | 'L';
}

/**
 * Audit `detail_code` → a specific, named change.
 *
 * Deliberately concrete. "Improve your structured data" is not a fix; "add FAQ
 * schema to the pages that answer buyer questions" is one, because someone can
 * be handed it and know what to open. §7 Epic 8's acceptance criterion is that
 * a fix list "correctly names those gaps with actionable language" — this table
 * is the deterministic floor that already meets it, before Epic 8's LLM pass
 * enriches it.
 *
 * Four entries say only what the audit read — launch checklist 3.49, 2026-10-03. They
 * used to say more, and each was untrue of a case that carries its code:
 *
 *  - `ROBOTS_TXT_DISALLOW`: "nothing else on this list can take effect", and a title that
 *    named "the crawlers that build AI answers". The check reads one robots.txt group, the
 *    one for all crawlers, for a `Disallow: /` line (`technical_audit._fetch_side_files`);
 *    it ignores Allow lines and every named crawler's group, so a crawler may be let in,
 *    or an AI crawler may have a group of its own, and the finding still stands. The code
 *    reaches this list on a read audit, where the crawler was let in despite the rule, and
 *    on one where robots.txt kept the crawler off the page (`_robots_refused_checks`), so
 *    the sentences say what the rule asks and never whether our crawler got in. The title
 *    names the `User-agent: *` group and not "all crawlers": a crawler with a group of its
 *    own ignores the `*` group (RFC 9309), so "the rules for all crawlers" was not strictly
 *    true either.
 *  - `NO_DATE_SIGNAL_AVAILABLE`: "anywhere on the page". The audit reads the page's
 *    Last-Modified header and the datePublished and dateModified in its JSON-LD, and no
 *    date printed on the page.
 *  - `CONTENT_STALE`: "publish a visible updated date", and "Freshness is one of the few
 *    signals available for judging whether an answer is still current". The audit takes the
 *    newest of the page's Last-Modified header and the datePublished and dateModified in its
 *    JSON-LD (`audit_site`), and calls the page stale when that is more than 180 days old
 *    (`build_checks`: a warning to 365 days, a failure after). A date printed on the page is
 *    not read, so publishing one would not clear the finding; and nothing the audit measures
 *    supports a claim about which signals are available for judging an answer.
 *  - `NO_ORGANIZATION_OR_LOCALBUSINESS`: "Nothing on the page states … what this
 *    organisation is". The check matches eight schema.org type names
 *    (`technical_audit.TRACKED_SCHEMA`), so a page marked up as `ProfessionalService` does
 *    state what it is and still fails. The sentence that said what the markup is for went
 *    too: nothing the audit measures supports it.
 *
 * `report_narrative.py` carries the same four, word for word, and the PDF prints that
 * copy. No shared fixture holds these codes (`report-copy.json` has no detail-code table
 * and `report-cases.json` carries only `NO_FAQ_SCHEMA` and `NO_PRODUCT_OR_SERVICE_SCHEMA`),
 * so what holds the two together is `auditFixWording.test.ts` here, which reads the API's
 * table back and compares it with this one, and `test_audit_fix_wording.py` there, which
 * pins the text and runs each sentence's claims against the audit.
 */
export const FIX_FOR_DETAIL_CODE: Record<string, FixCopy> = {
  ROBOTS_TXT_DISALLOW: {
    title: 'Remove Disallow: / from the User-agent: * rules in robots.txt',
    detail:
      'The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt. That rule asks crawlers that have no rule of their own to stay out of the whole site, except where another rule lets them in. A crawler that obeys it does not read the pages it covers. Removing it, or narrowing it to the paths that should stay private, clears this finding.',
    effort: 'S',
  },
  META_ROBOTS_NOINDEX: {
    title: 'Remove the noindex directive from pages that should be found',
    detail:
      'A page marked noindex is excluded from the indexes these answers are drawn from, however good its content is.',
    effort: 'S',
  },
  NO_ROBOTS_TXT: {
    title: 'Publish a robots.txt',
    detail:
      'Crawlers currently get no guidance about the site. A robots.txt that names the sitemap is the cheapest way to give them one.',
    effort: 'S',
  },
  NO_SITEMAP_XML: {
    title: 'Publish an XML sitemap and reference it from robots.txt',
    detail:
      'A sitemap is how a crawler discovers pages that are not linked from the homepage. Without one, deeper pages may never be read.',
    effort: 'S',
  },
  NO_CANONICAL_LINK: {
    title: 'Add canonical links so duplicate URLs consolidate',
    detail:
      'Without a canonical, the same page reachable at several URLs splits its own signals between them.',
    effort: 'S',
  },
  NO_STRUCTURED_DATA: {
    title: 'Add schema.org structured data to the main pages',
    detail:
      'There is no structured data on the audited page. Structured data is how a machine reads what the business is and what it sells, rather than inferring it from prose.',
    effort: 'M',
  },
  NO_ORGANIZATION_OR_LOCALBUSINESS: {
    title: 'Add Organization or LocalBusiness schema to the homepage',
    detail:
      'The audit found none of Organization, Corporation, OnlineBusiness, LocalBusiness, Dentist, Restaurant, Store or MedicalBusiness in the audited page’s JSON-LD or microdata. Adding Organization or LocalBusiness markup to the homepage clears this finding.',
    effort: 'S',
  },
  NO_FAQ_SCHEMA: {
    title: 'Add FAQPage schema to the pages that answer buyer questions',
    detail:
      'Buyers reach these engines by asking questions. FAQ markup pairs a question with its answer explicitly, which is the form an answer engine can quote and cite directly.',
    effort: 'M',
  },
  NO_PRODUCT_OR_SERVICE_SCHEMA: {
    title: 'Add Product or Service schema to the offering pages',
    detail:
      'Without it, what the business actually sells has to be inferred from page copy rather than read from the markup.',
    effort: 'M',
  },
  NO_TITLE: {
    title: 'Give the page a title element',
    detail: 'The title is the shortest statement of what a page is. This one has none.',
    effort: 'S',
  },
  NO_META_DESCRIPTION: {
    title: 'Add a meta description to the key pages',
    detail:
      'A missing description leaves the summary of the page to be generated from whatever text happens to be near the top.',
    effort: 'S',
  },
  NO_OPEN_GRAPH_TAGS: {
    title: 'Add Open Graph tags',
    detail:
      'Open Graph tags are what most systems read when they need a page’s identity in a compact form.',
    effort: 'S',
  },
  NO_H1: {
    title: 'Add a single H1 that states what the page is about',
    detail: 'The page has no top-level heading, so its subject has to be inferred.',
    effort: 'S',
  },
  MULTIPLE_H1: {
    title: 'Reduce the page to one H1',
    detail:
      'Several competing top-level headings leave the page’s actual subject ambiguous to anything parsing it.',
    effort: 'S',
  },
  CONTENT_STALE: {
    title: 'Refresh the audited page and publish an updated dateModified in its JSON-LD',
    detail:
      'The newest date the audit read from the audited page, in its Last-Modified header or in a datePublished or dateModified in its JSON-LD, is more than 180 days old. Dates printed on the page are not read. Once the page is refreshed, a dateModified from the last 180 days in the JSON-LD (YYYY-MM-DD), or a Last-Modified header that recent, clears this finding.',
    effort: 'M',
  },
  NO_DATE_SIGNAL_AVAILABLE: {
    title: 'Publish machine-readable dates on the main pages',
    detail:
      'The audit read no date from the audited page’s Last-Modified header or from a datePublished or dateModified in its JSON-LD, so it cannot tell whether the page is fresh or stale. Dates printed on the page are not read. A dateModified in the JSON-LD (YYYY-MM-DD), or a Last-Modified header from the server, gives the audit a date to read.',
    effort: 'S',
  },
};

/**
 * Fixes derived from a scoring dimension rather than an audit check.
 *
 * These are the "the number is low, and here is the lever" fixes. Each names
 * the mechanism the sub-score actually measures, so the recommendation and the
 * measurement cannot drift apart.
 */
export const FIX_FOR_DIMENSION: Record<string, FixCopy> = {
  mention_rate: {
    title: 'Publish pages that answer the questions buyers actually ask',
    detail:
      'The brand is missing from answers to prompts in its own category. The lever is coverage: a page that directly answers a buying question is a page an engine can name you from.',
    effort: 'L',
  },
  share_of_voice: {
    title: 'Compete on the comparisons where rivals currently appear alone',
    detail:
      'Rivals are named more often than the brand in the same answers. Comparison and alternatives pages are the surfaces those answers are assembled from.',
    effort: 'L',
  },
  // v3: the dimension is the authority of the sources cited alongside the
  // brand, so the lever is presence on the sources engines already treat as
  // authorities — not the brand's own pages, which was the retired reading.
  citation_strength: {
    title: 'Get onto the sources that answers already cite as authorities',
    detail:
      'Answers that name the brand draw on sources with less authority than those that name the best-placed rival. The lever is presence on the review platforms and reference sources engines already cite — a current listing on G2, Capterra and Trustpilot, a Better Business Bureau profile, a Wikipedia entry where one is warranted — and coverage by the sources engines reach for across every industry.',
    effort: 'M',
  },
  sentiment: {
    title: 'Address how the brand is characterised where it is described unfavourably',
    detail:
      'Where the brand is named, the surrounding characterisation is not consistently positive. The lever is the third-party material those answers draw on — reviews, comparisons and documentation.',
    effort: 'M',
  },
  technical_foundation: {
    title: 'Fix the structural signals that make the site machine-readable',
    detail:
      'Structured data, crawlability and freshness are the inputs this dimension measures, and each is a concrete change rather than a judgement call.',
    effort: 'M',
  },
};

/**
 * The unclaimed-domain fix — Epic 7.1, Direction C.
 *
 * A function rather than a table entry, because the whole value of this
 * recommendation is that it NAMES the domain. "Get cited by more third-party
 * sources" is the abstract version and it is worth nothing; "answers about you
 * cite eesel.ai six times and you nowhere — that is the page to go get onto"
 * is the version an agency can sell and a client can act on.
 *
 * Everything interpolated is a fact: a domain, a count, and the subject's own
 * citation count. Nothing describes what is ON the domain, which would be
 * republishing someone else's content (the facts-only rule) — and we have never
 * read it in any case.
 */
export function fixForUnclaimedDomains(
  domains: readonly { domain: string; citations: number }[],
  subjectCitations: number,
): FixCopy | null {
  const [heaviest, ...rest] = domains;
  if (!heaviest) return null;

  const others = rest.length > 0 ? ` The same is true of ${listDomains(rest)}.` : '';
  const standing =
    subjectCitations === 0
      ? 'Nothing on your own site was cited at all.'
      : subjectCitations < heaviest.citations
        ? `Your own pages were cited ${subjectCitations} ${plural(subjectCitations, 'time')} across the same answers.`
        : '';

  return {
    title: `Get onto ${heaviest.domain} — the source these answers keep citing`,
    detail:
      `${heaviest.domain} was cited ${heaviest.citations} ${plural(heaviest.citations, 'time')} ` +
      `and belongs to neither you nor any rival in this scan.${others} ` +
      `${standing} A page on a source an engine already trusts is the shortest route into the ` +
      `answer, because the engine does not have to start trusting a new domain to use it.`.trim(),
    effort: 'M',
  };
}

function listDomains(rows: readonly { domain: string }[]): string {
  const names = rows.map((r) => r.domain);
  if (names.length === 1) return names[0]!;
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

function plural(n: number, word: string): string {
  return n === 1 ? word : `${word}s`;
}

/** Audit-check labels, for the evidence rows in the proof beat. */
export const CHECK_LABEL: Record<string, string> = {
  site_reachable: 'Site reachable',
  indexable: 'Indexable',
  robots_txt_present: 'robots.txt present',
  sitemap_present: 'XML sitemap present',
  canonical_present: 'Canonical link present',
  schema_present: 'Structured data present',
  schema_business_entity: 'Organization / LocalBusiness schema',
  schema_faq: 'FAQ schema',
  schema_product_or_service: 'Product / Service schema',
  meta_title: 'Title element',
  meta_description: 'Meta description',
  open_graph_tags: 'Open Graph tags',
  single_h1: 'Single H1',
  content_freshness: 'Content freshness',
  cwv_lcp: 'Largest Contentful Paint',
  cwv_cls: 'Cumulative Layout Shift',
  cwv_inp: 'Interaction to Next Paint',
};

export function dimensionLabel(key: string): string {
  return DIMENSION_LABEL[key] ?? key;
}

export function engineLabel(key: string): string {
  return ENGINE_LABEL[key] ?? key;
}

export function checkLabel(key: string): string {
  return CHECK_LABEL[key] ?? key;
}
