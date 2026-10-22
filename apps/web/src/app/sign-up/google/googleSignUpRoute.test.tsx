// @vitest-environment jsdom

/**
 * /sign-up/google, mounted — founder decision 6, 2026-10-01.
 *
 * One "expired" screen used to stand for two different things: the API
 * refusing a ticket, and a page that has no ticket to ask about. The second is
 * what a reload does, because the ticket is taken out of the address bar once
 * it is read, so it now says that, in the words /invite and /reset-password
 * use for the same thing. The panel's own test pins each state's copy; what
 * only a mount can show is which state each arrival lands on.
 *
 * The real route in jsdom, with the API client mocked at its edge
 * (`home.test.tsx`'s pattern) and the address bar left real, because reading
 * the ticket out of it and clearing it afterwards is the thing under test.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { GooglePending } from '@avp/shared-types';

const googlePending = vi.fn<(ticket: string) => Promise<GooglePending>>();
const joinWithGoogle = vi.fn<(ticket: string) => Promise<unknown>>();
const declineGoogleInvitation = vi.fn<(ticket: string) => Promise<{ ticket: string }>>();
const completeGoogleSignUp = vi.fn<(payload: unknown) => Promise<unknown>>();
const assign = vi.fn<(url: string) => void>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      googlePending: (ticket: string) => googlePending(ticket),
      joinWithGoogle: (ticket: string) => joinWithGoogle(ticket),
      declineGoogleInvitation: (ticket: string) => declineGoogleInvitation(ticket),
      completeGoogleSignUp: (payload: unknown) => completeGoogleSignUp(payload),
    },
  };
});

// Imported after the mock so the route sees the mocked module.
const { default: GoogleSignUpRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NO_CODE = {
  title: 'This sign-in link is not valid',
  detail:
    "This page no longer has the link's code (it is removed from the address bar once read). Start again from the sign-in page.",
};
const EXPIRED = {
  title: 'That Google sign-in can no longer be used',
  detail: 'It works once, for ten minutes, in the browser that started it. Start again from the sign-in page.',
};

const pending: GooglePending = {
  email: 'dana@northlight.example',
  suggestedName: 'Dana Whitfield',
  invitedTo: null,
};

/** What the API answers to a ticket it does not know, has spent, or has let lapse. */
const refused = () =>
  new ApiProblem({
    type: '/problems/invalid-google-ticket',
    title: 'That Google sign-in can no longer be used',
    status: 400,
    detail: 'That Google sign-in can no longer be used. Start again from the sign-in page.',
    instance: '/api/v1/auth/google/pending',
  });

/** Let every pending microtask settle: the mocked peek resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;

/** Be at /sign-up/google with this fragment, the way Google's callback redirect leaves the browser. */
function open(hash: string): void {
  window.history.replaceState(null, '', `/sign-up/google${hash}`);
}

async function mount(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<GoogleSignUpRoute />));
  await settle();
  return el;
}

function unmount(): void {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
}

/** The refusal card's two sentences, as a person reads them: title, then detail. */
const refusal = (el: HTMLElement) =>
  [...el.querySelectorAll('[role="alert"] p')].map((p) => p.textContent);

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

beforeEach(() => {
  googlePending.mockReset();
  joinWithGoogle.mockReset();
  declineGoogleInvitation.mockReset();
  completeGoogleSignUp.mockReset();
  assign.mockReset();
  // jsdom does not navigate and its `location` cannot be spied on in place, so
  // `assign` is a spy; everything else reads through to the real address bar.
  const real = window.location;
  vi.stubGlobal('location', {
    get hash() {
      return real.hash;
    },
    get search() {
      return real.search;
    },
    get pathname() {
      return real.pathname;
    },
    assign,
  });
});

