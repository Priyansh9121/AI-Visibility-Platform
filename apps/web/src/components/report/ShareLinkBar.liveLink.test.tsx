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
// A failed scan's link no longer opens the report (3.29), so neither the end date nor
// "works" may be said of it. The lead says so after the sentence that says the scan failed.
const FAILED_LEAD = 'This scan failed, so its report cannot be shared.';
const DEAD_LINK = 'Its existing link no longer opens the report.';
let root: Root | null = null;

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

/** Every "The link works until ..." line on the bar: none, or one. */
const dateLines = (el: HTMLElement) =>
  [...el.querySelectorAll('p')]
    .map((p) => p.textContent ?? '')
    .filter((text) => text.startsWith('The link works until'));

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

  // This was "shows the minted date once Get shareable link is pressed": the API used to move a
  // live link's end to 30 days from the press, so the bar showed a later date. `shareLink` is a
  // mock here, answering as the API now does (3.28), so what the bar shows after a press is the end
  // it already showed. It is the API's tests that pin that behaviour, not this one:
  // test_share_link.py's TestExpiry, in test_pressing_on_a_live_link_returns_it_exactly_as_it_is
  // (a live link comes back unchanged) and
  // test_pressing_after_the_link_expired_makes_a_new_link_and_the_old_address_stays_dead
  // (an ended one is replaced, with a new end).
  it('keeps the date it had once Get shareable link is pressed, when the API returns a live link as it is', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: LIVE });
    const el = mounted({ shareExpiresAt: LIVE });
    expect(dateLines(el)).toEqual([LIVE_LINE]);
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(shareLink).toHaveBeenCalledTimes(1);
    // The same single line, and no other date. The person has the URL now, and Withdraw stays.
    expect(dateLines(el)).toEqual([LIVE_LINE]);
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(URL_);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  // The link ended or was replaced after the page loaded: the API then answers with a different
  // end, and the bar says that one, not the end it was mounted with.
  it('replaces the date with the one the API returns when it is not the end the page loaded with', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    expect(dateLines(el)).toEqual([LIVE_LINE]);
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(dateLines(el)).toEqual([MINTED_LINE]);
  });

  it("shows the new link's own date when a press follows a withdrawal", async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
    expect(el.textContent).toContain('Link withdrawn.');
    expect(dateLines(el)).toEqual([]);
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    // A new link, so the date is the one the API gave it, and the old one is not said again.
    expect(dateLines(el)).toEqual([MINTED_LINE]);
    expect(el.textContent).not.toContain(LIVE_LINE);
  });
});

describe('a failed Get shareable link while a link is live', () => {
  // With a link live the press asks for it and nothing is created, so a failure says it could not be
  // got (3.52). With no link live a press does create one, and its failure is as it was.
  const FAILURE_LIVE = 'We could not get the link. Try again.';
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
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(FAILURE_LIVE);
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
  it('shows the failed-scan sentence and that the link no longer opens, drops Get and the date line, and keeps Withdraw', async () => {
    const detail = FAILED_LEAD;
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
    // Not vacuous: before the refusal the page is a live link's, with its date.
    expect(el.textContent).toContain(LIVE_LINE);
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.textContent).toContain(detail);
    expect(el.textContent).toContain(DEAD_LINK);
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.textContent).not.toContain('The link works until');
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
  it('says it cannot be shared and its link no longer opens, with no Get button and no end date, and still offers Withdraw', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    expect(el.textContent).toContain(FAILED_LEAD);
    expect(el.textContent).toContain(DEAD_LINK);
    expect(button(el, 'Get shareable link')).toBeUndefined();
    // The link does not open the report, so a date it "works until" would be untrue.
    expect(el.textContent).not.toContain('The link works until');
    expect(el.textContent).not.toContain(LIVE_LINE);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('withdraws from there: confirm, then the link is gone and the failed-scan sentence stays', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');
    expect(withdrawShareLink).not.toHaveBeenCalled();
    // The same focus step as any live link: the safe answer first.
    expect(document.activeElement).toBe(button(el, 'Keep link'));
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
    expect(withdrawShareLink).toHaveBeenCalledWith('scan_01');
    expect(el.querySelector('[role="status"]')?.textContent).toBe(
      'Link withdrawn. It no longer opens this report.',
    );
    expect(button(el, 'Withdraw link')).toBeUndefined();
    expect(button(el, 'Get shareable link')).toBeUndefined();
    // With nothing left to withdraw the lead is the failed-scan sentence alone.
    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(FAILED_LEAD);
  });

  it('keeping the link brings Withdraw back, still without the date', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    expect(withdrawShareLink).not.toHaveBeenCalled();
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(el.textContent).not.toContain('The link works until');
    expect(el.textContent).toContain(DEAD_LINK);
  });

  it('with no live link it has no Withdraw either', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: null });
    expect(el.querySelectorAll('button')).toHaveLength(0);
  });
});

