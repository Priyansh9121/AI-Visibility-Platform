/**
 * The agency dashboard — Epic 9.3, slice 2 of Epic 9.
 *
 * Presentational and pure, so it can be rendered to static markup and asserted
 * over the way ReportView is. The route owns fetching and re-run state.
 *
 * WHY THERE IS NO LIVE PROGRESS HERE
 * ----------------------------------
 * Epic 9.1 concluded slice 2 "must show progress, not wait", and at Epic 9.2's
 * measured 361.3s that instinct is still right. But progress is not buildable
 * against today's API, and the reason is structural rather than a matter of
 * taste:
 *
 *   POST /clients/{clientId}/scans runs the entire pipeline INLINE and calls
 *   db.commit() once, after the scan finishes (routers/scans.py). The scan row
 *   is created inside that uncommitted transaction, so no other request can
 *   see it while it runs.
 *
 * GET /api/v1/dashboard therefore cannot observe a scan in flight — a running
 * scan is invisible until the moment it completes, at which point it appears
 * already finished. Polling would poll for a status that cannot exist yet, so
 * this screen does not poll. It states the wait honestly instead, following the
 * precedent set on the intake screen: no progress bar, because we cannot
 * measure real progress and a fake one is a lie the user will notice.
 *
 * Making progress real is a backend change — scans have to become a queued job
 * that commits QUEUED before it starts working. That is out of scope here and
 * recorded in the build log.
 */

import type { JSX } from 'react';
import { Badge, Button, Card, CardBody, DataTable, VisibilityBadge } from '@avp/design-system';
import type { BadgeTone, Column } from '@avp/design-system';
import type { Dashboard, ScanStatus, ScanSummary } from '@avp/shared-types';

/** Scan statuses that mean "work is already under way for this client". */
const IN_FLIGHT: readonly ScanStatus[] = ['queued', 'running'];

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
}

export function DashboardView({
  dashboard,
  onRerun,
  rerunning,
  rerunError,
}: DashboardViewProps): JSX.Element {
  const { agency, seats, clientCount, scanCount, recentScans, isEmpty } = dashboard;

  // A client is busy if ANY of its scans is unfinished — not just this row's.
  // Two rows for the same client must not offer a re-run because the older one
  // happens to have finished.
  const busyClients = new Set(
    recentScans.filter((s) => IN_FLIGHT.includes(s.status)).map((s) => s.clientId),
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
      align: 'end',
      render: (scan) => <ScoreCell composite={scan.compositeScore} />,
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
          <h2 className="text-ui-md font-medium text-text-primary">Recent scans</h2>
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
 * The score cell.
 *
 * `compositeScore` is a string-encoded decimal and is null for BOTH "not scored
 * yet" and INSUFFICIENT_DATA. Neither is a zero, and the endpoint left-joins
 * the score precisely so those scans still appear — so the cell renders an
 * em dash and says why, rather than a 0 that reads as "invisible".
 */
function ScoreCell({ composite }: { composite: string | null | undefined }): JSX.Element {
  if (composite == null) {
    return (
      <span className="text-ui-sm text-text-tertiary" title="No score for this scan yet">
        —
      </span>
    );
  }
  const value = Number(composite);
  if (Number.isNaN(value)) {
    return <span className="text-ui-sm text-text-tertiary">—</span>;
  }
  return (
    <span className="flex items-center justify-end gap-2">
      <span className="font-mono text-ui-base text-text-primary">{value.toFixed(1)}</span>
      <VisibilityBadge score={value} />
    </span>
  );
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
