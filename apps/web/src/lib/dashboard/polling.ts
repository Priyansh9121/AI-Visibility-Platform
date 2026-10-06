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
 * The route (`app/dashboard/page.tsx`) is a thin wrapper over this: it builds a
 * poller, hands it every dashboard it reads, and tells it when the tab is shown
 * or hidden.
 *
 * U7 (the founder's brief of 2026-10-05: keep it fast at the start, back off as
 * a scan ages, stop when the tab is hidden, no visible change) changed two
 * things, both below: how often it polls (`pollDelayMs`) and what a hidden tab
 * costs (nothing: it has no timer).
 */

import type { Dashboard, ScanStatus, ScanSummary } from '@avp/shared-types';

/**
 * One step of the back-off: a scan younger than `untilMs` is polled every
 * `everyMs`. Ordered, and the last step has no end.
 */
const POLL_SCHEDULE = [
  { untilMs: 2 * 60_000, everyMs: 5_000 },
  { untilMs: 4 * 60_000, everyMs: 10_000 },
  { untilMs: Number.POSITIVE_INFINITY, everyMs: 15_000 },
] as const;

/**
 * Five seconds: how often a young scan is polled, and how soon a failed poll
 * is tried again.
 *
 * Costed against the real numbers rather than picked for feel (Epic 9.7). A
 * scan's observable life was then ~303s (Epic 9.2: prompt generation 14.6s +
 * scan loop 288.5s), and the transition worth catching — RUNNING to a terminal
 * status — happens exactly once at the end of it.
 *
 *   at 5s   ~61 polls per scan, and the completed state is at most 5s stale
 *           against a 300s+ wait: 1.6%, imperceptible
 *   at 1s   ~303 polls to observe one transition, five times the traffic for
 *           latency nobody can feel
 *   at 30s  a user stares at "Running" for up to half a minute after it
 *           finished, which reads as broken
 *
 * Server cost was not the constraint: one dashboard load measured ~1.74ms of
 * database time, so a whole scan's worth of polling was ~106ms. Epic 9.7 chose
 * no back-off for that reason. U7 asked for one regardless, and `POLL_SCHEDULE`
 * is it; this is the fast end of it, unchanged.
 */
export const POLL_INTERVAL_MS = POLL_SCHEDULE[0].everyMs;

/**
 * Fifteen seconds: the slowest the dashboard is ever polled, from a scan's
 * fourth minute on.
 *
 * The brief offered 15 to 30s and this is the low end, for the reason Epic 9.7
 * gave against 30s: what a person watching can feel is how long after a scan
 * lands the page says so, and half a minute "reads as broken". A scan lands
 * once, at the end of minutes of waiting (25 finished scans measured on
 * 2026-10-03, `scans/scanOutcome.ts`: the shortest under a minute, 21 between
 * two and six minutes, the longest 7.6; the brief puts a typical one at six to
 * seven), so the pace in force at that moment is the one a viewer notices, and
 * with this ceiling it is never slower than 15s.
 */
export const POLL_CEILING_MS = POLL_SCHEDULE[2].everyMs;

/**
 * How long to wait before the next poll, given how long the scan has been
 * waited on — U7.
 *
 *   under 2 minutes   every 5s    the Epic 9.7 pace, for the stretch where a
 *                                 new scan is first seen and a scan that ends
 *                                 early is caught
 *   2 to 4 minutes    every 10s   a step rather than a cliff
 *   4 minutes on      every 15s   the ceiling, for as long as the scan runs. A
 *                                 scan may run up to 40 minutes
 *                                 (`MAX_SCAN_DURATION`) and one that was
 *                                 submitted and never claimed is failed after
 *                                 15 (`IN_FLIGHT_WINDOW`); either ending is
 *                                 shown within one ceiling of happening
 *
 * Over a 6.5-minute scan that is 46 polls where a flat 5s made 78; over 15
 * minutes, 80 where it made 180. `polling.test.ts` counts them.
 *
 * THE AGE IS HOW LONG THE POLLER HAS BEEN WAITING, not the scan's own age,
 * because the dashboard does not carry one: a scan summary has `createdAt` and
 * `finishedAt`, no `startedAt` or `submittedAt`, and `createdAt` can be days old
 * for a scan that competitor detection opened and a later run adopted
 * (`routers/scans.py`, where `submitted_at` is stamped). A server stamp read
 * against this browser's clock would also depend on the two agreeing. So the
 * clock starts when the poller starts waiting, and starts again whenever a scan
 * joins what it is waiting on (`sync`, below); a page opened part-way through a
 * scan polls fast for its first two minutes and then backs off, which is never
 * slower than the scan's real age would allow.
 *
 * An age that cannot be read — negative, because the clock stepped back, or NaN
 * — is a young one: the fast side.
 */
export function pollDelayMs(ageMs: number): number {
  // `>=` is false for NaN, which makes it a young age too.
  const age = ageMs >= 0 ? ageMs : 0;
  for (const step of POLL_SCHEDULE) {
    if (age < step.untilMs) return step.everyMs;
  }
  return POLL_CEILING_MS;
}

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
 * few seconds about a scan that is never going to start.
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
  return dashboard.recentScans.some((scan) => isWaitedOn(scan, watching));
}

