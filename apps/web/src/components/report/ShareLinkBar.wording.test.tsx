// @vitest-environment jsdom

/**
 * The share bar's words for a link that is already live, and how Copy is announced — checklist 3.52.
 *
 * Since 3.28 a press of Get shareable link on a live link returns that link as it is, so for a link
 * live on load the lead ("Creates a link …") and the busy label ("Creating…") described something that
 * does not happen. The lead now says the report has a live link, and the busy label says the link is
 * being got; a press that fails with a link live says the link could not be got, not created. With no
 * link live all three are as they were. The lead is a polite live region: a refusal edits it in place
 * and is read out, and withdrawing a link that was live on load replaces its element, so the change
 * back to the no-link lead is not read out beside "Link withdrawn…".
 *
 * "Copied" renamed a button, which screen readers do not reliably announce, so a copy that worked also
 * says "Link copied." in the status region the bar already has, beside "Link ready. It works until …",
 * which it must leave as it was.
 *
 * jsdom has no stylesheet, so "visually hidden" is asserted as the design system's class, whose rule
 * ShareLinkBar.linkReady.test.tsx reads from its source.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const shareLink = vi.fn();
const withdrawShareLink = vi.fn();
const writeText = vi.fn();

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
const { RETENTION } = await import('@/lib/legal/retention');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NO_LINK_LEAD =
  'Creates a link anyone can open without an account. Links last 30 days from when they are created. Only share it with the client this report is about.';
const LIVE_LEAD =
  'This report has a live link. Anyone with the link can open the report without an account. Links last 30 days from when they are created. Only share it with the client this report is about.';
const FAILED_LEAD = 'This scan failed, so its report cannot be shared. Its existing link no longer opens the report.';
// What a press that fails says: with a link live nothing was being created, so it was being got.
const NOT_GOT = 'We could not get the link. Try again.';
const NOT_CREATED = 'The link could not be created. Try again.';

const LIVE = '2026-11-04T09:30:00Z';
const LIVE_STAMP = '04 Nov 2026, 09:30 UTC';
const MINTED = '2026-11-20T10:00:00Z';
const MINTED_STAMP = '20 Nov 2026, 10:00 UTC';
const NEWER = '2026-11-21T10:00:00Z';
const NEWER_STAMP = '21 Nov 2026, 10:00 UTC';
const READY = `Link ready. It works until ${MINTED_STAMP}.`;
const READY_LIVE = `Link ready. It works until ${LIVE_STAMP}.`;
const READY_NEWER = `Link ready. It works until ${NEWER_STAMP}.`;
const COPIED = 'Link copied.';
const WITHDRAWN = 'Link withdrawn. It no longer opens this report.';
const URL_ = 'https://app.example/share#TOKENSENTINEL0123456789';
const NEWER_URL = 'https://app.example/share#TOKENSENTINELnewer9876543210';

let root: Root | null = null;
const observers: MutationObserver[] = [];

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);
const field = (el: HTMLElement) => el.querySelector<HTMLInputElement>('input[readonly]');
const status = (el: HTMLElement) => el.querySelector('[role="status"]') as HTMLElement;
const alert = (el: HTMLElement) => el.querySelector('[role="alert"]');
const leadElement = (el: HTMLElement) => el.querySelector('[data-testid="share-lead"]');
const lead = (el: HTMLElement) => leadElement(el)?.textContent;
/** The sentences a status region holds, one per paragraph. */
const sentences = (el: HTMLElement) => [...status(el).querySelectorAll('p')].map((p) => p.textContent);
const sentence = (el: HTMLElement, text: string) =>
  [...status(el).querySelectorAll('p')].find((p) => p.textContent === text);

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

