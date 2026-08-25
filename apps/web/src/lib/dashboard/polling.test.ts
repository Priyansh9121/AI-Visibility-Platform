/**
 * The polling rules — Epic 9.7.
 *
 * **This is the first use of vitest's fake timers in this repo.** Stated rather
 * than slipped in: every frontend test before this rendered to static markup and
 * asserted over the HTML, which cannot express "and five seconds later". The
 * timing rules are the part of a poller that can actually be wrong — starting
 * when it should not, never stopping, leaking an interval, going silent after a
 * blip — and none of those are visible in a rendered string.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Dashboard, ScanStatus } from '@avp/shared-types';
import {
  FAILURES_BEFORE_NOTICE,
  POLL_INTERVAL_MS,
  createPoller,
  isTerminal,
  settled,
  shouldPoll,
} from './polling';
import { dashboard, scan } from '@/lib/dashboard/__fixtures__/dashboards';

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
    // so "poll any non-terminal scan" means polling forever, every five
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

  it('does no work while the tab is hidden, and resumes when it returns', async () => {
    const running = board(['running', 'a']);
    let visible = false;
    const h = harness([running, running], { visible: () => visible });
    h.poller.sync(running);

    await h.advance(POLL_INTERVAL_MS * 3);
    expect(h.calls()).toBe(0); // backgrounded: timer alive, no requests

    visible = true;
    await h.advance(POLL_INTERVAL_MS);
    expect(h.calls()).toBe(1);
  });

  it('polls immediately when the tab is refocused rather than waiting out the interval', async () => {
    const running = board(['running', 'a']);
    const h = harness([running, running]);
    h.poller.sync(running);

    h.poller.pokeNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(h.calls()).toBe(1);
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
    expect(calls).toBe(1); // three intervals skipped while one was in flight

    release();
    poller.stop();
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
