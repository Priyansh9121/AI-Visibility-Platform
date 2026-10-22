// @vitest-environment jsdom

/**
 * The "Check who has access" notice — A6 part 4, launch checklist 3.123.
 *
 * When an owner's or an admin's Google sign-in has replaced their password, the seat list carries
 * `accessReview`: the ids of the people who were in the agency before. The panel opens with a notice
 * that names them, with the roster's own Remove on each, and one "Keep these people" button that
 * closes it. What is held here:
 *
 * - **The words are the founder's, and there are no others.** A heading, one sentence, the people,
 *   "Remove" on each, and "Keep these people". The test lists every piece of text in the notice.
 * - **It is shown when there is something to review and not otherwise**, and to a person who can
 *   manage seats: no review, an empty one, one naming nobody the roster holds, and a member, show
 *   nothing, and the panel is then exactly what it was.
 * - **Its Remove is the roster's removal**, with its inline question and its explanation, asked in
 *   the notice and not mirrored in the roster (and the roster's is not mirrored in the notice).
 * - **Keep removes nobody**, tells the API once, and re-reads; a failure is the API's own sentence
 *   and leaves the notice where it was.
 * - **A failure is said where the person is acting.** The panel has one alert: inside the notice,
 *   under Keep, when the action that failed began there (Keep, or a Remove asked in the notice), and
 *   at the bottom of the panel for a removal asked in the roster. It is not lost if the notice goes.
 * - **Focus is not stranded** when Keep closes the notice: it is handed to the start of the roster.
 *   A removal moves no focus, even the last one on the notice, so the sentence that says what it did
 *   is not cut off.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ADMIN_ID,
  OWNER_ID,
  PENDING_ID,
  ownerOnlySeats,
  seatsWithAccessReview,
  seatsWithPending,
} from '@/lib/settings/__fixtures__/seats';
import type { SeatList } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';

const removeSeat = vi.fn<(userId: string) => Promise<void>>();
const keepAccessReview = vi.fn<() => Promise<void>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      removeSeat: (userId: string) => removeSeat(userId),
      keepAccessReview: () => keepAccessReview(),
    },
  };
});

// Imported after the mock so the panel sees the mocked module.
const { SeatsPanel } = await import('./SeatsPanel');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The founder's words (2026-10-22), written out here and not read from the component.
const HEADING = 'Check who has access';
const SENTENCE =
  'These people had access to this agency before you first signed in with Google. Remove anyone who should not have access.';
const KEEP = 'Keep these people';
// What removing a seat costs, as the roster has always said it.
const COST =
  'Removing a seat signs that person out everywhere immediately — not at their next sign-in, and not when a token expires. Their scans and reports stay exactly where they are. You can invite the same address again later.';
const RELEASED = 'That seat has been released and their sessions have been signed out.';
const FALLBACK = 'The change did not go through. Check your connection and try again.';

type Props = Partial<Parameters<typeof SeatsPanel>[0]>;

const panel = (seats: SeatList, props: Props = {}) => (
  <SeatsPanel
    agencyId="agcy_01NORTHLIGHT"
    seats={seats}
    currentUserId={OWNER_ID}
    currentUserRole="owner"
    canManage
    onChanged={() => {}}
    {...props}
  />
);

/** The panel's markup as a DOM, for the tests that look at what a person would read. */
function drawn(seats: SeatList, props: Props = {}): HTMLElement {
  const el = document.createElement('div');
  el.innerHTML = renderToStaticMarkup(panel(seats, props));
  return el;
}

/** Every piece of text under `node`, in order, trimmed: what a person reads there, and no more. */
function texts(node: Node): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let next = walker.nextNode(); next; next = walker.nextNode()) {
    const text = (next.textContent ?? '').trim();
    if (text) out.push(text);
  }
  return out;
}

const notice = (el: ParentNode) => el.querySelector<HTMLElement>('[data-testid="access-review"]');
const noticeButtons = (el: ParentNode) =>
  [...(notice(el)?.querySelectorAll('button') ?? [])] as HTMLButtonElement[];
const noticeButton = (el: ParentNode, label: string) =>
  noticeButtons(el).find((b) => b.textContent === label);
/** The roster's own buttons: the table, which the notice is not part of. */
const rosterButtons = (el: ParentNode, label: string) =>
  ([...el.querySelectorAll('table button')] as HTMLButtonElement[]).filter(
    (b) => b.textContent === label,
  );
const seatCount = (el: ParentNode) =>
  [...el.querySelectorAll('p')].find((p) => p.textContent?.includes('seats in use')) as
    | HTMLElement
    | undefined;
