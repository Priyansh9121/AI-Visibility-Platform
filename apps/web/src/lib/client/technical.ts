/**
 * Turning a stored technical audit into what the Technical screen draws.
 *
 * Pure, and separated from the view for the reason `lib/client/trends.ts` is:
 * the interesting behaviour is arithmetic over a shape that can be partly
 * missing, and that is asserted directly rather than through a render.
 */

import type { AuditCheck, TechnicalAudit } from '@avp/shared-types';
import type { LedgerDimension } from '@avp/design-system';
import { FIX_FOR_DETAIL_CODE, ROBOTS_DISALLOWED, ROBOTS_UNREACHABLE } from '@/lib/report/strings';

/**
 * The four components in the order the audit weights them, heaviest first.
 *
 * Mirrors `COMPONENT_WEIGHTS` in `services/technical_audit.py`. The ORDER is
 * this file's own choice — heaviest first, so the Ledger stacks the way the
 * report's does — but the keys and the labels are the API's.
 */
export const COMPONENT_ORDER: readonly string[] = [
  'indexation',
  'content_freshness',
  'structured_data',
  'schema_presence',
];

export const COMPONENT_LABEL: Record<string, string> = {
  indexation: 'Indexation',
  content_freshness: 'Content Freshness',
  structured_data: 'Structured Data',
  schema_presence: 'Schema Presence',
};

/**
 * Why a component could not be measured, in the words a person would use.
 *
 * The API sends a machine code; this is the only place it becomes prose, the
 * same split `EXCLUSION_REASON` uses on the report.
 */
export const EXCLUSION_COPY: Record<string, string> = {
  NO_DATE_SIGNAL_AVAILABLE:
    'The site publishes no date signal at all — no Last-Modified header and no dated structured data. Scoring that as stale would punish a publishing convention rather than a visibility problem, so its weight was shared across the components that could be measured.',
};

/**
 * What a check's code says on a row of this screen, where the fix list's wording does
 * not do — launch checklist 3.39. Looked up BEFORE `FIX_FOR_DETAIL_CODE` (below).
 *
 * Two kinds of code are here.
 *
 * Codes no fix is worded for. The four Core Web Vitals codes are the lab and field ones:
 * `LAB_MEASUREMENT_NOT_FIELD_DATA` rides on a measured LCP or CLS (a pass, a warning or a
 * failure alike); the two `*_NOT_OBSERVED` codes and `FIELD_METRIC_REQUIRES_REAL_USER_DATA`
 * ride on a check that did not apply. Each sentence is true of every check that carries
 * its code: the vitals are read once, on one load of the page, by a browser that never
 * clicks; a vital is absent when the crawler recorded none, whatever the reason.
 * `ROBOTS_DISALLOWED` and `ROBOTS_UNREACHABLE` are here because a check on a read audit
 * can carry them: the sitemap is asked for only if robots.txt allows it, and when the file
 * that decides it forbids it, or could not be read, that check does not apply
 * (`technical_audit._robots_file_checks`). The file is the site's or, after a redirect,
 * the host it led to, so the sentence says "the robots.txt this depends on". "This" is
 * whatever the row beside it names.
 *
 * Codes the fix list words, where that wording, standing alone on a row as the statement
 * of what the check found, says more than the check found or points at something that is
 * not on the row: "nothing else on this list" (`ROBOTS_TXT_DISALLOW`), "excluded from the
 * indexes these answers are drawn from" (`META_ROBOTS_NOINDEX`), "Without it"
 * (`NO_PRODUCT_OR_SERVICE_SCHEMA`), "anywhere on the page" (`NO_DATE_SIGNAL_AVAILABLE`,
 * which looks at the Last-Modified header and the structured data, not the page's text),
 * "Several competing" (`MULTIPLE_H1`, which is two or more) and "Nothing on the page
 * states … what this organisation is" (`NO_ORGANIZATION_OR_LOCALBUSINESS`, which looks
 * for eight type names only: `technical_audit.TRACKED_SCHEMA`, so `ProfessionalService`
 * or `Plumber` markup fails it). The fix table is left as it is: the report's fix beat
 * and the PDF (`report_narrative.py`) share it. Those six say what the check found, in
 * this screen's own words.
 */
