// @vitest-environment jsdom

/**
 * The share bar says what the link does, and lets the agency take it back.
 * The API half (`DELETE /scans/{id}/share`) is proved in test_share_link.py;
 * this is the part a person sees. Mounted in jsdom, API mocked at its edge.
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
const { RETENTION, counted } = await import('@/lib/legal/retention');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LINK_URL = 'https://app.example/share#SHARESENTINELtoken0123456789';
let root: Root | null = null;

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

function mounted(): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<ShareLinkBar scanId="scan_01" />));
  return el;
}

async function withLink(): Promise<HTMLDivElement> {
  shareLink.mockResolvedValue({ url: LINK_URL });
  const el = mounted();
  await act(async () => {
    button(el, 'Get shareable link')!.click();
  });
  await settle();
  return el;
}

/** Press Withdraw link, then confirm it. */
async function confirmWithdraw(el: HTMLElement) {
  act(() => button(el, 'Withdraw link')!.click());
  await act(async () => {
    button(el, 'Withdraw link')!.click();
  });
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

describe('what the lead sentence promises', () => {
  it('says the link expires after 30 days and who to share it with', () => {
    const el = mounted();
    expect(el.textContent).toContain(
      'Creates a link anyone can open without an account. Links expire after 30 days. Only share it with the client this report is about.',
    );
  });

  it('no longer says the link cannot be withdrawn', () => {
    const el = mounted();
    expect(el.textContent).not.toContain('cannot be withdrawn');
    expect(el.textContent).not.toContain('does not expire');
  });

  it('takes the number of days from RETENTION, which legalPages.test.tsx pins to the API', () => {
    // A number typed into this sentence is a second copy of `SHARE_TTL`, and the
    // privacy page's copy of it is already held to the API's by a test. So the
    // sentence reads the same constant: change it and the sentence follows.
    expect(RETENTION.shareLinkDays).toBe(30);
    const el = mounted();
    expect(el.textContent).toContain(`Links expire after ${counted(RETENTION.shareLinkDays, 'day')}.`);
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';

    const held = RETENTION.shareLinkDays;
    const mutable = RETENTION as { shareLinkDays: number };
    try {
      mutable.shareLinkDays = 45;
      expect(mounted().textContent).toContain('Links expire after 45 days.');
      act(() => root?.unmount());
      root = null;
      document.body.innerHTML = '';
      mutable.shareLinkDays = 1;
      expect(mounted().textContent).toContain('Links expire after 1 day.');
    } finally {
      mutable.shareLinkDays = held;
    }
  });
});

describe('withdrawing a link', () => {
  it('offers Withdraw link beside Copy once a link is shown, and not before', async () => {
    const el = mounted();
    expect(button(el, 'Withdraw link')).toBeUndefined();
    shareLink.mockResolvedValue({ url: LINK_URL });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('asks first, and nothing is sent until it is confirmed', async () => {
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain(
      'Withdraw this link? Anyone who opens it will no longer see the report.',
    );
    expect(button(el, 'Keep link')).toBeDefined();
    expect(withdrawShareLink).not.toHaveBeenCalled();
    act(() => button(el, 'Keep link')!.click());
    expect(el.textContent).not.toContain('Withdraw this link?');
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
  });

  it('on success drops the URL from the page and says so', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = await withLink();
    await confirmWithdraw(el);
    expect(withdrawShareLink).toHaveBeenCalledWith('scan_01');
    expect(el.innerHTML).not.toContain('SHARESENTINEL');
    expect(el.querySelector('input')).toBeNull();
    expect(el.querySelector('[role="status"]')?.textContent).toBe(
      'Link withdrawn. It no longer opens this report.',
    );
    expect(button(el, 'Get shareable link')).toBeDefined();
  });

  it('shows Withdrawing… and disables the button while it works', async () => {
    let finish!: () => void;
    withdrawShareLink.mockReturnValue(new Promise<void>((r) => (finish = r)));
    const el = await withLink();
    await confirmWithdraw(el);
    const working = button(el, 'Withdrawing…') as HTMLButtonElement;
    expect(working.disabled).toBe(true);
    await act(async () => finish());
    await settle();
  });

  it('on an API problem shows its detail and keeps the link', async () => {
    withdrawShareLink.mockRejectedValue(
      new ApiProblem({
        type: '/problems/not-found',
        title: 'Not found',
        status: 404,
        detail: 'No scan with that identifier.',
        instance: '/x',
      }),
    );
    const el = await withLink();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe('No scan with that identifier.');
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
    expect(button(el, 'Withdraw link')).toBeDefined();
  });

  it('on any other failure says the link could not be withdrawn, may still open, and keeps it', async () => {
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await withLink();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'The link could not be withdrawn and may still open. Try again.',
    );
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
  });
});

/**
 * COPY AND WITHDRAW SHARE ONE STATE — the security review of 2026-10-01.
 *
 * `copy()` awaits the clipboard, and the clipboard settles whenever the browser
 * gets round to it. By then a Withdraw can have finished, be under way, or the
 * bar can be showing a newer link, so what `copy` does with the answer must not
 * assume the state it started from. Before: it set a fresh `ready` state with
 * the URL it had captured, so pressing Copy during "Withdrawing…" could bring the
 * dead URL back onto the page after the 204, and a clipboard that settled first
 * reset `withdraw` to idle and re-opened the button for a second DELETE.
 */
describe('Copy and Withdraw do not trample each other', () => {
  const NEWER_URL = 'https://app.example/share#SHARESENTINELnewer9876543210';

  function deferred<T = void>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }

  const writeText = vi.fn();
  beforeEach(() => {
    writeText.mockReset();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: (text: string) => writeText(text) },
    });
  });
  afterEach(() => {
    delete (navigator as { clipboard?: unknown }).clipboard;
  });

  /** How the clipboard settles: it grants the copy, or it refuses. */
  const SETTLES = [
    ['grants the copy', 'resolve'],
    ['refuses it', 'reject'],
  ] as const;

  it('Copy puts the link on the clipboard and says Copied', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    await act(async () => {
      button(el, 'Copy')!.click();
    });
    await settle();
    expect(writeText).toHaveBeenCalledWith(LINK_URL);
    expect(button(el, 'Copied')).toBeDefined();
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
  });

  it('a clipboard that refuses leaves the link on screen and Copy as it was', async () => {
    writeText.mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    const el = await withLink();
    await act(async () => {
      button(el, 'Copy')!.click();
    });
    await settle();
    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe('');
  });

  it('Copy is disabled while the link is being withdrawn', async () => {
    const del = deferred();
    withdrawShareLink.mockReturnValue(del.promise);
    const el = await withLink();
    expect((button(el, 'Copy') as HTMLButtonElement).disabled).toBe(false);
    await confirmWithdraw(el);
    expect(button(el, 'Withdrawing…')).toBeDefined();
    expect((button(el, 'Copy') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => del.resolve());
    await settle();
  });

  it.each(SETTLES)(
    'a clipboard that %s AFTER the 204 leaves the link withdrawn, with no URL on the page',
    async (_how, settle_) => {
      const clip = deferred();
      writeText.mockReturnValue(clip.promise);
      withdrawShareLink.mockResolvedValue(undefined);
      const el = await withLink();
      act(() => button(el, 'Copy')!.click());
      await confirmWithdraw(el);
      expect(el.querySelector('[role="status"]')?.textContent).toBe(
        'Link withdrawn. It no longer opens this report.',
      );

      await act(async () => {
        if (settle_ === 'resolve') clip.resolve();
        else clip.reject(new DOMException('denied', 'NotAllowedError'));
      });
      await settle();

      expect(el.innerHTML).not.toContain('SHARESENTINEL');
      expect(el.querySelector('input')).toBeNull();
      expect(el.querySelector('[role="status"]')?.textContent).toBe(
        'Link withdrawn. It no longer opens this report.',
      );
      expect(button(el, 'Copy')).toBeUndefined();
      expect(button(el, 'Get shareable link')).toBeDefined();
    },
  );

  it.each(SETTLES)(
    'a clipboard that %s while Withdrawing… does not re-open the button for a second DELETE',
    async (_how, settle_) => {
      const clip = deferred();
      const del = deferred();
      writeText.mockReturnValue(clip.promise);
      withdrawShareLink.mockReturnValue(del.promise);
      const el = await withLink();
      act(() => button(el, 'Copy')!.click());
      await confirmWithdraw(el);
      expect((button(el, 'Withdrawing…') as HTMLButtonElement).disabled).toBe(true);

      await act(async () => {
        if (settle_ === 'resolve') clip.resolve();
        else clip.reject(new DOMException('denied', 'NotAllowedError'));
      });
      await settle();

      // Still working: one request out, nothing to press for a second.
      expect((button(el, 'Withdrawing…') as HTMLButtonElement).disabled).toBe(true);
      expect(button(el, 'Withdraw link')).toBeUndefined();
      expect(withdrawShareLink).toHaveBeenCalledTimes(1);

      await act(async () => del.resolve());
      await settle();
      expect(el.querySelector('[role="status"]')?.textContent).toBe(
        'Link withdrawn. It no longer opens this report.',
      );
      expect(el.innerHTML).not.toContain('SHARESENTINEL');
    },
  );

  it('pressing Copy while the Withdraw question is open leaves the question open', async () => {
    writeText.mockResolvedValue(undefined);
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    expect(el.textContent).toContain('Withdraw this link?');
    await act(async () => {
      button(el, 'Copy')!.click();
    });
    await settle();
    expect(button(el, 'Copied')).toBeDefined();
    expect(el.textContent).toContain('Withdraw this link?');
    expect(button(el, 'Keep link')).toBeDefined();
  });

  it('a clipboard answer for an older link is not said to a newer one', async () => {
    const clip = deferred();
    writeText.mockReturnValue(clip.promise);
    withdrawShareLink.mockResolvedValue(undefined);
    shareLink.mockResolvedValueOnce({ url: LINK_URL }).mockResolvedValueOnce({ url: NEWER_URL });
    const el = mounted();
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    act(() => button(el, 'Copy')!.click());
    await confirmWithdraw(el);
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(NEWER_URL);

    await act(async () => clip.resolve());
    await settle();

    // The newer link was never copied, so it still says Copy.
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(NEWER_URL);
    expect(button(el, 'Copy')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
  });
});

