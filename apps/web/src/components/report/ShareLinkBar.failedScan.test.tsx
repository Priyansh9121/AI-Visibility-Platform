// @vitest-environment jsdom

/**
 * A failed scan cannot be shared — checklist 3.24. The API half (409
 * `/problems/failed-scan-not-shareable`) is proved in test_failed_scan_hidden.py.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const shareLink = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, shareLink: (id: string) => shareLink(id) } };
});

const { ShareLinkBar } = await import('./ShareLinkBar');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SENTENCE = 'This scan failed, so its report cannot be shared.';
let root: Root | null = null;

function mounted(scanStatus?: string): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<ShareLinkBar scanId="scan_01" scanStatus={scanStatus} />));
  return el;
}

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

beforeEach(() => {
  shareLink.mockReset();
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the share bar for a failed scan', () => {
  it('shows no button and says why', () => {
    const el = mounted('failed');
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.querySelectorAll('button')).toHaveLength(0);
    expect(el.textContent).toContain(SENTENCE);
    expect(el.textContent).not.toContain('Creates a link anyone can open');
  });

  it.each(['succeeded', 'partial', 'running', 'queued', 'cancelled', undefined])(
    'keeps the button for %s',
    (status) => {
      const el = mounted(status);
      expect(button(el, 'Get shareable link')).toBeDefined();
      expect(el.textContent).not.toContain(SENTENCE);
    },
  );

  it('turns into the failed-scan state, with the API detail, if the 409 ever comes back', async () => {
    const detail = 'This scan failed, so its report cannot be shared.';
    shareLink.mockRejectedValue(
      new ApiProblem({
        type: '/problems/failed-scan-not-shareable',
        title: 'Failed scan cannot be shared',
        status: 409,
        detail,
        instance: '/api/v1/scans/scan_01/share',
      }),
    );
    const el = mounted('partial');
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    // The refusal becomes the failed-scan state: the sentence, and no Get button.
    expect(el.textContent).toContain(detail);
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.querySelectorAll('button')).toHaveLength(0);
  });

  it('still says the generic line for any other failure', async () => {
    shareLink.mockRejectedValue(new Error('boom'));
    const el = mounted('succeeded');
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'The link could not be created. Try again.',
    );
  });
});
