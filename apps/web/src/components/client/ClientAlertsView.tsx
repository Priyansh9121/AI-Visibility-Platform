'use client';

/**
 * A client's Alerts screen — Epic E.
 *
 * WHAT IT SHOWS
 * -------------
 * What changed between a scan and the scan it was compared against. Generated
 * once at the end of a scan and read here — never recomputed, because an alert
 * that re-derived on every request could appear and disappear between two page
 * loads of the same data, and an acknowledgement would have nothing stable to
 * attach to.
 *
 * NEWEST FIRST, unlike the trends. A trend reads left to right from its
 * earliest point; this is a LOG, and the entry an operator is looking for is
 * the most recent one. `ClientPromptsView` orders itself the same way.
 *
 * AN EMPTY FEED IS AMBIGUOUS, SO THIS SCREEN REFUSES TO LEAVE IT AMBIGUOUS
 * ------------------------------------------------------------------------
 * An alert needs a baseline scan at least `minBaselineHours` older. Most
 * clients have exactly one scan and can therefore never have an alert; a client
 * whose only other scan is a re-run from an hour ago is in the same position.
 * "No alerts" would read as an all-clear in both cases, and the data supports
 * that reading in neither. So the empty state reports which situation it is,
 * and says why — the same discipline Epic B's `subjectCitable` applies to a
 * claim about citations.
 *
 * Pure and prop-driven, so every state is reachable by a static render.
 */

import { useState, type JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  MetaChip,
  CardBody,
  EmptyState,
  ErrorState,
  LoadingState,
  StatRow,
  StatTile,
} from '@avp/design-system';
import type { Alert, AlertFeed, Me } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import { accentFor } from '@/components/client/clientNav';
import {
  latestComposite,
  latestScanId,
  type ClientDetailState,
} from '@/components/client/ClientDetailView';
import { engineShort } from '@/lib/client/engines';
import { Bot, CalendarDays, Check } from 'lucide-react';

/** The screen's accent, read from the nav table — never a second literal. */
const ACCENT = accentFor('alerts') ?? 0;
const TILE = [ACCENT, ACCENT + 1, ACCENT + 2] as const;

/**
 * What each kind is called on screen, and the one line explaining it.
 *
 * The labels say what was MEASURED, matching the enum. Two of them are
 * deliberately not what the epic's brief asked for — "net tone turning
 * negative" has never once happened in real data, and a citation cannot change
 * hands — and the copy reflects what the rule actually detects rather than what
 * it was originally called.
 */
const KIND: Record<string, { label: string; tone: 'warn' | 'danger' | 'neutral' }> = {
  visibility_drop: { label: 'Visibility fell', tone: 'warn' },
  sentiment_decline: { label: 'Tone declined', tone: 'warn' },
  owned_citation_lost: { label: 'Own citation lost', tone: 'warn' },
};

export type AlertsState =
  | { kind: 'loading' }
  | { kind: 'ready'; feed: AlertFeed }
  | { kind: 'error'; title: string; detail: string };

export function ClientAlertsView({
  state,
  alerts,
  me,
  onAcknowledge,
  acknowledging,
  failed,
}: {
  state: ClientDetailState;
  alerts: AlertsState;
  me: Me | null;
  onAcknowledge?: ((alertId: string) => void) | undefined;
  /** Ids currently in flight, so a row can say so rather than looking inert. */
  acknowledging?: ReadonlySet<string> | undefined;
  /** Ids whose last acknowledge attempt failed. Said on the row, not swallowed. */
  failed?: ReadonlySet<string> | undefined;
}): JSX.Element {
  if (state.kind === 'loading' || state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="alerts"
        latestReportScanId={null}
      >
        {state.kind === 'loading' ? (
          <LoadingState message="Loading this client…" />
        ) : (
          <ErrorState title={state.title} detail={state.detail} />
        )}
      </ClientSpace>
    );
  }

  const { client, history } = state;
  return (
    <ClientSpace
      client={client}
      me={me}
      current="alerts"
      latestReportScanId={latestScanId(history)}
      latestScore={latestComposite(history)}
    >
      <AlertsBody
        alerts={alerts}
        onAcknowledge={onAcknowledge}
        acknowledging={acknowledging}
        failed={failed}
      />
    </ClientSpace>
  );
}

