// @vitest-environment jsdom

/**
 * After Get shareable link the link is announced and its field takes focus — checklist 3.33.
 *
 * The button that was pressed leaves the page with the link, so focus fell on <body> and
 * nothing said the link was there. Now the status region reads "Link ready. It works until
 * <date>." and focus goes to the link field, its text selected. A mint that fails gives focus
 * back to Get shareable link, which a browser took off it while it was disabled, so a retry is
 * one key away. A failed scan never reaches this path (no Get button), and the 3.24 and 3.29
 * states are asserted as they were.
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
const LIVE_STAMP = '04 Nov 2026, 09:30 UTC';
const MINTED = '2026-11-20T10:00:00Z';
const MINTED_STAMP = '20 Nov 2026, 10:00 UTC';
const READY = `Link ready. It works until ${MINTED_STAMP}.`;
const URL_ = 'https://app.example/share#TOKENSENTINEL0123456789';
const FAILURE = 'The link could not be created. Try again.';
const WITHDRAWN = 'Link withdrawn. It no longer opens this report.';
const FAILED_LEAD = 'This scan failed, so its report cannot be shared.';
let root: Root | null = null;

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);
const field = (el: HTMLElement) => el.querySelector<HTMLInputElement>('input[readonly]');
const status = (el: HTMLElement) => el.querySelector('[role="status"]');
const alert = (el: HTMLElement) => el.querySelector('[role="alert"]');

function mounted(
  props: { scanStatus?: string | undefined; shareExpiresAt?: string | null | undefined } = {},
): HTMLDivElement {
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

/** A real press: the button has focus, then the click, then the request settles. */
async function press(el: HTMLElement, label: string) {
  const target = button(el, label)!;
  act(() => target.focus());
  await act(async () => {
    target.click();
  });
  await settle();
}

async function confirmWithdraw(el: HTMLElement) {
  act(() => button(el, 'Withdraw link')!.click());
  await act(async () => {
    button(el, 'Withdraw link')!.click();
  });
  await settle();
}

/**
 * What a browser does when the focused button is disabled: focus falls to <body> (Chromium's
 * focus fix-up, measured in the share bar itself). jsdom keeps it on the disabled button, and
 * its `blur()` ignores a disabled one, so it is said here, by focusing an element and removing
 * it, and a test cannot pass for want of it.
 */
const loseFocus = () =>
  act(() => {
    const sink = document.createElement('button');
    document.body.appendChild(sink);
    sink.focus();
    sink.remove();
  });

/** Press Get with its request held open, lose focus as a browser does, then fail the request. */
async function failedMint(el: HTMLElement, failure: unknown = new TypeError('Failed to fetch')) {
  let fail!: (e: unknown) => void;
  shareLink.mockReturnValueOnce(new Promise((_, reject) => (fail = reject)));
  const get = button(el, 'Get shareable link')!;
  act(() => get.focus());
  await act(async () => {
    get.click();
  });
  // Not vacuous: the request is out, Get is disabled, and focus is on <body>.
  expect(button(el, 'Creating…')?.disabled).toBe(true);
  loseFocus();
  expect(document.activeElement).toBe(document.body);
  await act(async () => fail(failure));
  await settle();
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

describe('focus goes to the link field when the link is made', () => {
  it('from no link: the field has focus, with its text selected', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted();
    expect(field(el)).toBeNull();
    const get = button(el, 'Get shareable link')!;
    act(() => get.focus());
    // Not vacuous: focus starts on the button that is about to leave the page.
    expect(document.activeElement).toBe(get);

    await press(el, 'Get shareable link');

    expect(field(el)?.value).toBe(URL_);
    expect(document.activeElement).toBe(field(el));
    expect([field(el)?.selectionStart, field(el)?.selectionEnd]).toEqual([0, URL_.length]);
  });

  it('from a live link: the field has focus', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    // Not vacuous: a live link from the payload has no field to focus until it is made again.
    expect(field(el)).toBeNull();

    await press(el, 'Get shareable link');

    expect(document.activeElement).toBe(field(el));
    expect(field(el)?.value).toBe(URL_);
    expect([field(el)?.selectionStart, field(el)?.selectionEnd]).toEqual([0, URL_.length]);
  });

  it('a link made again after a withdraw takes focus again', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted();
    await press(el, 'Get shareable link');
    await confirmWithdraw(el);
    expect(field(el)).toBeNull();

    await press(el, 'Get shareable link');

    expect(document.activeElement).toBe(field(el));
    expect(field(el)).not.toBeNull();
  });

  it('after Keep link on a live link, the field still takes it, not the Withdraw link that came back', async () => {
    // Keep link hands focus back to Withdraw link through a flag that stays set. A mint
    // replaces that link, and the Withdraw link that returns with the new one must not
    // claim focus ahead of the field.
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    // Not vacuous: the flag is set, so Withdraw link has focus.
    expect(document.activeElement).toBe(button(el, 'Withdraw link'));

    await press(el, 'Get shareable link');

    expect(document.activeElement).toBe(field(el));
    expect(document.activeElement).not.toBe(button(el, 'Withdraw link'));
  });

  it('only a mint moves it: a re-render of the bar leaves focus where the person put it', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted();
    await press(el, 'Get shareable link');
    expect(document.activeElement).toBe(field(el));

    act(() => button(el, 'Copy')!.focus());
    act(() => root!.render(<ShareLinkBar scanId="scan_01" />));

    expect(document.activeElement).toBe(button(el, 'Copy'));
  });
});

