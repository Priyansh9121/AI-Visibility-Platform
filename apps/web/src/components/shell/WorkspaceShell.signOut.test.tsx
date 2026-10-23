// @vitest-environment jsdom

/**
 * Sign out, driven — copy fixes, 1e (the founder, 2026-10-22).
 *
 * "A 401 from the sign-out counts as signed out: do what a successful sign-out does today. Any
 * other failure shows 'Sign-out did not complete. Try again.' where the person can see it, a
 * screen reader announces it, and the person stays signed in."
 *
 * Until then the handler had no failure path at all: the rejection went unhandled and the person
 * was left on the page with nothing said. Nothing clicked the button in a test either, so this
 * file mounts the real shell in jsdom, the API module mocked at its edge (every export real but
 * `api.logOut`, because the shell tells a 401 by `ApiProblem`), and presses the real button.
 * jsdom does not navigate, and its `location` cannot be spied on in place, so `location.assign`
 * is stubbed as `BillingPanel.test.tsx` does.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ApiProblem } from '@/lib/api';

const logOut = vi.fn<() => Promise<void>>();
const assign = vi.fn<(url: string) => void>();
const scrollIntoView = vi.fn<(options?: ScrollIntoViewOptions) => void>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      logOut: () => logOut(),
      // Never reached: the Clients disclosure stays closed, so the list is not fetched.
      clients: async () => ({ data: [], nextCursor: null }),
      dashboard: async () => ({ data: [] }),
    },
  };
});

// Imported after the mock so the shell sees the mocked module.
const { WorkspaceShell } = await import('./WorkspaceShell');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The founder's sentence, word for word. */
const SENTENCE = 'Sign-out did not complete. Try again.';

let root: Root | null = null;
const observers: MutationObserver[] = [];

beforeEach(() => {
  logOut.mockReset();
  assign.mockReset();
  scrollIntoView.mockReset();
  window.sessionStorage.clear();
  vi.stubGlobal('location', { assign });
  // jsdom lays nothing out and scrolls nothing, and has no `scrollIntoView`.
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    value: scrollIntoView,
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  for (const observer of observers.splice(0)) observer.disconnect();
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

/** Let the pressed handler finish: the mocked call settles, then React renders what it set. */
const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

function mounted(): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => {
    root!.render(
      <WorkspaceShell current="dashboard" agencyName="Local Dev Agency 2" seats={{ used: 1, limit: 3 }}>
        <p>the page</p>
      </WorkspaceShell>,
    );
  });
  return el;
}

const footer = (el: HTMLElement) => el.querySelector<HTMLElement>('.avp-shell__footer')!;
const region = (el: HTMLElement) => footer(el).querySelector<HTMLElement>('[role="alert"]');
const button = (el: HTMLElement) =>
  [...footer(el).querySelectorAll<HTMLButtonElement>('button')].find(
    (b) => b.textContent === 'Sign out',
  )!;
const sentences = (el: HTMLElement) =>
  [...(region(el)?.querySelectorAll('p') ?? [])].map((p) => p.textContent);
/** The wrapper around the alert region and the button: what is scrolled into view. */
const group = (el: HTMLElement) => region(el)!.parentElement!;

const press = (el: HTMLElement) =>
  act(() => {
    button(el).click();
  });

const problem = (status: number, type: string) =>
  new ApiProblem({ type, title: 'Problem', status, detail: 'detail', instance: '/auth/logout' });