function unmount() {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
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

/** Press Copy again: the button is called "Copied" once a copy has worked, and "Copy" until then. */
const pressCopyAgain = (el: HTMLElement) => press(el, button(el, 'Copy') ? 'Copy' : 'Copied');

async function confirmWithdraw(el: HTMLElement) {
  act(() => button(el, 'Withdraw link')!.click());
  await act(async () => {
    button(el, 'Withdraw link')!.click();
  });
  await settle();
}

/** A request that stays out until the test answers it. */
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Press Get with its request held open; the answer is given by the returned functions. */
async function pressHeld(el: HTMLElement) {
  const request = deferred<{ url: string; expiresAt: string }>();
  shareLink.mockReturnValueOnce(request.promise);
  await act(async () => {
    button(el, 'Get shareable link')!.click();
  });
  return {
    async resolve(link: { url: string; expiresAt: string }) {
      await act(async () => request.resolve(link));
      await settle();
    },
    async reject(failure: unknown) {
      await act(async () => request.reject(failure));
      await settle();
    },
  };
}

/** What a sighted reader is given: the bar's text with every visually hidden element taken out. */
const HIDDEN = '.avp-visually-hidden';
function sightedText(el: HTMLElement): string {
  const clone = el.cloneNode(true) as HTMLElement;
  for (const hidden of [...clone.querySelectorAll(HIDDEN)]) hidden.remove();
  return clone.textContent ?? '';
}
const times = (text: string, part: string) => text.split(part).length - 1;

/** The API refusing a press for a failed scan (409), as it does when the page has not yet learned it failed. */
const REFUSAL = 'This scan failed, so its report cannot be shared.';
const refusal = () =>
  new ApiProblem({
    type: '/problems/failed-scan-not-shareable',
    title: 'Failed scan cannot be shared',
    status: 409,
    detail: REFUSAL,
    instance: '/api/v1/scans/scan_01/share',
  });

/**
 * What a screen reader hears from a live region is what the region GAINS. This watches one, and
 * each call to the function it returns gives what changed since the last: the nodes added, the
 * nodes removed, and whether any text was edited in place.
 */
function watch(region: Element) {
  const records: MutationRecord[] = [];
  const observer = new MutationObserver((batch) => records.push(...batch));
  observer.observe(region, { childList: true, subtree: true, characterData: true });
  observers.push(observer);
  return () => {
    records.push(...observer.takeRecords());
    const taken = records.splice(0, records.length);
    return {
      added: taken.flatMap((r) => [...r.addedNodes]),
      removed: taken.flatMap((r) => [...r.removedNodes]),
      edited: taken.some((r) => r.type === 'characterData'),
    };
  };
}

beforeEach(() => {
  shareLink.mockReset();
  withdrawShareLink.mockReset();
  writeText.mockReset();
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: (text: string) => writeText(text) },
  });
});
afterEach(() => {
  for (const observer of observers.splice(0)) observer.disconnect();
  unmount();
  delete (navigator as { clipboard?: unknown }).clipboard;
});

