/**
 * Turning a client's scan history into trend series — Epic 9.20.
 *
 * Pure, and separated from the views for the reason `lib/report/derive.ts` is:
 * the interesting behaviour here is what happens when the shape of the data
 * CHANGES between scans, and that is arithmetic, not rendering. It is asserted
 * directly rather than through a render.
 *
 * THE CASE THIS FILE EXISTS FOR
 * -----------------------------
 * A competitor set is re-detected per scan. A rival can appear in scan 1,
 * vanish in scan 2 and return in scan 3 — and a cited domain certainly can,
 * since it depends on what the engines happened to cite that day.
 *
 * Two wrong answers are easy to reach and both are silent:
 *
 *   1. **Drop the series** when it is missing anywhere. The chart then shows
 *      fewer rivals than the client has, and nothing says so.
 *   2. **Fill the gap with zero.** The line dives to the floor and back, which
 *      asserts the rival's share of voice really was nothing that day. It was
 *      not measured, which is a different fact.
 *
 * So a missing reading becomes `null`, every series spans every point, and
 * `TrendChart` breaks the line rather than joining through it.
 */

import type { ClientHistory, HistoryScan } from '@avp/shared-types';
import type { TidePointInput, TrendPoint, TrendSeriesInput } from '@avp/design-system';

/** How many lines a trend will draw before it stops. */
export const MAX_SERIES = 6;

/**
 * A trend needs at least two readings.
 *
 * One point is not a trend, it is a measurement — and drawing an axis around a
 * single dot implies a shape the data has not got. Both trend screens check
 * this and say so instead.
 */
export function hasTrend(history: ClientHistory): boolean {
  return history.scans.length >= 2;
}

/**
 * The x-axis: one column per scan that produced a reading, oldest first.
 *
 * **The label resolution follows the data.** Re-running a client twice in an
 * afternoon is normal — it is what the dashboard's Re-run button is for — and
 * three columns all reading "29 Aug" tell the reader nothing about which came
 * first. So when every scan falls on one UTC day the axis switches to the
 * clock; otherwise it shows the day. Caught in a live browser on the real
 * `plausible.io` history, whose three scans are 01:37, 03:48 and 04:32 on the
 * same date.
 */
export function trendPoints(history: ClientHistory): TrendPoint[] {
  const days = new Set(history.scans.map((s) => s.scannedAt.slice(0, 10)));
  const sameDay = days.size <= 1 && history.scans.length > 1;
  return history.scans.map((scan) => ({
    label: sameDay ? shortTime(scan.scannedAt) : shortDay(scan.scannedAt),
    stamp: scan.scannedAt,
  }));
}

/**
 * Sources — one line per cited domain, citations across the history.
 *
 * Ranked by TOTAL citations across every scan rather than by the latest, so a
 * domain that mattered early and faded still earns its line. Ties break on the
 * domain name so the chart is stable between loads.
 *
 * The client's own domain is always the subject series when it was cited at
 * all, and is always drawn even if it would not have made the cut on volume:
 * "who cites you" is the question this screen exists to answer, and a chart
 * that dropped the client for being outranked would be answering a different
 * one.
 */
export function sourceSeries(history: ClientHistory, limit = MAX_SERIES): TrendSeriesInput[] {
  const totals = new Map<string, number>();
  const subjectDomains = new Set<string>();

  for (const scan of history.scans) {
    for (const d of scan.citedDomains) {
      totals.set(d.domain, (totals.get(d.domain) ?? 0) + d.citations);
      if (d.citesSubject) subjectDomains.add(d.domain);
    }
  }

  const ranked = [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([domain]) => domain);

  // The client's own domain is pinned in, then the rest fill the remaining
  // slots. `history.domain` is the registrable domain the scan ran against.
  const own = ranked.find((d) => subjectDomains.has(d) || d === history.domain);
  const chosen = [
    ...(own != null ? [own] : []),
    ...ranked.filter((d) => d !== own).slice(0, Math.max(0, limit - (own != null ? 1 : 0))),
  ];

  return chosen.map((domain) => ({
    key: domain,
    label: domain,
    isSubject: domain === own,
    values: history.scans.map((scan) => valueForDomain(scan, domain)),
  }));
}

/**
 * A domain's citations in one scan.
 *
 * **Zero and null are different here, and both occur.** A scan that ran and
 * cited nothing from this domain genuinely measured zero citations — that is a
 * reading, and it plots. `null` is reserved for a scan whose citation list was
 * truncated before this domain could appear, which the endpoint caps at 40 per
 * scan.
 *
 * The distinction is decidable: if the scan returned fewer rows than the cap,
 * its list is complete and an absent domain really is a zero. If it returned
 * the full cap, a domain below the cut is unknown rather than absent.
 */
function valueForDomain(scan: HistoryScan, domain: string): number | null {
  const hit = scan.citedDomains.find((d) => d.domain === domain);
  if (hit) return hit.citations;
  return scan.citedDomains.length >= CITED_DOMAIN_CAP ? null : 0;
}

/** Mirrors `max_domains_per_scan` in services/client_history.py. */
const CITED_DOMAIN_CAP = 40;

