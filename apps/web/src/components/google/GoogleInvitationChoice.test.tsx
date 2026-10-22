// @vitest-environment jsdom

/**
 * "You've been invited to join {agency}. Join, or start your own agency?" — launch checklist 3.126.
 *
 * The sentence and the two button labels are the founder's, word for word, and the view carries
 * nothing else. What each press does, and that a press cannot be made twice, is pinned here; what
 * the page does with each outcome (the cards it already had) is `googleSignUpRoute.test.tsx`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const joinWithGoogle = vi.fn<(ticket: string) => Promise<unknown>>();
const declineGoogleInvitation = vi.fn<(ticket: string) => Promise<{ ticket: string }>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      joinWithGoogle: (ticket: string) => joinWithGoogle(ticket),
      declineGoogleInvitation: (ticket: string) => declineGoogleInvitation(ticket),
    },
  };
});

const { GoogleInvitationChoice } = await import('./GoogleInvitationChoice');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TICKET = 'OFFERTICKETsentinel0123456789abcdef';
const onJoined = vi.fn<() => void>();
const onOwnAgency = vi.fn<(ticket: string) => void>();
const onFailed = vi.fn<(away: boolean) => void>();

let root: Root | null = null;

function mount(agency = 'Northlight Partners'): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(
      <GoogleInvitationChoice
        agency={agency}
        ticket={TICKET}
        onJoined={onJoined}
        onOwnAgency={onOwnAgency}
        onFailed={onFailed}
        animate={false}
      />,
    ),
  );
  return el;
}

const buttons = (el: HTMLElement) => [...el.querySelectorAll('button')];
const labelled = (el: HTMLElement, label: string) =>
  buttons(el).find((b) => b.textContent === label)!;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const problem = (status: number) =>
  new ApiProblem({
    type: status === 400 ? '/problems/invalid-google-ticket' : '/problems/unknown',
    title: 'Request failed',
    status,
    detail: `The server returned ${status}.`,
    instance: '/api/v1/auth/google/join',
  });

beforeEach(() => {
  for (const spy of [joinWithGoogle, declineGoogleInvitation, onJoined, onOwnAgency, onFailed]) {
    spy.mockReset();
  }
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the view', () => {
  it("is the founder's sentence with the agency's name in it, and two buttons, and nothing else", () => {
    const el = mount();
    const sentence = "You've been invited to join Northlight Partners. Join, or start your own agency?";
    expect(el.querySelector('h1')?.textContent).toBe(sentence);
    expect(buttons(el).map((b) => b.textContent)).toEqual(['Join', 'Start your own agency']);
    // Every character a person can read: the sentence, then the two labels.
    expect(el.textContent).toBe(`${sentence}JoinStart your own agency`);
  });

  it('makes "Join" the primary button and the other the second choice', () => {
    const el = mount();
    expect(labelled(el, 'Join').className).toContain('avp-btn--primary');
    expect(labelled(el, 'Start your own agency').className).toContain('avp-btn--secondary');
  });

  it("shows the agency's name as text: the inviter chose it, and a name is not markup", () => {
    const el = mount('<b>Acme</b> & <script>alert(1)</script>');
    expect(el.querySelector('h1')?.textContent).toBe(
      "You've been invited to join <b>Acme</b> & <script>alert(1)</script>. Join, or start your own agency?",
    );
    expect(el.querySelector('b')).toBeNull();
    expect(el.querySelector('script')).toBeNull();
  });

  it('lets the heading break a long unbroken agency name, so it stays inside the card', () => {
    // The longest name the API allows (200 characters) with no space to break the line at, such as
    // a domain: without a break it runs past the card and the page scrolls sideways (WCAG 1.4.10).
    const long = 'A'.repeat(200);
    const el = mount(long);
    const heading = el.querySelector('h1')!;
    expect(heading.textContent).toBe(
      `You've been invited to join ${long}. Join, or start your own agency?`,
    );
    // jsdom lays nothing out, so what is pinned is the class that does the breaking.
    expect(heading.classList.contains('break-words')).toBe(true);
  });

  it('puts the ticket nowhere a person or the page can read it', () => {
    const el = mount();
    expect(el.innerHTML).not.toContain(TICKET);
  });

  it('takes no focus when it arrives, and its heading can be given it', () => {
    const el = mount();
    expect(document.activeElement).toBe(document.body);
    expect(el.querySelector('h1')?.getAttribute('tabindex')).toBe('-1');
  });
});

describe('"Join"', () => {
  it('spends the ticket and reports success, once', async () => {
    joinWithGoogle.mockResolvedValue({});
    const el = mount();
    await act(async () => {
      labelled(el, 'Join').click();
    });
    expect(joinWithGoogle).toHaveBeenCalledTimes(1);
    expect(joinWithGoogle).toHaveBeenCalledWith(TICKET);
    expect(declineGoogleInvitation).not.toHaveBeenCalled();
    expect(onJoined).toHaveBeenCalledTimes(1);
    expect(onOwnAgency).not.toHaveBeenCalled();
    expect(onFailed).not.toHaveBeenCalled();
  });

  it('disables both buttons while it is in flight, and a second press asks nothing', async () => {
    const answer = deferred<unknown>();
    joinWithGoogle.mockReturnValue(answer.promise);
    const el = mount();
    await act(async () => {
      labelled(el, 'Join').click();
    });
    expect(buttons(el).map((b) => b.disabled)).toEqual([true, true]);
    // The labels do not change: a "Joining…" would be a sentence nobody gave.
    expect(buttons(el).map((b) => b.textContent)).toEqual(['Join', 'Start your own agency']);
    await act(async () => {
      labelled(el, 'Join').click();
      labelled(el, 'Start your own agency').click();
    });
    expect(joinWithGoogle).toHaveBeenCalledTimes(1);
    expect(declineGoogleInvitation).not.toHaveBeenCalled();
    await act(async () => answer.resolve({}));
    expect(onJoined).toHaveBeenCalledTimes(1);
  });
});

/**
 * The click handler React holds for a button, called directly. A click never reaches a disabled
 * button (jsdom drops it and so does React), so the tests above prove `disabled` and nothing
 * behind it. This reaches the handler another way, as any path that reaches the handler although
 * the buttons are disabled would, and shows the `busy` guard is what stops the second answer.
 * It reads React's own, internal `__reactProps$…` key on the node: if a React upgrade renames it,
 * the expectation below fails and says so (the other way in is to spy on the props handed to
 * the design system's Button).
 */