describe('the lead says what is true of the link the bar has (3.52)', () => {
  it('with no link live it still says the bar creates one: the sentence it has always had', () => {
    for (const shareExpiresAt of [null, undefined]) {
      const el = mounted({ shareExpiresAt });
      expect(lead(el)).toBe(NO_LINK_LEAD);
      unmount();
    }
  });

  it('with a link live on load it says the report has a live link, and does not say the bar creates one', () => {
    const el = mounted({ shareExpiresAt: LIVE });
    expect(lead(el)).toBe(LIVE_LEAD);
    expect(el.textContent).not.toContain('Creates a link');
    // Still the three things a person sending the report is told: who can open it, how long, and to whom.
    expect(lead(el)).toContain('Anyone with the link can open the report without an account.');
    expect(lead(el)).toContain('Links last 30 days from when they are created.');
    expect(lead(el)).toContain('Only share it with the client this report is about.');
  });

  it('takes its days from RETENTION, as the no-link lead does', () => {
    // A number typed into the sentence would be a second copy of the API's `SHARE_TTL`.
    const mutable = RETENTION as { shareLinkDays: number };
    const held = mutable.shareLinkDays;
    try {
      mutable.shareLinkDays = 45;
      expect(lead(mounted({ shareExpiresAt: LIVE }))).toBe(LIVE_LEAD.replace('30 days', '45 days'));
      unmount();
      mutable.shareLinkDays = 1;
      expect(lead(mounted({ shareExpiresAt: LIVE }))).toBe(LIVE_LEAD.replace('30 days', '1 day'));
    } finally {
      mutable.shareLinkDays = held;
    }
  });

  it('is the same, word for word, while a press for the link is out and after the bar has it', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    const region = leadElement(el);
    const seen = [lead(el)];

    const press_ = await pressHeld(el);
    seen.push(lead(el));
    await press_.resolve({ url: URL_, expiresAt: LIVE });
    seen.push(lead(el));

    // Not vacuous: the press went out and came back with the link, and Get has left for its field.
    expect(shareLink).toHaveBeenCalledTimes(1);
    expect(field(el)?.value).toBe(URL_);
    expect(seen).toEqual([LIVE_LEAD, LIVE_LEAD, LIVE_LEAD]);
    // The lead is a polite live region, so a change in its words is read out. The same element with
    // the same words says nothing new on a press that only returns the link.
    expect(leadElement(el)).toBe(region);
    expect(region?.getAttribute('aria-live')).toBe('polite');
  });

  it('is the no-link lead throughout for a link the bar makes from nothing', async () => {
    const el = mounted({});
    const seen = [lead(el)];

    const press_ = await pressHeld(el);
    seen.push(lead(el));
    await press_.resolve({ url: URL_, expiresAt: MINTED });
    seen.push(lead(el));

    expect(field(el)?.value).toBe(URL_);
    expect(seen).toEqual([NO_LINK_LEAD, NO_LINK_LEAD, NO_LINK_LEAD]);
  });

  it('keeps the live lead when the press fails: the link is still live', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    const press_ = await pressHeld(el);
    await press_.reject(new TypeError('Failed to fetch'));

    expect(alert(el)?.textContent).toBe(NOT_GOT);
    expect(lead(el)).toBe(LIVE_LEAD);
  });

  it('goes back to the no-link lead once the live link is withdrawn, and not before', async () => {
    const withdrawal = deferred();
    withdrawShareLink.mockReturnValue(withdrawal.promise);
    const el = mounted({ shareExpiresAt: LIVE });

    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');
    expect(lead(el)).toBe(LIVE_LEAD);

    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    expect(button(el, 'Withdrawing…')).toBeDefined();
    // The link still opens until the request is answered.
    expect(lead(el)).toBe(LIVE_LEAD);

    await act(async () => withdrawal.resolve());
    await settle();
    // Not vacuous: the withdrawal went through.
    expect(status(el).textContent).toBe(WITHDRAWN);
    expect(lead(el)).toBe(NO_LINK_LEAD);
    expect(button(el, 'Get shareable link')).toBeDefined();
  });

  it('keeps the live lead when the link is kept, and when its withdrawal fails: it may still open', async () => {
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted({ shareExpiresAt: LIVE });

    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    expect(lead(el)).toBe(LIVE_LEAD);

    await confirmWithdraw(el);
    expect(alert(el)?.textContent).toBe('The link could not be withdrawn and may still open. Try again.');
    expect(lead(el)).toBe(LIVE_LEAD);
  });

  it('is the no-link lead for a link made after a withdrawal: the bar made it', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({ shareExpiresAt: LIVE });
    await confirmWithdraw(el);
    expect(lead(el)).toBe(NO_LINK_LEAD);

    await press(el, 'Get shareable link');

    expect(field(el)?.value).toBe(URL_);
    expect(lead(el)).toBe(NO_LINK_LEAD);
  });

  it('on a failed scan is the failed-scan sentence, never the live lead, whatever link it has', () => {
    const el = mounted({ scanStatus: 'failed', shareExpiresAt: LIVE });
    expect(lead(el)).toBe(FAILED_LEAD);
    expect(el.textContent).not.toContain('live link');
  });
});