describe('the status region says the link is ready, and when it ends', () => {
  it('reads "Link ready. It works until <date>." with the stamp the works-until line uses', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted();
    await press(el, 'Get shareable link');
    expect(status(el)?.textContent).toBe('Link ready. It works until 20 Nov 2026, 10:00 UTC.');
    // The same stamp, to the minute and with its zone, as the line that stays on the bar.
    expect(el.textContent).toContain('The link works until 20 Nov 2026, 10:00 UTC.');
  });

  it('from a live link it carries the new date, not the old one', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    // Not vacuous: a live link from the payload says nothing here, only in its own line.
    expect(status(el)?.textContent).toBe('');
    expect(el.textContent).toContain(`The link works until ${LIVE_STAMP}.`);

    await press(el, 'Get shareable link');

    expect(status(el)?.textContent).toBe(READY);
    expect(status(el)?.textContent).not.toContain(LIVE_STAMP);
  });

  it('is said in the region that was on the page before it, empty, so it is announced', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted();
    const region = status(el);
    expect(region?.textContent).toBe('');

    await press(el, 'Get shareable link');

    expect(status(el)).toBe(region);
    expect(region?.textContent).toBe(READY);
  });

  it('is the one announcement of the date: the works-until line is not live, and nothing else carries it', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted();
    const lead = el.querySelector('[data-testid="share-lead"]')?.textContent;
    await press(el, 'Get shareable link');

    const line = [...el.querySelectorAll('p')].find((p) =>
      p.textContent?.startsWith('The link works until'),
    );
    // Not vacuous: the line is on the bar, re-rendered with the new date.
    expect(line?.textContent).toBe(`The link works until ${MINTED_STAMP}.`);
    expect(line?.closest('[role="status"], [role="alert"], [aria-live]')).toBeNull();

    const live = [...el.querySelectorAll('[role="status"], [role="alert"], [aria-live]')].filter(
      (region) => region.textContent?.includes(MINTED_STAMP),
    );
    expect(live).toEqual([status(el)]);
    // And the polite lead, the one live region that can change on a mint, did not.
    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(lead);
  });

  it('says only that the link is ready when the response has no end date, and makes none up', async () => {
    shareLink.mockResolvedValue({ url: URL_ });
    const el = mounted();
    await press(el, 'Get shareable link');
    expect(status(el)?.textContent).toBe('Link ready.');
    expect(el.textContent).not.toContain('works until');
    expect(el.textContent).not.toContain('—');
  });

  it('stays while the link is on the bar: Copy and the Withdraw question leave it as it was', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
      const el = mounted();
      await press(el, 'Get shareable link');
      await press(el, 'Copy');
      expect(button(el, 'Copied')).toBeDefined();
      expect(status(el)?.textContent).toBe(READY);
      act(() => button(el, 'Withdraw link')!.click());
      expect(el.textContent).toContain('Withdraw this link?');
      expect(status(el)?.textContent).toBe(READY);
    } finally {
      delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });

  it('gives way to the withdrawn sentence when the link is withdrawn, and says nothing of the link after', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted();
    await press(el, 'Get shareable link');
    await confirmWithdraw(el);
    expect(status(el)?.textContent).toBe(WITHDRAWN);
    expect(el.textContent).not.toContain('Link ready');
  });

  it('is left as it was by a withdraw that failed: the link may still open', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted();
    await press(el, 'Get shareable link');
    await confirmWithdraw(el);
    expect(alert(el)?.textContent).toBe(
      'The link could not be withdrawn and may still open. Try again.',
    );
    expect(status(el)?.textContent).toBe(READY);
  });
});