/**
 * Rankings — one line per competitor, SHARE OF VOICE, plus the client.
 *
 * WHY SHARE OF VOICE AND NOT COMPOSITE
 * ------------------------------------
 * Two reasons, and the first is decisive: **there is no per-competitor
 * composite and there deliberately never has been.** `CompetitorComparison`
 * says why — sentiment is classified toward the subject only and technical
 * foundation is the subject's own site, so 25% of the composite's weight has no
 * rival input, and a rival "composite" over the other 75% would not be
 * comparable to the client's. Plotting the client's real composite against five
 * rivals' partial ones would be a chart whose lines mean different things.
 *
 * The second is that share of voice is the only one of the three measured
 * dimensions that is a genuine SHARE. `scoring.share_of_voice` is subject
 * mentions over total brand mentions; `compare_competitors` is that rival's
 * appearances over the identical total. Same numerator kind, same denominator —
 * so the lines sum toward 100 across the field and a rise for one really is a
 * fall for another. Measured on live data: subject plus five rivals summed to
 * 99.99 on one scan and 100.00 on another.
 *
 * Mention rate would also be honest per line but does not compose: every brand
 * in the field can be at 100% at once, so the chart would carry no information
 * about the contest between them, which is the whole point of Rankings.
 */
export function rankingSeries(history: ClientHistory, limit = MAX_SERIES): TrendSeriesInput[] {
  // Rivals ranked by their best share across the history, so a competitor who
  // led early and faded is still shown — the fade IS the finding.
  const best = new Map<string, { name: string; peak: number }>();
  for (const scan of history.scans) {
    for (const c of scan.competitors) {
      const sov = num(c.shareOfVoice);
      const seen = best.get(c.name);
      if (!seen || (sov != null && sov > seen.peak)) {
        best.set(c.name, { name: c.name, peak: sov ?? seen?.peak ?? 0 });
      }
    }
  }

  const rivals = [...best.values()]
    .sort((a, b) => b.peak - a.peak || a.name.localeCompare(b.name))
    .slice(0, Math.max(0, limit - 1));

  return [
    {
      key: '__subject__',
      label: history.name,
      isSubject: true,
      values: history.scans.map((s) => num(s.shareOfVoice)),
    },
    ...rivals.map((r) => ({
      key: r.name,
      label: r.name,
      // Null, NOT zero, for a scan whose set did not contain this rival. See
      // the file header — this is the case the whole module exists for.
      values: history.scans.map((scan) => {
        const hit = scan.competitors.find((c) => c.name === r.name);
        return hit ? num(hit.shareOfVoice) : null;
      }),
    })),
  ];
}

/**
 * Rivals that are not in every scan's set — named, so the chart's gaps are
 * explained rather than left to be noticed.
 */
export function intermittentRivals(history: ClientHistory): string[] {
  if (history.scans.length < 2) return [];
  const names = new Set(history.scans.flatMap((s) => s.competitors.map((c) => c.name)));
  return [...names]
    .filter((name) =>
      history.scans.some((scan) => !scan.competitors.some((c) => c.name === name)),
    )
    .sort();
}

/** A decimal the API sends as a string, or null. Never NaN, never a silent 0. */
function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * A short, UTC day label.
 *
 * UTC for the reason `lib/dates.ts` gives: locale formatting differs between CI
 * and a browser, and a chart axis that renders differently in a test than on
 * screen is a chart nobody can assert against.
 */
function shortDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const day = String(d.getUTCDate()).padStart(2, '0');
  const month = MONTHS[d.getUTCMonth()] ?? '';
  return `${day} ${month}`;
}

/** UTC clock, for a history whose scans all landed on one day. */
function shortTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const h = String(d.getUTCHours()).padStart(2, '0');
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/* ============================== Sentiment ============================= */

/**
 * The tide's x-axis and buckets — Epic A.
 *
 * Reuses `trendPoints`' label resolution rather than formatting its own, so the
 * Sentiment tab's columns read identically to Sources' and Rankings' for the
 * same three scans. Three tabs with three different date formats for the same
 * history would be three answers to "when was this".
 *
 * An engine absent from a scan's `sentiment` array stays absent here — the
 * layout draws no column for it and reports it as missing, which is the
 * difference between "was down" and "described you neutrally".
 */
export function tidePoints(history: ClientHistory): TidePointInput[] {
  const labels = trendPoints(history);
  return history.scans.map((scan, i) => ({
    label: labels[i]?.label ?? scan.scannedAt.slice(0, 10),
    stamp: scan.scannedAt,
    byEngine: Object.fromEntries(
      (scan.sentiment ?? []).map((row) => [
        row.engine,
        {
          positive: row.positive,
          neutral: row.neutral,
          negative: row.negative,
          unclassified: row.unclassified,
        },
      ]),
    ),
  }));
}

/**
 * Has any scan classified a tone at all?
 *
 * Distinct from `hasTrend`, deliberately. One scan IS a readable tide — a
 * single scan's split of positive/neutral/negative is a finding, unlike a
 * single point on a line, which is not a direction. So the Sentiment tab shows
 * its chart from the first scan onward and only falls back to an empty state
 * when nothing anywhere has a tone.
 */
export function hasSentiment(history: ClientHistory): boolean {
  return history.scans.some((scan) =>
    (scan.sentiment ?? []).some((r) => r.positive + r.neutral + r.negative > 0),
  );
}

/**
 * How many answers named the client but were never classified.
 *
 * Reported so the screen can say why a tide is thinner than a scan's answer
 * count, rather than leaving the reader to wonder. `unclassified` is answers
 * where the client was NOT named, which is a different fact and is reported
 * separately per engine.
 */
export function unclassifiedTotal(history: ClientHistory): number {
  return history.scans.reduce(
    (sum, scan) => sum + (scan.sentiment ?? []).reduce((s, r) => s + r.unclassified, 0),
    0,
  );
}