/**
 * THE UI REVIEW OF 2026-10-01.
 *
 * Reflow, focus and announcement through the withdraw steps, and what a failed
 * withdraw says when the server, not the request, is what failed.
 */
describe('the link field keeps its room — WCAG 1.4.10 reflow', () => {
  it('takes the whole row on a phone and shares it from `sm` up', async () => {
    const el = await withLink();
    const wrapper = el.querySelector('input[readonly]')!.closest('.min-w-0')!;
    const classes = wrapper.className.split(/\s+/);
    for (const cls of ['min-w-0', 'basis-full', 'sm:flex-1', 'sm:basis-0']) {
      expect(classes, cls).toContain(cls);
    }
    // A bare `flex-1` has a zero basis: with Withdraw in the row it squeezed the
    // field to 0px at 320px, and Copy and Withdraw took the width instead.
    expect(classes).not.toContain('flex-1');
  });
});

describe('focus goes where the person is, at every step of a withdraw', () => {
  const QUESTION = 'Withdraw this link? Anyone who opens it will no longer see the report.';

  it('opening the question puts focus on Keep link, the safe answer', async () => {
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    expect(document.activeElement).toBe(button(el, 'Keep link'));
  });

  it('both buttons are described by the question they answer, Keep link included', async () => {
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    // Only one button is called "Withdraw link" now: the one that confirms.
    const danger = button(el, 'Withdraw link')!;
    const keep = button(el, 'Keep link')!;
    const id = danger.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    expect(el.ownerDocument.getElementById(id!)?.textContent).toBe(QUESTION);
    // Focus lands on Keep link, so it is the one a screen reader reads first:
    // without the question it announces "Keep link, button" and nothing to keep it FROM.
    expect(keep.getAttribute('aria-describedby')).toBe(id);
  });

  it('Keep link gives focus back to the Withdraw link that opened the question', async () => {
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    expect(el.textContent).not.toContain('Withdraw this link?');
    const trigger = button(el, 'Withdraw link');
    expect(trigger).toBeDefined();
    expect(document.activeElement).toBe(trigger);
  });

  it('a link that has only just been made does not take focus', async () => {
    const el = await withLink();
    expect(el.contains(document.activeElement) && document.activeElement !== document.body).toBe(
      false,
    );
  });

  it.each([
    ['a 502 from a proxy', new ApiProblem({
      type: '/problems/unknown', title: 'Request failed', status: 502,
      detail: 'The server returned 502.', instance: '/x',
    })],
    ['a 404 refusal', new ApiProblem({
      type: '/problems/not-found', title: 'Not found', status: 404,
      detail: 'No scan with that identifier.', instance: '/x',
    })],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])('after a failed withdraw (%s) focus is on Withdraw link, where it can be tried again', async (_what, failure) => {
    // The button that was pressed disables while the request is out and the
    // question closes when it fails, so focus fell on <body> and the retry was
    // a Tab away from the top of the page.
    withdrawShareLink.mockRejectedValue(failure);
    const el = await withLink();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).not.toBe('');
    const trigger = button(el, 'Withdraw link');
    expect(trigger).toBeDefined();
    expect(document.activeElement).toBe(trigger);
    expect(el.textContent).not.toContain('Withdraw this link?');
  });

  it('and the retry works: the question opens on Keep link again, and confirming withdraws', async () => {
    withdrawShareLink.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    withdrawShareLink.mockResolvedValueOnce(undefined);
    const el = await withLink();
    await confirmWithdraw(el);
    act(() => button(el, 'Withdraw link')!.click());
    expect(document.activeElement).toBe(button(el, 'Keep link'));
    await act(async () => {
      button(el, 'Withdraw link')!.click();
    });
    await settle();
    expect(withdrawShareLink).toHaveBeenCalledTimes(2);
    expect(el.querySelector('[role="status"]')?.textContent).toBe(
      'Link withdrawn. It no longer opens this report.',
    );
  });

  it('a failed withdraw does not leave focus claimed for a trigger that has not come back', async () => {
    // The flag that carries focus is cleared when the question is asked again,
    // so a link made later does not take focus when it appears.
    withdrawShareLink.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    withdrawShareLink.mockResolvedValueOnce(undefined);
    shareLink.mockResolvedValueOnce({ url: LINK_URL }).mockResolvedValueOnce({ url: LINK_URL });
    const el = mounted();
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    await confirmWithdraw(el);
    await confirmWithdraw(el);
    expect(el.querySelector('[role="status"]')?.textContent).toBe(
      'Link withdrawn. It no longer opens this report.',
    );
    act(() => (document.activeElement as HTMLElement | null)?.blur());
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    // A link that has only just been made takes no focus.
    expect(document.activeElement).toBe(document.body);
  });

  it('asking again after Keep puts focus on Keep link again, not on the trigger', async () => {
    const el = await withLink();
    act(() => button(el, 'Withdraw link')!.click());
    act(() => button(el, 'Keep link')!.click());
    act(() => button(el, 'Withdraw link')!.click());
    expect(document.activeElement).toBe(button(el, 'Keep link'));
  });
});

