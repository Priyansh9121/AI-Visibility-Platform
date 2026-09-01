/**
 * The agency dashboard — Epic 9.3, slice 2 of Epic 9.
 *
 * Presentational and pure, so it can be rendered to static markup and asserted
 * over the way ReportView is. The route owns fetching and re-run state.
 *
 * IT POLLS NOW — Epic 9.7
 * -----------------------
 * Three epics wrote a paragraph here about why it could not. The history, kept
 * short because the conclusion changed: as originally built the scan endpoint
 * ran inline and committed once at the end, so a running scan was invisible and
 * there was no status to observe. Epic 9.5 made the status real, Epic 9.6 made
 * it safe to act on, and both left the poller unbuilt so the screen needed a
 * manual refresh to advance.
 *
 * It no longer does. The route refreshes the dashboard every 5s while a scan is
 * running, or while a scan this session started has not finished, and stops as
 * soon as it lands. This component takes `live` and renders that it is
 * watching, so an auto-updating page says so rather than appearing to move on
 * its own. The rules are in `lib/dashboard/polling.ts`.
 *
 * There is still no progress BAR, for the reason the intake screen gives: we
 * cannot measure real progress, and a fake one is a lie the user will notice.
 * Phase-level progress needs the Epic 9.1 phase table exposed by the API, which
 * is not built, and no amount of polling substitutes for it.
 */

import type { JSX } from 'react';
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  ErrorState,
  LuminanceLedger,
  ScoreMeter,
  StatRow,
  StatTile,
} from '@avp/design-system';
import type { BadgeTone, Column, LedgerDimension, ScoreAbsence } from '@avp/design-system';
import type { Dashboard, ScanStatus, ScanSummary } from '@avp/shared-types';
import { formatStamp } from '@/lib/dates';

/**
 * Scan statuses that block a re-run.
 *
 * **RUNNING only — deliberately not QUEUED.** Epic 9.6.
 *
 * QUEUED used to be here too, on the reasonable-sounding grounds that a queued
 * scan is about to run. Measured, it is not a state a real scan spends any time
 * in: under the background executor the QUEUED → RUNNING transition takes 1.1ms
 * (median of 20, 4.0ms worst) against a RUNNING phase of ~303s. Roughly one
 * part in 275,000.
 *
 * The only QUEUED scan that lasts is the one detection leaves behind. Running
 * competitor detection opens a scan for the CompetitorSet to hang off
 * (`get_or_create_scan`), and if no scan is run afterwards that row stays open
 * indefinitely — so treating QUEUED as busy disabled re-run for that client
 * forever, on the strength of a scan nobody had started. Worse, it read as
 * "Queued", which looks correct and does not invite the question.
 *
 * Offering re-run on it is not merely harmless, it is the point: the placeholder
 * exists precisely so a later scan reuses it, and re-run is what runs it.
 *
 * This is safe because the double-spend defence is no longer this list.
 * `uq_scans_one_open_per_client` makes a second open scan impossible, and a
 * losing request adopts the winner's row (Epic 9.6), so a click inside that
 * 1.1ms window returns the same scan rather than buying another. This flag now
 * decides only whether offering the action would confuse, not whether it costs.
 */
const BLOCKS_RERUN: readonly ScanStatus[] = ['running'];

/**
 * Statuses where a missing score means "not yet" rather than "not at all".
 *
 * Both render without a number, but they are different facts and the screen
 * says which: a queued scan has no score because nothing has run, while a
 * finished scan with no score was either never scored or scored as
 * INSUFFICIENT_DATA — a permanent state until it is re-run. Rendering both as
 * one dash, as this screen did until Epic 9.9, made a real outcome look like a
 * rendering fault.
 */
const SCORE_PENDING: readonly ScanStatus[] = ['queued', 'running'];

const STATUS_LABEL: Record<ScanStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Complete',
  // PARTIAL is a real outcome, not an edge case: some engines answered and
  // others did not. It is shown as its own state rather than folded into
  // either success or failure, because the score behind it is measured on
  // fewer answers and the report says so.
  partial: 'Partial',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const STATUS_TONE: Record<ScanStatus, BadgeTone> = {
  queued: 'neutral',
  running: 'beacon',
  succeeded: 'success',
  partial: 'warn',
  failed: 'danger',
  cancelled: 'neutral',
};

/**
 * Re-exported so this module's existing callers and its test are unchanged.
 *
 * The implementation moved to `lib/dates.ts` in Epic 9.14, when the seat panel
 * became the second screen that needed UTC formatting. It was moved rather
 * than copied: the reason it exists at all — locale formatting differs between
 * CI and a browser — applies just as much to the second screen, and two copies
 * would drift where one cannot.
 */