/**
 * The scans `shouldPoll`'s rule is satisfied by, by id.
 *
 * The poller compares one answer with the last to tell a scan that has joined
 * from one that has merely moved on (a watched scan going from queued to
 * running is the same scan, and does not start the schedule over). Empty exactly
 * when `shouldPoll` is false.
 */
export function pollTargets(dashboard: Dashboard, watching: ReadonlySet<string>): Set<string> {
  return new Set(
    dashboard.recentScans.filter((scan) => isWaitedOn(scan, watching)).map((scan) => scan.id),
  );
}

function isWaitedOn(scan: ScanSummary, watching: ReadonlySet<string>): boolean {
  return scan.status === 'running' || (watching.has(scan.id) && !isTerminal(scan.status));
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
  /** Whether the tab can be seen. Defaults to the Page Visibility API. */
  isVisible?: () => boolean;
  /** The clock a scan's age is read from, in ms. Defaults to `Date.now`. */
  now?: () => number;
}

export interface Poller {
  /** Start, restart or stop, based on what the latest dashboard shows. */
  sync: (dashboard: Dashboard) => void;
  /**
   * The tab was hidden or shown: call it from `visibilitychange`; it reads
   * `isVisible()` itself. Hidden, the timer is cleared. Shown, it polls once at
   * once and the schedule carries on from there.
   */
  visibilityChanged: () => void;
  /** Stop for good and release the timer. Idempotent. */
  stop: () => void;
  /**
   * Whether a scan is being waited on. True while the tab is hidden too, when
   * nothing is scheduled but polling resumes on return.
   */
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
    now = () => Date.now(),
  } = options;

  /**
   * The one pending poll, if any. A chain of single timeouts rather than an
   * interval, because the wait changes with the scan's age; and one is never
   * pending while a request is out, so a slow answer cannot have another poll
   * stacked behind it.
   */
  let timer: ReturnType<typeof setTimeout> | null = null;
  /** A scan is being waited on. Stays true while the tab is hidden: it resumes. */
  let polling = false;
  /** The scans being waited on, to tell when a new one joins. */
  let waitingOn: ReadonlySet<string> = new Set();
  /** When the schedule's clock started, by `now()`. */
  let since = 0;
  let failures = 0;
  let inFlight = false;
  let disposed = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  /** Wait on nothing: no timer, and a fresh clock for whatever comes next. */
  const halt = (): void => {
    polling = false;
    waitingOn = new Set();
    clearTimer();
  };

  /**
   * Set up the next poll, if one is due: something is being waited on, nothing
   * is already scheduled or out, and the tab can be seen. Called wherever any of
   * those may just have become true, so no caller has to know about the others.
   */
  const schedule = (): void => {
    if (disposed || !polling || inFlight || timer !== null || !isVisible()) return;
    // A failed poll is tried again at the fast interval whatever the scan's
    // age, as it was before the back-off: a blip heals as quickly as it did, and
    // the notice for persistent failure still comes after three of them, about
    // fifteen seconds in, rather than after three ceilings.
    const delay = failures > 0 ? POLL_INTERVAL_MS : pollDelayMs(now() - since);
    timer = setTimeout(() => {
      timer = null;
      void tick();
    }, delay);
  };

  async function tick(): Promise<void> {
    // Hiding the tab clears the timer, so one cannot fire for a hidden tab; the
    // check is for a tab that went out of sight without the page being told. A
    // request already out is not doubled.
    if (disposed || !polling || inFlight || !isVisible()) return;
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
        halt();
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
      // The next poll is set up once this one has landed, whichever way it went.
      schedule();
    }
  }

  function sync(dashboard: Dashboard): void {
    if (disposed) return;
    const targets = pollTargets(dashboard, getWatching());
    if (targets.size === 0) {
      halt();
      failures = 0;
      onTrouble(null);
      return;
    }
    // A scan the poller was not already waiting on starts the schedule over, so
    // the youngest scan sets the pace: a Re-run while another scan is old gets
    // the fast phase rather than the ceiling. A scan that merely changed state
    // (queued to running) is the same scan and does not.
    const joined = [...targets].some((id) => !waitingOn.has(id));
    waitingOn = targets;
    if (!polling) {
      polling = true;
      since = now();
    } else if (joined) {
      since = now();
      clearTimer();
    }
    schedule();
  }

  function visibilityChanged(): void {
    if (disposed) return;
    if (!isVisible()) {
      // No timer at all, rather than one that wakes to do nothing. A poll
      // already out lands as usual and sets up nothing after it: `schedule`
      // looks at the tab.
      clearTimer();
      return;
    }
    if (!polling) return;
    // Shown again: poll once at once rather than make the reader wait out an
    // interval on a stale view. That poll's landing sets up the next one at the
    // pace the scan's age sets; the clock kept running while the tab was away.
    // A poll already out stands in for this one (`tick` does not double it) and
    // does the same on landing.
    clearTimer();
    void tick();
  }

  return {
    sync,
    visibilityChanged,
    stop: () => {
      disposed = true;
      halt();
    },
    isRunning: () => polling && !disposed,
  };
}