describe('the lead is one live region through a press, a mint and Copy, and a new one after a withdraw (3.52)', () => {
  // The lead is `aria-live="polite"`, so a refusal that edits it in place is read out. Withdrawing a
  // link that was live on load changes its words too, in the same moment as "Link withdrawn…" says
  // what happened, and reading both is noise or a dropped confirmation. So that change replaces
  // the element: a region that arrives with its words already in it is not announced.

  it('for a link live on load it is the same element, never written into, through the press that returns the link, Copy and the Withdraw question', async () => {
    writeText.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    const region = leadElement(el)!;
    const changes = watch(region);

    const press_ = await pressHeld(el);
    expect(leadElement(el)).toBe(region);
    await press_.resolve({ url: URL_, expiresAt: LIVE });
    // Not vacuous: the press came back with the link, and Get has left for its field.
    expect(field(el)?.value).toBe(URL_);
    expect(leadElement(el)).toBe(region);

    await press(el, 'Copy');
    expect(button(el, 'Copied')).toBeDefined();
    expect(leadElement(el)).toBe(region);
    await pressCopyAgain(el);
    expect(leadElement(el)).toBe(region);

    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');
    expect(leadElement(el)).toBe(region);
    act(() => button(el, 'Keep link')!.click());
    expect(leadElement(el)).toBe(region);

    const { added, removed, edited } = changes();
    expect([added.length, removed.length, edited]).toEqual([0, 0, false]);
    expect(region.textContent).toBe(LIVE_LEAD);
  });

  it('for a link the bar makes from nothing it is the same element through the mint and Copy, and through withdrawing that link: its words never change', async () => {
    writeText.mockResolvedValue(undefined);
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({});
    const region = leadElement(el)!;
    const changes = watch(region);

    await press(el, 'Get shareable link');
    expect(field(el)?.value).toBe(URL_);
    expect(leadElement(el)).toBe(region);
    await press(el, 'Copy');
    expect(button(el, 'Copied')).toBeDefined();
    expect(leadElement(el)).toBe(region);
    await confirmWithdraw(el);
    expect(status(el).textContent).toBe(WITHDRAWN);
    expect(leadElement(el)).toBe(region);

    const { added, removed, edited } = changes();
    expect([added.length, removed.length, edited]).toEqual([0, 0, false]);
    expect(region.textContent).toBe(NO_LINK_LEAD);
  });

  it('withdrawing a link live on load replaces the lead with a new element, and the old one is not edited', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    const before = leadElement(el)!;
    const changes = watch(before);

    await confirmWithdraw(el);

    const after = leadElement(el)!;
    // Not vacuous: the withdrawal went through, and the lead says what it says with no link.
    expect(status(el).textContent).toBe(WITHDRAWN);
    expect(after.textContent).toBe(NO_LINK_LEAD);
    expect(after).not.toBe(before);
    expect(before.isConnected).toBe(false);
    // The region a reader had was taken out whole, with its words as they were; nothing was written
    // into it, so there is no change in a live region to read out beside "Link withdrawn…".
    expect(before.textContent).toBe(LIVE_LEAD);
    expect(changes().edited).toBe(false);
    // The new one is the lead as it always is: the test id, and polite, so a refusal is still read out.
    expect(after.getAttribute('data-testid')).toBe('share-lead');
    expect(after.getAttribute('aria-live')).toBe('polite');
  });

  it('a refusal on a link live on load is written into the same element, so it is read out', async () => {
    shareLink.mockRejectedValue(refusal());
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: LIVE });
    const region = leadElement(el)!;
    expect(region.textContent).toBe(LIVE_LEAD);
    const changes = watch(region);

    await press(el, 'Get shareable link');

    expect(leadElement(el)).toBe(region);
    expect(region.textContent).toBe(`${REFUSAL} Its existing link no longer opens the report.`);
    expect(region.getAttribute('aria-live')).toBe('polite');
    // Not vacuous: its words were changed in place.
    const { added, removed, edited } = changes();
    expect(added.length + removed.length + (edited ? 1 : 0)).toBeGreaterThan(0);
  });

  it('a refusal with no link live is written into the same element too', async () => {
    shareLink.mockRejectedValue(refusal());
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: null });
    const region = leadElement(el)!;

    await press(el, 'Get shareable link');

    expect(leadElement(el)).toBe(region);
    expect(region.textContent).toBe(REFUSAL);
  });

  it('a refusal after a withdraw is still read out: the new lead keeps its element through it', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink.mockRejectedValue(refusal());
    const el = mounted({ scanStatus: 'partial', shareExpiresAt: LIVE });
    await confirmWithdraw(el);
    const region = leadElement(el)!;
    expect(region.textContent).toBe(NO_LINK_LEAD);

    await press(el, 'Get shareable link');

    expect(leadElement(el)).toBe(region);
    expect(region.textContent).toBe(REFUSAL);
  });
});