const occurrences = (el: HTMLElement, sentence: string) =>
  (el.textContent ?? '').split(sentence).length - 1;

// --- what the notice says -------------------------------------------------------------------------

describe('the notice says what the founder said and nothing else', () => {
  it('has the heading, the sentence, each person with Remove, and Keep these people', () => {
    const el = drawn(seatsWithAccessReview);
    expect(texts(notice(el)!)).toEqual([
      HEADING,
      SENTENCE,
      'Sam Okafor',
      'sam@northlight.example',
      'Remove',
      // An invited seat's name IS its address, so it is said once.
      'rae@northlight.example',
      'Remove',
      KEEP,
    ]);
  });

  it('is a region named by its heading, which is an h3 like the panel’s other cards', () => {
    const el = drawn(seatsWithAccessReview);
    const region = notice(el)!;
    const heading = region.querySelector('h3')!;
    expect(heading.textContent).toBe(HEADING);
    expect(region.getAttribute('role')).toBe('region');
    expect(region.getAttribute('aria-labelledby')).toBe(heading.id);
    expect(heading.id).not.toBe('');
  });

  it('has Remove on each person, named for them, and one Keep', () => {
    const el = drawn(seatsWithAccessReview);
    const buttons = noticeButtons(el);
    expect(buttons.map((b) => b.textContent)).toEqual(['Remove', 'Remove', KEEP]);
    // The word is the same on every row, so the name a screen reader says is the person's too; it
    // starts with the visible label.
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Remove Sam Okafor',
      'Remove rae@northlight.example',
      null,
    ]);
  });

  it('marks its list as a list, which a list without markers is not read as by every screen reader', () => {
    const list = notice(drawn(seatsWithAccessReview))!.querySelector('ul')!;
    expect(list.getAttribute('role')).toBe('list');
    expect(list.querySelectorAll('li')).toHaveLength(2);
  });

  it('comes first in the panel, above the roster', () => {
    const html = renderToStaticMarkup(panel(seatsWithAccessReview));
    expect(html.indexOf(HEADING)).toBeGreaterThan(-1);
    expect(html.indexOf(HEADING)).toBeLessThan(html.indexOf('seats in use'));
  });

  it('lists the people in the order the API gave, whatever the roster’s', () => {
    const reversed: SeatList = {
      ...seatsWithAccessReview,
      accessReview: { userIds: [PENDING_ID, ADMIN_ID] },
    };
    const el = drawn(reversed);
    expect(texts(notice(el)!).slice(2, 6)).toEqual([
      'rae@northlight.example',
      'Remove',
      'Sam Okafor',
      'sam@northlight.example',
    ]);
  });

  it('offers nobody the holder: only the people the response names', () => {
    const el = drawn(seatsWithAccessReview);
    expect(notice(el)!.textContent).not.toContain('Dana Whitfield');
  });
});

describe('nothing is shown when there is nothing to review', () => {
  const plain = renderToStaticMarkup(panel(seatsWithPending));

  it.each([
    ['no review at all', seatsWithPending],
    ['a null review', { ...seatsWithPending, accessReview: null } as SeatList],
    ['a review naming nobody', { ...seatsWithPending, accessReview: { userIds: [] } } as SeatList],
    [
      'a review naming only people the roster does not hold',
      { ...seatsWithPending, accessReview: { userIds: ['user_01GONE'] } } as SeatList,
    ],
  ])('%s: no notice, and the panel is exactly what it was', (_name, seats) => {
    const html = renderToStaticMarkup(panel(seats));
    expect(html).not.toContain(HEADING);
    expect(html).not.toContain(KEEP);
    expect(html).toBe(plain);
  });

  it('names only the people the roster holds', () => {
    const seats: SeatList = {
      ...seatsWithPending,
      accessReview: { userIds: ['user_01GONE', ADMIN_ID] },
    };
    const el = drawn(seats);
    expect(texts(notice(el)!)).toContain('Sam Okafor');
    expect(notice(el)!.querySelectorAll('li')).toHaveLength(1);
  });

  it('never lists the signed-in person: the roster offers no Remove on your own row either', () => {
    const seats: SeatList = {
      ...seatsWithPending,
      accessReview: { userIds: [OWNER_ID, ADMIN_ID] },
    };
    const el = drawn(seats);
    expect(texts(notice(el)!)).toEqual([
      HEADING,
      SENTENCE,
      'Sam Okafor',
      'sam@northlight.example',
      'Remove',
      KEEP,
    ]);
    // A review that names only the holder has nobody to show.
    const onlyYou = renderToStaticMarkup(
      panel({ ...seatsWithPending, accessReview: { userIds: [OWNER_ID] } }),
    );
    expect(onlyYou).not.toContain(HEADING);
  });

  it('is not shown to a person who cannot manage seats', () => {
    const html = renderToStaticMarkup(panel(seatsWithAccessReview, { canManage: false }));
    expect(html).not.toContain(HEADING);
    expect(html).not.toContain(KEEP);
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class, and is built of design-system parts', () => {
    const html = renderToStaticMarkup(panel(seatsWithAccessReview));
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
    const region = notice(drawn(seatsWithAccessReview))!;
    // The notice sets no inline style (the roster's highlighted row has its own, the table's).
    expect(region.outerHTML).not.toContain('style=');
    expect(region.className).toContain('avp-card');
    expect(region.querySelectorAll('button.avp-btn')).toHaveLength(3);
  });
});

