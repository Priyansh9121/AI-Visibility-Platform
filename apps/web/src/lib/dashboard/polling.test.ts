/**
 * The polling rules — Epic 9.7.
 *
 * **This is the first use of vitest's fake timers in this repo.** Stated rather
 * than slipped in: every frontend test before this rendered to static markup and
 * asserted over the HTML, which cannot express "and five seconds later". The
 * timing rules are the part of a poller that can actually be wrong — starting
 * when it should not, never stopping, leaking an interval, going silent after a
 * blip — and none of those are visible in a rendered string.
 *
 * U7 added the back-off and the hidden-tab pause. The schedule itself is a pure
 * function and is tested without a clock; what the poller does with it (the
 * exact instants of every poll, a hidden tab having no timer at all, one
 * immediate poll on return, no second timer after a quick hide and show, nothing
 * after unmount) is tested on fake timers, reading the instants off the fake
 * clock rather than sleeping.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Dashboard, ScanStatus } from '@avp/shared-types';
import {
  FAILURES_BEFORE_NOTICE,
  POLL_CEILING_MS,
  POLL_INTERVAL_MS,
  createPoller,
  isTerminal,
  pollDelayMs,
  pollTargets,
  settled,
  shouldPoll,
  type Poller,
} from './polling';
import { dashboard, scan } from '@/lib/dashboard/__fixtures__/dashboards';

const SECOND = 1_000;
const MINUTE = 60 * SECOND;

const board = (...statuses: [ScanStatus, string][]): Dashboard =>
  dashboard(statuses.map(([status, id]) => scan({ id, status })));

describe('what counts as finished', () => {
  it('treats every terminal status as terminal', () => {
    for (const s of ['succeeded', 'partial', 'failed', 'cancelled'] as ScanStatus[]) {
      expect(isTerminal(s)).toBe(true);
    }
  });

  it('queued and running are not terminal', () => {
    expect(isTerminal('queued')).toBe(false);
    expect(isTerminal('running')).toBe(false);
  });
});

describe('deciding whether to poll at all', () => {
  const nothing = new Set<string>();

  it('polls while a scan is running', () => {
    expect(shouldPoll(board(['running', 'scan_a']), nothing)).toBe(true);
  });

  it('does NOT poll for a detect-only placeholder', () => {
    // The trap Epic 9.6 removed from the re-run button, and it would be worse
    // here: detection leaves a QUEUED scan nothing will ever move on its own,
    // so "poll any non-terminal scan" means polling forever, every few
    // seconds, about a scan that is never going to start.
    expect(shouldPoll(board(['queued', 'scan_placeholder']), nothing)).toBe(false);
  });

  it('DOES poll a queued scan this session started', () => {
    // POST returns 202 while the row is still QUEUED and the executor promotes
    // it ~1.1ms later — after the refresh that follows the click has read it.
    // Keying on RUNNING alone would look once, see QUEUED, and never look again.
    expect(shouldPoll(board(['queued', 'scan_mine']), new Set(['scan_mine']))).toBe(true);
  });

  it('stops once the watched scan reaches a terminal status', () => {
    const watching = new Set(['scan_mine']);
    expect(shouldPoll(board(['succeeded', 'scan_mine']), watching)).toBe(false);
    expect(shouldPoll(board(['partial', 'scan_mine']), watching)).toBe(false);
    expect(shouldPoll(board(['failed', 'scan_mine']), watching)).toBe(false);
  });

  it('does not poll an empty or all-finished dashboard', () => {
    expect(shouldPoll(dashboard([]), nothing)).toBe(false);
    expect(shouldPoll(board(['succeeded', 'a'], ['failed', 'b']), nothing)).toBe(false);
  });

  it('polls if any one scan is running, among finished ones', () => {
    expect(shouldPoll(board(['succeeded', 'a'], ['running', 'b']), nothing)).toBe(true);
  });

  it('reports which watched scans have settled', () => {
    const watching = new Set(['a', 'b']);
    expect(settled(board(['succeeded', 'a'], ['running', 'b']), watching)).toEqual(['a']);
  });
});

describe('which scans the poller is waiting on', () => {
  it('names the running scans and the unfinished ones this session started, and no others', () => {
    const watching = new Set(['mine', 'mine_done']);
    const page = board(
      ['running', 'running_one'],
      ['queued', 'mine'],
      ['succeeded', 'mine_done'],
      ['queued', 'scan_placeholder'],
      ['failed', 'old_failure'],
    );
    expect([...pollTargets(page, watching)].sort()).toEqual(['mine', 'running_one']);
  });

  it('is empty exactly when shouldPoll says there is nothing to wait for', () => {
    const watching = new Set(['mine']);
    const pages = [
      dashboard([]),
      board(['succeeded', 'a'], ['failed', 'b']),
      board(['queued', 'scan_placeholder']),
      board(['queued', 'mine']),
      board(['running', 'a']),
      board(['succeeded', 'mine']),
    ];
    for (const page of pages) {
      expect(pollTargets(page, watching).size > 0).toBe(shouldPoll(page, watching));
    }
  });
});

describe('the back-off schedule', () => {
  it('is five seconds for the first two minutes of a scan', () => {
    expect(POLL_INTERVAL_MS).toBe(5 * SECOND);
    expect(pollDelayMs(0)).toBe(5 * SECOND);
    expect(pollDelayMs(2 * MINUTE - 1)).toBe(5 * SECOND);
  });

  it('eases to ten seconds for the next two minutes', () => {
    expect(pollDelayMs(2 * MINUTE)).toBe(10 * SECOND);
    expect(pollDelayMs(4 * MINUTE - 1)).toBe(10 * SECOND);
  });

  it('settles at fifteen seconds and stays there however old the scan gets', () => {
    expect(POLL_CEILING_MS).toBe(15 * SECOND);
    expect(pollDelayMs(4 * MINUTE)).toBe(15 * SECOND);
    expect(pollDelayMs(15 * MINUTE)).toBe(15 * SECOND); // the in-flight window
    expect(pollDelayMs(40 * MINUTE)).toBe(15 * SECOND); // the longest a scan may run
    expect(pollDelayMs(7 * 24 * 60 * MINUTE)).toBe(15 * SECOND);
    expect(pollDelayMs(Number.POSITIVE_INFINITY)).toBe(15 * SECOND);
  });

  it('only ever backs off: never under five seconds, never over the ceiling, never quicker with age', () => {
    let previous = 0;
    for (let age = 0; age <= 45 * MINUTE; age += SECOND) {
      const delay = pollDelayMs(age);
      expect(delay).toBeGreaterThanOrEqual(POLL_INTERVAL_MS);
      expect(delay).toBeLessThanOrEqual(POLL_CEILING_MS);
      expect(delay).toBeGreaterThanOrEqual(previous);
      previous = delay;
    }
  });

  it('reads an age it cannot trust as a young one, so a clock that stepped back polls fast', () => {
    expect(pollDelayMs(-1)).toBe(POLL_INTERVAL_MS);
    expect(pollDelayMs(-10 * MINUTE)).toBe(POLL_INTERVAL_MS);
    expect(pollDelayMs(Number.NaN)).toBe(POLL_INTERVAL_MS);
  });

  it('costs 46 polls over a 6.5-minute scan where a flat five seconds cost 78, and 80 over 15 minutes where it cost 180', () => {
    // The numbers the header of polling.ts quotes. A poll is made one delay after the last,
    // the delay read at the age of the last.
    const pollsWithin = (spanMs: number): number => {
      let at = 0;
      let polls = 0;
      for (;;) {
        at += pollDelayMs(at);
        if (at > spanMs) return polls;
        polls += 1;
      }
    };
    expect(pollsWithin(6.5 * MINUTE)).toBe(46);
    expect(Math.floor((6.5 * MINUTE) / POLL_INTERVAL_MS)).toBe(78);
    expect(pollsWithin(15 * MINUTE)).toBe(80);
    expect(Math.floor((15 * MINUTE) / POLL_INTERVAL_MS)).toBe(180);
  });
});

describe('the poller', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  /** Build a poller over a scripted sequence of dashboard responses. */
  function harness(
    responses: (Dashboard | Error)[],
    opts: { watching?: string[]; visible?: () => boolean } = {},
  ) {
    const seen: Dashboard[] = [];
    const trouble: (string | null)[] = [];
    let unauthorized = 0;
    let call = 0;
    const watching = new Set(opts.watching ?? []);

    const poller = createPoller({
      fetchDashboard: async () => {
        const next = responses[Math.min(call, responses.length - 1)]!;
        call += 1;
        if (next instanceof Error) throw next;
        return next;
      },
      getWatching: () => watching,
      onDashboard: (d) => seen.push(d),
      onUnauthorized: () => {
        unauthorized += 1;
      },
      onTrouble: (m) => trouble.push(m),
      ...(opts.visible ? { isVisible: opts.visible } : { isVisible: () => true }),
    });

    return {
      poller,
      seen,
      trouble,
      calls: () => call,
      unauthorized: () => unauthorized,
      watching,
      /** Advance time and let the pending fetch promises settle. */
      async advance(ms: number) {
        await vi.advanceTimersByTimeAsync(ms);
      },
    };
  }

  it('does not start when nothing is in flight', async () => {
    const h = harness([dashboard([])]);
    h.poller.sync(board(['succeeded', 'a']));

    expect(h.poller.isRunning()).toBe(false);
    await h.advance(POLL_INTERVAL_MS * 5);
    expect(h.calls()).toBe(0);
  });

  it('starts when a scan is running, and refreshes on each interval', async () => {
    const running = board(['running', 'a']);
    const h = harness([running, running]);
    h.poller.sync(running);

    expect(h.poller.isRunning()).toBe(true);
    expect(h.calls()).toBe(0); // nothing fires before the first interval elapses

    await h.advance(POLL_INTERVAL_MS);
    expect(h.calls()).toBe(1);
    expect(h.seen).toHaveLength(1);

    await h.advance(POLL_INTERVAL_MS);
    expect(h.calls()).toBe(2);
  });

  it('stops as soon as the scan finishes — the whole point', async () => {
    const running = board(['running', 'a']);
    const done = board(['succeeded', 'a']);
    const h = harness([running, done]);
    h.poller.sync(running);

    await h.advance(POLL_INTERVAL_MS); // still running
    expect(h.poller.isRunning()).toBe(true);

    await h.advance(POLL_INTERVAL_MS); // now succeeded
    expect(h.poller.isRunning()).toBe(false);
    expect(h.seen.at(-1)).toBe(done);

    const after = h.calls();
    await h.advance(POLL_INTERVAL_MS * 10);
    expect(h.calls()).toBe(after); // and it stays stopped
  });

  it('follows a watched scan from queued through running to finished', async () => {
    const queued = board(['queued', 'mine']);
    const running = board(['running', 'mine']);
    const done = board(['succeeded', 'mine']);
    const h = harness([running, done], { watching: ['mine'] });

    h.poller.sync(queued); // queued, but ours — so it polls
    expect(h.poller.isRunning()).toBe(true);

    await h.advance(POLL_INTERVAL_MS);
    expect(h.poller.isRunning()).toBe(true);

    await h.advance(POLL_INTERVAL_MS);
    expect(h.poller.isRunning()).toBe(false);
    expect(h.seen.at(-1)).toBe(done);
  });

  it('leaks nothing on stop', async () => {
    const running = board(['running', 'a']);
    const h = harness([running]);
    h.poller.sync(running);

    h.poller.stop();
    expect(h.poller.isRunning()).toBe(false);

    await h.advance(POLL_INTERVAL_MS * 10);
    expect(h.calls()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cannot be restarted after stop', async () => {
    const running = board(['running', 'a']);
    const h = harness([running]);
    h.poller.stop();

    h.poller.sync(running); // an in-flight response arriving after unmount
    expect(h.poller.isRunning()).toBe(false);
    await h.advance(POLL_INTERVAL_MS * 3);
    expect(h.calls()).toBe(0);
  });

  it('does not stack requests when a poll outlives its interval', async () => {
    const running = board(['running', 'a']);
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let calls = 0;

    const poller = createPoller({
      fetchDashboard: async () => {
        calls += 1;
        await gate;
        return running;
      },
      getWatching: () => new Set(),
      onDashboard: () => {},
      onUnauthorized: () => {},
      onTrouble: () => {},
      isVisible: () => true,
    });
    poller.sync(running);

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 4);
    expect(calls).toBe(1); // nothing is scheduled while a request is out, so none stacks behind it
    expect(vi.getTimerCount()).toBe(0);

    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(1); // the next poll is set up once this one has landed
    poller.stop();
  });
});

