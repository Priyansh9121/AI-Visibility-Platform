'use client';

/**
 * The three screens inside a client's space — Epic 9.20.
 *
 * Pure and prop-driven, so every state is reachable by a static render. The
 * routes own fetching. That split is the one `DashboardView` / `dashboard/page`
 * established in Epic 9.3 and every screen since has followed.
 */

import type { JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  MetaChip,
  ScoreHero,
  ScoreMeter,
  StatRow,
  StatTile,
  TrendChart,
} from '@avp/design-system';
import type { BadgeTone, Column, ScoreAbsence, TrendSeriesInput } from '@avp/design-system';
import { seriesStyle } from '@avp/design-system';
import { CalendarDays, PieChart, Users } from 'lucide-react';
import type {
  AlertFeed,
  Client,
  ClientHistory,
  HistoryScan,
  Me,
} from '@avp/shared-types';
import { ClientSpace, type ClientSection } from '@/components/client/ClientSpace';
import { formatStamp } from '@/lib/dates';
import {
  alertAnnotations,
  hasTrend,
  intermittentRivals,
  rankingSeries,
  sourceSeries,
  trendPoints,
} from '@/lib/client/trends';

export type ClientDetailState =
  | { kind: 'loading' }
  | { kind: 'ready'; client: Client; history: ClientHistory }
  | { kind: 'error'; title: string; detail: string };

const STATUS_TONE: Record<string, BadgeTone> = {
  succeeded: 'success',
  partial: 'warn',
  failed: 'danger',
  cancelled: 'neutral',
  queued: 'neutral',
  running: 'beacon',
};

const STATUS_LABEL: Record<string, string> = {
  succeeded: 'Complete',
  partial: 'Partial',
  failed: 'Failed',
  cancelled: 'Cancelled',
  queued: 'Queued',
  running: 'Running',
};

/** The scan a client's Report item opens: the newest one with a reading. */
export function latestScanId(history: ClientHistory): string | null {
  const last = history.scans[history.scans.length - 1];
  return last ? last.scanId : null;
}

/**
 * The newest composite among scans that have one — the sidebar's figure.
 *
 * The same rule `ClientMetaFigures` follows for its Latest tile: a scan
 * without a reading is not a zero, so it is skipped rather than reported.
 * `null` when no scan has produced one.
 */
export function latestComposite(history: ClientHistory): number | null {
  for (let i = history.scans.length - 1; i >= 0; i -= 1) {
    const n = num(history.scans[i]!.composite);
    if (n !== null) return n;
  }
  return null;
}

function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * The frame plus whichever body — one component so the loading and error
 * branches cannot drift between three screens.
 */
function Frame({
  state,
  me,
  current,
  figures,
  children,
}: {
  state: ClientDetailState;
  me: Me | null;
  current: ClientSection;
  /**
   * What sits between the head and the body — Epic 14. Defaults to the four
   * shared figures; the Overview passes its hero plus the figures without the
   * one the hero already carries.
   */
  figures?: (ready: { client: Client; history: ClientHistory }) => JSX.Element;
  children?: (ready: { client: Client; history: ClientHistory }) => JSX.Element;
}): JSX.Element {
  if (state.kind === 'loading') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current={current}
        latestReportScanId={null}
      >
        <LoadingState message="Loading this client…" />
      </ClientSpace>
    );
  }

  if (state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current={current}
        latestReportScanId={null}
      >
        <ErrorState
          title={state.title}
          detail={state.detail}
          action={
            <Button variant="secondary" onClick={() => window.location.assign('/clients')}>
              Back to clients
            </Button>
          }
        />
      </ClientSpace>
    );
  }

  const { client, history } = state;
  return (
    <ClientSpace
      client={client}
      me={me}
      current={current}
      latestReportScanId={latestScanId(history)}
      latestScore={latestComposite(history)}
      figures={figures ? figures({ client, history }) : <ClientMetaFigures history={history} />}
    >
      {children?.({ client, history })}
    </ClientSpace>
  );
}