export const CHECK_DETAIL_SENTENCE: Readonly<Record<string, string>> = {
  LCP_NOT_OBSERVED:
    'The crawler did not record a Largest Contentful Paint for this page, so there is no figure.',
  CLS_NOT_OBSERVED:
    'The crawler did not record a Cumulative Layout Shift for this page, so there is no figure.',
  LAB_MEASUREMENT_NOT_FIELD_DATA:
    'Measured by the crawler on a single load of the page, not from real visitors.',
  FIELD_METRIC_REQUIRES_REAL_USER_DATA:
    'This is measured from real visitors tapping and clicking. The crawler never interacts with a page, so it cannot measure it.',
  [ROBOTS_DISALLOWED]:
    'The robots.txt this depends on asks crawlers like ours not to load this, so it was not checked.',
  [ROBOTS_UNREACHABLE]:
    'The crawler could not get a usable answer from the robots.txt this depends on, so it was not checked.',
  // The fix list's six whose wording, alone on a row, says more than the check found.
  // `ROBOTS_TXT_DISALLOW` reaches a row only on an audit that was read, so AVPScanBot was let
  // in by its own group or an Allow: the rule is under `*` (`technical_audit.py`).
  ROBOTS_TXT_DISALLOW:
      'The site’s robots.txt has a rule that asks crawlers to stay out of the whole site (Disallow: /), except where another rule lets them in.',
  META_ROBOTS_NOINDEX:
    'The page is marked noindex, which asks search engines to leave it out of their indexes.',
  NO_PRODUCT_OR_SERVICE_SCHEMA:
    'The audited page has no Product or Service markup, so what the business sells has to be inferred from page copy rather than read from the markup.',
  NO_DATE_SIGNAL_AVAILABLE:
    'No usable Last-Modified header and no datePublished or dateModified in the page’s structured data was found, so its freshness cannot be established either way.',
  MULTIPLE_H1:
    'More than one top-level heading leaves the page’s subject ambiguous to anything parsing it.',
  NO_ORGANIZATION_OR_LOCALBUSINESS:
    'The audited page’s JSON-LD and microdata name none of the types this check looks for: Organization, Corporation, OnlineBusiness, LocalBusiness, Dentist, Restaurant, Store or MedicalBusiness.',
};

/**
 * What a check's `detailCode` says, as a sentence, or null when it has none (no code, or
 * one nothing words). Never the code itself: the screen prints this instead of it.
 *
 * This screen's own sentence first (`CHECK_DETAIL_SENTENCE`); otherwise the fix list's
 * `detail` word for word (`FIX_FOR_DETAIL_CODE`: ten of its sixteen), so the report and
 * this screen do not say one thing two ways where they need not. A code that is neither is
 * shown as nothing rather than as itself.
 */
export function checkDetailSentence(code: string | null | undefined): string | null {
  if (!code) return null;
  if (Object.hasOwn(CHECK_DETAIL_SENTENCE, code)) return CHECK_DETAIL_SENTENCE[code]!;
  if (Object.hasOwn(FIX_FOR_DETAIL_CODE, code)) return FIX_FOR_DETAIL_CODE[code]!.detail;
  return null;
}

/** A decimal the API sends as a string, or null. Never NaN, never a silent 0. */
export function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * The audit drawn as a Luminance Ledger.
 *
 * **This is the Ledger's exact correctness condition, not an approximation of
 * it.** Technical Foundation is a weighted sum of these four components, and
 * `layoutLedger`'s composite is a weighted sum of its dimensions — so the total
 * lit height of the column IS the sub-score, the same way it is on the report.
 * Verified against six stored audits: every one re-sums to its stored value,
 * including one whose freshness component is excluded and whose weights
 * therefore redistribute to 40.00 / 26.67 / 33.33.
 *
 * The weights come from `componentWeights`, which is the EFFECTIVE weight after
 * redistribution — not the nominal 30/20/25/25. Using the nominal weights would
 * draw a column whose lit height did not match the score whenever a component
 * was excluded, which is exactly the case worth drawing honestly.
 */
export function auditDimensions(audit: TechnicalAudit): LedgerDimension[] {
  const components = audit.components ?? {};
  const weights = audit.componentWeights ?? {};
  return COMPONENT_ORDER.filter((key) => key in components).map((key) => ({
    key,
    label: COMPONENT_LABEL[key] ?? key,
    weight: num(weights[key]) ?? 0,
    subscore: num(components[key]) ?? 0,
  }));
}

/** Components the audit could not measure, with the reason spelled out. */
export function excludedComponents(
  audit: TechnicalAudit,
): { key: string; label: string; code: string; copy: string }[] {
  const excluded = (audit.excludedComponents ?? {}) as Record<string, string>;
  return Object.entries(excluded).map(([key, code]) => ({
    key,
    label: COMPONENT_LABEL[key] ?? key,
    code,
    copy: EXCLUSION_COPY[code] ?? 'This component could not be measured on this site.',
  }));
}

export type CheckTone = 'pass' | 'warn' | 'fail' | 'not_applicable' | 'error';

export interface CheckTally {
  pass: number;
  warn: number;
  fail: number;
  not_applicable: number;
  error: number;
  /** Checks that actually returned a verdict — the denominator worth showing. */
  measured: number;
}