function handlerOf(button: HTMLButtonElement): () => void {
  const key = Object.keys(button).find((k) => k.startsWith('__reactProps$'));
  expect(
    key,
    "React no longer keeps a node's props under __reactProps$; reach the click handler another way (for example, spy on the props handed to Button)",
  ).toBeDefined();
  return (button as unknown as Record<string, { onClick: () => void }>)[key!]!.onClick;
}

describe('the guard behind the disabled buttons', () => {
  it.each([
    ['Join', () => joinWithGoogle, () => declineGoogleInvitation],
    ['Start your own agency', () => declineGoogleInvitation, () => joinWithGoogle],
  ])('after %s is pressed, neither handler asks the API again while it is in flight', async (label, first, other) => {
    const answer = deferred<unknown>();
    first().mockReturnValue(answer.promise);
    const el = mount();
    await act(async () => {
      labelled(el, label).click();
    });
    expect(first()).toHaveBeenCalledTimes(1);

    await act(async () => {
      handlerOf(labelled(el, 'Join'))();
      handlerOf(labelled(el, 'Start your own agency'))();
    });

    expect(first()).toHaveBeenCalledTimes(1);
    expect(other()).not.toHaveBeenCalled();
    await act(async () => answer.resolve({ ticket: 'NEWticket-2' }));
  });
});

describe('"Start your own agency"', () => {
  it('spends the ticket and hands on the new one', async () => {
    declineGoogleInvitation.mockResolvedValue({ ticket: 'NEWticket-2' });
    const el = mount();
    await act(async () => {
      labelled(el, 'Start your own agency').click();
    });
    expect(declineGoogleInvitation).toHaveBeenCalledTimes(1);
    expect(declineGoogleInvitation).toHaveBeenCalledWith(TICKET);
    expect(joinWithGoogle).not.toHaveBeenCalled();
    expect(onOwnAgency).toHaveBeenCalledTimes(1);
    expect(onOwnAgency).toHaveBeenCalledWith('NEWticket-2');
    expect(onJoined).not.toHaveBeenCalled();
    expect(onFailed).not.toHaveBeenCalled();
  });

  it('disables both buttons while it is in flight, and a second press asks nothing', async () => {
    const answer = deferred<{ ticket: string }>();
    declineGoogleInvitation.mockReturnValue(answer.promise);
    const el = mount();
    await act(async () => {
      labelled(el, 'Start your own agency').click();
    });
    expect(buttons(el).map((b) => b.disabled)).toEqual([true, true]);
    await act(async () => {
      labelled(el, 'Join').click();
      labelled(el, 'Start your own agency').click();
    });
    expect(declineGoogleInvitation).toHaveBeenCalledTimes(1);
    expect(joinWithGoogle).not.toHaveBeenCalled();
    await act(async () => answer.resolve({ ticket: 'NEWticket-2' }));
    expect(onOwnAgency).toHaveBeenCalledTimes(1);
  });
});

describe('a failed answer', () => {
  const ANSWERS: Array<[string, () => ReturnType<typeof vi.fn>]> = [
    ['Join', () => joinWithGoogle],
    ['Start your own agency', () => declineGoogleInvitation],
  ];

  it.each(ANSWERS)('%s refused by the API (a 400) is reported as the API saying no', async (label, spy) => {
    spy().mockRejectedValue(problem(400));
    const el = mount();
    await act(async () => {
      labelled(el, label).click();
    });
    expect(onFailed).toHaveBeenCalledTimes(1);
    expect(onFailed).toHaveBeenCalledWith(false);
    expect(onJoined).not.toHaveBeenCalled();
    expect(onOwnAgency).not.toHaveBeenCalled();
  });

  it.each([
    ['no answer at all', new TypeError('Failed to fetch')],
    ['a 502 from a proxy', problem(502)],
    ['a 500', problem(500)],
  ])('%s is reported as the API being away', async (_what, failure) => {
    joinWithGoogle.mockRejectedValue(failure);
    const el = mount();
    await act(async () => {
      labelled(el, 'Join').click();
    });
    expect(onFailed).toHaveBeenCalledWith(true);
  });

  it('writes no sentence of its own about it, and lets the buttons be pressed again', async () => {
    joinWithGoogle.mockRejectedValue(problem(400));
    const el = mount();
    const before = el.textContent;
    await act(async () => {
      labelled(el, 'Join').click();
    });
    expect(el.textContent).toBe(before);
    expect(el.querySelector('[role="alert"]')).toBeNull();
    expect(buttons(el).map((b) => b.disabled)).toEqual([false, false]);
  });
});