describe('what a press that fails says (3.52)', () => {
  /** Get is described by the sentence, which is the alert's own (3.33), and the failure leaves the link where it was. */
  const describedBy = (el: HTMLElement) => {
    const get = button(el, 'Get shareable link')!;
    const id = get.getAttribute('aria-describedby')!;
    return get.ownerDocument.getElementById(id);
  };

  it('with a link live it says the link could not be got: nothing was being created', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    const press_ = await pressHeld(el);
    await press_.reject(new TypeError('Failed to fetch'));

    expect(alert(el)?.textContent).toBe(NOT_GOT);
    expect(el.textContent).not.toContain('could not be created');
    // 3.33 kept: Get is described by it, and the link and its Withdraw are still there.
    expect(describedBy(el)?.textContent).toBe(NOT_GOT);
    expect(describedBy(el)?.closest('[role="alert"]')).toBe(alert(el));
    expect(el.textContent).toContain(`The link works until ${LIVE_STAMP}.`);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('with no link live it still says the link could not be created', async () => {
    const el = mounted({});
    const press_ = await pressHeld(el);
    await press_.reject(new TypeError('Failed to fetch'));

    expect(alert(el)?.textContent).toBe(NOT_CREATED);
    expect(el.textContent).not.toContain('We could not get the link');
    expect(describedBy(el)?.textContent).toBe(NOT_CREATED);
    expect(describedBy(el)?.closest('[role="alert"]')).toBe(alert(el));
  });

  it('after the live link is withdrawn it says the link could not be created again: there is no link to get', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    await confirmWithdraw(el);
    const press_ = await pressHeld(el);
    await press_.reject(new TypeError('Failed to fetch'));

    expect(alert(el)?.textContent).toBe(NOT_CREATED);
  });

  it('a second failure with a link live says it again, and a retry that works takes it away', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    const first = await pressHeld(el);
    await first.reject(new TypeError('Failed to fetch'));
    expect(alert(el)?.textContent).toBe(NOT_GOT);

    const second = await pressHeld(el);
    await second.reject(new TypeError('Failed to fetch'));
    expect(alert(el)?.textContent).toBe(NOT_GOT);

    const third = await pressHeld(el);
    await third.resolve({ url: URL_, expiresAt: LIVE });
    expect(alert(el)?.textContent).toBe('');
    expect(field(el)?.value).toBe(URL_);
  });
});