function AlertsBody({
  alerts,
  onAcknowledge,
  acknowledging,
  failed,
}: {
  alerts: AlertsState;
  onAcknowledge?: ((alertId: string) => void) | undefined;
  acknowledging?: ReadonlySet<string> | undefined;
  failed?: ReadonlySet<string> | undefined;
}): JSX.Element {
  if (alerts.kind === 'loading') return <LoadingState message="Reading alerts…" />;
  if (alerts.kind === 'error') {
    return <ErrorState title={alerts.title} detail={alerts.detail} />;
  }

  const { feed } = alerts;
  if (feed.alerts.length === 0) return <NothingToReport feed={feed} />;

  return (
    <Feed
      feed={feed}
      onAcknowledge={onAcknowledge}
      acknowledging={acknowledging}
      failed={failed}
    />
  );
}

function Feed({
  feed,
  onAcknowledge,
  acknowledging,
  failed,
}: {
  feed: AlertFeed;
  onAcknowledge?: ((alertId: string) => void) | undefined;
  acknowledging?: ReadonlySet<string> | undefined;
  failed?: ReadonlySet<string> | undefined;
}): JSX.Element {
  const [showAcknowledged, setShowAcknowledged] = useState(false);
  /*
   * Rows acknowledged in THIS session stay on screen.
   *
   * Without this the row disappears the instant it is acknowledged — it drops
   * out of `outstanding` and the rows below jump up under the cursor. On the
   * real data that is not a corner case: Notion's tone event produced three
   * alerts on one scan, so acknowledging the first makes the other two move
   * while the operator is reading them.
   *
   * The fix is not to animate the exit. It is to not have one: the row stays
   * where it is, visibly settled, and filters out on the next load. Nothing
   * moves, and the state change is still legible.
   */
  const [justAcknowledged, setJustAcknowledged] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const outstanding = feed.alerts.filter((a) => a.acknowledgedAt == null);
  const shown = showAcknowledged
    ? feed.alerts
    : feed.alerts.filter(
        (a) => a.acknowledgedAt == null || justAcknowledged.has(a.id),
      );
  const settled = feed.alerts.length - outstanding.length;

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-display text-ed-xs leading-display tracking-display text-text-primary">
          What changed since the last comparable scan
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          Each entry compares one scan against the most recent scan at least{' '}
          {feed.minBaselineHours} hours older. Scans closer together than that
          are not compared: two runs an hour apart measure the same reality
          twice, and the difference between them is the engines answering
          differently, not the picture changing.
        </p>
      </div>

      <StatRow min="11rem">
        <StatTile
          label="Outstanding"
          value={String(feed.unacknowledged)}
          accent={TILE[0]}
          emphasis={feed.unacknowledged > 0}
          note="Not yet acknowledged by anyone."
        />
        <StatTile
          label="Acknowledged"
          value={String(settled)}
          accent={TILE[1]}
          note="Seen. Kept, not deleted."
        />
        <StatTile
          label="Scans compared"
          value={`${feed.scansCompared} / ${feed.scansTotal}`}
          accent={TILE[2]}
          note="Scans that had an old enough baseline to compare against."
        />
      </StatRow>

      {settled > 0 && (
        <div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowAcknowledged((v) => !v)}
          >
            {showAcknowledged
              ? `Hide ${settled} acknowledged`
              : `Show ${settled} acknowledged`}
          </Button>
        </div>
      )}

      <ol className="flex flex-col gap-3">
        {shown.map((alert) => (
          <li key={alert.id}>
            <AlertRow
              alert={alert}
              onAcknowledge={
                onAcknowledge == null
                  ? undefined
                  : (id) => {
                      setJustAcknowledged((prev) => new Set(prev).add(id));
                      onAcknowledge(id);
                    }
              }
              pending={acknowledging?.has(alert.id) ?? false}
              failed={failed?.has(alert.id) ?? false}
            />
          </li>
        ))}
      </ol>
    </section>
  );
}

