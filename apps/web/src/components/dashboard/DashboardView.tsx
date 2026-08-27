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
import { Badge, Button, Card, CardBody, DataTable, ScoreMeter } from '@avp/design-system';
import type { BadgeTone, Column, ScoreAbsence } from '@avp/design-system';
import type { Dashboard, ScanStatus, ScanSummary } from '@avp/shared-types';

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

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

/**
 * Format an ISO timestamp in UTC.
 *
 * Deliberately not `toLocaleDateString`: that varies with the host's locale and
 * timezone, which would make this component render differently in CI than in a
 * browser and make the assertions below untrustworthy.
 */
export function formatStamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  const day = String(at.getUTCDate()).padStart(2, '0');
  const hh = String(at.getUTCHours()).padStart(2, '0');
  const mm = String(at.getUTCMinutes()).padStart(2, '0');
  return `${day} ${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()}, ${hh}:${mm} UTC`;
}

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
      render: (scan) => <Badge tone={STATUS_TONE[scan.status]}>{STATUS_LABEL[scan.status]}</Badge>,
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
            className="text-ui-sm font-medium text-beacon-600 underline"
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
      <header className="flex flex-wrap items-end justify-between gap-6 border-b border-line-hairline pb-8">
        <div>
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">Agency</p>
          <h1 className="mt-2 font-editorial text-ed-sm leading-display tracking-display text-text-primary">
            {agency.name}
          </h1>
        </div>
        <dl className="flex flex-wrap items-end gap-8">
          <Stat label="Seats" value={`${seats.used} / ${seats.limit}`} />
          <Stat label="Clients" value={String(clientCount)} />
          <Stat label="Scans" value={String(scanCount)} />
        </dl>
      </header>

      {pollProblem != null && (
        <Card elevation="seated">
          <CardBody>
            <p className="text-ui-md font-medium text-text-primary">
              This page has stopped updating
            </p>
            <p className="mt-2 text-ui-base leading-prose text-text-secondary">{pollProblem}</p>
          </CardBody>
        </Card>
      )}

      {rerunError != null && (
        <Card elevation="seated">
          <CardBody>
            <p className="text-ui-md font-medium text-text-primary">The scan could not be started</p>
            <p className="mt-2 text-ui-base leading-prose text-text-secondary">{rerunError}</p>
          </CardBody>
        </Card>
      )}

      {isEmpty ? <EmptyAgency /> : (
        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-ui-md font-medium text-text-primary">Recent scans</h2>
            {live === true && (
              <p className="text-ui-sm text-text-tertiary">
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

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{label}</dt>
      <dd className="text-ui-lg font-medium text-text-primary">{value}</dd>
    </div>
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

/** A brand new agency: no clients, no scans, nothing to list. */
function EmptyAgency(): JSX.Element {
  return (
    <Card elevation="seated">
      <CardBody>
        <h2 className="font-editorial text-ed-2xs leading-display text-text-primary">
          No scans yet
        </h2>
        <p className="mt-3 max-w-measure text-ui-base leading-prose text-text-secondary">
          A scan starts with a website. We read the site the way a buyer would, work out who
          it competes with, then ask AI assistants the questions its buyers ask — and record
          who they name.
        </p>
        <p className="mt-3 max-w-measure text-ui-sm leading-prose text-text-tertiary">
          A full scan takes about six minutes.
        </p>
        <div className="mt-5">
          <Button variant="primary" onClick={() => window.location.assign('/')}>
            Add your first client
          </Button>
        </div>
      </CardBody>
    </Card>
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
    <span className="text-ui-base text-text-secondary">
      {clientCount === 1
        ? 'One client added, but no scans have been run yet.'
        : `${clientCount} clients added, but no scans have been run yet.`}
    </span>
  );
}