/* ============================== Overview ============================== */

export function ClientOverviewView({
  state,
  me,
}: {
  state: ClientDetailState;
  me: Me | null;
}): JSX.Element {
  return (
    <Frame
      state={state}
      me={me}
      current="overview"
      figures={({ history }) =>
        history.scans.length === 0 ? (
          <ClientMetaFigures history={history} />
        ) : (
          <>
            <OverviewHero history={history} />
            <ClientMetaFigures history={history} omitLatest />
          </>
        )
      }
    >
      {({ history }) => {
        if (history.scans.length === 0) {
          return (
            <EmptyState
              eyebrow="Nothing measured yet"
              title="This client has not been scanned"
              body="A scan is what produces a score, a competitor set and a report. Until one runs there is nothing here to show."
              {...(history.scansWithoutData > 0
                ? {
                    note: `${history.scansWithoutData} ${
                      history.scansWithoutData === 1 ? 'scan exists' : 'scans exist'
                    } for this client but produced no reading — queued, running, or ended without answers.`,
                  }
                : {})}
              action={
                <Button variant="primary" onClick={() => window.location.assign('/')}>
                  Run a scan
                </Button>
              }
            />
          );
        }

        const columns: readonly Column<HistoryScan>[] = [
          {
            key: 'when',
            header: 'Scanned',
            render: (s) => (
              <span className="text-ui-sm text-text-secondary">{formatStamp(s.scannedAt)}</span>
            ),
          },
          {
            key: 'status',
            header: 'Status',
            render: (s) => (
              <Badge tone={STATUS_TONE[s.status] ?? 'neutral'}>
                {STATUS_LABEL[s.status] ?? s.status}
              </Badge>
            ),
          },
          {
            key: 'score',
            header: 'Visibility',
            render: (s) => (
              <ScoreMeter
                score={num(s.composite)}
                absence={('unscored' as ScoreAbsence)}
              />
            ),
          },
          {
            key: 'sov',
            header: 'Share of voice',
            render: (s) => {
              const v = num(s.shareOfVoice);
              return (
                <span className="text-ui-sm text-text-secondary">
                  {v === null ? 'Not measured' : `${v.toFixed(1)}%`}
                </span>
              );
            },
          },
          {
            key: 'rivals',
            header: 'Rivals',
            render: (s) => (
              <span className="text-ui-sm text-text-secondary">{s.competitors.length}</span>
            ),
          },
          {
            key: 'go',
            header: '',
            align: 'end',
            render: (s) => (
              <a
                className="text-ui-sm font-medium text-beacon-600 underline underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-700"
                href={`/scans/${s.scanId}/report`}
              >
                View report
              </a>
            ),
          },
        ];

        return (
          <Card elevation="seated">
            <CardHeader>
              <CardTitle>Scan history</CardTitle>
            </CardHeader>
            {/*
              Oldest first, matching the trends. The dashboard's list is
              newest-first and stays so: that one is "what happened lately"
              across every client, this one is this client's timeline, and a
              timeline that runs backwards beside two charts that run forwards
              would be a third reading order on one screen.
            */}
            <DataTable
              columns={columns}
              rows={history.scans}
              rowKey={(s) => s.scanId}
              caption="Oldest first, matching the Sources and Rankings trends."
            />
          </Card>
        );
      }}
    </Frame>
  );
}

/**
 * The Overview's hero — Epic 14: the Luminance Ledger's idea at the top of a
 * client's space.
 *
 * The latest composite, as the first and largest thing on the screen, with
 * the change since the reading before it and the score's own line across the
 * client's history beside it. Every figure is read off `history`, which the
 * screen already has: the latest and previous composites are the newest two
 * scans WITH a reading (a scan without one is not a zero and is not a point on
 * the line), the delta is their difference, and the trend is the composite
 * series the Rankings and Sources screens never draw because they plot the
 * field rather than the client alone.
 *
 * One scan is a reading, not a direction: with fewer than two scored scans
 * there is no delta and no line, and the hero says the number on its own.
 */