describe('a mint that fails announces its failure as before, with no field to take focus', () => {
  it('with no link before it: there is no field to focus, nothing is said to be ready, and the alert says why', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted();
    await press(el, 'Get shareable link');

    expect(el.querySelector('input')).toBeNull();
    expect(document.activeElement?.tagName).not.toBe('INPUT');
    expect(status(el)?.textContent).toBe('');
    expect(alert(el)?.textContent).toBe(FAILURE);
    // Still there to try again.
    expect(button(el, 'Get shareable link')).toBeDefined();
  });

  it('with a live link: its line and Withdraw stay, nothing is said to be ready, no field takes focus', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ shareExpiresAt: LIVE });
    await press(el, 'Get shareable link');

    expect(el.querySelector('input')).toBeNull();
    expect(document.activeElement?.tagName).not.toBe('INPUT');
    expect(status(el)?.textContent).toBe('');
    expect(alert(el)?.textContent).toBe(FAILURE);
    expect(el.textContent).toContain(`The link works until ${LIVE_STAMP}.`);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('a 409 refusal is the failed-scan state, as before: its sentence, no field, nothing ready', async () => {
    shareLink.mockRejectedValue(
      new ApiProblem({
        type: '/problems/failed-scan-not-shareable',
        title: 'Failed scan cannot be shared',
        status: 409,
        detail: FAILED_LEAD,
        instance: '/api/v1/scans/scan_01/share',
      }),
    );
    const el = mounted({ scanStatus: 'partial' });
    await press(el, 'Get shareable link');

    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(FAILED_LEAD);
    expect(el.querySelector('input')).toBeNull();
    expect(status(el)?.textContent).toBe('');
    expect(alert(el)?.textContent).toBe('');
    expect(button(el, 'Get shareable link')).toBeUndefined();
    // Get has left the page, so there is nothing for focus to come back to.
    expect(document.activeElement).toBe(document.body);
  });
});