/**
 * U7's two additions, driven on the fake clock.
 *
 * `watch` is a poller over a dashboard the test can change, with a tab it can show and hide (what
 * the route's visibilitychange listener does: flip the state, then tell the poller) and a record
 * of the instant of every poll, in ms from when the poller was built.
 */
function watch(
  first: Dashboard,
  opts: { watching?: string[]; hidden?: boolean; syncFromPage?: boolean } = {},
) {
  const began = Date.now();
  const polledAt: number[] = [];
  const seen: Dashboard[] = [];
  const trouble: (string | null)[] = [];
  const watching = new Set(opts.watching ?? []);
  let latest = first;
  let failure: { error: unknown } | null = null;
  let held: Promise<void> | null = null;
  let visible = !opts.hidden;
  let unauthorized = 0;

  const poller: Poller = createPoller({
    fetchDashboard: async () => {
      polledAt.push(Date.now() - began);
      if (held) await held;
      if (failure) throw failure.error;
      return latest;
    },
    getWatching: () => watching,
    // The route hands every dashboard it reads, polled or not, to `sync` too (`apply`).
    onDashboard: (d) => {
      seen.push(d);
      if (opts.syncFromPage) poller.sync(d);
    },
    onUnauthorized: () => {
      unauthorized += 1;
    },
    onTrouble: (m) => trouble.push(m),
    isVisible: () => visible,
  });

  return {
    poller,
    watching,
    seen,
    trouble,
    polledAt: () => [...polledAt],
    calls: () => polledAt.length,
    unauthorized: () => unauthorized,
    /** The ms between each poll and the one before it. */
    gaps: () => polledAt.slice(1).map((at, i) => at - polledAt[i]!),
    elapsed: () => Date.now() - began,
    respond: (d: Dashboard) => {
      latest = d;
    },
    fail: (error: unknown) => {
      failure = { error };
    },
    recover: () => {
      failure = null;
    },
    /** Make every poll wait; the returned function lets the waiting ones through. */
    hold: () => {
      let open!: () => void;
      held = new Promise<void>((resolve) => (open = resolve));
      return () => {
        held = null;
        open();
      };
    },
    /** What the route's listener does when the tab is shown or hidden. */
    show: (shown: boolean) => {
      visible = shown;
      poller.visibilityChanged();
    },
    advance: (ms: number) => vi.advanceTimersByTimeAsync(ms),
  };
}