describe('the busy label on Get shareable link (3.52)', () => {
  it('with no link live a press says Creating…, which is what it does', async () => {
    const el = mounted({});
    expect(button(el, 'Get shareable link')).toBeDefined();

    const press_ = await pressHeld(el);

    expect(button(el, 'Creating…')?.disabled).toBe(true);
    expect(button(el, 'Getting the link…')).toBeUndefined();
    expect(button(el, 'Get shareable link')).toBeUndefined();
    await press_.resolve({ url: URL_, expiresAt: MINTED });
  });

  it('with a link live on load a press says Getting the link…, and nothing on the bar says it is creating one', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    expect(button(el, 'Get shareable link')).toBeDefined();

    const press_ = await pressHeld(el);

    expect(button(el, 'Getting the link…')?.disabled).toBe(true);
    expect(button(el, 'Creating…')).toBeUndefined();
    expect(el.textContent).not.toContain('Creating');
    expect(button(el, 'Get shareable link')).toBeUndefined();
    await press_.resolve({ url: URL_, expiresAt: LIVE });
    // The press is answered: no busy button is left, and the link is on the bar.
    expect(button(el, 'Getting the link…')).toBeUndefined();
    expect(field(el)?.value).toBe(URL_);
  });

  it('with a link live on load a press that fails gives the button its own name back', async () => {
    const el = mounted({ shareExpiresAt: LIVE });
    const press_ = await pressHeld(el);
    expect(button(el, 'Getting the link…')).toBeDefined();

    await press_.reject(new TypeError('Failed to fetch'));

    expect(button(el, 'Getting the link…')).toBeUndefined();
    expect(button(el, 'Get shareable link')?.disabled).toBe(false);
  });

  it('after the live link is withdrawn a press says Creating… again: there is no link to get', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = mounted({ shareExpiresAt: LIVE });
    await confirmWithdraw(el);
    expect(button(el, 'Get shareable link')).toBeDefined();

    const press_ = await pressHeld(el);

    expect(button(el, 'Creating…')?.disabled).toBe(true);
    expect(button(el, 'Getting the link…')).toBeUndefined();
    await press_.resolve({ url: URL_, expiresAt: MINTED });
  });
});