describe('a mint that fails gives focus back to Get shareable link, so a retry is one key away', () => {
  /** What Enter or Space does on a focused button: a click on it, so the retry is that key. */
  async function keyOnFocused() {
    await act(async () => {
      (document.activeElement as HTMLElement).click();
    });
    await settle();
  }

  it('with no link before it: focus is on Get shareable link, the alert still says why, and the key retries', async () => {
    const el = mounted();
    await failedMint(el);

    expect(alert(el)?.textContent).toBe(FAILURE);
    expect(document.activeElement).toBe(button(el, 'Get shareable link'));
    expect((document.activeElement as HTMLButtonElement).disabled).toBe(false);

    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    await keyOnFocused();
    expect(field(el)?.value).toBe(URL_);
    expect(status(el)?.textContent).toBe(READY);
    expect(alert(el)?.textContent).toBe('');
    // And the retry that works takes focus on the link's field, as a first press does.
    expect(document.activeElement).toBe(field(el));
  });

  it('with a live link before it: focus is on Get shareable link, its line and Withdraw stay, and the key retries', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    await failedMint(el);

    expect(alert(el)?.textContent).toBe(FAILURE);
    expect(document.activeElement).toBe(button(el, 'Get shareable link'));
    expect(el.textContent).toContain(`The link works until ${LIVE_STAMP}.`);
    expect(button(el, 'Withdraw link')).toBeDefined();

    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    await keyOnFocused();
    expect(field(el)?.value).toBe(URL_);
    expect(status(el)?.textContent).toBe(READY);
    expect(document.activeElement).toBe(field(el));
  });

  it('after Keep link on a live link, focus still goes to Get shareable link, not to the Withdraw link that came back', async () => {
    // Keep link hands focus back to Withdraw link through a flag that stays set, and a failed
    // mint puts the Withdraw link back on the page: it must not claim focus ahead of Get.
    const el = mounted({ shareExpiresAt: LIVE });
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    // Not vacuous: the flag is set, so Withdraw link has focus.
    expect(document.activeElement).toBe(button(el, 'Withdraw link'));

    await failedMint(el);

    expect(document.activeElement).toBe(button(el, 'Get shareable link'));
    expect(document.activeElement).not.toBe(button(el, 'Withdraw link'));
  });

  it('a person who has moved on while the request was out keeps their place', async () => {
    const elsewhere = document.createElement('input');
    document.body.appendChild(elsewhere);
    const el = mounted();
    let fail!: (e: unknown) => void;
    shareLink.mockReturnValueOnce(new Promise((_, reject) => (fail = reject)));
    const get = button(el, 'Get shareable link')!;
    act(() => get.focus());
    await act(async () => {
      get.click();
    });
    act(() => elsewhere.focus());

    await act(async () => fail(new TypeError('Failed to fetch')));
    await settle();

    expect(alert(el)?.textContent).toBe(FAILURE);
    expect(document.activeElement).toBe(elsewhere);
  });

  it('says nothing new: no status, no extra alert, and the lead is as it was', async () => {
    const el = mounted();
    const lead = el.querySelector('[data-testid="share-lead"]')?.textContent;
    await failedMint(el);
    expect(status(el)?.textContent).toBe('');
    expect(alert(el)?.textContent).toBe(FAILURE);
    expect(el.querySelector('[data-testid="share-lead"]')?.textContent).toBe(lead);
  });
});

/** What a screen reader reads after a control's name: the text of the elements `aria-describedby` points at. */
const description = (node: Element | undefined): string | null => {
  const ids = node?.getAttribute('aria-describedby');
  if (!ids) return null;
  return ids
    .split(/\s+/)
    .map((id) => node!.ownerDocument.getElementById(id)?.textContent ?? '')
    .join(' ');
};