/** What a screen reader hears from a live region is what the region GAINS. */
function watch(target: Element) {
  const records: MutationRecord[] = [];
  const observer = new MutationObserver((batch) => records.push(...batch));
  observer.observe(target, { childList: true, subtree: true, characterData: true });
  observers.push(observer);
  return () => {
    records.push(...observer.takeRecords());
    const taken = records.splice(0, records.length);
    return {
      added: taken.flatMap((r) => [...r.addedNodes]),
      removed: taken.flatMap((r) => [...r.removedNodes]),
    };
  };
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('a sign-out that worked, or that finds nobody signed in, goes home', () => {
  it('a 204 goes to the home page and says nothing', async () => {
    logOut.mockResolvedValue(undefined);
    const el = mounted();

    press(el);
    await flush();

    expect(logOut).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain(SENTENCE);
  });

  it('a 401 counts as signed out: it does what a 204 does', async () => {
    logOut.mockRejectedValue(problem(401, '/problems/authentication-required'));
    const el = mounted();

    press(el);
    await flush();

    expect(logOut).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain(SENTENCE);
  });

  it('a 401 with no problem document in it (a proxy’s, say) counts too', async () => {
    // `request()` turns an answer it cannot read into a problem of the type `/problems/unknown`
    // that carries the real status.
    logOut.mockRejectedValue(problem(401, '/problems/unknown'));
    const el = mounted();

    press(el);
    await flush();

    expect(assign).toHaveBeenCalledWith('/');
    expect(sentences(el)).toEqual([]);
  });
});

describe('any other failure says so, announces it, and leaves the person signed in', () => {
  const FAILURES: Array<[string, () => unknown]> = [
    ['a 503 (Redis is down)', () => problem(503, '/problems/service-unavailable')],
    ['a 500 with no problem document', () => problem(500, '/problems/unknown')],
    ['a 403 (the origin was refused)', () => problem(403, '/problems/origin-not-allowed')],
    ['a 429', () => problem(429, '/problems/rate-limited')],
    ['a 404', () => problem(404, '/problems/not-found')],
    ['no answer at all (the network failed)', () => new TypeError('Failed to fetch')],
    ['a rejection that is not an error', () => 'down'],
  ];

  it.each(FAILURES)('%s', async (_what, failure) => {
    logOut.mockRejectedValue(failure());
    const el = mounted();

    press(el);
    await flush();

    expect(logOut).toHaveBeenCalledTimes(1);
    expect(sentences(el)).toEqual([SENTENCE]);
    // Signed in still: no navigation, and the button to try again is there and works.
    expect(assign).not.toHaveBeenCalled();
    expect(button(el)).toBeDefined();
    expect(button(el).disabled).toBe(false);
    expect(el.textContent).toContain('the page');
  });

  it('the sentence is exactly the founder’s, and it is the only thing the region says', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();

    press(el);
    await flush();

    expect(region(el)!.textContent).toBe('Sign-out did not complete. Try again.');
  });

  it('is seen: an ordinary paragraph in the error colour, not hidden from anyone', async () => {
    logOut.mockRejectedValue(problem(500, '/problems/unknown'));
    const el = mounted();

    press(el);
    await flush();

    const paragraph = region(el)!.querySelector('p')!;
    expect(paragraph.className.split(/\s+/)).toContain('text-danger');
    // Nothing between the sentence and the footer takes it off the screen or out of the reading
    // order, and it is not a visually hidden announcement: sighted people must read it too.
    for (let node: Element | null = paragraph; node && node !== footer(el); node = node.parentElement) {
      expect(node.getAttribute('aria-hidden'), node.outerHTML).toBeNull();
      expect(node.hasAttribute('hidden'), node.outerHTML).toBe(false);
      expect(node.getAttribute('style'), node.outerHTML).toBeNull();
      expect(node.className, node.outerHTML).not.toMatch(/avp-visually-hidden|sr-only/);
    }
  });
});

describe('the alert region is on the page before there is anything to say', () => {
  it('is there from the first render, empty, as an alert', () => {
    const el = mounted();

    expect(region(el)).not.toBeNull();
    expect(region(el)!.getAttribute('role')).toBe('alert');
    // `alert` is assertive of itself: no `aria-live` of its own, or only "assertive".
    expect([null, 'assertive']).toContain(region(el)!.getAttribute('aria-live'));
    expect(region(el)!.textContent).toBe('');
    expect(sentences(el)).toEqual([]);
  });

  it('adds no gap while it is empty: it is not a flex item of the footer, which has one', () => {
    const el = mounted();

    // jsdom has no layout, so this pins the structure that gives none. The footer is a flex
    // column with a gap, and an empty flex item still has a gap beside it; the region shares a
    // wrapper with the button, which is one flex item, and brings its own space with the sentence.
    const wrapper = region(el)!.parentElement!;
    expect(wrapper.contains(button(el))).toBe(true);
    expect(wrapper.parentElement!.className.split(/\s+/)).toContain('gap-3');
    expect(region(el)!.parentElement).not.toBe(wrapper.parentElement);
  });

  it('gains the sentence: the same region, with a paragraph added to it', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();
    const before = region(el)!;
    const gained = watch(before);

    press(el);
    await flush();

    expect(region(el)).toBe(before);
    const { added } = gained();
    const paragraphs = added.filter((n): n is HTMLElement => n instanceof HTMLElement);
    expect(paragraphs.map((p) => p.textContent)).toEqual([SENTENCE]);
  });

  it('a press empties it before the answer comes, and the failure fills it', async () => {
    const el = mounted();

    // A first failure leaves the sentence up...
    logOut.mockRejectedValueOnce(problem(500, '/problems/unknown'));
    press(el);
    await flush();
    expect(sentences(el)).toEqual([SENTENCE]);

    // ...and the next press takes it away at once, while nothing has been answered yet.
    const pending = deferred();
    logOut.mockReturnValueOnce(pending.promise);
    press(el);
    expect(sentences(el)).toEqual([]);
    expect(assign).not.toHaveBeenCalled();

    pending.reject(problem(503, '/problems/service-unavailable'));
    await flush();
    expect(sentences(el)).toEqual([SENTENCE]);
  });

  it('a second failure is a new paragraph, so the same words are read again', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();
    const gained = watch(region(el)!);

    press(el);
    await flush();
    const first = region(el)!.querySelector('p')!;
    gained();

    press(el);
    await flush();
    const second = region(el)!.querySelector('p')!;
    const { added, removed } = gained();

    expect(second).not.toBe(first);
    expect(removed).toContain(first);
    expect(added).toContain(second);
    expect(sentences(el)).toEqual([SENTENCE]);
  });
});