// The 3.29 review. A failed scan's link opens nothing whatever the agency does, so no
// sentence on the bar may talk as if it might: not the question before Withdraw, not the
// failure of a Withdraw, and no field to copy a link from.
describe('a failed scan that was shared before it failed: what the bar asks and offers', () => {
  const MAY_STILL_OPEN = 'The link could not be withdrawn and may still open. Try again.';
  const NOT_WITHDRAWN = 'The link could not be withdrawn. Try again.';
  const problem = (status: number) =>
    new ApiProblem({
      type: '/problems/unknown',
      title: 'Request failed',
      status,
      detail: `The server returned ${status}.`,
      instance: '/api/v1/scans/scan_01/share',
    });

  async function confirmWithdraw(el: HTMLElement) {
    act(() => button(el, 'Withdraw link')!.click());
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
  }

  it.each([
    ['a 502 from a proxy', problem(502)],
    ['a 500', problem(500)],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])(
    'after %s it says only that the link could not be withdrawn, and puts focus back on Withdraw',
    async (_what, failure) => {
      withdrawShareLink.mockRejectedValue(failure);
      const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
      await confirmWithdraw(el);
      // Not vacuous: the request went out and failed.
      expect(withdrawShareLink).toHaveBeenCalledWith('scan_01');
      expect(el.querySelector('[role="alert"]')?.textContent).toBe(NOT_WITHDRAWN);
      expect(el.textContent).not.toContain('may still open');
      expect(el.textContent).not.toContain('The server returned');
      // Still there to try again, and focus is on it rather than on <body>.
      const trigger = button(el, 'Withdraw link');
      expect(trigger).toBeDefined();
      expect(document.activeElement).toBe(trigger);
    },
  );

  it('and a scan that did not fail still says the link may still open', async () => {
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: LIVE });
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(MAY_STILL_OPEN);
  });

  it("asks that a failed scan's link will be removed permanently, and does not say anyone will no longer see a report", () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link? It will be removed permanently.');
    expect(el.textContent).not.toContain('no longer see');
    // The buttons are the ones every link has.
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(button(el, 'Keep link')).toBeDefined();
  });

  it('and a scan that did not fail keeps the question it had', () => {
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain(
      'Withdraw this link? Anyone who opens it will no longer see the report.',
    );
    expect(el.textContent).not.toContain('removed permanently');
  });

  it('stops offering a link made earlier in the visit for copying once the page knows the scan failed', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ scanStatus: 'partial' });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    // Not vacuous: a link just made is offered, with its field and Copy.
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(URL_);
    expect(button(el, 'Copy')).toBeDefined();

    // The report is read again and says the scan failed: the same bar gets the new status.
    act(() =>
      root!.render(<ShareLinkBar scanId="scan_01" scanStatus="failed" shareExpiresAt={undefined} />),
    );
    expect(el.querySelector('input')).toBeNull();
    expect(button(el, 'Copy')).toBeUndefined();
    expect(el.textContent).not.toContain('The link works until');
    expect(button(el, 'Withdraw link')).toBeDefined();
    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(
      `${FAILED_LEAD} ${DEAD_LINK}`,
    );
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

  it('on a failed scan with a live link, says the existing link no longer opens the report', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(
      `${FAILED_LEAD} ${DEAD_LINK}`,
    );
    // The two claims 3.24 and 3.25 made of a link that has since stopped opening anything.
    expect(el.textContent).not.toContain('works until');
    expect(el.textContent).not.toContain('Its existing link works');
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

  // Was "keeps the date line while Creating…, with Withdraw disabled", and ended on the minted date.
  // `shareLink` answers as the API now does for a live link (3.28): the same end, so the line is
  // the same after it too. The busy label is "Getting the link…" with a link live (3.52): nothing
  // is created, and ShareLinkBar.wording.test.tsx holds the labels for both cases.
  it('keeps the date line while Getting the link… and after the same end comes back, with Withdraw disabled meanwhile', async () => {
    let finish!: (v: { url: string; expiresAt: string }) => void;
    shareLink.mockReturnValue(new Promise((r) => (finish = r)));
    const el = mounted({ shareExpiresAt: LIVE });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    expect(button(el, 'Getting the link…')!.disabled).toBe(true);
    expect(dateLines(el)).toEqual([LIVE_LINE]);
    expect(button(el, 'Withdraw link')!.disabled).toBe(true);
    await act(async () => finish({ url: URL_, expiresAt: LIVE }));
    await settle();
    expect(dateLines(el)).toEqual([LIVE_LINE]);
  });
});