function OverviewHero({ history }: { history: ClientHistory }): JSX.Element {
  const scored = history.scans
    .map((s) => ({ scan: s, value: num(s.composite) }))
    .filter((p): p is { scan: HistoryScan; value: number } => p.value !== null);
  const latest = scored[scored.length - 1] ?? null;
  const previous = scored.length >= 2 ? scored[scored.length - 2]! : null;
  const delta = latest && previous ? latest.value - previous.value : null;
  const sov = latest ? num(latest.scan.shareOfVoice) : null;

  const series: TrendSeriesInput[] = [
    {
      key: 'composite',
      label: 'Score',
      isSubject: true,
      values: history.scans.map((s) => num(s.composite)),
    },
  ];

  return (
    <ScoreHero
      label="Latest score"
      score={latest ? latest.value : null}
      absence="Not scored yet"
      {...(delta != null ? { delta } : {})}
      meta={
        // Three facts, each in the fact's shape — Epic 16.2. Beside the
        // delta they used to be four items of grey text at one weight, which
        // is the report byline's failure on a Working screen. The delta stays
        // as text: it is a reading with a direction, not a credential.
        latest ? (
          <>
            <MetaChip icon={<CalendarDays />}>{`Scanned ${formatStamp(latest.scan.scannedAt)}`}</MetaChip>
            {sov !== null && <MetaChip icon={<PieChart />}>{`${sov.toFixed(1)}% share of voice`}</MetaChip>}
            <MetaChip icon={<Users />}>
              {latest.scan.competitors.length === 1
                ? '1 rival in the set'
                : `${latest.scan.competitors.length} rivals in the set`}
            </MetaChip>
          </>
        ) : (
          <span>No scan of this client has produced a reading.</span>
        )
      }
      aside={
        scored.length >= 2 ? (
          <TrendChart
            points={trendPoints(history)}
            series={series}
            unit=""
            yMax={100}
            height={200}
            /*
              Drawn at the width it is shown at. The default 720-unit chart in
              a hero aside half that wide would scale its 11px ticks to 6px —
              the Epic 9.21 failure in the other direction.
            */
            layoutOptions={{ width: 420, height: 200 }}
            title="Score across scans"
            palette="working"
            area
            ariaLabel={`AI Visibility Score across ${history.scans.length} scans of ${history.name}: ${scored
              .map((p) => p.value.toFixed(0))
              .join(', ')}.`}
          />
        ) : undefined
      }
      animate={false}
    />
  );
}

/* =============================== Sources ============================== */

export function ClientSourcesView({
  state,
  me,
  alerts,
}: {
  state: ClientDetailState;
  me: Me | null;
  /**
   * Alerts for this client, so a scan that produced one is marked on the
   * trend — Epic E. Optional: the chart is complete without it, and a failed
   * alert request must not cost the trend.
   */
  alerts?: AlertFeed | null;
}): JSX.Element {
  return (
    <Frame state={state} me={me} current="sources">
      {({ history }) => {
        if (!hasTrend(history)) return <NoTrendYet history={history} what="sources" />;
        const points = trendPoints(history);
        const series = sourceSeries(history);
        return (
          <section className="flex flex-col gap-6">
            <Intro
              heading="Who gets cited when engines answer"
              lead="Every time an engine answered one of this client's prompts it cited sources. This is how often each domain was cited, scan by scan."
            />
            <Card elevation="seated" className="grid items-start gap-8 p-6 lg:grid-cols-[auto_minmax(16rem,1fr)]">
                <TrendChart
                  points={points}
                  series={series}
                  unit=""
                  height={340}
                  title="Citations per domain"
                  annotations={alertAnnotations(alerts ?? null)}
                  /*
                    THE PER-CONTEXT PALETTE — Epic 9.24.

                    The same `TrendChart` the report embeds, asked to draw itself
                    for an operator rather than for a document. Competitor series
                    take the Working accent hues; the client stays `beacon-600`,
                    the dash patterns stay, and the hidden data table is unchanged
                    — render.test.tsx strips the paint attributes and asserts the
                    two renderings are otherwise identical.

                    Worth the change here specifically: these charts carry up to
                    eight series, and five neutral greys separated by lightness are
                    genuinely hard to follow at 1.5px across a wide Working column.
                    On the report they stay neutral, because there the audience is a
                    CMO and §1's non-judgmental rule is doing different work.
                  */
                  palette="working"
                  area
                  caption="Counted across every answered prompt in each scan. A gap means the domain fell below what that scan recorded, not that it was cited zero times."
                  ariaLabel={`Citations per domain across ${points.length} scans of ${history.name}. ${series
                    .map((s) => `${s.label}: ${s.values.map((v) => (v === null ? 'not measured' : v)).join(', ')}`)
                    .join('. ')}`}
                />
              <SeriesLedger series={series} unit={""} />
            </Card>
          </section>
        );
      }}
    </Frame>
  );
}