function AlertRow({
  alert,
  onAcknowledge,
  pending,
  failed,
}: {
  alert: Alert;
  onAcknowledge?: ((alertId: string) => void) | undefined;
  pending: boolean;
  /**
   * The last acknowledge attempt failed.
   *
   * Said on the row rather than swallowed. A button that re-enables with
   * nothing else changed reads as "the click did not register", so the
   * operator clicks again — and keeps clicking.
   */
  failed: boolean;
}): JSX.Element {
  const kind = KIND[alert.kind] ?? { label: alert.kind, tone: 'neutral' as const };
  const seen = alert.acknowledgedAt != null;

  return (
    <Card className={seen ? 'avp-alert is-acknowledged' : 'avp-alert'}>
      <CardBody>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="flex flex-wrap items-center gap-2">
              <Badge tone={seen ? 'neutral' : kind.tone}>{kind.label}</Badge>
              {/*
                The engine, in the fact's shape — Epic 16.2. Epic E made it
                secondary rather than tertiary because when three rows share
                the badge "Tone declined" the ENGINE is the only thing telling
                them apart, and the discriminator cannot be the quietest thing
                in the row. A chip keeps that and adds the distinction the
                three-pill rule draws: the badge is a state, this is a fact.
              */}
              {alert.engine != null && (
                <MetaChip icon={<Bot />}>{engineShort(alert.engine)}</MetaChip>
              )}
              {seen && <MetaChip icon={<Check />}>Acknowledged</MetaChip>}
            </span>
            {/* OUR sentence about OUR numbers — see the API's facts-only note. */}
            <p className="max-w-measure text-ui-base text-text-primary">{alert.detail}</p>
            {/* The two scans compared, as one fact rather than a caps line. */}
            <p>
              <MetaChip icon={<CalendarDays />}>
                <time dateTime={alert.scannedAt}>{fmtDate(alert.scannedAt)}</time>
                {' vs '}
                <time dateTime={alert.baselineScannedAt}>{fmtDate(alert.baselineScannedAt)}</time>
              </MetaChip>
            </p>
          </div>
          {!seen && onAcknowledge != null && (
            <div className="flex flex-col items-end gap-1">
              <Button
                variant="secondary"
                size="sm"
                className="avp-alert__ack"
                disabled={pending}
                onClick={() => onAcknowledge(alert.id)}
              >
                {pending ? 'Acknowledging…' : failed ? 'Try again' : 'Acknowledge'}
              </Button>
              {failed && (
                <span
                  role="alert"
                  className="avp-alert__failure text-ui-2xs text-danger"
                >
                  That did not save.
                </span>
              )}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * Nothing in the feed — and the two reasons for that are different findings.
 *
 * A client whose scans were never comparable has not been checked; a client
 * whose scans WERE compared and produced nothing has been checked and is fine.
 * Showing the same words for both would claim the first is the second.
 */
function NothingToReport({ feed }: { feed: AlertFeed }): JSX.Element {
  const compared = feed.scansCompared > 0;

  return (
    <EmptyState
      eyebrow="Alerts"
      title={compared ? 'Nothing has changed enough to report' : 'Nothing compared yet'}
      body={
        compared
          ? `Every scan with an old enough baseline was checked against it, and nothing moved beyond the thresholds. This is an all-clear, not an absence of data.`
          : `An alert compares a scan against the most recent scan at least ${feed.minBaselineHours} hours older, and this client does not have two scans that far apart yet. Nothing has been checked — which is not the same as nothing being wrong.`
      }
      note={
        compared
          ? undefined
          : `${feed.scansTotal} scan${feed.scansTotal === 1 ? '' : 's'} so far, none with a comparable baseline.`
      }
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          Run a scan
        </Button>
      }
    />
  );
}