/** `from`, `from + step`, ... up to and including `to`, given in seconds, returned in ms. */
const everyS = (from: number, to: number, step: number): number[] => {
  const out: number[] = [];
  for (let s = from; s <= to; s += step) out.push(s * SECOND);
  return out;
};

describe('the poller backs off as a scan ages', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const running = board(['running', 'a']);

  it('makes its first poll five seconds after polling begins, as it did before the back-off', async () => {
    const w = watch(running);
    w.poller.sync(running);

    await w.advance(5 * SECOND - 1);
    expect(w.calls()).toBe(0);
    await w.advance(1);
    expect(w.calls()).toBe(1);
    w.poller.stop();
  });

  it('polls on the schedule: every 5 s to two minutes, every 10 s to four, every 15 s after', async () => {
    const w = watch(running);
    w.poller.sync(running);

    await w.advance(6.5 * MINUTE);
    expect(w.polledAt()).toEqual([
      ...everyS(5, 120, 5),
      ...everyS(130, 240, 10),
      ...everyS(255, 390, 15),
    ]);
    w.poller.stop();
  });

  it('stays at the ceiling for as long as the scan runs', async () => {
    const w = watch(running);
    w.poller.sync(running);

    await w.advance(40 * MINUTE); // the longest a scan may run
    const gaps = w.gaps();
    expect(Math.max(...gaps)).toBe(POLL_CEILING_MS);
    // 24 polls at 5 s and 12 at 10 s put the 36th poll at four minutes: the 35 gaps before it
    // belong to the fast and middle phases, every gap after it is the ceiling.
    expect(new Set(gaps.slice(35))).toEqual(new Set([POLL_CEILING_MS]));
    w.poller.stop();
  });

  it('counts the age from when polling began, not from when the page opened', async () => {
    const w = watch(running);
    w.poller.sync(board(['succeeded', 'a'])); // the page opens on a finished scan: nothing to poll
    await w.advance(10 * MINUTE);
    expect(w.calls()).toBe(0);

    const began = w.elapsed();
    w.poller.sync(running); // a scan starts ten minutes in
    await w.advance(30 * SECOND);
    expect(w.polledAt().map((at) => at - began)).toEqual(everyS(5, 30, 5));
    w.poller.stop();
  });

  it('does not start the clock over when a scan it was already watching moves from queued to running', async () => {
    const w = watch(board(['running', 'mine']), { watching: ['mine'] });
    w.poller.sync(board(['queued', 'mine']));

    await w.advance(6.5 * MINUTE);
    expect(w.polledAt()).toEqual([
      ...everyS(5, 120, 5),
      ...everyS(130, 240, 10),
      ...everyS(255, 390, 15),
    ]);
    w.poller.stop();
  });

  it('reads the age from the clock it is given, not from the timers', async () => {
    let clock = 0;
    const polledAt: number[] = [];
    const poller = createPoller({
      fetchDashboard: async () => {
        polledAt.push(Date.now());
        return running;
      },
      getWatching: () => new Set(),
      onDashboard: () => {},
      onUnauthorized: () => {},
      onTrouble: () => {},
      isVisible: () => true,
      now: () => clock,
    });
    poller.sync(running);

    clock = 10 * MINUTE; // that clock says the scan is ten minutes old when the first poll lands
    await vi.advanceTimersByTimeAsync(5 * SECOND); // the first poll was set up at five seconds
    expect(polledAt).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(15 * SECOND - 1);
    expect(polledAt).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1); // the next waited the ceiling, not five seconds
    expect(polledAt).toHaveLength(2);
    poller.stop();
  });

  it('goes back to the fast phase when a second scan joins one that is already old', async () => {
    const a = board(['running', 'a']);
    const ab = board(['running', 'a'], ['running', 'b']);
    const w = watch(a);
    w.poller.sync(a);
    await w.advance(10 * MINUTE); // scan a is at the ceiling by now
    expect(w.gaps().at(-1)).toBe(POLL_CEILING_MS);

    // Re-run on another client: the route reads the dashboard and hands it to the poller.
    w.respond(ab);
    const joined = w.elapsed();
    w.poller.sync(ab);

    await w.advance(2.5 * MINUTE);
    expect(w.polledAt().filter((at) => at > joined).map((at) => at - joined)).toEqual([
      ...everyS(5, 120, 5),
      ...everyS(130, 150, 10),
    ]);
    w.poller.stop();
  });

  it('retries a failed poll at the fast interval, so a page gone stale is still said so about fifteen seconds in', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(10 * MINUTE); // at the ceiling; the last poll was at exactly ten minutes
    expect(w.polledAt().at(-1)).toBe(10 * MINUTE);

    w.fail(new Error('network')); // the server goes away
    await w.advance(25 * SECOND - 1);
    expect(w.trouble.filter(Boolean)).toHaveLength(0); // two failures so far
    await w.advance(1);
    expect(w.trouble.at(-1)).toContain('Live updates have stopped');
    // The next poll was due at 15 s; after it, one every 5 s.
    expect(w.polledAt().slice(-3)).toEqual([615 * SECOND, 620 * SECOND, 625 * SECOND]);

    w.recover();
    await w.advance(5 * SECOND);
    expect(w.trouble.at(-1)).toBeNull(); // the retry got through
    await w.advance(15 * SECOND);
    // and the pace goes back to the one the scan's age sets
    expect(w.polledAt().slice(-2)).toEqual([630 * SECOND, 645 * SECOND]);
    w.poller.stop();
  });
});