describe('Copy says so in the status region, not only by renaming the button (3.52)', () => {
  /** The bar with a link made from nothing, ready to copy. */
  async function withLink(): Promise<HTMLDivElement> {
    shareLink.mockResolvedValue({ url: URL_, expiresAt: MINTED });
    const el = mounted({});
    await press(el, 'Get shareable link');
    return el;
  }

  it('a copy that worked adds "Link copied." beside the ready sentence, and the button says Copied', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    expect(sentences(el)).toEqual([READY]);

    await press(el, 'Copy');

    // Not vacuous: the clipboard was written, with the link.
    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(button(el, 'Copied')).toBeDefined();
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('says it in the status region the bar already had, which reads what is added and not the whole region', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    const region = status(el);

    await press(el, 'Copy');

    expect(status(el)).toBe(region);
    expect(region.getAttribute('role')).toBe('status');
    expect(region.getAttribute('aria-live')).not.toBe('assertive');
    // `status` is atomic unless told otherwise: a screen reader would read the whole region again,
    // "Link ready. It works until …" with it, on every copy.
    expect(region.getAttribute('aria-atomic')).toBe('false');
  });

  it('is for screen readers: visually hidden, so a sighted reader sees nothing new on the bar', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    const before = sightedText(el);

    await press(el, 'Copy');

    const copied = sentence(el, COPIED);
    expect(copied, 'the sentence is on the bar').toBeDefined();
    expect(copied!.matches(HIDDEN)).toBe(true);
    // Clipped, not taken out of what a screen reader is given.
    expect(copied!.closest('[aria-hidden="true"], [hidden]')).toBeNull();
    expect(status(el).closest(HIDDEN)).toBeNull();
    // The one change on screen is the button's own name, as before 3.52.
    expect(sightedText(el)).toBe(before.replace('Copy', 'Copied'));
    expect(sightedText(el)).not.toContain('Link copied');
  });

  it('leaves the ready sentence as it was: the same element, with no change to it', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    const ready = sentence(el, READY)!;
    const changes = watch(status(el));

    await press(el, 'Copy');

    const { added, removed, edited } = changes();
    // What the region gained is the one new sentence, and nothing it already held was touched.
    expect(added.map((node) => node.textContent)).toEqual([COPIED]);
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
    expect(ready.isConnected).toBe(true);
    expect(ready.textContent).toBe(READY);
    expect(sentence(el, READY)).toBe(ready);
  });

  it('a second copy is announced again: the region gains a new element with the same words', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    await press(el, 'Copy');
    const first = sentence(el, COPIED)!;
    const changes = watch(status(el));

    await pressCopyAgain(el);

    expect(writeText).toHaveBeenCalledTimes(2);
    const second = sentence(el, COPIED)!;
    const { added, removed } = changes();
    // The same words in the same element are no change to a live region, so nothing would be read.
    // Each copy puts a NEW element in it, and takes the last one out.
    expect(second).not.toBe(first);
    expect(first.isConnected).toBe(false);
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(second);
    expect(removed).toHaveLength(1);
    expect(removed[0]).toBe(first);
    // One sentence, not two: they do not pile up, and the ready sentence is still there.
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('a third copy is announced again too', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    await press(el, 'Copy');
    await pressCopyAgain(el);
    const second = sentence(el, COPIED)!;
    const changes = watch(status(el));

    await pressCopyAgain(el);

    const third = sentence(el, COPIED)!;
    expect(third).not.toBe(second);
    expect(changes().added).toHaveLength(1);
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it.each<[string, string | undefined, string, string]>([
    ['from no link', undefined, MINTED, READY],
    ['from a link live on load', LIVE, LIVE, READY_LIVE],
  ])('%s: the link the press gives is announced as copied beside its own ready sentence', async (_from, shareExpiresAt, expiresAt, ready) => {
    writeText.mockResolvedValue(undefined);
    shareLink.mockResolvedValue({ url: URL_, expiresAt });
    const el = mounted({ shareExpiresAt });
    await press(el, 'Get shareable link');
    expect(sentences(el)).toEqual([ready]);

    await press(el, 'Copy');

    expect(sentences(el)).toEqual([ready, COPIED]);
  });

  it('a copy that is refused says nothing: Copy stays Copy, and no alert is raised', async () => {
    writeText.mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    const el = await withLink();
    const changes = watch(status(el));

    await press(el, 'Copy');

    // Not vacuous: the clipboard was asked.
    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY]);
    expect(changes().added).toEqual([]);
    expect(alert(el)?.textContent).toBe('');
    // The link is on screen and selectable, which is what the person does instead.
    expect(field(el)?.value).toBe(URL_);
  });

  it('with no clipboard to write to (a page over plain HTTP) it says nothing either', async () => {
    delete (navigator as { clipboard?: unknown }).clipboard;
    const el = await withLink();

    await press(el, 'Copy');

    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY]);
    expect(alert(el)?.textContent).toBe('');
  });

  it('a refusal after a copy that worked takes "Link copied." away with "Copied": it does not claim a copy that did not happen', async () => {
    writeText.mockResolvedValueOnce(undefined);
    writeText.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'));
    const el = await withLink();
    await press(el, 'Copy');
    expect(sentences(el)).toEqual([READY, COPIED]);
    expect(button(el, 'Copied')).toBeDefined();

    await press(el, 'Copied');

    expect(writeText).toHaveBeenCalledTimes(2);
    expect(button(el, 'Copy')).toBeDefined();
    expect(sentences(el)).toEqual([READY]);
  });

  it('a copy that worked is still said when the Withdraw question is open, and both sentences stay through it', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');

    await press(el, 'Copy');
    expect(sentences(el)).toEqual([READY, COPIED]);

    act(() => button(el, 'Keep link')!.click());
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('once the link is withdrawn the region says only that, and nothing of a copy', async () => {
    writeText.mockResolvedValue(undefined);
    withdrawShareLink.mockResolvedValue(undefined);
    const el = await withLink();
    await press(el, 'Copy');
    expect(sentences(el)).toEqual([READY, COPIED]);

    await confirmWithdraw(el);

    expect(status(el).textContent).toBe(WITHDRAWN);
    expect(sentences(el)).toEqual([WITHDRAWN]);
    expect(el.textContent).not.toContain('Link copied');
  });

  it('a clipboard that answers after the link was withdrawn and another made does not announce a copy of the new one', async () => {
    const clip = deferred();
    writeText.mockReturnValue(clip.promise);
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink
      .mockResolvedValueOnce({ url: URL_, expiresAt: MINTED })
      .mockResolvedValueOnce({ url: NEWER_URL, expiresAt: NEWER });
    const el = mounted({});
    await press(el, 'Get shareable link');
    act(() => button(el, 'Copy')!.click());
    await confirmWithdraw(el);
    await press(el, 'Get shareable link');
    expect(field(el)?.value).toBe(NEWER_URL);

    await act(async () => clip.resolve());
    await settle();

    // The newer link was never copied: it says Copy, and the region says only that it is ready.
    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY_NEWER]);
  });

  it('the failed-scan state announces nothing of a copy: it has no link to copy', () => {
    for (const shareExpiresAt of [LIVE, null]) {
      const el = mounted({ scanStatus: 'failed', shareExpiresAt });
      expect(button(el, 'Copy')).toBeUndefined();
      expect(sentences(el)).toEqual([]);
      unmount();
    }
  });

  it('a link copied before the page learned the scan failed is no longer said to be copied: its link opens nothing', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    await press(el, 'Copy');
    // Not vacuous: copied, and said.
    expect(sentences(el)).toEqual([READY, COPIED]);

    // The report is read again and says the scan failed: the same bar gets the new status.
    act(() =>
      root!.render(<ShareLinkBar scanId="scan_01" scanStatus="failed" shareExpiresAt={undefined} />),
    );

    expect(button(el, 'Copy')).toBeUndefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain('Link copied');
  });
});

