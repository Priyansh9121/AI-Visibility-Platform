/**
 * Dashboard polling — Epic 9.7.
 *
 * Epic 9.1 asked slice 2 to "show progress, not wait". Epic 9.5 made a scan's
 * status genuinely readable while it runs, and Epic 9.6 made it safe to act on.
 * Neither built the poller, so a user who pressed Re-run watched the row turn
 * Queued and then had to refresh by hand to learn anything else. This closes
 * that.
 *
 * WHY THIS IS FRAMEWORK-FREE
 * -------------------------
 * The timing rules below — when to start, when to stop, what a failed poll
 * means — are the part that can actually be wrong, and `renderToStaticMarkup`
 * (this project's frontend test approach) does not run effects, so a hook
 * cannot be asserted with it. Rather than add a React test renderer, the logic
 * is extracted and tested directly with fake timers, exactly the way
 * `derive.ts`, `ledgerLayout.ts` and `answerShelfLayout.ts` pull decidable
 * logic out of components so it can be asserted rather than trusted.
 * `useDashboardPolling` is a thin wrapper over this.
 */

import type { Dashboard, ScanStatus } from '@avp/shared-types';

/**
 * Five seconds.
 *
 * Costed against the real numbers rather than picked for feel. A scan's
 * observable life is ~303s (Epic 9.2: prompt generation 14.6s + scan loop
 * 288.5s), and the transition worth catching — RUNNING to a terminal status —
 * happens exactly once at the end of it.
 *
 *   at 5s   ~61 polls per scan, and the completed state is at most 5s stale
 *           against a 300s+ wait: 1.6%, imperceptible
 *   at 1s   ~303 polls to observe one transition, five times the traffic for
 *           latency nobody can feel
 *   at 30s  a user stares at "Running" for up to half a minute after it
 *           finished, which reads as broken
 *
 * Server cost is not the constraint: one dashboard load measured ~1.74ms of
 * database time (Epic 9.7), so a whole scan's worth of polling is ~106ms.
 *
 * No backoff. It would optimise something already measured as free, and add a
 * second timing behaviour to reason about and test for no gain — and there is
 * no long tail to protect against, because Epic 9.5's reaper caps a stuck scan
 * at 900s.
 */
export const POLL_INTERVAL_MS = 5_000;

/** Consecutive failures tolerated silently before the user is told. */
export const FAILURES_BEFORE_NOTICE = 3;

const TERMINAL: readonly ScanStatus[] = ['succeeded', 'partial', 'failed', 'cancelled'];

export function isTerminal(status: ScanStatus): boolean {
  return TERMINAL.includes(status);
}

/**
 * Whether anything on screen is still expected to change.
 *
 * **RUNNING, or a scan this session started that has not finished yet.** The
 * second clause is not redundant, and the first is deliberately not "any
 * non-terminal scan".
 *
 * Polling every non-terminal scan would poll forever on a detect-only
 * placeholder: `POST /clients/{id}/competitors/detect` opens a QUEUED scan for
 * the CompetitorSet to hang off, and nothing moves it until somebody runs a
 * scan. That is the same trap Epic 9.6 removed from the re-run button, and it
 * would be worse here — a permanently open dashboard making a request every
 * five seconds about a scan that is never going to start.
 *
 * But keying on RUNNING alone would miss the scan the user just started.
 * `POST /clients/{id}/scans` returns 202 while the row is still QUEUED, and the
 * executor promotes it ~1.1ms later (Epic 9.6's measurement) — after the
 * refresh that follows the click has already read it. Without the watch set the
 * poller would look once, see QUEUED, decline to poll, and never notice it
 * moved. So a scan this session started is watched until it reaches a terminal
 * status, whatever it currently reads as.
 */
export function shouldPoll(dashboard: Dashboard, watching: ReadonlySet<string>): boolean {
  return dashboard.recentScans.some(
    (scan) =>
      scan.status === 'running' || (watching.has(scan.id) && !isTerminal(scan.status)),
  );
}

/** Watched scans that have finished, and can stop being watched. */
export function settled(dashboard: Dashboard, watching: ReadonlySet<string>): string[] {
  return dashboard.recentScans
    .filter((scan) => watching.has(scan.id) && isTerminal(scan.status))
    .map((scan) => scan.id);
}

export interface PollerOptions {
  fetchDashboard: () => Promise<Dashboard>;
  /** The scans this browser session started; read fresh on every decision. */
  getWatching: () => ReadonlySet<string>;
  onDashboard: (dashboard: Dashboard) => void;
  /** An expired session. Polling stops — retrying a 401 forever is noise. */
  onUnauthorized: () => void;
  /** Null clears the notice; a string is shown to the user. */
  onTrouble: (message: string | null) => void;
  /** Backgrounded tabs do not poll. Defaults to the Page Visibility API. */
  isVisible?: () => boolean;
  intervalMs?: number;
}

export interface Poller {
  /** Start or stop, based on what the latest dashboard shows. */
  sync: (dashboard: Dashboard) => void;
  /** Poll immediately, if polling is active — used when a tab is refocused. */
  pokeNow: () => void;
  /** Stop and release the timer. Idempotent. */
  stop: () => void;
  /** Test/diagnostic: is a timer currently scheduled? */
  isRunning: () => boolean;
}

function defaultIsVisible(): boolean {
  if (typeof document === 'undefined') return true;
  return document.visibilityState !== 'hidden';
}

function isUnauthorized(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { status?: number }).status === 401;
}

export function createPoller(options: PollerOptions): Poller {
  const {
    fetchDashboard,
    getWatching,
    onDashboard,
    onUnauthorized,
    onTrouble,
    isVisible = defaultIsVisible,
    intervalMs = POLL_INTERVAL_MS,
  } = options;

  let timer: ReturnType<typeof setInterval> | null = null;
  let failures = 0;
  let inFlight = false;
  let disposed = false;

  const stop = (): void => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };

  const start = (): void => {
    if (timer !== null || disposed) return;
    timer = setInterval(() => void tick(), intervalMs);
  };

  async function tick(): Promise<void> {
    // A backgrounded tab keeps its timer but does no work, so refocusing
    // resumes instantly rather than waiting out a restart.
    if (!isVisible()) return;
    // A slow response must not stack requests behind it.
    if (inFlight) return;
    inFlight = true;
    try {
      const dashboard = await fetchDashboard();
      if (disposed) return;
      failures = 0;
      onTrouble(null);
      onDashboard(dashboard);
      sync(dashboard);
    } catch (error) {
      if (disposed) return;
      if (isUnauthorized(error)) {
        stop();
        onUnauthorized();
        return;
      }
      failures += 1;
      // Keep polling. A blip should heal itself without the user reading about
      // it — but silence must not be indefinite, so once failures persist the
      // user is told that what they are looking at has stopped updating.
      if (failures >= FAILURES_BEFORE_NOTICE) {
        onTrouble(
          'Live updates have stopped — the server could not be reached. ' +
            'Still retrying; refresh if this persists.',
        );
      }
    } finally {
      inFlight = false;
    }
  }

  function sync(dashboard: Dashboard): void {
    if (disposed) return;
    if (shouldPoll(dashboard, getWatching())) {
      start();
    } else {
      stop();
      failures = 0;
      onTrouble(null);
    }
  }

  return {
    sync,
    pokeNow: () => {
      if (timer !== null) void tick();
    },
    stop: () => {
      disposed = true;
      stop();
    },
    isRunning: () => timer !== null,
  };
}