afterEach(() => {
  unmount();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

describe('a page opened with no ticket in the fragment', () => {
  it("says it no longer has the link's code, and never asks the API", async () => {
    open('');
    const el = await mount();
    expect(refusal(el)).toEqual([NO_CODE.title, NO_CODE.detail]);
    expect(googlePending).not.toHaveBeenCalled();
  });

  it('is what a reload gets: the first visit takes the ticket out of the address bar, so the second has none', async () => {
    googlePending.mockResolvedValue(pending);
    open('#ticket=abc_DEF-1');

    const first = await mount();
    expect(first.textContent).toContain('Name your agency');
    expect(googlePending).toHaveBeenCalledTimes(1);
    expect(googlePending).toHaveBeenCalledWith('abc_DEF-1');
    expect(window.location.hash).toBe('');

    // The reload: the same page, at the address the first visit left behind.
    unmount();
    const second = await mount();
    expect(refusal(second)).toEqual([NO_CODE.title, NO_CODE.detail]);
    expect(second.textContent).not.toContain('Name your agency');
    expect(googlePending).toHaveBeenCalledTimes(1);
  });
});

const UNREACHABLE = {
  title: 'We could not reach the server',
  detail: 'Check your connection and try again. The sign-in link works for ten minutes.',
};

/** An ApiProblem as `request` builds it for a response that is not the API's JSON (a proxy's 502). */
const serverProblem = (status: number) =>
  new ApiProblem({
    type: '/problems/unknown',
    title: 'Request failed',
    status,
    detail: `The server returned ${status}.`,
    instance: '/auth/google/pending',
  });

describe('a ticket the API refuses', () => {
  it('a 400 from the API is the expired state, in the words it always had', async () => {
    googlePending.mockRejectedValue(refused());
    open('#ticket=spent');
    const el = await mount();
    expect(googlePending).toHaveBeenCalledWith('spent');
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
    expect(button(el, 'Try again')).toBeUndefined();
  });
});

describe('an API that did not answer', () => {
  it.each([
    ['a network error', new TypeError('Failed to fetch')],
    ['a 502 from a proxy', serverProblem(502)],
    ['a 500', serverProblem(500)],
    ['a problem with no status', serverProblem(0)],
  ])('%s is the unreachable state, not expired', async (_what, failure) => {
    googlePending.mockRejectedValue(failure);
    open('#ticket=abc');
    const el = await mount();
    expect(refusal(el)).toEqual([UNREACHABLE.title, UNREACHABLE.detail]);
    expect(el.textContent).not.toContain('can no longer be used');
  });

  it('Try again peeks again with the same ticket, and shows the form when the API is back', async () => {
    googlePending.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    googlePending.mockResolvedValueOnce(pending);
    open('#ticket=abc');
    const el = await mount();
    expect(googlePending).toHaveBeenCalledTimes(1);
    await act(async () => {
      button(el, 'Try again')!.click();
    });
    await settle();
    expect(googlePending).toHaveBeenCalledTimes(2);
    expect(googlePending).toHaveBeenLastCalledWith('abc');
    expect(el.textContent).toContain('Name your agency');
  });

  it('Try again that is refused by the API now says expired', async () => {
    googlePending.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    googlePending.mockRejectedValueOnce(refused());
    open('#ticket=abc');
    const el = await mount();
    await act(async () => {
      button(el, 'Try again')!.click();
    });
    await settle();
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
  });
});

describe('both refusals link to the sign-in page', () => {
  const ARRIVALS: Array<[string, string, Error | null]> = [
    ['a page with no ticket', '', null],
    ['a ticket the API refuses', '#ticket=spent', refused()],
  ];

  it.each(ARRIVALS)('%s: "Back to sign in" goes to /', async (_what, hash, failure) => {
    if (failure !== null) googlePending.mockRejectedValue(failure);
    open(hash);
    const el = await mount();
    const back = button(el, 'Back to sign in');
    expect(back, 'no "Back to sign in" button on screen').toBeDefined();
    act(() => back!.click());
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
  });
});


/**
 * "Try again" REPLACES the card that holds the focused button — first with the
 * "Checking…" line, then with whatever the peek finds — so unless focus is
 * carried along, every step drops it on <body> (the UI review of 2026-10-01).
 * Each step now hands it to what is there: the line, then the heading of the
 * form, or the button of the card that replaced the old one. A first visit,
 * which has nothing to carry, takes none.
 */
describe('Try again does not drop focus on the page', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }

  /** Away on the first peek; the second is whatever the test hands back. */
  async function afterTryAgain(second: Promise<GooglePending>) {
    googlePending.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    googlePending.mockReturnValueOnce(second);
    open('#ticket=abc');
    const el = await mount();
    const tryAgain = button(el, 'Try again')!;
    act(() => tryAgain.focus());
    expect(document.activeElement).toBe(tryAgain);
    await act(async () => {
      tryAgain.click();
    });
    return el;
  }

  it('while it checks, focus is on the "Checking…" line', async () => {
    const peek = deferred<GooglePending>();
    const el = await afterTryAgain(peek.promise);
    const line = el.querySelector('[role="status"]');
    expect(line?.textContent).toBe('Checking your Google sign-in…');
    expect(document.activeElement).toBe(line);
    await act(async () => peek.resolve(pending));
    await settle();
  });

  it('when the form comes back, focus is on its heading', async () => {
    const peek = deferred<GooglePending>();
    const el = await afterTryAgain(peek.promise);
    await act(async () => peek.resolve(pending));
    await settle();
    const heading = el.querySelector('h1');
    expect(heading?.textContent).toBe('Name your agency');
    expect(document.activeElement).toBe(heading);
  });

  it('when the API refuses the ticket, focus is on the way back', async () => {
    const peek = deferred<GooglePending>();
    const el = await afterTryAgain(peek.promise);
    await act(async () => peek.reject(refused()));
    await settle();
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
    expect(document.activeElement).toBe(button(el, 'Back to sign in'));
  });

  it('when the API is still away, focus is on the new Try again', async () => {
    const peek = deferred<GooglePending>();
    const el = await afterTryAgain(peek.promise);
    await act(async () => peek.reject(new TypeError('Failed to fetch')));
    await settle();
    expect(refusal(el)).toEqual([UNREACHABLE.title, UNREACHABLE.detail]);
    expect(document.activeElement).toBe(button(el, 'Try again'));
  });

  it('typing in the form afterwards does not pull focus back to the heading', async () => {
    const peek = deferred<GooglePending>();
    const el = await afterTryAgain(peek.promise);
    await act(async () => peek.resolve(pending));
    await settle();
    const field = el.querySelector<HTMLInputElement>('input[autocomplete="organization"]')!;
    act(() => field.focus());
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      set.call(field, 'Northlight');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(field.value).toBe('Northlight');
    expect(document.activeElement).toBe(field);
  });

  it('a first visit takes no focus: nothing was carried', async () => {
    googlePending.mockResolvedValue(pending);
    open('#ticket=abc');
    const el = await mount();
    expect(el.textContent).toContain('Name your agency');
    expect(document.activeElement).toBe(document.body);
  });

  it('a first visit to a refusal takes none either', async () => {
    googlePending.mockRejectedValue(refused());
    open('#ticket=spent');
    const el = await mount();
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
    expect(document.activeElement).toBe(document.body);
  });
});