describe('what happens is announced, because the regions were already there', () => {
  it('both are mounted, and empty, before anything is said in them', () => {
    const el = mounted();
    expect(el.querySelector('[role="status"]')).not.toBeNull();
    expect(el.querySelector('[role="alert"]')).not.toBeNull();
    expect(el.querySelector('[role="status"]')?.textContent).toBe('');
    expect(el.querySelector('[role="alert"]')?.textContent).toBe('');
  });

  it('a withdrawn link is said in the region that was there before it was', async () => {
    withdrawShareLink.mockResolvedValue(undefined);
    const el = await withLink();
    const region = el.querySelector('[role="status"]');
    expect(region?.textContent).toBe('');
    await confirmWithdraw(el);
    expect(el.querySelector('[role="status"]')).toBe(region);
    expect(region?.textContent).toBe('Link withdrawn. It no longer opens this report.');
  });

  it('a failed withdraw is said in the region that was there before it', async () => {
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await withLink();
    const region = el.querySelector('[role="alert"]');
    expect(region?.textContent).toBe('');
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')).toBe(region);
    expect(region?.textContent).toBe('The link could not be withdrawn and may still open. Try again.');
  });

  it('a link that could not be made is an alert too, in the same region', async () => {
    shareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted();
    const region = el.querySelector('[role="alert"]');
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')).toBe(region);
    expect(region?.textContent).toBe('The link could not be created. Try again.');
  });

  it('the alerts are danger-coloured, as every other alert in the product is', async () => {
    shareLink.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    withdrawShareLink.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted();
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    expect(el.querySelector('[role="alert"] p')?.className).toContain('text-danger');

    shareLink.mockResolvedValue({ url: LINK_URL });
    await act(async () => {
      button(el, 'Get shareable link')!.click();
    });
    await settle();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"] p')?.className).toContain('text-danger');
  });
});

describe('a failed withdraw says what is true when it is the server that failed', () => {
  const MAY_STILL_OPEN = 'The link could not be withdrawn and may still open. Try again.';
  const problem = (status: number) =>
    new ApiProblem({
      type: '/problems/unknown',
      title: 'Request failed',
      status,
      detail: `The server returned ${status}.`,
      instance: '/api/v1/scans/scan_01/share',
    });

  it.each([
    ['a 502 from a proxy', problem(502)],
    ['a 500', problem(500)],
    ['a 503', problem(503)],
    ['a problem with no status', problem(0)],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])('%s: the link may still open, and the API\'s own words are not shown', async (_what, failure) => {
    withdrawShareLink.mockRejectedValue(failure);
    const el = await withLink();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(MAY_STILL_OPEN);
    expect(el.textContent).not.toContain('The server returned');
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK_URL);
  });

  it.each([400, 403, 404, 409])('a %i is the API refusing, so its own detail is shown', async (status) => {
    withdrawShareLink.mockRejectedValue(problem(status));
    const el = await withLink();
    await confirmWithdraw(el);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(`The server returned ${status}.`);
  });
});