/* ============================== Rankings ============================== */

export function ClientRankingsView({
  state,
  me,
  alerts,
}: {
  state: ClientDetailState;
  me: Me | null;
  /**
   * Alerts for this client, so a scan that produced one is marked on the
   * trend — Epic E. Optional: the chart is complete without it, and a failed
   * alert request must not cost the trend.
   */
  alerts?: AlertFeed | null;
}): JSX.Element {
  return (
    <Frame state={state} me={me} current="rankings">
      {({ history }) => {
        if (!hasTrend(history)) return <NoTrendYet history={history} what="rankings" />;
        const points = trendPoints(history);
        const series = rankingSeries(history);
        const intermittent = intermittentRivals(history);
        return (
          <section className="flex flex-col gap-6">
            <Intro
              heading="How the field is sharing the answers"
              lead="Share of voice is this client's mentions as a fraction of every brand named in the same answers — so a rival's rise is this client's fall, and the lines sum across the field."
            />
            <Card elevation="seated" className="grid items-start gap-8 p-6 lg:grid-cols-[auto_minmax(16rem,1fr)]">
                <TrendChart
                  points={points}
                  series={series}
                  unit="%"
                  yMax={100}
                  height={340}
                  title="Share of voice"
                  annotations={alertAnnotations(alerts ?? null)}
                  /*
                    THE PER-CONTEXT PALETTE — Epic 9.24.

                    The same `TrendChart` the report embeds, asked to draw itself
                    for an operator rather than for a document. Competitor series
                    take the Working accent hues; the client stays `beacon-600`,
                    the dash patterns stay, and the hidden data table is unchanged
                    — render.test.tsx strips the paint attributes and asserts the
                    two renderings are otherwise identical.

                    Worth the change here specifically: these charts carry up to
                    eight series, and five neutral greys separated by lightness are
                    genuinely hard to follow at 1.5px across a wide Working column.
                    On the report they stay neutral, because there the audience is a
                    CMO and §1's non-judgmental rule is doing different work.
                  */
                  palette="working"
                  area
                  caption="Share of voice, not the composite score: there is no per-competitor composite, because sentiment and technical foundation are measured on this client's site alone."
                  ariaLabel={`Share of voice across ${points.length} scans. ${series
                    .map((s) => `${s.label}: ${s.values.map((v) => (v === null ? 'not measured' : `${v}%`)).join(', ')}`)
                    .join('. ')}`}
                />
              <SeriesLedger series={series} unit={"%"} />
            </Card>
            {intermittent.length > 0 && (
              // The gaps in the chart, explained rather than left to be
              // noticed. A rival missing from one scan's set is a real event —
              // detection re-runs per scan — and the honest reading is "not
              // measured", never a fall to zero.
              <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
                {intermittent.length === 1
                  ? `${intermittent[0]} was not in every scan's competitor set, so its line breaks where it was not measured.`
                  : `${intermittent.join(', ')} were not in every scan's competitor set, so their lines break where they were not measured.`}
              </p>
            )}
          </section>
        );
      }}
    </Frame>
  );
}

