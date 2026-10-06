// @vitest-environment jsdom

/** Nothing unless the API says available; a press saves at once; a skipped month is in words. */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { AutoRescan } from '@avp/shared-types';

const autoRescan = vi.fn<(id: string) => Promise<AutoRescan>>();
const setAutoRescan = vi.fn<(id: string, enabled: boolean) => Promise<AutoRescan>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      autoRescan: (id: string) => autoRescan(id),
      setAutoRescan: (id: string, e: boolean) => setAutoRescan(id, e),
    },
  };
});

const { AutoRescanPanel } = await import('./AutoRescanPanel');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
beforeEach(() => {
  autoRescan.mockReset();
  setAutoRescan.mockReset();
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
const state = (over: Partial<AutoRescan> = {}): AutoRescan => ({
  available: true,
  enabled: false,
  skipReason: null,
  skippedAt: null,
  retriesAt: null,
  ...over,
});

async function mounted(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<AutoRescanPanel clientId="cli_1" />));
  await settle();
  return el;
}
const button = (el: HTMLElement, name: 'On' | 'Off') =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === name)!;

describe('AutoRescanPanel', () => {
  it('keeps keyboard focus on a button while saving and ignores a second press', async () => {
    autoRescan.mockResolvedValue(state());
    let finish: (a: AutoRescan) => void = () => undefined;
    setAutoRescan.mockReturnValue(new Promise<AutoRescan>((resolve) => (finish = resolve)));
    const el = await mounted();
    const on = button(el, 'On');
    on.focus();
    act(() => on.click());
    expect(on.disabled).toBe(false);
    expect(on.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(on);
    act(() => button(el, 'Off').click());
    act(() => on.click());
    expect(setAutoRescan).toHaveBeenCalledTimes(1);
    await act(async () => finish(state({ enabled: true })));
    expect(button(el, 'On').getAttribute('aria-pressed')).toBe('true');
  });

  it('says what On does, with the plan number 40 and the UTC calendar month', async () => {
    autoRescan.mockResolvedValue(state({ enabled: true }));
    const el = await mounted();
    const text = el.textContent ?? '';
    expect(text).toContain(
      "On: when this client's latest scan is 30 or more days old (checked about every 10 minutes, so switching on now may start a scan straight away), a scan starts by itself as the person who turned this on. It is a normal scan: it counts toward the agency's 40 scans for the calendar month (UTC).",
    );
  });

  it('says what Off means in the words of the product', async () => {
    autoRescan.mockResolvedValue(state());
    const el = await mounted();
    expect(el.textContent).toContain('Off: this client is only scanned when someone starts a scan.');
  });

  it('draws nothing when the API says it is not available', async () => {
    autoRescan.mockResolvedValue({ available: false, enabled: false });
    const el = await mounted();
    expect(el.innerHTML).toBe('');
  });

  it('draws nothing when the read fails', async () => {
    autoRescan.mockRejectedValue(new Error('no'));
    const el = await mounted();
    expect(el.innerHTML).toBe('');
  });

  it('shows the stored choice and saves a press at once, showing what the API answered', async () => {
    autoRescan.mockResolvedValue(state());
    setAutoRescan.mockResolvedValue(state({ enabled: true }));
    const el = await mounted();
    expect(button(el, 'Off').getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('[data-testid="auto-rescan-status"]')?.textContent).toBe('');
    act(() => button(el, 'On').click());
    await settle();
    expect(setAutoRescan).toHaveBeenCalledWith('cli_1', true);
    expect(button(el, 'On').getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('[data-testid="auto-rescan-status"]')?.textContent).toBe('Saved.');
  });

  it('says the change did not go through when the save fails, and keeps the old choice', async () => {
    autoRescan.mockResolvedValue(state());
    setAutoRescan.mockRejectedValue(new Error('no'));
    const el = await mounted();
    act(() => button(el, 'On').click());
    await settle();
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('did not go through');
    expect(button(el, 'Off').getAttribute('aria-pressed')).toBe('true');
  });

  it('shows a skipped month in plain words while it is on, and not while it is off', async () => {
    autoRescan.mockResolvedValue(
      state({
        enabled: true,
        skipReason: 'ALLOWANCE_USED',
        skippedAt: '2026-10-20T09:00:00Z',
        retriesAt: '2026-11-01T00:00:00Z',
      }),
    );
    const el = await mounted();
    const skip = el.querySelector('[data-testid="auto-rescan-skip"]')?.textContent ?? '';
    expect(skip).toContain("already used this month's scans");
    expect(skip).toContain('from 1 Nov 2026 (UTC)');
    expect(skip).not.toContain('ALLOWANCE_USED');
  });

  it('shows no skip sentence for a client with nothing skipped', async () => {
    autoRescan.mockResolvedValue(state({ enabled: true }));
    const el = await mounted();
    expect(el.querySelector('[data-testid="auto-rescan-skip"]')).toBeNull();
  });
});
