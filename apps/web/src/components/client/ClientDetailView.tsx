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
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  ScoreMeter,
  TrendChart,
} from '@avp/design-system';
import type { BadgeTone, Column, ScoreAbsence } from '@avp/design-system';
import type { Client, ClientHistory, HistoryScan, Me } from '@avp/shared-types';
import { ClientSpace, type ClientSection } from '@/components/client/ClientSpace';
import { formatStamp } from '@/lib/dates';
import {
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
  children,
}: {
  state: ClientDetailState;
  me: Me | null;
  current: ClientSection;
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
      meta={
        // A `dl`, because these are label/value pairs and `Stat` renders
        // `dt`/`dd` — the same treatment the dashboard and clients headers use.
        <dl className="flex flex-wrap items-end gap-8">
          <Stat label="Scans" value={String(history.scans.length)} />
          {history.scansWithoutData > 0 && (
            <Stat label="No reading" value={String(history.scansWithoutData)} />
          )}
        </dl>
      }
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
    <Frame state={state} me={me} current="overview">
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
          <section className="flex flex-col gap-4">
            <h2 className="text-ui-md font-medium text-text-primary">Scan history</h2>
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
          </section>
        );
      }}
    </Frame>
  );
}

/* =============================== Sources ============================== */

export function ClientSourcesView({
  state,
  me,
}: {
  state: ClientDetailState;
  me: Me | null;
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
            <TrendChart
              points={points}
              series={series}
              unit=""
              height={340}
              title="Citations per domain"
              caption="Counted across every answered prompt in each scan. A gap means the domain fell below what that scan recorded, not that it was cited zero times."
              ariaLabel={`Citations per domain across ${points.length} scans of ${history.name}. ${series
                .map((s) => `${s.label}: ${s.values.map((v) => (v === null ? 'not measured' : v)).join(', ')}`)
                .join('. ')}`}
            />
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
}: {
  state: ClientDetailState;
  me: Me | null;
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
            <TrendChart
              points={points}
              series={series}
              unit="%"
              yMax={100}
              height={340}
              title="Share of voice"
              caption="Share of voice, not the composite score: there is no per-competitor composite, because sentiment and technical foundation are measured on this client's site alone."
              ariaLabel={`Share of voice across ${points.length} scans. ${series
                .map((s) => `${s.label}: ${s.values.map((v) => (v === null ? 'not measured' : `${v}%`)).join(', ')}`)
                .join('. ')}`}
            />
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

function Intro({ heading, lead }: { heading: string; lead: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-2">
      <h2 className="max-w-headline font-editorial text-ed-xs leading-display tracking-display text-text-primary">
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

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{label}</dt>
      <dd className="text-ui-lg font-medium text-text-primary">{value}</dd>
    </div>
  );
}