/* ============================== fragments ============================= */


/**
 * The series, as a table beside the chart — Epic 9.24.
 *
 * WHY THIS EXISTS, AND WHY IT IS NOT A WIDER CHART
 * ------------------------------------------------
 * Epic 9.21 bounded `TrendChart` at its drawn width, because an SVG at
 * `width: 100%` over a fixed viewBox scales its TYPE with its box and an 11px
 * axis label was rendering at 17.6px. That bound is correct and is not being
 * loosened — the chart fills ~720px of a 1200px Working column and must keep
 * doing so.
 *
 * What 9.21 left behind is the space to the right of it, and this is what fills
 * it. Not decoration: reading an exact value off a line chart is guesswork, and
 * an operator comparing "are we ahead of Matomo this month" wants the number.
 * The swatch is the SAME `seriesStyle(..., 'working')` call the chart makes, so
 * the ledger keys to the lines rather than restating them in a second palette.
 *
 * EVERY VALUE HERE IS ALREADY ON SCREEN. Nothing is fetched and nothing is
 * computed that the chart's own hidden data table does not already carry — this
 * is the same series, read as figures.
 *
 * A null is a gap, not a zero, exactly as it is on the line: a series measured
 * once has no direction, and one not measured in the latest scan says so rather
 * than reporting its last known value as if it were current.
 */
