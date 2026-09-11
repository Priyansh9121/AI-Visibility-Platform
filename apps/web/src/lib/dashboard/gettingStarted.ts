/**
 * The getting-started checklist, derived — Epic 19.
 *
 * WHAT THIS IS
 * ------------
 * Five things that make an agency a working customer of this product, each
 * read off the dashboard response and nothing else: a client exists, a scan
 * has run, a scan has been scored, a second seat is filled, a report has a
 * live share link. The dashboard already carried four of the facts; Epic 19
 * added the two counts `recentScans` cannot answer, because it is a page and
 * not a history.
 *
 * DERIVED, NOT STORED. `DashboardStats`'s rule — *no new request is made and
 * nothing new is computed from outside this screen* — applied to onboarding.
 * There is no `onboarding_progress` table and no per-step flag; a step is
 * done when the account is in that state, and stops being done when it
 * leaves it. Delete every client and the first step unlights; revoke the
 * only share link and the last one does. The checklist describes the account
 * as it is, not a diary of what once happened. The one stored fact is
 * dismissal, because that is a decision and not a state.
 *
 * THE STEPS ARE THIS PRODUCT'S, NOT ANYBODY ELSE'S
 * ------------------------------------------------
 * The competitor research (docs/competitor-research-2026-09-10.md) is the
 * evidence that a checklist with a visible count is a pattern worth having.
 * It is not where these steps come from. They were read off what an agency
 * actually does here between signing up and sending a client a report, and
 * every one is checkable against a column that exists. Candidates that were
 * not are recorded in build-log.md: "view a report" (a GET, unrecorded),
 * "download a PDF" (the same), and anything naming a feature this product
 * does not have.
 *
 * Framework-free, so it is tested with plain objects.
 */

import type { Dashboard } from '@avp/shared-types';

export type GettingStartedKey = 'client' | 'scan' | 'score' | 'teammate' | 'share';

export interface GettingStartedAction {
  label: string;
  href: string;
}

export interface GettingStartedStep {
  key: GettingStartedKey;
  /** A verb phrase. Never "Step 1". */
  label: string;
  done: boolean;
  /** What is true right now. A fact, not an instruction. */
  detail: string;
  /** The way to do it, when there is one to press. */
  action?: GettingStartedAction;
}

export interface GettingStarted {
  /** Whether the dashboard should render it at all. */
  visible: boolean;
  complete: boolean;
  lit: number;
  total: number;
  /** "Two of five lit." — the count, in words, for the head and for a screen reader. */
  lead: string;
  steps: readonly GettingStartedStep[];
}

export const GETTING_STARTED_TITLE = 'Getting started';
export const GETTING_STARTED_COMPLETE_TITLE = 'Up and running';
export const GETTING_STARTED_COMPLETE_LEAD =
  'All five are lit. Close this and the dashboard is yours.';

const WORDS = ['none', 'one', 'two', 'three', 'four', 'five'] as const;

const plural = (n: number, noun: string): string => `${n} ${noun}${n === 1 ? '' : 's'}`;

export function deriveGettingStarted(dashboard: Dashboard): GettingStarted {
  const {
    clientCount,
    scanCount,
    scoredScanCount,
    sharedScanCount,
    seats,
    recentScans,
    gettingStartedDismissed,
  } = dashboard;

  const running = recentScans.some((s) => s.status === 'running' || s.status === 'queued');
  const latestScored = recentScans.find((s) => s.compositeScore != null);
  const freeSeats = Math.max(0, seats.limit - seats.used);

  const client: GettingStartedStep = {
    key: 'client',
    label: 'Add a client',
    done: clientCount >= 1,
    detail: clientCount >= 1 ? plural(clientCount, 'client') : 'A website we read the way a buyer would.',
    ...(clientCount >= 1 ? {} : { action: { label: 'Add your first client', href: '/' } }),
  };

  const scan: GettingStartedStep = {
    key: 'scan',
    label: 'Run a scan',
    done: scanCount >= 1,
    detail:
      scanCount >= 1
        ? running
          ? 'One is running now.'
          : plural(scanCount, 'scan')
        : clientCount >= 1
          ? 'About six minutes from start to score.'
          : 'After a client is added.',
    ...(scanCount >= 1 || clientCount === 0
      ? {}
      : { action: { label: 'Run the first scan', href: '/' } }),
  };

  // No action: a score follows from a scan, and the only thing to press for
  // one is the re-run button already in the list below.
  const score: GettingStartedStep = {
    key: 'score',
    label: 'Get a score',
    done: scoredScanCount >= 1,
    detail:
      scoredScanCount >= 1
        ? `${scoredScanCount} scored`
        : running
          ? 'A scan is running. Its score lands here when it finishes.'
          : scanCount >= 1
            ? 'The scans so far produced no score. Re-run one from the list below.'
            : 'After the first scan finishes.',
  };

  const teammate: GettingStartedStep = {
    key: 'teammate',
    label: 'Invite a teammate',
    done: seats.used >= 2,
    detail:
      seats.used >= 2
        ? `${seats.used} of ${seats.limit} seats in use`
        : freeSeats > 0
          ? `${plural(freeSeats, 'seat')} free.`
          : 'Every seat is in use.',
    ...(seats.used >= 2 || freeSeats === 0
      ? {}
      : { action: { label: 'Invite a teammate', href: '/settings' } }),
  };

  const share: GettingStartedStep = {
    key: 'share',
    label: 'Share a report',
    done: sharedScanCount >= 1,
    detail:
      sharedScanCount >= 1
        ? sharedScanCount === 1
          ? 'One live link.'
          : `${sharedScanCount} live links.`
        : scoredScanCount >= 1
          ? latestScored != null
            ? 'Open a report and create its link.'
            : 'Open any scored report and create its link.'
          : 'After the first score.',
    ...(sharedScanCount === 0 && latestScored != null
      ? { action: { label: 'Open the latest report', href: `/scans/${latestScored.id}/report` } }
      : {}),
  };

  const steps = [client, scan, score, teammate, share] as const;
  const lit = steps.filter((s) => s.done).length;
  const complete = lit === steps.length;
  const litWord = WORDS[lit] ?? String(lit);
  const lead = complete
    ? GETTING_STARTED_COMPLETE_LEAD
    : `${litWord.charAt(0).toUpperCase()}${litWord.slice(1)} of ${WORDS[steps.length]} lit.`;

  return {
    visible: !gettingStartedDismissed,
    complete,
    lit,
    total: steps.length,
    lead,
    steps,
  };
}