/**
 * An address with an INVITATION waiting (3.126). The peek names the inviting agency, and the page
 * asks the founder's question instead of asking for an agency's name. Every outcome that is not
 * the two buttons working is a card this page already had.
 */
describe('an address with an invitation waiting', () => {
  const QUESTION = "You've been invited to join Northlight Partners. Join, or start your own agency?";
  const invited: GooglePending = {
    email: 'rae@northlight.example',
    suggestedName: 'Rae Park',
    invitedTo: 'Northlight Partners',
  };

  /** The page, opened on an offer ticket the peek names an agency for. */
  async function mountOffer(): Promise<HTMLDivElement> {
    googlePending.mockResolvedValueOnce(invited);
    open('#ticket=offer-1');
    return mount();
  }

  it('asks the question and offers the two buttons, and not the form', async () => {
    const el = await mountOffer();
    expect(googlePending).toHaveBeenCalledTimes(1);
    expect(googlePending).toHaveBeenCalledWith('offer-1');
    expect(el.querySelector('h1')?.textContent).toBe(QUESTION);
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toEqual([
      'Back',
      'Join',
      'Start your own agency',
    ]);
    expect(el.textContent).not.toContain('Name your agency');
    expect(el.querySelector('form')).toBeNull();
    // Nothing is asked of the API until a button is pressed.
    expect(joinWithGoogle).not.toHaveBeenCalled();
    expect(declineGoogleInvitation).not.toHaveBeenCalled();
    // A first visit takes no focus, like the other states.
    expect(document.activeElement).toBe(document.body);
  });

  it('Join spends the ticket and ends on the dashboard', async () => {
    joinWithGoogle.mockResolvedValue({});
    const el = await mountOffer();
    await act(async () => {
      button(el, 'Join')!.click();
    });
    await settle();
    expect(joinWithGoogle).toHaveBeenCalledTimes(1);
    expect(joinWithGoogle).toHaveBeenCalledWith('offer-1');
    expect(declineGoogleInvitation).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/dashboard');
  });

  it('Start your own agency peeks the NEW ticket and shows the form, which spends that one', async () => {
    declineGoogleInvitation.mockResolvedValue({ ticket: 'own-2' });
    completeGoogleSignUp.mockResolvedValue({});
    const el = await mountOffer();
    // The second peek is the new ticket's: the same person, with no invitation named.
    googlePending.mockResolvedValueOnce({ ...pending, email: 'rae@northlight.example' });
    await act(async () => {
      button(el, 'Start your own agency')!.click();
    });
    await settle();
    await settle();

    expect(declineGoogleInvitation).toHaveBeenCalledTimes(1);
    expect(declineGoogleInvitation).toHaveBeenCalledWith('offer-1');
    expect(joinWithGoogle).not.toHaveBeenCalled();
    expect(googlePending).toHaveBeenCalledTimes(2);
    expect(googlePending).toHaveBeenLastCalledWith('own-2');
    const heading = el.querySelector('h1');
    expect(heading?.textContent).toBe('Name your agency');
    expect(el.textContent).not.toContain("You've been invited");
    // The button that was pressed is gone; focus is carried to the form's heading.
    expect(document.activeElement).toBe(heading);
    expect(assign).not.toHaveBeenCalled();

    // The existing form spends the NEW ticket, never the one the choice had.
    const field = el.querySelector<HTMLInputElement>('input[autocomplete="organization"]')!;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      set.call(field, 'Rae Park Studio');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(completeGoogleSignUp).toHaveBeenCalledWith({
      ticket: 'own-2',
      agencyName: 'Rae Park Studio',
      fullName: 'Dana Whitfield',
    });
  });

  it.each([
    ['Join', () => joinWithGoogle, 'Join'],
    ['Start your own agency', () => declineGoogleInvitation, 'Start your own agency'],
  ])('%s refused by the API is the "can no longer be used" card, with focus on the way back', async (label, spy) => {
    spy().mockRejectedValue(refused());
    const el = await mountOffer();
    await act(async () => {
      button(el, label)!.click();
    });
    await settle();
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
    expect(el.querySelector('h1')).toBeNull();
    expect(document.activeElement).toBe(button(el, 'Back to sign in'));
    expect(assign).not.toHaveBeenCalled();
  });

  it.each([
    ['Join', () => joinWithGoogle],
    ['Start your own agency', () => declineGoogleInvitation],
  ])('%s while the API does not answer is the "could not reach the server" card', async (label, spy) => {
    spy().mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mountOffer();
    await act(async () => {
      button(el, label)!.click();
    });
    await settle();
    expect(refusal(el)).toEqual([UNREACHABLE.title, UNREACHABLE.detail]);
    expect(document.activeElement).toBe(button(el, 'Try again'));
  });

  it('Try again after a failed Join peeks the same ticket and asks the question again', async () => {
    joinWithGoogle.mockRejectedValue(serverProblem(502));
    const el = await mountOffer();
    await act(async () => {
      button(el, 'Join')!.click();
    });
    await settle();
    googlePending.mockResolvedValueOnce(invited);
    await act(async () => {
      button(el, 'Try again')!.click();
    });
    await settle();
    expect(googlePending).toHaveBeenCalledTimes(2);
    expect(googlePending).toHaveBeenLastCalledWith('offer-1');
    expect(el.querySelector('h1')?.textContent).toBe(QUESTION);
    expect(document.activeElement).toBe(el.querySelector('h1'));
  });

  it('a peek the API refuses (the invitation was withdrawn meanwhile) is the card it always was', async () => {
    googlePending.mockRejectedValue(refused());
    open('#ticket=offer-1');
    const el = await mount();
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
    expect(el.querySelector('h1')).toBeNull();
  });
});
