// @vitest-environment jsdom

/** The switch saves a press at once, shows what the API answered, and says so in a live region. */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const setScanEmails = vi.fn<(enabled: boolean) => Promise<{ available: boolean; enabled: boolean }>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, setScanEmails: (e: boolean) => setScanEmails(e) } };
});

const { ScanEmailsPanel } = await import('./ScanEmailsPanel');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
beforeEach(() => {
  setScanEmails.mockReset();
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function mounted(enabled: boolean): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<ScanEmailsPanel enabled={enabled} />));
  return el;
}
const button = (el: HTMLElement, name: 'On' | 'Off') =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === name)!;
const status = (el: HTMLElement) => el.querySelector('[data-testid="scan-emails-status"]')!;

describe('ScanEmailsPanel', () => {
  it('starts with the stored choice pressed and an empty status region already on the page', () => {
    const el = mounted(true);
    expect(button(el, 'On').getAttribute('aria-pressed')).toBe('true');
    expect(button(el, 'Off').getAttribute('aria-pressed')).toBe('false');
    expect(status(el).textContent).toBe('');
    expect(el.querySelector('[role="group"]')?.getAttribute('aria-label')).toBe('Scan-finished emails');
  });

  it('saves a press, shows what the API answered, and says "Saved." in the region', async () => {
    setScanEmails.mockResolvedValue({ available: true, enabled: false });
    const el = mounted(true);
    const region = status(el);
    act(() => button(el, 'Off').click());
    expect(setScanEmails).toHaveBeenCalledTimes(1);
    expect(setScanEmails).toHaveBeenCalledWith(false);
    await settle();
    expect(button(el, 'Off').getAttribute('aria-pressed')).toBe('true');
    expect(status(el)).toBe(region);
    expect(region.textContent).toBe('Saved.');
  });

  it('keeps focus on the pressed button while saving and ignores another press', async () => {
    let finish!: (v: { available: boolean; enabled: boolean }) => void;
    setScanEmails.mockReturnValue(new Promise((r) => { finish = r; }));
    const el = mounted(true);
    const off = button(el, 'Off');
    off.focus();
    act(() => off.click());
    expect(document.activeElement).toBe(off);
    expect(off.disabled).toBe(false);
    expect(off.getAttribute('aria-disabled')).toBe('true');
    act(() => button(el, 'On').click());
    act(() => off.click());
    expect(setScanEmails).toHaveBeenCalledTimes(1);
    finish({ available: true, enabled: false });
    await settle();
    expect(document.activeElement).toBe(off);
    expect(off.getAttribute('aria-disabled')).toBe('false');
  });

  it('keeps the old choice and says it did not go through when the request fails', async () => {
    setScanEmails.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted(true);
    act(() => button(el, 'Off').click());
    await settle();
    expect(button(el, 'On').getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'The change did not go through. Check your connection and try again.',
    );
    expect(status(el).textContent).toBe('');
  });

  it('does not ask again for the choice it already shows', () => {
    const el = mounted(true);
    act(() => button(el, 'On').click());
    expect(setScanEmails).not.toHaveBeenCalled();
  });
});
