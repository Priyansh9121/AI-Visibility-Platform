// @vitest-environment jsdom

/**
 * The dashboard route's polling, as the route wires it — U7.
 *
 * `lib/dashboard/polling.test.ts` proves the rules on a bare poller. This file mounts the real
 * route (`app/dashboard/page.tsx`, the API mocked at its edge) because what the brief says about
 * the wiring cannot be seen any other way, and a static render does not run effects: the
 * visibilitychange listener is added once and taken off again on unmount, a hidden tab leaves no
 * timer behind, showing the tab polls once at once, and leaving the page leaves nothing running.
 *
 * Every wait is the fake clock's. The tab is hidden and shown the way a browser does it:
 * `document.visibilityState` changes, then `visibilitychange` is dispatched on the document.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { Dashboard } from '@avp/shared-types';
import { dashboard, scan } from '@/lib/dashboard/__fixtures__/dashboards';

const dashboardCall = vi.fn<() => Promise<Dashboard>>();
const runScanCall = vi.fn<(clientId: string) => Promise<{ id: string }>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      dashboard: () => dashboardCall(),
      runScan: (clientId: string) => runScanCall(clientId),
      // The allowance label and the role are not under test; both reads fail quietly, which is
      // what the screen does for a member.
      billing: () => Promise.reject(new Error('not under test')),
      me: () => Promise.reject(new Error('not under test')),
    },
  };
});

// Imported after the mock so the route sees it.
const { default: DashboardRoute } = await import('./page');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const UPDATES_ITSELF = 'this page updates itself';

const running = dashboard([
  scan({ id: 'scan_running', status: 'running', compositeScore: null, finishedAt: null }),
]);
const finished = dashboard([scan({ id: 'scan_running', status: 'succeeded' })]);

let root: Root | null = null;
let el: HTMLDivElement;
let tab: 'visible' | 'hidden' = 'visible';

async function arrive(options: { strict?: boolean } = {}): Promise<void> {
  el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(options.strict ? <StrictMode><DashboardRoute /></StrictMode> : <DashboardRoute />);
    await vi.advanceTimersByTimeAsync(0);
  });
}

/** Move the fake clock and let everything it sets off settle. */
const step = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** What the browser does when the tab is hidden or shown. */
function setTab(state: 'visible' | 'hidden'): void {
  act(() => {
    tab = state;
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

const reads = () => dashboardCall.mock.calls.length;

beforeEach(() => {
  vi.useFakeTimers();
  tab = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => tab });
  dashboardCall.mockReset();
  runScanCall.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  // Drop the own property the tests put on the document, so the next file sees jsdom's.
  delete (document as { visibilityState?: unknown }).visibilityState;
  vi.useRealTimers();
});

describe('no visible change: the page polls as it did, from the same moment', () => {
  it('asks again five seconds after it finds a scan running, and says the page updates itself', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    expect(reads()).toBe(1); // the load
    expect(el.textContent).toContain(UPDATES_ITSELF);

    await step(5 * SECOND - 1);
    expect(reads()).toBe(1);
    await step(1);
    expect(reads()).toBe(2);
    expect(el.textContent).toContain(UPDATES_ITSELF);
  });

  it('stops when the scan lands, and the page stops saying it updates itself', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    dashboardCall.mockResolvedValue(finished);
    await step(5 * SECOND);

    expect(el.textContent).not.toContain(UPDATES_ITSELF);
    expect(vi.getTimerCount()).toBe(0);
    const atLanding = reads();

    // Neither time nor the tab coming and going brings it back.
    setTab('hidden');
    setTab('visible');
    await step(10 * MINUTE);
    expect(reads()).toBe(atLanding);
  });
});

describe('the route backs off as the scan ages', () => {
  it('reads the dashboard on the schedule: the load, then 46 polls over 6.5 minutes', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();

    await step(6.5 * MINUTE);
    expect(reads()).toBe(1 + 46);
  });

  it('is one poller under StrictMode, which mounts every effect twice in development', async () => {
    dashboardCall.mockResolvedValue(running);
    const add = vi.spyOn(document, 'addEventListener');
    const remove = vi.spyOn(document, 'removeEventListener');
    await arrive({ strict: true });
    const onMount = reads();
    const listeners = (spy: typeof add | typeof remove) =>
      spy.mock.calls.filter(([type]) => type === 'visibilitychange').length;

    // Added, taken off, added again: one left standing.
    expect(listeners(add) - listeners(remove)).toBe(1);

    await step(6.5 * MINUTE);
    expect(reads() - onMount).toBe(46);
    add.mockRestore();
    remove.mockRestore();
  });

  it('starts the fast pace again when a Re-run adds a scan to one that has been running a while', async () => {
    const alpha = { clientId: 'clnt_alpha', clientName: 'Alpha', clientDomain: 'alpha.example' };
    const beta = { clientId: 'clnt_beta', clientName: 'Beta', clientDomain: 'beta.example' };
    const alphaRunning = scan({
      ...alpha,
      id: 'scan_alpha',
      status: 'running',
      compositeScore: null,
      finishedAt: null,
    });
    const betaOld = scan({ ...beta, id: 'scan_beta_old', status: 'succeeded' });
    const betaNew = scan({
      ...beta,
      id: 'scan_beta_new',
      status: 'queued',
      compositeScore: null,
      finishedAt: null,
    });
    dashboardCall.mockResolvedValue(dashboard([alphaRunning, betaOld]));
    await arrive();

    // Alpha has been running for ten minutes and a second: a poll at exactly ten minutes, the
    // next one due at 15 s.
    await step(10 * MINUTE + SECOND);

    dashboardCall.mockResolvedValue(dashboard([betaNew, alphaRunning, betaOld]));
    runScanCall.mockResolvedValue({ id: 'scan_beta_new' });
    const rerun = el.querySelector<HTMLButtonElement>('button[aria-label="Re-run scan for Beta"]');
    expect(rerun, 'no Re-run button for Beta').not.toBeNull();
    act(() => rerun!.click());
    await step(0);
    expect(runScanCall).toHaveBeenCalledWith('clnt_beta');
    const afterPress = reads();

    // The new scan is five seconds old at the next poll, not up to fifteen.
    await step(5 * SECOND - 1);
    expect(reads()).toBe(afterPress);
    await step(1);
    expect(reads()).toBe(afterPress + 1);
  });
});