describe('a hidden tab is not polled', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const running = board(['running', 'a']);

  it('has no timer while the tab is hidden, and asks the server nothing', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(12 * SECOND); // polls at 5 s and 10 s
    expect(w.calls()).toBe(2);

    w.show(false);
    expect(vi.getTimerCount()).toBe(0); // stopped, not merely ignored
    await w.advance(30 * MINUTE);
    expect(w.calls()).toBe(2);
    expect(w.poller.isRunning()).toBe(true); // still wanted: it picks up again when shown
    w.poller.stop();
  });

  it('polls once at once when the tab is shown, then goes back to the schedule', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(12 * SECOND);
    w.show(false);
    await w.advance(20 * SECOND); // 32 s into the scan
    expect(w.calls()).toBe(2);

    w.show(true);
    expect(w.calls()).toBe(3); // at once: no time has passed
    await w.advance(0);
    expect(w.calls()).toBe(3);
    expect(vi.getTimerCount()).toBe(1); // and the next poll is set up

    await w.advance(5 * SECOND - 1);
    expect(w.calls()).toBe(3);
    await w.advance(1);
    expect(w.calls()).toBe(4); // one interval on: the scan is 37 s old, so five seconds
    w.poller.stop();
  });

  it('does not start the fast phase over on return: the scan went on ageing while the tab was away', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(1 * MINUTE);
    w.show(false);
    await w.advance(10 * MINUTE);

    w.show(true);
    await w.advance(0);
    const onReturn = w.calls();
    await w.advance(15 * SECOND - 1);
    expect(w.calls()).toBe(onReturn);
    await w.advance(1);
    expect(w.calls()).toBe(onReturn + 1); // eleven minutes old: the ceiling
    w.poller.stop();
  });

  it('lets a poll that was out when the tab was hidden land, and schedules nothing after it', async () => {
    const w = watch(running);
    const release = w.hold();
    w.poller.sync(running);
    await w.advance(5 * SECOND); // the first poll is out, and held
    expect(w.calls()).toBe(1);

    w.show(false);
    release();
    await w.advance(0);
    expect(w.seen).toHaveLength(1); // the answer is still taken
    expect(vi.getTimerCount()).toBe(0); // but a hidden tab is given no next poll
    await w.advance(10 * MINUTE);
    expect(w.calls()).toBe(1);

    w.show(true);
    expect(w.calls()).toBe(2);
    w.poller.stop();
  });

  it('hiding and showing in quick succession leaves one request and one timer, never two', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(20 * SECOND); // polls at 5, 10, 15 and 20 s
    expect(w.calls()).toBe(4);

    const release = w.hold();
    w.show(false);
    w.show(true); // polls at once, and the answer is held
    w.show(false);
    w.show(true); // that poll is still out: not a second request
    expect(w.calls()).toBe(5);
    expect(vi.getTimerCount()).toBe(0);

    release();
    await w.advance(0);
    expect(vi.getTimerCount()).toBe(1);

    await w.advance(60 * SECOND);
    expect(w.calls()).toBe(5 + 12); // one poll every five seconds, not two
    expect(new Set(w.gaps().slice(4))).toEqual(new Set([5 * SECOND]));
    expect(vi.getTimerCount()).toBe(1);
    w.poller.stop();
  });

  it('a finished scan stays finished: showing the tab does not bring polling back', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(5 * SECOND);
    w.respond(board(['succeeded', 'a']));
    await w.advance(5 * SECOND); // the second poll reads the scan as finished
    expect(w.calls()).toBe(2);
    expect(w.poller.isRunning()).toBe(false);
    expect(vi.getTimerCount()).toBe(0);

    w.show(false);
    w.show(true);
    await w.advance(10 * MINUTE);
    expect(w.calls()).toBe(2);
    w.poller.stop();
  });

  it('a scan found while the tab is hidden waits to be seen, then polls once at once', async () => {
    const w = watch(running, { hidden: true });
    w.poller.sync(running);
    expect(vi.getTimerCount()).toBe(0);
    await w.advance(10 * MINUTE);
    expect(w.calls()).toBe(0);

    w.show(true);
    expect(w.calls()).toBe(1);
    w.poller.stop();
  });

  it('showing the tab when nothing is being watched polls nothing', async () => {
    const finished = board(['succeeded', 'a']);
    const w = watch(finished);
    w.poller.sync(finished);

    w.show(false);
    w.show(true);
    await w.advance(1 * MINUTE);
    expect(w.calls()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('the poller never polls twice', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const running = board(['running', 'a']);

  it('is one timer however often it is told what it is looking at', async () => {
    const w = watch(running);
    for (let i = 0; i < 5; i += 1) w.poller.sync(running);
    expect(vi.getTimerCount()).toBe(1);

    await w.advance(5 * SECOND);
    expect(w.calls()).toBe(1);
    w.poller.stop();
  });

  it('keeps to the schedule when the page also hands it every dashboard it polls, as the route does', async () => {
    const w = watch(running, { syncFromPage: true });
    w.poller.sync(running);

    await w.advance(6.5 * MINUTE);
    expect(w.polledAt()).toEqual([
      ...everyS(5, 120, 5),
      ...everyS(130, 240, 10),
      ...everyS(255, 390, 15),
    ]);
    expect(vi.getTimerCount()).toBe(1); // between polls, exactly one
    w.poller.stop();
  });
});

describe('after the page is gone', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const running = board(['running', 'a']);

  it('clears the timer, and a visibility change that arrives after that starts nothing', async () => {
    const w = watch(running);
    w.poller.sync(running);
    await w.advance(7 * SECOND);
    expect(vi.getTimerCount()).toBe(1);

    w.poller.stop();
    expect(vi.getTimerCount()).toBe(0);
    expect(w.poller.isRunning()).toBe(false);

    w.show(false);
    w.show(true);
    w.poller.sync(running);
    await w.advance(10 * MINUTE);
    expect(w.calls()).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a poll still out when the page goes lands on nothing', async () => {
    const w = watch(running);
    const release = w.hold();
    w.poller.sync(running);
    await w.advance(5 * SECOND);
    expect(w.calls()).toBe(1);

    w.poller.stop(); // the route unmounts with the request out
    release();
    await w.advance(10 * MINUTE);

    expect(w.seen).toHaveLength(0);
    expect(w.trouble).toHaveLength(0);
    expect(w.unauthorized()).toBe(0);
    expect(w.calls()).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a failure or an expired session that lands after the page is gone is not reported either', async () => {
    for (const error of [new Error('network'), { status: 401 }]) {
      const w = watch(running);
      const release = w.hold();
      w.fail(error);
      w.poller.sync(running);
      await w.advance(5 * SECOND);

      w.poller.stop();
      release();
      await w.advance(10 * MINUTE);

      expect(w.trouble).toHaveLength(0);
      expect(w.unauthorized()).toBe(0);
      expect(w.calls()).toBe(1);
      expect(vi.getTimerCount()).toBe(0);
    }
  });
});

describe('when polling fails', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function failing(error: unknown, watching: string[] = []) {
    const running = board(['running', 'a']);
    const trouble: (string | null)[] = [];
    let unauthorized = 0;
    let calls = 0;
    const poller = createPoller({
      fetchDashboard: async () => {
        calls += 1;
        throw error;
      },
      getWatching: () => new Set(watching),
      onDashboard: () => {},
      onUnauthorized: () => {
        unauthorized += 1;
      },
      onTrouble: (m) => trouble.push(m),
      isVisible: () => true,
    });
    poller.sync(running);
    return { poller, trouble, calls: () => calls, unauthorized: () => unauthorized };
  }

  it('keeps trying after a blip, and says nothing about it', async () => {
    const f = failing(new Error('network'));

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(f.calls()).toBe(1);
    expect(f.poller.isRunning()).toBe(true);
    expect(f.trouble.filter(Boolean)).toHaveLength(0);

    f.poller.stop();
  });

  it('tells the user once failures persist — silence must not be indefinite', async () => {
    const f = failing(new Error('network'));

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * FAILURES_BEFORE_NOTICE);

    expect(f.calls()).toBe(FAILURES_BEFORE_NOTICE);
    expect(f.trouble.at(-1)).toContain('Live updates have stopped');
    // Still trying. The notice reports reality; it does not give up on it.
    expect(f.poller.isRunning()).toBe(true);

    f.poller.stop();
  });

  it('stops on an expired session rather than retrying a 401 forever', async () => {
    const f = failing({ status: 401 });

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    expect(f.unauthorized()).toBe(1);
    expect(f.poller.isRunning()).toBe(false);

    const after = f.calls();
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    expect(f.calls()).toBe(after);
  });

  it('clears the notice once a poll succeeds again', async () => {
    const running = board(['running', 'a']);
    const trouble: (string | null)[] = [];
    let calls = 0;
    const poller = createPoller({
      fetchDashboard: async () => {
        calls += 1;
        if (calls <= FAILURES_BEFORE_NOTICE) throw new Error('network');
        return running;
      },
      getWatching: () => new Set(),
      onDashboard: () => {},
      onUnauthorized: () => {},
      onTrouble: (m) => trouble.push(m),
      isVisible: () => true,
    });
    poller.sync(running);

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * FAILURES_BEFORE_NOTICE);
    expect(trouble.at(-1)).toContain('Live updates have stopped');

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(trouble.at(-1)).toBeNull();

    poller.stop();
  });
});