describe('a sighted reader still sees the link\'s end date exactly once (3.38, kept)', () => {
  it('on a link live on load: once, in the standing line, with nothing announced and no date in the lead', () => {
    const el = mounted({ shareExpiresAt: LIVE });

    expect(times(sightedText(el), LIVE_STAMP)).toBe(1);
    expect(sightedText(el)).toContain(`The link works until ${LIVE_STAMP}.`);
    // Nothing hidden carries it either, and the status region is there and empty.
    expect(times(el.textContent ?? '', LIVE_STAMP)).toBe(1);
    expect(status(el).textContent).toBe('');
    expect(lead(el)).not.toContain(LIVE_STAMP);
  });

  it.each<[string, string | undefined, string, string]>([
    ['from no link', undefined, MINTED, MINTED_STAMP],
    ['from a link live on load', LIVE, LIVE, LIVE_STAMP],
  ])('%s: once after the link is got, and still once after Copy, twice', async (_from, shareExpiresAt, expiresAt, stamp) => {
    writeText.mockResolvedValue(undefined);
    shareLink.mockResolvedValue({ url: URL_, expiresAt });
    const el = mounted({ shareExpiresAt });
    const onScreen = () => times(sightedText(el), stamp);

    await press(el, 'Get shareable link');
    expect(onScreen()).toBe(1);
    expect(sightedText(el)).toContain(`The link works until ${stamp}.`);

    await press(el, 'Copy');
    // Not vacuous: the copy worked, so the bar has said so.
    expect(button(el, 'Copied')).toBeDefined();
    expect(onScreen()).toBe(1);

    await pressCopyAgain(el);
    expect(onScreen()).toBe(1);

    // The announcement still carries the date, as it did: in the text twice and on the screen once.
    expect(times(el.textContent ?? '', stamp)).toBe(2);
    expect(lead(el)).not.toContain(stamp);
  });
});