export { formatStamp };

export interface DashboardViewProps {
  dashboard: Dashboard;
  /** Starts a scan for a client. Omitted in read-only renders and in tests. */
  onRerun?: (clientId: string) => void;
  /** Clients whose re-run request is currently in flight from this browser. */
  rerunning?: ReadonlySet<string>;
  /** A failed re-run, surfaced above the table rather than swallowed. */
  rerunError?: string | null;
  /** The page is refreshing itself. Told to the user, not just done to them. */
  live?: boolean;
  /** Polling has been failing. Surfaced, because a silently stale page lies. */
  pollProblem?: string | null;
}

export function DashboardView({
  dashboard,
  onRerun,
  rerunning,
  rerunError,
  live,
  pollProblem,
}: DashboardViewProps): JSX.Element {
  const { agency, seats, clientCount, scanCount, recentScans, isEmpty } = dashboard;

  // A client is busy if ANY of its scans is running — not just this row's. Two
  // rows for the same client must not offer a re-run because the older one
  // happens to have finished.
  const busyClients = new Set(
    recentScans.filter((s) => BLOCKS_RERUN.includes(s.status)).map((s) => s.clientId),
  );

  const columns: readonly Column<ScanSummary>[] = [
    {
      key: 'client',
      header: 'Client',
      render: (scan) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-ui-base font-medium text-text-primary">{scan.clientName}</span>
          <span className="font-mono text-ui-xs text-text-tertiary">{scan.clientDomain}</span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (scan) => (
        // `live` on the running one only. This screen re-reads itself every 5s
        // while a scan is in flight and the row it is waiting on is otherwise
        // indistinguishable from a finished one — same chip, same weight. A
        // badge that breathes says which row the page is watching, and it is
        // the only motion here that runs without anybody doing anything,
        // because it is the only thing that is genuinely still happening.
        <Badge tone={STATUS_TONE[scan.status]} live={scan.status === 'running'}>
          {STATUS_LABEL[scan.status]}
        </Badge>
      ),
    },
    {
      key: 'score',
      header: 'Visibility',
      render: (scan) => (
        <ScoreCell composite={scan.compositeScore} status={scan.status} />
      ),
    },
    {
      key: 'started',
      header: 'Started',
      render: (scan) => (
        <span className="text-ui-sm text-text-secondary">{formatStamp(scan.createdAt)}</span>
      ),
    },
    {
      key: 'finished',
      header: 'Finished',
      render: (scan) => (
        <span className="text-ui-sm text-text-secondary">{formatStamp(scan.finishedAt)}</span>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      align: 'end',
      render: (scan) => (
        <div className="flex items-center justify-end gap-2">
          <a
            // Underline offset and an eased hover, on the same 120ms every
            // button and nav item in this product has used since Epic 0. It
            // was the one interactive thing on the screen still behaving
            // exactly like an unstyled anchor.
            className="text-ui-sm font-medium text-beacon-600 underline underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-700"
            href={`/scans/${scan.id}/report`}
          >
            View report
          </a>
          <RerunButton
            scan={scan}
            busy={busyClients.has(scan.clientId)}
            starting={rerunning?.has(scan.clientId) ?? false}
            onRerun={onRerun}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-6 border-b border-line-hairline pb-8">
        <div>
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">Agency</p>
          <h1 className="mt-2 font-editorial text-ed-sm leading-display tracking-display text-text-primary">
            {agency.name}
          </h1>
        </div>
        <DashboardStats
          seats={seats}
          clientCount={clientCount}
          scanCount={scanCount}
          recentScans={recentScans}
        />
      </header>

      {/*
        Both of these were the same Card + title + detail written out longhand,
        which is what `ErrorState` now owns app-wide (Epic 9.11). The copy stays
        here rather than moving into the component: only this screen knows that
        a poll failure means the page is stale rather than broken.
      */}
      {pollProblem != null && (
        <ErrorState title="This page has stopped updating" detail={pollProblem} />
      )}

      {rerunError != null && (
        <ErrorState title="The scan could not be started" detail={rerunError} />
      )}

      {isEmpty ? <EmptyAgency /> : (
        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-ui-md font-medium text-text-primary">Recent scans</h2>
            {live === true && (
              <p className="flex items-center gap-3 text-ui-sm text-text-tertiary">
                <Badge tone="beacon" live>
                  Live
                </Badge>
                A scan is running — this page updates itself.
              </p>
            )}
          </div>
          <DataTable
            columns={columns}
            rows={recentScans}
            rowKey={(scan) => scan.id}
            caption="Newest first."
            emptyMessage={<NoScansYet clientCount={clientCount} />}
          />
        </section>
      )}
    </div>
  );
}

/**
 * The header figures — Epic 9.24.
 *
 * WHAT CHANGED, AND WHAT DID NOT
 * ------------------------------
 * Three numbers in a caption strip became six tiles carrying the accent layer.
 * **No new request is made and nothing new is computed from outside this
 * screen.** Seats, Clients and Scans are the same three fields the endpoint has
 * always served; the other three count `recentScans`, the array already in hand
 * and already rendered as a table two hundred pixels below. The header now says
 * what the table says, at a glance, which is what a Working screen is for.
 *
 * This is the answer to "big empty margins" that the brief asked for: the space
 * is holding something real rather than being narrowed away. Epic 9.21 settled
 * that narrowing these screens moves nothing.
 *
 * HONEST ABSENCE IS PRESERVED. "Running" counts scans whose status is running —
 * a real state, never inferred. "Needs attention" counts FAILED and PARTIAL,
 * both of which are outcomes the product already names; it is emphasised only
 * when it is non-zero, so a healthy agency does not get a red-ish tile shouting
 * a zero at it. Nothing here invents a measurement.
 */
function DashboardStats({
  seats,
  clientCount,
  scanCount,
  recentScans,
}: {
  seats: Dashboard['seats'];
  clientCount: number;
  scanCount: number;
  recentScans: readonly ScanSummary[];
}): JSX.Element {
  const running = recentScans.filter((s) => s.status === 'running').length;
  const attention = recentScans.filter(
    (s) => s.status === 'failed' || s.status === 'partial',
  ).length;
  // Only over scans that HAVE a score. A scan without one is not a zero — the
  // same rule ScoreCell follows — so it is excluded from the average rather
  // than dragging it down.
  const scored = recentScans
    .map((s) => (s.compositeScore == null ? null : Number(s.compositeScore)))
    .filter((n): n is number => n != null && !Number.isNaN(n));
  const median =
    scored.length === 0
      ? null
      : [...scored].sort((a, b) => a - b)[Math.floor((scored.length - 1) / 2)]!;

  return (
    <StatRow>
      <StatTile label="Seats" value={`${seats.used} / ${seats.limit}`} accent={0} />
      <StatTile label="Clients" value={String(clientCount)} accent={1} />
      <StatTile label="Scans" value={String(scanCount)} accent={2} />
      <StatTile
        label="Running now"
        value={String(running)}
        accent={3}
        note={running > 0 ? 'This page is updating itself.' : undefined}
      />
      <StatTile
        label="Needs attention"
        value={String(attention)}
        accent={4}
        emphasis={attention > 0}
        note={attention > 0 ? 'Failed or partial, in the list below.' : undefined}
      />
      <StatTile
        label="Median visibility"
        /*
          "No scores yet", not "Not scored" — Epic 9.24. `ScoreMeter`'s
          vocabulary ("Measuring" / "Not scored") describes ONE SCAN's state,
          and this is an aggregate that has no scans to average. Reusing the
          per-scan phrase here would say something false about a scan, and it
          also collided with a real guarantee: DashboardView.test.tsx asserts a
          queued row reads "Measuring" and NOT "Not scored" anywhere on the
          page. Getting the words right kept that assertion at full strength
          instead of narrowing it to make room for this tile.
        */
        value={median == null ? 'No scores yet' : median.toFixed(0)}
        /*
          NO ACCENT, unlike the five counts beside it — Epic 9.24.

          This tile's value is a SCORE, and this product already has a colour
          language for a score: the visibility ramp, where hue means how visible
          you are. Wrapping a score in a categorical hue puts two colour
          languages on one tile and invites the reading that the tile's colour
          says something about the number. The bench hues sit 30 degrees clear
          of every ramp stop precisely so they cannot be confused with one;
          spending that separation on decoration around an actual score would
          give it away. Counts take accents. Measurements do not.
        */
        // Says WHAT it is the median of. A bare median across "recent scans"
        // with no denominator is a number nobody can check.
        note={
          median == null
            ? 'No scan here has produced a score yet.'
            : `Across ${scored.length} scored ${scored.length === 1 ? 'scan' : 'scans'} below.`
        }
      />
    </StatRow>
  );
}

/**
 * The score cell — `ScoreMeter`, Epic 9.9.
 *
 * `compositeScore` is a string-encoded decimal and is null for BOTH "not scored
 * yet" and INSUFFICIENT_DATA. Neither is a zero, and the endpoint left-joins
 * the score precisely so those scans still appear.
 *
 * It previously rendered a bare em dash for both, which read as a rendering
 * fault rather than a fact. `ScoreMeter` draws an empty track and NAMES the
 * absence, and `status` is what separates the two: a scan still in flight is
 * "Measuring", a finished one without a score is "Not scored". That
 * distinction is read off data the endpoint already serves — nothing is
 * inferred and nothing is invented.
 *
 * The meter carries the same identity as the report's Luminance Ledger: lit
 * length IS the score. That is why this screen and the report now read as one
 * product rather than a table that links to a document.
 */
function ScoreCell({
  composite,
  status,
}: {
  composite: string | null | undefined;
  status: ScanStatus;
}): JSX.Element {
  const absence: ScoreAbsence = SCORE_PENDING.includes(status) ? 'measuring' : 'unscored';
  const value = composite == null ? null : Number(composite);
  return <ScoreMeter score={value == null || Number.isNaN(value) ? null : value} absence={absence} />;
}

function RerunButton({
  scan,
  busy,
  starting,
  onRerun,
}: {
  scan: ScanSummary;
  busy: boolean;
  starting: boolean;
  onRerun: ((clientId: string) => void) | undefined;
}): JSX.Element {
  const disabled = busy || starting || onRerun == null;
  const label = starting ? 'Starting…' : busy ? 'Running…' : 'Re-run';
  return (
    <Button
      size="sm"
      variant="secondary"
      disabled={disabled}
      aria-label={`Re-run scan for ${scan.clientName}`}
      // A double click would not be caught by the server: get_or_create_scan
      // reuses an unfinished scan, but the first request has not committed one
      // yet, so a second request would create a SECOND scan and spend a second
      // scan's worth of model calls. This disable is the only guard available
      // from here — see the module note.
      onClick={onRerun == null ? undefined : () => onRerun(scan.clientId)}
    >
      {label}
    </Button>
  );
}

/**
 * THE FIVE DIMENSIONS, UNLIT — the figure on an empty dashboard.
 *
 * Not example data and not a placeholder score: these are the real dimensions
 * and the real §6 weights from `scoring-spec.md`, the same five the landing
 * page publishes and the same five every report is built from. What is missing
 * is the only thing that could be missing here — the measurements — so every
 * sub-score is zero and the column draws as an unlit void with the weights
 * named beside it.
 *
 * That is the point of drawing it at all. The palette's organising idea is
 * that visibility IS luminance, and a brand-new agency's dashboard is the one
 * screen in the product where nothing is lit yet. Showing the shape of the
 * scan unlit says what a scan will produce and how much each part of it is
 * worth, using this product's own chart rather than a graphic imported from
 * nowhere (ip-safety.md #4).
 *
 * `unmeasured` is what keeps it honest: without it the Ledger would tell a
 * screen reader "AI Visibility Score: 0 out of 100", which is a measurement
 * nobody took. See LuminanceLedger.tsx.
 */
const UNMEASURED_DIMENSIONS: readonly LedgerDimension[] = [
  { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 0 },
  { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 0 },
  { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 0 },
  { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 0 },
  { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 0 },
];

/** A brand new agency: no clients, no scans, nothing to list. */
function EmptyAgency(): JSX.Element {
  return (
    <EmptyState
      eyebrow="Nothing measured yet"
      title="No scans yet"
      body="A scan starts with a website. We read the site the way a buyer would, work out who it competes with, then ask AI assistants the questions its buyers ask — and record who they name."
      note="A full scan takes about six minutes. The five bars are the dimensions it fills in, sized by how much each is worth."
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          Add your first client
        </Button>
      }
      figure={
        <LuminanceLedger
          subjectName="Your first client"
          dimensions={UNMEASURED_DIMENSIONS}
          height={260}
          unmeasured
          // No dim-to-lit dissolve: there is nothing to light. Passing
          // `animate` would also make this screen perform on every load, which
          // is exactly what the dashboard is excluded from.
          animate={false}
        />
      }
    />
  );
}

/**
 * Clients exist, but none has been scanned.
 *
 * A distinct state from `isEmpty`: the endpoint sets `isEmpty` only when the
 * agency has neither clients nor scans, so this gap is reachable and would
 * otherwise render as a blank table with no explanation.
 */
function NoScansYet({ clientCount }: { clientCount: number }): JSX.Element {
  return (
    <EmptyState
      // No eyebrow. This one sits directly under the "Recent scans" heading
      // and inside that section's own table, so a structural label here would
      // be the same words twice, six lines apart.
      title={
        clientCount === 1
          ? 'One client added, but no scans have been run yet.'
          : `${clientCount} clients added, but no scans have been run yet.`
      }
      body="A client is a website we know about; a scan is what measures it. Until one runs there is no score, no competitor set and no report to send."
      action={
        <Button variant="secondary" onClick={() => window.location.assign('/')}>
          Run the first scan
        </Button>
      }
    />
  );
}