describe('after a failure the person can try again', () => {
  it('a press that works then goes home and leaves no sentence behind', async () => {
    logOut.mockRejectedValueOnce(problem(503, '/problems/service-unavailable'));
    logOut.mockResolvedValueOnce(undefined);
    const el = mounted();

    press(el);
    await flush();
    expect(sentences(el)).toEqual([SENTENCE]);
    expect(assign).not.toHaveBeenCalled();

    press(el);
    await flush();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
    expect(sentences(el)).toEqual([]);
  });

  it('a press that finds nobody signed in then goes home too', async () => {
    logOut.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    logOut.mockRejectedValueOnce(problem(401, '/problems/authentication-required'));
    const el = mounted();

    press(el);
    await flush();
    expect(sentences(el)).toEqual([SENTENCE]);

    press(el);
    await flush();
    expect(assign).toHaveBeenCalledWith('/');
    expect(sentences(el)).toEqual([]);
  });
});

describe('the sentence and the button are brought into view together', () => {
  // Under 60rem the sidebar is a strip that scrolls sideways, and on a phone its footer runs past
  // the edge of the screen before anything is added to it; the wide sidebar scrolls on a short
  // window, and the sentence can push the button below the fold while staying in sight itself.
  // jsdom has no layout, so this pins that the GROUP of the two (the wrapper around the alert
  // region and the button) asks to be scrolled into view, the least it takes, and not what a
  // browser then does with the request (`nearest` moves nothing when the group is already in view).
  it('scrolls the group of the sentence and the button into view, the least it takes', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();
    expect(scrollIntoView).not.toHaveBeenCalled();

    press(el);
    await flush();

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', inline: 'nearest' });
    const target = scrollIntoView.mock.contexts[0] as HTMLElement;
    expect(target).toBe(group(el));
    // The group holds the button as well as the sentence, so the button to try again comes into
    // view with it, and not only the paragraph.
    expect(target.contains(button(el))).toBe(true);
    expect(target.contains(region(el)!.querySelector('p'))).toBe(true);
  });

  it('does it again for a second failure, to the same group', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();

    press(el);
    await flush();
    press(el);
    await flush();

    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    expect(scrollIntoView.mock.contexts[1]).toBe(group(el));
    expect(scrollIntoView.mock.contexts[1]).toBe(scrollIntoView.mock.contexts[0]);
  });

  it('does not scroll for a sign-out that worked, or that found nobody signed in', async () => {
    logOut.mockResolvedValueOnce(undefined);
    logOut.mockRejectedValueOnce(problem(401, '/problems/authentication-required'));
    const el = mounted();

    press(el);
    await flush();
    press(el);
    await flush();

    expect(assign).toHaveBeenCalledTimes(2);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});

describe('the button keeps the look and the place it had', () => {
  it('is full width, as it was when it was a flex item of the footer and stretched', () => {
    const el = mounted();

    // The wrapper around the alert region makes it a block in the footer's column, where it would
    // otherwise be as wide as its label (checked in a browser, at three widths).
    const classes = button(el).classList;
    expect(classes.contains('avp-btn--full')).toBe(true);
    // ...and it is still the small ghost button.
    expect(classes.contains('avp-btn--ghost')).toBe(true);
    expect(classes.contains('avp-btn--sm')).toBe(true);
    expect(button(el).parentElement).toBe(group(el));
  });

  it('keeps focus: the same button, still the active element, after a failure', async () => {
    logOut.mockRejectedValue(problem(503, '/problems/service-unavailable'));
    const el = mounted();
    const pressed = button(el);
    pressed.focus();
    expect(document.activeElement).toBe(pressed);

    press(el);
    // Nothing has been answered yet: nothing moved focus away from the button.
    expect(document.activeElement).toBe(pressed);
    await flush();

    expect(sentences(el)).toEqual([SENTENCE]);
    // The button was not taken out and put back (focus would have gone with it), and a second
    // failure leaves it there too.
    expect(button(el)).toBe(pressed);
    expect(pressed.isConnected).toBe(true);
    expect(document.activeElement).toBe(pressed);

    press(el);
    await flush();
    expect(button(el)).toBe(pressed);
    expect(document.activeElement).toBe(pressed);
  });
});