// --- Remove, in the notice -----------------------------------------------------------------------

let root: Root | null = null;
const onChanged = vi.fn<() => void | Promise<void>>();

/** Mount the panel as the signed-in owner. Returns its container and a way to hand it new props. */
function mounted(seats: SeatList, props: Props = {}) {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  const draw = (next: SeatList, more: Props = props) =>
    act(() => root!.render(panel(next, { onChanged, ...more })));
  draw(seats);
  return { el, draw };
}

const refusalOf = (userId: string) =>
  new ApiProblem({
    type: '/problems/permission-denied',
    title: 'Forbidden',
    status: 403,
    detail: 'You cannot remove someone whose role is above your own.',
    instance: `/api/v1/users/${userId}`,
  });

/** Let every pending microtask settle — the mocked request resolves on the next tick. */
const settle = () =>
  act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });

beforeEach(() => {
  removeSeat.mockReset();
  keepAccessReview.mockReset();
  onChanged.mockReset();
  onChanged.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('Remove in the notice is the roster’s removal, asked in the notice', () => {
  it('asks first, in the notice, with the roster’s buttons and its explanation', () => {
    const { el } = mounted(seatsWithAccessReview);
    expect(occurrences(el, COST)).toBe(0);

    act(() => noticeButton(el, 'Remove')!.click());

    // Sam's row in the notice now asks; rae's still offers Remove; the explanation is said once,
    // inside the notice.
    expect(noticeButtons(el).map((b) => b.textContent)).toEqual([
      'Yes, remove',
      'Cancel',
      'Remove',
      KEEP,
    ]);
    expect(notice(el)!.textContent).toContain(COST);
    expect(occurrences(el, COST)).toBe(1);
    // Not mirrored in the roster: its rows still offer Remove, and it asks nothing.
    expect(rosterButtons(el, 'Yes, remove')).toHaveLength(0);
    expect(rosterButtons(el, 'Remove')).toHaveLength(2);
    expect(removeSeat).not.toHaveBeenCalled();
  });

  it('Cancel takes the question back and removes nobody', () => {
    const { el } = mounted(seatsWithAccessReview);
    act(() => noticeButton(el, 'Remove')!.click());
    act(() => noticeButton(el, 'Cancel')!.click());
    expect(noticeButtons(el).map((b) => b.textContent)).toEqual(['Remove', 'Remove', KEEP]);
    expect(occurrences(el, COST)).toBe(0);
    expect(removeSeat).not.toHaveBeenCalled();
  });

  it('Yes, remove removes that person with the existing call, says so, and re-reads', async () => {
    removeSeat.mockResolvedValue(undefined);
    const { el } = mounted(seatsWithAccessReview);
    act(() => noticeButton(el, 'Remove')!.click());
    await act(async () => {
      noticeButton(el, 'Yes, remove')!.click();
    });
    await settle();

    expect(removeSeat).toHaveBeenCalledTimes(1);
    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(el.textContent).toContain(RELEASED);
    expect(keepAccessReview).not.toHaveBeenCalled();
  });

  const REFUSED = 'You cannot remove someone whose role is above your own.';
  const refusal = () =>
    new ApiProblem({
      type: '/problems/permission-denied',
      title: 'Forbidden',
      status: 403,
      detail: REFUSED,
      instance: `/api/v1/users/${ADMIN_ID}`,
    });

  it('a refusal is the API’s sentence, said in the notice under Keep, and the person is still listed', async () => {
    removeSeat.mockRejectedValue(refusal());
    const { el } = mounted(seatsWithAccessReview);
    act(() => noticeButton(el, 'Remove')!.click());
    await act(async () => {
      noticeButton(el, 'Yes, remove')!.click();
    });
    await settle();

    // The one alert is where the person pressed the button, not below three cards.
    const alerts = el.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.textContent).toBe(REFUSED);
    expect(notice(el)!.contains(alerts[0]!)).toBe(true);
    expect(
      noticeButton(el, KEEP)!.compareDocumentPosition(alerts[0]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
    expect(texts(notice(el)!)).toContain('Sam Okafor');
  });

  it('a refusal of a removal asked in the roster is said at the bottom of the panel, outside the notice', async () => {
    removeSeat.mockRejectedValue(refusal());
    const { el } = mounted(seatsWithAccessReview);
    act(() => rosterButtons(el, 'Remove')[0]!.click());
    await act(async () => {
      rosterButtons(el, 'Yes, remove')[0]!.click();
    });
    await settle();

    const alerts = el.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.textContent).toBe(REFUSED);
    expect(notice(el)!.contains(alerts[0]!)).toBe(false);
  });

  it('a question asked in the roster is answered in the roster and not mirrored in the notice', () => {
    const { el } = mounted(seatsWithAccessReview);
    // The roster's first Remove is Sam's, who is also on the notice.
    act(() => rosterButtons(el, 'Remove')[0]!.click());

    expect(rosterButtons(el, 'Yes, remove')).toHaveLength(1);
    expect(noticeButtons(el).map((b) => b.textContent)).toEqual(['Remove', 'Remove', KEEP]);
    expect(notice(el)!.textContent).not.toContain(COST);
    // The roster's own explanation, word for word what it has always said.
    expect(occurrences(el, COST)).toBe(1);
  });
});

// --- Keep these people ---------------------------------------------------------------------------

describe('Keep these people', () => {
  it('tells the API once, removes nobody, and re-reads', async () => {
    keepAccessReview.mockResolvedValue(undefined);
    const { el } = mounted(seatsWithAccessReview);

    await act(async () => {
      noticeButton(el, KEEP)!.click();
    });
    await settle();

    expect(keepAccessReview).toHaveBeenCalledTimes(1);
    expect(keepAccessReview).toHaveBeenCalledWith();
    expect(removeSeat).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalledTimes(1);
    // It says nothing of its own: no new sentence anywhere.
    expect(el.querySelector('[role="alert"]')).toBeNull();
    expect(el.querySelector('[data-testid="seat-notice"]')?.textContent).toBe('');
  });

  it('is disabled with the other buttons while it is in flight, so it cannot be pressed twice', async () => {
    let answer!: () => void;
    keepAccessReview.mockReturnValue(
      new Promise<void>((resolve) => {
        answer = resolve;
      }),
    );
    const { el } = mounted(seatsWithAccessReview);
    act(() => {
      noticeButton(el, KEEP)!.click();
    });
    expect(noticeButtons(el).every((b) => b.disabled)).toBe(true);
    act(() => {
      noticeButton(el, KEEP)!.click(); // a disabled button takes no click
    });
    expect(keepAccessReview).toHaveBeenCalledTimes(1);

    await act(async () => {
      answer();
    });
    await settle();
    expect(noticeButtons(el).every((b) => !b.disabled)).toBe(true);
  });

  const SORRY = 'You need to sign in again.';
  const sorry = () =>
    new ApiProblem({
      type: '/problems/authentication-required',
      title: 'Unauthorized',
      status: 401,
      detail: SORRY,
      instance: '/api/v1/auth/me/access-review/keep',
    });

  it('a refusal is the API’s sentence, said in the notice under Keep; the notice stays and nothing is re-read', async () => {
    keepAccessReview.mockRejectedValue(sorry());
    const { el } = mounted(seatsWithAccessReview);
    await act(async () => {
      noticeButton(el, KEEP)!.click();
    });
    await settle();

    const alerts = el.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.textContent).toBe(SORRY);
    expect(notice(el)!.contains(alerts[0]!)).toBe(true);
    expect(
      noticeButton(el, KEEP)!.compareDocumentPosition(alerts[0]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('no answer at all is the panel’s plain fallback, said in the notice', async () => {
    keepAccessReview.mockRejectedValue(new TypeError('Failed to fetch'));
    const { el } = mounted(seatsWithAccessReview);
    await act(async () => {
      noticeButton(el, KEEP)!.click();
    });
    await settle();
    const alerts = el.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.textContent).toBe(FALLBACK);
    expect(notice(el)!.contains(alerts[0]!)).toBe(true);
  });

  it('a sentence said in the notice is not lost if the notice goes before it is read', async () => {
    keepAccessReview.mockRejectedValue(sorry());
    const { el, draw } = mounted(seatsWithAccessReview);
    await act(async () => {
      noticeButton(el, KEEP)!.click();
    });
    await settle();
    expect(notice(el)!.contains(el.querySelector('[role="alert"]')!)).toBe(true);

    draw(seatsWithPending); // the seat list comes back without the review
    expect(notice(el)).toBeNull();
    const alerts = el.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]!.textContent).toBe(SORRY);
  });

  it('an error in a panel with no notice is still said at the bottom, as it always was', async () => {
    removeSeat.mockRejectedValue(refusalOf(ADMIN_ID));
    const { el } = mounted(seatsWithPending);
    act(() => rosterButtons(el, 'Remove')[0]!.click());
    await act(async () => {
      rosterButtons(el, 'Yes, remove')[0]!.click();
    });
    await settle();
    expect(notice(el)).toBeNull();
    expect(el.querySelectorAll('[role="alert"]')).toHaveLength(1);
  });

  it('the notice goes once the seat list no longer carries it, and focus is handed to the roster', async () => {
    keepAccessReview.mockResolvedValue(undefined);
    const { el, draw } = mounted(seatsWithAccessReview);
    // The route re-reads after a change: what comes back has no review.
    onChanged.mockImplementation(() => {
      draw(seatsWithPending);
    });
    const keepButton = noticeButton(el, KEEP)!;
    keepButton.focus();
    expect(document.activeElement).toBe(keepButton);

    await act(async () => {
      keepButton.click();
    });
    await settle();

    expect(notice(el)).toBeNull();
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(seatCount(el));
  });
});

// --- the last person goes, and so does the notice -------------------------------------------------

describe('removing the people the notice names', () => {
  const justAdmin: SeatList = { ...seatsWithAccessReview, accessReview: { userIds: [ADMIN_ID] } };
  const withoutAdmin: SeatList = {
    ...ownerOnlySeats,
    members: seatsWithPending.members.filter((m) => m.id !== ADMIN_ID),
  };

  it('the last one removed ends the notice and leaves focus alone, so the sentence that says it is read', async () => {
    removeSeat.mockResolvedValue(undefined);
    const { el, draw } = mounted(justAdmin);
    onChanged.mockImplementation(() => {
      draw(withoutAdmin);
    });
    act(() => noticeButton(el, 'Remove')!.click());
    const yes = noticeButton(el, 'Yes, remove')!;
    yes.focus();
    await act(async () => {
      yes.click();
    });
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(notice(el)).toBeNull();
    // The status region says what happened, as for every removal, and no focus move talks over it.
    expect(el.querySelector('[data-testid="seat-notice"]')?.textContent).toBe(RELEASED);
    expect(document.activeElement).not.toBe(seatCount(el));
    // Nobody pressed Keep: removing them was the answer.
    expect(keepAccessReview).not.toHaveBeenCalled();
  });

  it('one of several removed leaves the notice for the others, and does not move focus', async () => {
    removeSeat.mockResolvedValue(undefined);
    const { el, draw } = mounted(seatsWithAccessReview);
    onChanged.mockImplementation(() => {
      draw({
        ...seatsWithAccessReview,
        members: seatsWithAccessReview.members.filter((m) => m.id !== ADMIN_ID),
        accessReview: { userIds: [PENDING_ID] },
      });
    });
    act(() => noticeButton(el, 'Remove')!.click());
    await act(async () => {
      noticeButton(el, 'Yes, remove')!.click();
    });
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(texts(notice(el)!)).toEqual([
      HEADING,
      SENTENCE,
      'rae@northlight.example',
      'Remove',
      KEEP,
    ]);
    expect(document.activeElement).not.toBe(seatCount(el));
  });

  it('a person removed from the roster, last on the notice, ends it the same way and moves no focus', async () => {
    removeSeat.mockResolvedValue(undefined);
    const { el, draw } = mounted(justAdmin);
    onChanged.mockImplementation(() => {
      draw(withoutAdmin);
    });
    act(() => rosterButtons(el, 'Remove')[0]!.click());
    const yes = rosterButtons(el, 'Yes, remove')[0]!;
    yes.focus();
    await act(async () => {
      yes.click();
    });
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(notice(el)).toBeNull();
    expect(el.querySelector('[data-testid="seat-notice"]')?.textContent).toBe(RELEASED);
    expect(document.activeElement).not.toBe(seatCount(el));
  });
});

describe('the roster’s own question is unchanged', () => {
  it('says what a removal costs, word for word, when a roster row is being confirmed', () => {
    const { el } = mounted(seatsWithPending);
    expect(occurrences(el, COST)).toBe(0);
    act(() => rosterButtons(el, 'Remove')[0]!.click());
    expect(occurrences(el, COST)).toBe(1);
    expect(rosterButtons(el, 'Yes, remove')).toHaveLength(1);
  });
});
