// @vitest-environment jsdom

/**
 * Withdraw without minting first — checklist 3.25. The report payload says when the
 * scan's live link ends (`shareExpiresAt`); the bar shows that and Withdraw from the
 * first render. The API half is in test_share_expiry_on_report.py.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const shareLink = vi.fn();
const withdrawShareLink = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      shareLink: (id: string) => shareLink(id),
      withdrawShareLink: (id: string) => withdrawShareLink(id),
    },
  };
});

const { ShareLinkBar } = await import('./ShareLinkBar');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LIVE = '2026-11-04T09:30:00Z';
const LIVE_LINE = 'The link works until 04 Nov 2026, 09:30 UTC.';
const MINTED = '2026-11-20T10:00:00Z';
const MINTED_LINE = 'The link works until 20 Nov 2026, 10:00 UTC.';
const URL_ = 'https://app.example/share#TOKENSENTINEL0123456789';
let root: Root | null = null;

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

function mounted(props: { scanStatus?: string | undefined; shareExpiresAt?: string | null | undefined }): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(
      <ShareLinkBar
        scanId="scan_01"
        scanStatus={props.scanStatus}
        shareExpiresAt={props.shareExpiresAt}
      />,
    ),
  );
  return el;
}

beforeEach(() => {
  shareLink.mockReset();
  withdrawShareLink.mockReset();
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('a link that is already live', () => {
  it('shows its end date and Withdraw without minting, and keeps Get shareable link', () => {
    const el = mounted({ shareExpiresAt: LIVE });
    expect(shareLink).not.toHaveBeenCalled();
    expect(el.textContent).toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(button(el, 'Get shareable link')).toBeDefined();
    // The token is never sent, so there is no field and nothing to copy.
    expect(el.querySelector('input')).toBeNull();
    expect(button(el, 'Copy')).toBeUndefined();
  });

  it('withdraws from there: confirm, then the line and Withdraw go away', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');
    expect(withdrawShareLink).not.toHaveBeenCalled();
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
    expect(withdrawShareLink).toHaveBeenCalledWith('scan_01');
    expect(el.textContent).toContain('Link withdrawn.');
    expect(el.textContent).not.toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeUndefined();
  });

  it('keeping the link brings the Withdraw button back', () => {
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    expect(withdrawShareLink).not.toHaveBeenCalled();
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(el.textContent).toContain(LIVE_LINE);
  });

  it('shows the minted date once Get shareable link is pressed', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.textContent).toContain(MINTED_LINE);
    expect(el.textContent).not.toContain(LIVE_LINE);
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(URL_);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });
});

describe('a failed Get shareable link while a link is live', () => {
  const FAILURE = 'The link could not be created. Try again.';

  it('keeps the date line and Withdraw, and says the failure in the alert region', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.textContent).toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(FAILURE);
  });

  it('and Withdraw still works afterwards', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    act(() => button(el, 'Withdraw link')!.click());
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
    expect(withdrawShareLink).toHaveBeenCalledWith('scan_01');
    expect(el.textContent).toContain('Link withdrawn.');
  });

  it('with no live link it is the plain failure, as before', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ shareExpiresAt: null });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(FAILURE);
    expect(button(el, 'Withdraw link')).toBeUndefined();
    expect(el.textContent).not.toContain('The link works until');
  });
});

describe('a 409 refusal while a link is live', () => {
  it('shows the failed-scan sentence, drops Get, and keeps the date line and Withdraw', async () => {
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
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.textContent).toContain(detail);
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.textContent).toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });
});

describe('nothing live', () => {
  it('has no Withdraw and no date line', () => {
    for (const shareExpiresAt of [null, undefined]) {
      const el = mounted({ shareExpiresAt });
      expect(button(el, 'Withdraw link')).toBeUndefined();
      expect(el.textContent).not.toContain('The link works until');
      expect(button(el, 'Get shareable link')).toBeDefined();
      act(() => root?.unmount());
      root = null;
      document.body.innerHTML = '';
    }
  });

  it('shows the date from the mint response after Get shareable link', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: null });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.textContent).toContain(MINTED_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });
});

describe('a failed scan that was shared before it failed', () => {
  it('says it cannot be shared, with no Get button, and still offers Withdraw and the date', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    expect(el.textContent).toContain('This scan failed, so its report cannot be shared.');
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.textContent).toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('with no live link it has no Withdraw either', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: null });
    expect(el.querySelectorAll('button')).toHaveLength(0);
  });
});

describe('review fixes', () => {
  it('disables Get shareable link while a withdrawal is being confirmed or sent', async () => {
    let finish!: () => void;
    withdrawShareLink.mockReturnValue(new Promise<void>((r) => (finish = r)));
    const el = mounted({ shareExpiresAt: LIVE });
    expect(button(el, 'Get shareable link')!.disabled).toBe(false);
    act(() => button(el, 'Withdraw link')!.click());
    expect(button(el, 'Get shareable link')!.disabled).toBe(true);
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    expect(button(el, 'Get shareable link')!.disabled).toBe(true);
    await act(async () => finish());
    await settle();
    // Withdrawn: Get is back and usable.
    expect(button(el, 'Get shareable link')!.disabled).toBe(false);
  });

  it('keeps Get usable again after Keep link', () => {
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    expect(button(el, 'Get shareable link')!.disabled).toBe(false);
  });

  it('says the end as a stamp with its time and zone, not a bare day', () => {
    const el = mounted({ shareExpiresAt: LIVE });
    expect(el.textContent).toContain('The link works until 04 Nov 2026, 09:30 UTC.');
  });

  it('on a failed scan with a live link, says the existing link still works', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    expect(el.textContent).toContain('This scan failed, so its report cannot be shared.');
    expect(el.textContent).toContain('Its existing link works until it is withdrawn or ends.');
    const bare = mounted({ scanStatus: 'failed', shareExpiresAt: null });
    expect(bare.textContent).not.toContain('Its existing link');
  });

  it('announces a refusal: the lead sentence is in a polite live region that was already there', async () => {
    shareLink.mockRejectedValue(
      new ApiProblem({
        type: '/problems/failed-scan-not-shareable',
        title: 'Failed scan cannot be shared',
        status: 409,
        detail: 'This scan failed, so its report cannot be shared.',
        instance: '/x',
      }),
    );
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: null });
    const region = el.querySelector('[data-testid="share-lead"]');
    expect(region).not.toBeNull();
    expect(region?.getAttribute('aria-live')).toBe('polite');
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector('[data-testid="share-lead"]')).toBe(region);
    expect(region?.textContent).toBe('This scan failed, so its report cannot be shared.');
  });

  it('keeps the date line while Creating…, with Withdraw disabled', async () => {
    let finish!: (v: { url: string; expiresAt: string }) => void;
    shareLink.mockReturnValue(new Promise((r) => (finish = r)));
    const el = mounted({ shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    expect(button(el, 'Creating…')!.disabled).toBe(true);
    expect(el.textContent).toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')!.disabled).toBe(true);
    await act(async () => finish({ url: URL_, expiresAt: MINTED }));
    await settle();
    expect(el.textContent).toContain(MINTED_LINE);
  });
});