describe('a hidden tab is not polled', () => {
  it('stops while the tab is hidden, polls once at once when it is shown, then goes back to the schedule', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    await step(12 * SECOND); // polls at 5 s and 10 s
    expect(reads()).toBe(3);

    setTab('hidden');
    expect(vi.getTimerCount()).toBe(0); // stopped, not merely ignored
    await step(30 * MINUTE);
    expect(reads()).toBe(3);

    setTab('visible');
    expect(reads()).toBe(4); // at once
    await step(0);
    expect(vi.getTimerCount()).toBe(1); // and the next one is set up
    await step(15 * SECOND - 1);
    expect(reads()).toBe(4);
    await step(1);
    expect(reads()).toBe(5); // the scan is half an hour old: the ceiling, not five seconds
  });

  it('a page opened in a background tab waits to be shown, then polls once at once', async () => {
    tab = 'hidden';
    dashboardCall.mockResolvedValue(running);
    await arrive();
    expect(reads()).toBe(1); // the load is not polling
    expect(vi.getTimerCount()).toBe(0);
    await step(10 * MINUTE);
    expect(reads()).toBe(1);

    setTab('visible');
    expect(reads()).toBe(2);
    await step(0); // let that poll's answer land inside act
  });

  it('hiding and showing the tab in quick succession leaves one poll and one timer', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    await step(20 * SECOND); // the load and the polls at 5, 10, 15 and 20 s
    expect(reads()).toBe(5);

    let release!: () => void;
    dashboardCall.mockImplementationOnce(
      () => new Promise<Dashboard>((resolve) => (release = () => resolve(running))),
    );
    setTab('hidden');
    setTab('visible'); // polls at once, and the answer is held
    setTab('hidden');
    setTab('visible'); // that poll is still out: not a second request
    expect(reads()).toBe(6);
    expect(vi.getTimerCount()).toBe(0);

    release();
    await step(0);
    expect(vi.getTimerCount()).toBe(1);
    await step(60 * SECOND);
    expect(reads()).toBe(6 + 12); // one poll every five seconds, not two
  });
});

describe('leaving the page leaves nothing running', () => {
  it('adds one visibilitychange listener to the document and takes the same one off on unmount', async () => {
    const add = vi.spyOn(document, 'addEventListener');
    const remove = vi.spyOn(document, 'removeEventListener');
    dashboardCall.mockResolvedValue(running);
    await arrive();
    const typed = (spy: typeof add | typeof remove) =>
      spy.mock.calls.filter(([type]) => type === 'visibilitychange').map(([, listener]) => listener);

    const added = typed(add);
    expect(added).toHaveLength(1);
    expect(typed(remove)).toHaveLength(0);

    act(() => root!.unmount());
    root = null;
    expect(typed(remove)).toEqual(added);
    add.mockRestore();
    remove.mockRestore();
  });

  it('clears the timer, and the tab coming and going or time passing starts nothing', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    await step(7 * SECOND);
    expect(vi.getTimerCount()).toBe(1);
    const before = reads();

    act(() => root!.unmount());
    root = null;
    expect(vi.getTimerCount()).toBe(0);

    setTab('hidden');
    setTab('visible');
    await step(10 * MINUTE);
    expect(reads()).toBe(before);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a poll still out when the page is left changes nothing and logs nothing', async () => {
    dashboardCall.mockResolvedValue(running);
    await arrive();
    let release!: () => void;
    dashboardCall.mockImplementationOnce(
      () => new Promise<Dashboard>((resolve) => (release = () => resolve(finished))),
    );
    await step(5 * SECOND); // the poll is out
    const before = reads();

    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    act(() => root!.unmount());
    root = null;
    release();
    await step(10 * MINUTE);

    expect(reads()).toBe(before);
    expect(vi.getTimerCount()).toBe(0);
    expect(logged).not.toHaveBeenCalled();
    logged.mockRestore();
  });
});