/**
 * Count the verdicts.
 *
 * `not_applicable` is counted but deliberately kept OUT of `measured`: a check
 * that does not apply to this site is not a check this site passed, and folding
 * it into a "17 of 17" would inflate the denominator with questions nobody
 * asked. Same discipline as scoring-spec.md's excluded dimensions.
 */
export function tally(checks: readonly AuditCheck[]): CheckTally {
  const t: CheckTally = { pass: 0, warn: 0, fail: 0, not_applicable: 0, error: 0, measured: 0 };
  const bump = (key: keyof CheckTally) => {
    t[key] += 1;
  };
  for (const c of checks) {
    const s = c.status as CheckTone;
    // An unrecognised status is COUNTED NOWHERE rather than crashing or being
    // folded into a bucket it does not belong in. The backend owns this enum;
    // a value added there should not silently inflate "passed" here.
    if (s === 'pass' || s === 'warn' || s === 'fail') {
      bump(s);
      bump('measured');
    } else if (s === 'not_applicable' || s === 'error') {
      bump(s);
    }
  }
  return t;
}

/**
 * Group the checks the way the audit actually reasons about them.
 *
 * The API returns them in one flat display order (`CHECK_ORDER` in
 * `routers/audits.py`). That order is already meaningful — crawlability first,
 * then markup, then content, then the lab measurements — so this groups along
 * exactly those seams rather than inventing a second taxonomy. A check the
 * groups do not know about falls into `Other` rather than being dropped.
 */
export const CHECK_GROUPS: readonly { title: string; note: string; keys: readonly string[] }[] = [
  {
    title: 'Crawlability',
    note: 'Whether an engine can reach and index the page at all. Everything below depends on this.',
    keys: ['site_reachable', 'indexable', 'robots_txt_present', 'sitemap_present', 'canonical_present'],
  },
  {
    title: 'Structured data',
    note: 'Whether the page says what kind of business it is in a form a machine can read.',
    keys: ['schema_present', 'schema_business_entity', 'schema_faq', 'schema_product_or_service'],
  },
  {
    title: 'Page markup',
    note: 'The basics an answer engine reads before anything else.',
    keys: ['meta_title', 'meta_description', 'open_graph_tags', 'single_h1'],
  },
  {
    title: 'Content',
    note: 'How recently the page changed.',
    keys: ['content_freshness'],
  },
  {
    title: 'Core Web Vitals',
    note: 'Measured and reported, but carrying no weight in the score — these are single cold-load lab numbers, and a noisy measurement must not move a client-facing figure.',
    keys: ['cwv_lcp', 'cwv_cls', 'cwv_inp'],
  },
];

export function groupChecks(
  checks: readonly AuditCheck[],
): { title: string; note: string; checks: AuditCheck[] }[] {
  const seen = new Set<string>();
  const groups = CHECK_GROUPS.map((g) => {
    const members = g.keys
      .map((k) => checks.find((c) => c.checkKey === k))
      .filter((c): c is AuditCheck => c !== undefined);
    members.forEach((c) => seen.add(c.checkKey));
    return { title: g.title, note: g.note, checks: members };
  }).filter((g) => g.checks.length > 0);

  // Anything the groups do not name still gets shown. A check silently missing
  // from a technical audit is worse than one in an "Other" bucket.
  const rest = checks.filter((c) => !seen.has(c.checkKey));
  if (rest.length > 0) {
    groups.push({ title: 'Other', note: 'Checks not covered by a group above.', checks: rest });
  }
  return groups;
}

/**
 * Check names, in this product's own words rather than the machine key.
 *
 * Unknown keys fall back to a de-slugged key, so a check added to the backend
 * appears immediately with a readable-enough name instead of vanishing or
 * blocking the screen until this table is updated.
 */
export const CHECK_LABEL: Record<string, string> = {
  site_reachable: 'Site reachable',
  indexable: 'Indexable',
  robots_txt_present: 'robots.txt present',
  sitemap_present: 'Sitemap present',
  canonical_present: 'Canonical URL',
  schema_present: 'Any structured data',
  schema_business_entity: 'Business entity markup',
  schema_faq: 'FAQ markup',
  schema_product_or_service: 'Product or service markup',
  meta_title: 'Title tag',
  meta_description: 'Meta description',
  open_graph_tags: 'Open Graph tags',
  single_h1: 'Exactly one H1',
  content_freshness: 'Content freshness',
  cwv_lcp: 'Largest Contentful Paint',
  cwv_cls: 'Cumulative Layout Shift',
  cwv_inp: 'Interaction to Next Paint',
};

export function checkLabel(key: string): string {
  return CHECK_LABEL[key] ?? key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}