function SeriesLedger({
  series,
  unit,
}: {
  series: readonly TrendSeriesInput[];
  unit: string;
}): JSX.Element {
  const rows = series.map((s) => {
    const measured = s.values
      .map((v, i) => ({ v, i }))
      .filter((p): p is { v: number; i: number } => p.v != null);
    const last = measured[measured.length - 1];
    const first = measured[0];
    return {
      key: s.key,
      label: s.label,
      isSubject: s.isSubject === true,
      latest: last && last.i === s.values.length - 1 ? last.v : null,
      // Only a direction when there are two readings to have one between.
      delta: measured.length >= 2 && last && first ? last.v - first.v : null,
    };
  });

  let competitorIndex = -1;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
        Latest reading
      </p>
      <table className="w-full">
        <thead className="sr-only">
          <tr>
            <th scope="col">Series</th>
            <th scope="col">Latest</th>
            <th scope="col">Change across this history</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            if (!row.isSubject) competitorIndex += 1;
            const style = seriesStyle(
              row.isSubject ? 'subject' : 'competitor',
              row.isSubject ? 0 : competitorIndex,
              'working',
            );
            return (
              <tr key={row.key} className="border-b border-line-hairline last:border-0">
                <td className="py-2 pr-3">
                  <span className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 flex-none rounded-full"
                      style={{ background: style.fill }}
                    />
                    <span
                      className={
                        row.isSubject
                          ? 'truncate text-ui-sm font-medium text-text-primary'
                          : 'truncate text-ui-sm text-text-secondary'
                      }
                    >
                      {row.label}
                    </span>
                  </span>
                </td>
                <td className="py-2 pr-3 text-right text-ui-sm tabular-nums text-text-primary">
                  {row.latest == null ? (
                    <span className="text-text-tertiary">Not measured</span>
                  ) : (
                    `${row.latest.toFixed(1)}${unit}`
                  )}
                </td>
                <td className="py-2 text-right text-ui-sm tabular-nums text-text-secondary">
                  {row.delta == null
                    ? // One reading is not a direction.
                      <span className="text-text-tertiary">—</span>
                    : `${row.delta > 0 ? '+' : row.delta < 0 ? '\u2212' : '\u00b1'}${Math.abs(row.delta).toFixed(1)}${unit}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Intro({ heading, lead }: { heading: string; lead: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-2">
      <h2 className="max-w-headline font-display text-ed-xs leading-display tracking-display text-text-primary">
        {heading}
      </h2>
      <p className="max-w-measure text-ui-base leading-prose text-text-secondary">{lead}</p>
    </div>
  );
}

/**
 * One scan is not a trend.
 *
 * Written in the voice the report already uses for thin data — the Ledger's
 * "Not enough data to score" and `ScoreMeter`'s named absences both state the
 * fact and say what would change it, rather than drawing a chart with one point
 * on it and letting the reader infer a shape that is not there.
 */
function NoTrendYet({
  history,
  what,
}: {
  history: ClientHistory;
  what: 'sources' | 'rankings';
}): JSX.Element {
  const one = history.scans.length === 1;
  return (
    <EmptyState
      eyebrow={what === 'sources' ? 'Sources' : 'Rankings'}
      title={one ? 'One scan is not a trend yet' : 'Nothing measured yet'}
      body={
        one
          ? what === 'sources'
            ? 'This client has been scanned once, so there is a reading but nothing to compare it against. A second scan is what turns a citation count into a direction.'
            : 'This client has been scanned once, so there is a share of voice but nothing to compare it against. A second scan is what turns a share into a direction.'
          : 'No scan of this client has produced a reading, so there is nothing to plot.'
      }
      {...(history.scansWithoutData > 0
        ? {
            note: `${history.scansWithoutData} further ${
              history.scansWithoutData === 1 ? 'scan' : 'scans'
            } produced no reading and cannot appear on a trend.`,
          }
        : {})}
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          {one ? 'Run another scan' : 'Run a scan'}
        </Button>
      }
    />
  );
}

/**
 * The figures beside a client's name — Epic 9.24.
 *
 * Two became four, and all four are read off `history`, which every screen in
 * this space already has in hand. Nothing is fetched and nothing is derived
 * from outside this component.
 *
 * `latest` and `best` are the newest and highest composite AMONG SCANS THAT
 * HAVE ONE. A scan without a reading is not a zero — the rule `ScoreMeter` and
 * the dashboard both follow — so those scans are excluded from the comparison
 * rather than dragging it down, and if none has a reading both tiles say so in
 * words instead of printing a number nobody measured.
 *
 * Exported from Epic A, so a screen in this space that is not built on `Frame`
 * — the Sentiment tab is the first — carries the same four figures rather than
 * growing its own. Two headers reporting a client's scan count differently
 * would be two answers to one question.
 */
export function ClientMetaFigures({
  history,
  omitLatest = false,
}: {
  history: ClientHistory;
  /** The Overview's hero already carries the latest reading — Epic 14. */
  omitLatest?: boolean;
}): JSX.Element {
  const scored = history.scans
    .map((s) => num(s.composite))
    .filter((n): n is number => n != null);
  const latest = scored.length > 0 ? scored[scored.length - 1]! : null;
  const best = scored.length > 0 ? Math.max(...scored) : null;

  return (
    <StatRow min="10rem">
      <StatTile label="Scans" value={String(history.scans.length)} accent={0} />
      {/*
        NO ACCENT ON THESE TWO — Epic 9.24.

        `latest` and `best` are visibility SCORES, and this product already has
        a colour language for a score: the visibility ramp, where hue means how
        visible you are. Wrapping a score in a categorical hue puts two colour
        languages on one tile and invites the reading that the tile's colour
        says something about the number. The bench hues are 30 degrees clear of
        every ramp stop precisely so they cannot be confused with one; sitting
        one directly around a score would give that separation away for
        decoration. Counts take accents; measurements do not.
      */}
      {!omitLatest && (
        <StatTile label="Latest" value={latest == null ? 'Not scored' : latest.toFixed(0)} />
      )}
      <StatTile label="Best" value={best == null ? 'Not scored' : best.toFixed(0)} />
      {history.scansWithoutData > 0 && (
        <StatTile
          label="No reading"
          value={String(history.scansWithoutData)}
          accent={1}
          emphasis
        />
      )}
    </StatRow>
  );
}
