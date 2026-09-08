/**
 * The report's string table — Epic 7.
 *
 * OUR OWN COPY, every word of it. The API returns machine codes
 * (`NO_FAQ_SCHEMA`, `NOT_YET_MEASURED`, `WEAK_SIGNAL`); this file is where they
 * become English. That split is deliberate and predates this epic: Epic 6 chose
 * to store `detail_code` rather than a sentence precisely so the human wording
 * would live in one place we control, be translatable, and be white-labelable.
 *
 * ip-safety.md #8 — no verbatim competitor marketing copy anywhere, including
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
  citation_strength: 'How often the brand’s own pages are the source an answer cites.',
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
      'No competitor set was detected for this scan, so there is nothing to measure a share against. The dimension is excluded and its weight spread across the others, rather than awarding points for a detection that did not happen.',
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

/** Why a number is lower or rougher than it would otherwise be. */
export const DEGRADATION_FLAG: Record<string, string> = {
  NO_AUTHORITY_DATA:
    'Citation Strength is measured against the best-cited brand in this scan rather than against an external authority ranking, which this system does not have a source for.',
  WEAK_COMPETITOR_SET:
    'The competitor set was only weakly corroborated — some rivals were surfaced by a single signal. Share of Voice should be read as indicative rather than exact.',
  NO_CITATIONS_IN_SCAN: 'No engine cited any source in this scan, for any brand.',
  NO_COMPETITOR_SET: 'No competitors were detected, so no share comparison was possible.',
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
      'Few candidates surfaced and the two detection signals largely disagreed. Treat this rival set as a starting point and correct it before sending the report on.',
  },
  no_signal: {
    label: 'No rivals detected',
    detail:
      'Neither search results nor AI answers surfaced a usable competitor set, so no comparison is shown. That is a limit of the detection, not a finding about the brand.',
  },
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
 */
export const FIX_FOR_DETAIL_CODE: Record<string, FixCopy> = {
  ROBOTS_TXT_DISALLOW: {
    title: 'Stop robots.txt from blocking the crawlers that build AI answers',
    detail:
      'The site currently tells crawlers to stay out. Until that is lifted, nothing else on this list can take effect — an answer engine cannot cite a page it is not allowed to read.',
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
      'Nothing on the page states, in machine-readable form, what this organisation is. This is the entity marker the rest of the structured data hangs off.',
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
    title: 'Refresh the audited page and publish a visible updated date',
    detail:
      'The most recent date signal on this page is old. Freshness is one of the few signals available for judging whether an answer is still current.',
    effort: 'M',
  },
  NO_DATE_SIGNAL_AVAILABLE: {
    title: 'Publish machine-readable dates on the main pages',
    detail:
      'No date could be found anywhere on the page, so its freshness cannot be established either way. A dateModified in the structured data fixes this.',
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
  citation_strength: {
    title: 'Make the site the source an answer cites, not just a name it mentions',
    detail:
      'The brand is named more often than its own pages are cited. Citation follows structure: pages that state a claim plainly, mark it up, and are crawlable get quoted; pages that bury it in prose get paraphrased without attribution.',
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
 * republishing someone else's content (ip-safety.md #7) — and we have never
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