describe('Get shareable link is described by the failure of its own mint, so a screen reader that drops the alert still says it', () => {
  // Focus goes back to Get in the same tick as the alert appears, and a screen reader may cut the
  // alert off for the focus change. Described by the sentence, Get says it when it is reached.
  const WITHDRAW_FAILURE = 'The link could not be withdrawn and may still open. Try again.';

  it('after a failed mint with no link before it: Get is described by the failure sentence, which is the alert\'s own', async () => {
    const el = mounted();
    await failedMint(el);

    const get = button(el, 'Get shareable link');
    expect(description(get)).toBe(FAILURE);
    const id = get!.getAttribute('aria-describedby')!;
    expect(get!.ownerDocument.getElementById(id)?.closest('[role="alert"]')).toBe(alert(el));
    expect(alert(el)?.textContent).toBe(FAILURE);
  });

  it('after a failed mint with a live link before it: Get is described by the failure sentence, which is the alert\'s own', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    await failedMint(el);

    const get = button(el, 'Get shareable link');
    expect(description(get)).toBe(FAILURE);
    const id = get!.getAttribute('aria-describedby')!;
    expect(get!.ownerDocument.getElementById(id)?.closest('[role="alert"]')).toBe(alert(el));
    expect(alert(el)?.textContent).toBe(FAILURE);
  });

  it.each<[string, unknown, string]>([
    ['a proxy or network failure', new TypeError('Failed to fetch'), WITHDRAW_FAILURE],
    [
      'the API refusing it',
      new ApiProblem({
        type: '/problems/not-found',
        title: 'Not found',
        status: 404,
        detail: 'No scan with that identifier.',
        instance: '/x',
      }),
      'No scan with that identifier.',
    ],
  ])('a failed withdraw beside Get (%s) is not wired to Get: it is about the link, not this button', async (_what, failure, sentence) => {
    withdrawShareLink.mockRejectedValue(failure);
    const el = mounted({ shareExpiresAt: LIVE });
    await confirmWithdraw(el);

    // Not vacuous: Get is on the page beside the withdraw failure, and the alert says it.
    expect(alert(el)?.textContent).toBe(sentence);
    const get = button(el, 'Get shareable link');
    expect(get).toBeDefined();
    expect(get!.hasAttribute('aria-describedby')).toBe(false);
  });

  it('a withdraw failure that follows a mint failure takes the description away with the sentence', async () => {
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ shareExpiresAt: LIVE });
    await failedMint(el);
    // Not vacuous: the mint's failure was on the bar, and Get was described by it.
    expect(description(button(el, 'Get shareable link'))).toBe(FAILURE);

    await confirmWithdraw(el);

    expect(alert(el)?.textContent).toBe(WITHDRAW_FAILURE);
    expect(button(el, 'Get shareable link')!.hasAttribute('aria-describedby')).toBe(false);
  });

  it('is gone when the next mint starts, and stays gone when it succeeds', async () => {
    const el = mounted();
    await failedMint(el);
    expect(description(button(el, 'Get shareable link'))).toBe(FAILURE);

    let finish!: (link: { url: string; expiresAt: string }) => void;
    shareLink.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    // The sentence it pointed at has left the alert, so Get does not point at it.
    expect(alert(el)?.textContent).toBe('');
    expect(button(el, 'Creating…')!.hasAttribute('aria-describedby')).toBe(false);

    await act(async () => finish({ url: URL_, expiresAt: MINTED }));
    await settle();
    // Made: Get leaves the page, and nothing on the bar points at a sentence that is not there.
    expect(button(el, 'Get shareable link')).toBeUndefined();
    expect(el.querySelector('[aria-describedby]')).toBeNull();
    expect(alert(el)?.textContent).toBe('');
  });

  it('a second failure describes Get again', async () => {
    const el = mounted();
    await failedMint(el);
    expect(description(button(el, 'Get shareable link'))).toBe(FAILURE);

    await failedMint(el);

    expect(description(button(el, 'Get shareable link'))).toBe(FAILURE);
    expect(alert(el)?.textContent).toBe(FAILURE);
  });
});

describe('a failed scan never says its link is ready', () => {
  it('a link made before the page learns the scan failed is neither said nor offered after', async () => {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ scanStatus: 'partial' });
    await press(el, 'Get shareable link');
    // Not vacuous: made, said, and offered.
    expect(status(el)?.textContent).toBe(READY);
    expect(field(el)?.value).toBe(URL_);

    // The report is read again and says the scan failed: the same bar gets the new status.
    act(() =>
      root!.render(<ShareLinkBar scanId="scan_01" scanStatus="failed" shareExpiresAt={undefined} />),
    );

    expect(status(el)?.textContent).toBe('');
    expect(el.textContent).not.toContain('works until');
    expect(el.querySelector('input')).toBeNull();
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('a failed scan has no Get button, so nothing is announced and nothing takes focus', () => {
    for (const shareExpiresAt of [LIVE, null]) {
      const el = mounted({ scanStatus: 'failed', shareExpiresAt });
      expect(button(el, 'Get shareable link')).toBeUndefined();
      expect(status(el)?.textContent).toBe('');
      expect(document.activeElement).toBe(document.body);
      act(() => root?.unmount());
      root = null;
      document.body.innerHTML = '';
    }
  });
});
