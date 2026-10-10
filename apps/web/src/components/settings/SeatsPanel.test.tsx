// @vitest-environment jsdom

/**
 * Seat management — Epic 9.14.
 *
 * The assertions here are the ones `test_seat_endpoints.py` proves at the API
 * boundary, re-checked at the point they reach a person. A backend that is
 * careful about the difference between a seat consumed and a link re-issued is
 * worth nothing if the screen reports both as "invited"; a backend that refuses
 * to invent a display name is worth nothing if the screen invents one anyway.
 *
 * The invite itself is MOUNTED (2026-09-29): whether the screen shows a link
 * to copy depends on the API's answer, which a static render never receives.
 * The pattern is `app/home.test.tsx`'s — the real component in jsdom, the API
 * module mocked at its edge.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ADMIN_ID,
  OWNER_ID,
  ownerOnlySeats,
  roomToSpareSeats,
  seatsWithPending,
} from '@/lib/settings/__fixtures__/seats';
import type { InviteSeatResponse, SeatList, UserRole } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';
import { RETENTION } from '@/lib/legal/retention';

const inviteSeat =
  vi.fn<(agencyId: string, email: string, role?: UserRole) => Promise<InviteSeatResponse>>();
const removeSeat = vi.fn<(userId: string) => Promise<void>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      inviteSeat: (agencyId: string, email: string, role?: UserRole) =>
        inviteSeat(agencyId, email, role),
      removeSeat: (userId: string) => removeSeat(userId),
    },
  };
});

// Imported after the mock so the panel sees the mocked module.
const { SeatsPanel } = await import('./SeatsPanel');

const render = (
  seats: SeatList,
  props: Partial<Parameters<typeof SeatsPanel>[0]> = {},
) =>
  renderToStaticMarkup(
    <SeatsPanel
      agencyId="agcy_01NORTHLIGHT"
      seats={seats}
      currentUserId={OWNER_ID}
      currentUserRole="owner"
      canManage
      onChanged={() => {}}
      {...props}
    />,
  );

describe('the roster', () => {
  it('lists every seat holder with their role and status', () => {
    const html = render(seatsWithPending);
    expect(html).toContain('Dana Whitfield');
    expect(html).toContain('Sam Okafor');
    expect(html).toContain('Owner');
    expect(html).toContain('Admin');
  });

  it('states seats used against the limit', () => {
    expect(render(seatsWithPending)).toContain('3 of 3 seats in use');
    expect(render(roomToSpareSeats)).toContain('2 of 5 seats in use');
  });

  it('marks the signed-in row rather than colouring it', () => {
    expect(render(seatsWithPending)).toContain('aria-current="true"');
  });
});

describe('a pending invitation is a seat, and says so', () => {
  it('shows the invitee as Invited, not as missing', () => {
    const html = render(seatsWithPending);
    expect(html).toContain('rae@northlight.example');
    expect(html).toContain('Invited');
  });

  it('says a pending seat counts, so the number and the list agree', () => {
    expect(render(seatsWithPending)).toContain(
      'A pending invitation holds a seat before it is accepted',
    );
  });

  it('never invents a display name from an email local part', () => {
    // The backend stores the address as `fullName` for an invited seat
    // precisely so nothing downstream has to guess. The screen must not undo
    // that by rendering a guessed name, and must not print the address twice.
    const html = render(seatsWithPending);
    expect(html).not.toContain('Rae ');
    expect(html.match(/rae@northlight\.example/g)).toHaveLength(2); // roster + invitation list
  });

  it('lists the outstanding link with its expiry', () => {
    const html = render(seatsWithPending);
    expect(html).toContain('Invitations outstanding');
    expect(html).toContain('link expires 4 Sep 2026');
  });

  it('says re-inviting takes no second seat', () => {
    expect(render(seatsWithPending)).toContain('It does not take a second seat.');
  });

  it('renders no invitation list when there is nothing outstanding', () => {
    expect(render(ownerOnlySeats)).not.toContain('Invitations outstanding');
  });
});

describe('removal is not offered where the API would refuse it', () => {
  it('offers no Remove on your own row, and says why in one phrase', () => {
    const html = render(seatsWithPending, { currentUserId: OWNER_ID });
    expect(html).toContain('This is you');
  });

  it('offers Remove on somebody else', () => {
    expect(render(seatsWithPending, { currentUserId: ADMIN_ID })).toContain('Remove');
  });

  it('offers nothing at all to a member', () => {
    const html = render(seatsWithPending, { canManage: false });
    expect(html).not.toContain('Remove');
    expect(html).not.toContain('>Invite<');
    expect(html).not.toContain('Invite someone');
  });
});

describe('the invite form', () => {
  it('collects an email and a role', () => {
    const html = render(roomToSpareSeats);
    expect(html).toContain('>Email<');
    expect(html).toContain('>Role<');
    expect(html).toContain('>Invite<');
  });

  it('defaults the role to the least-privileged seat', () => {
    const html = render(roomToSpareSeats);
    // The selected option is the one the API also defaults to.
    expect(html).toMatch(/<option[^>]*value="member"[^>]*selected/);
  });

  it('offers Owner as a role to an owner', () => {
    const html = render(roomToSpareSeats, { currentUserRole: 'owner' });
    expect(html).toMatch(/<option[^>]*value="owner"/);
  });

  it('does not offer Owner to an admin, whom the API would refuse', () => {
    const html = render(roomToSpareSeats, {
      currentUserId: ADMIN_ID,
      currentUserRole: 'admin',
    });
    expect(html).not.toMatch(/<option[^>]*value="owner"/);
    expect(html).toMatch(/<option[^>]*value="admin"/);
    expect(html).toMatch(/<option[^>]*value="member"/);
  });

  it('states the link rules up front', () => {
    const html = render(roomToSpareSeats);
    expect(html).toContain('works once and expires in seven days');
  });
});

describe('a full agency is told, not merely disabled', () => {
  it('says every seat is in use and what to do', () => {
    const html = render(seatsWithPending);
    expect(html).toContain('Every seat is in use');
    expect(html).toContain('Remove one to invite someone else');
  });

  it('disables the submit rather than letting it 409', () => {
    expect(render(seatsWithPending)).toMatch(/>Invite<[\s\S]{0,80}?$|disabled/);
  });

  it('says nothing about upgrading, because there is no plan surface', () => {
    const html = render(seatsWithPending).toLowerCase();
    for (const tell of ['upgrade', 'add seats', 'buy', 'plan', 'billing', 'pricing']) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('offers the form normally when there is room', () => {
    expect(render(roomToSpareSeats)).not.toContain('Every seat is in use');
  });
});

describe('what removing a seat costs is said before it happens', () => {
  it('does not show the consequence until a removal is being confirmed', () => {
    expect(render(seatsWithPending)).not.toContain('signs that person out everywhere');
  });
});

describe('errors are the API sentence, not a re-authored one', () => {
  it('renders a supplied problem detail verbatim', () => {
    const html = render(seatsWithPending, {
      initialError:
        'This agency is using 3 of 3 seats. Remove a seat or upgrade the plan before adding another user.',
    });
    expect(html).toContain('This agency is using 3 of 3 seats.');
    expect(html).toContain('role="alert"');
  });

  it('shows no alert before anything has been attempted', () => {
    expect(render(seatsWithPending)).not.toContain('role="alert"');
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = render(seatsWithPending);
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('uses the design system, not a hand-rolled select', () => {
    const html = render(roomToSpareSeats);
    expect(html).toContain('avp-field__select');
    expect(html).toContain('avp-table');
    expect(html).toContain('avp-card');
  });
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LINK = 'https://app.example/invite#INVITESENTINELtoken0123456789';
const NEW_SEAT = 'new.seat@northlight.example';
/** The new-seat notice with email set up: what is certain of the invite, and no claim about an email (3.85). */
const NEW_SEAT_NOTICE = `${NEW_SEAT} has been invited. They hold a seat from now, before they accept.`;
/** Added to it when the API handed the link back, which it does only when it sent no email (3.85). */
const NO_EMAIL_SENTENCE = 'No email was sent. Send them the invitation link yourself.';
const EMAIL_OFF_LINE =
  'Email isn\u2019t set up yet \u2014 send this link yourself. It works once and expires in 7 days.';

/** The API's answer to an invite: a new seat unless `seatConsumed` says otherwise. */
const answer = (acceptUrl: string | null, seatConsumed = true): InviteSeatResponse => ({
  user: {
    id: 'user_01NEWSEAT',
    email: NEW_SEAT,
    fullName: NEW_SEAT,
    role: 'member',
    status: 'invited',
    lastLoginAt: null,
    signInMethods: [],
    createdAt: '2026-09-29T00:00:00Z',
  },
  invitation: {
    id: 'invt_01NEWSEAT',
    email: NEW_SEAT,
    role: 'member',
    expiresAt: '2026-10-06T00:00:00Z',
    createdAt: '2026-09-29T00:00:00Z',
  },
  seats: { used: 3, limit: 5 },
  seatConsumed,
  acceptUrl,
});

/** Let every pending microtask settle — the mocked request resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;
const writeText = vi.fn<(text: string) => Promise<void>>();

/** Mount the panel as an owner with room to spare, and ask nothing of it yet. */
function mounted(): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(
      <SeatsPanel
        agencyId="agcy_01NORTHLIGHT"
        seats={roomToSpareSeats}
        currentUserId={OWNER_ID}
        currentUserRole="owner"
        canManage
        onChanged={() => {}}
      />,
    ),
  );
  return el;
}

/** Mount the panel as an owner with room to spare, invite NEW_SEAT, and wait for the answer. */
async function invited(): Promise<HTMLDivElement> {
  const el = mounted();
  const input = el.querySelector<HTMLInputElement>('input[type="email"]')!;
  act(() => {
    // React tracks the value itself; set it the way a browser would, then
    // announce it, so the controlled field's onChange sees the typing.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, NEW_SEAT);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await settle();
  return el;
}

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

beforeEach(() => {
  inviteSeat.mockReset();
  removeSeat.mockReset();
  writeText.mockReset();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('an invite with email not set up hands the link to the inviter — 2026-09-29', () => {
  it('shows the link once, with the one line and a Copy link button', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    expect(inviteSeat).toHaveBeenCalledWith('agcy_01NORTHLIGHT', NEW_SEAT, 'member');
    expect(el.textContent).toContain(EMAIL_OFF_LINE);
    const field = el.querySelector<HTMLInputElement>('input[readonly]');
    expect(field?.value).toBe(LINK);
    expect(el.querySelector(`label[for="${field?.id}"]`)?.textContent).toBe(
      `Invitation link for ${NEW_SEAT}`,
    );
    expect(button(el, 'Copy link')).toBeDefined();
    // The notice still says what happened to the seat, and claims no sending.
    expect(el.textContent).toContain(`${NEW_SEAT} has been invited.`);
    expect(el.textContent).not.toContain('has been sent');
  });

  it('copies the link, and says so', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await act(async () => {
      button(el, 'Copy link')!.click();
    });
    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(button(el, 'Copied')).toBeDefined();
  });

  it('keeps the link on screen to copy by hand when the clipboard is refused', async () => {
    writeText.mockRejectedValue(new DOMException('Denied', 'NotAllowedError'));
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await act(async () => {
      button(el, 'Copy link')!.click();
    });
    expect(button(el, 'Copy link')).toBeDefined();
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK);
    expect(el.querySelector('[role="alert"]')).toBeNull();
  });

  it('re-inviting does not say a new link was sent — it says to send it', async () => {
    inviteSeat.mockResolvedValue(answer(LINK, false));
    const el = await invited();
    expect(el.textContent).toContain(
      `${NEW_SEAT} already had a pending invitation. The old link no longer works — send them the new one.`,
    );
    expect(el.textContent).not.toContain('has been sent');
    expect(el.querySelector<HTMLInputElement>('input[readonly]')?.value).toBe(LINK);
  });
});

describe('an invite with email set up shows no link', () => {
  it('the API returns none, and the screen invents none', async () => {
    inviteSeat.mockResolvedValue(answer(null));
    const el = await invited();
    expect(el.textContent).toContain(`${NEW_SEAT} has been invited.`);
    expect(el.textContent).not.toContain('Email isn\u2019t set up');
    expect(el.textContent).not.toContain('/invite#');
    expect(el.querySelector('input[readonly]')).toBeNull();
    expect(button(el, 'Copy link')).toBeUndefined();
  });

  it('a re-invite says it tried to email a new link and how to try again, and never that it was sent — 3.85', async () => {
    inviteSeat.mockResolvedValue(answer(null, false));
    const el = await invited();
    expect(noticeSentences(el)).toEqual([
      'new.seat@northlight.example already had a pending invitation. The old link no longer works. We tried to email a new one; if it does not arrive, invite the same address again.',
    ]);
    // The API tries the send and swallows a failure, answering the same either way (email.py), so
    // "has been sent" was a claim it cannot back. Inviting the same address again re-sends: that is
    // the API's own stated recovery.
    expect(noticeRegion(el)!.textContent).not.toMatch(/has been sent|was sent/);
    expect(linkField(el)).toBeNull();
  });
});

/**
 * Copy is announced, as the share bar's is — checklist 3.70 (3.52 did the share bar) — and so is
 * the link's arrival (checklist 3.85).
 *
 * "Copied" renamed a button, which screen readers do not reliably announce. So a copy that worked
 * also says "Link copied." in a status region that is on the page before the link is, and a copy
 * that is refused says nothing: the link is on screen and selectable, and nothing on the panel
 * claims a copy that did not happen, so a refusal after a copy that worked takes the sentence away
 * and puts the button back to "Copy link", as the share bar's does. A link arriving is said in the
 * same region (3.85), in a sentence of its own that a copy leaves alone, so the region holds two
 * paragraphs and is not atomic. The panel's other live region, the notice ("… has been invited"),
 * is not touched by a copy.
 *
 * jsdom has no stylesheet, so "visually hidden" is asserted as the design system's class, whose rule
 * ShareLinkBar.linkReady.test.tsx reads from its source.
 */
const LINK2 = 'https://app.example/invite#SECONDINVITEtoken9876543210';
const COPIED = 'Link copied.';
/**
 * Said when the link arrives (3.85), with where it is: the one read-only field, which follows the
 * form in the document and is below it on screen at every width (checked in a browser, which jsdom
 * cannot do). The days are RETENTION's, as the visible line's are.
 */
const READY = 'Invitation link ready, in the field below the form. It works once and expires in 7 days.';
/** What a removal that worked says in the notice. */
const RELEASED = 'That seat has been released and their sessions have been signed out.';
const REGION = '[data-testid="invite-link-status"]';
/** The notice's own status region (3.85): the paragraph in it is not live, the region is. */
const NOTICE = '[data-testid="seat-notice"]';
const HIDDEN = '.avp-visually-hidden';

const observers: MutationObserver[] = [];

/** The status region that says the invitation link arrived and that a copy of it worked: not the notice, which has one of its own. */
const region = (el: HTMLElement) => el.querySelector<HTMLElement>(REGION);
/** The sentences that region holds, one per paragraph. */
const sentences = (el: HTMLElement) =>
  [...(region(el)?.querySelectorAll('p') ?? [])].map((p) => p.textContent);
/** The paragraph of that region that holds this sentence. */
const sentenceP = (el: HTMLElement, sentence: string) =>
  [...(region(el)?.querySelectorAll('p') ?? [])].find((p) => p.textContent === sentence);
/** The notice's region, and the sentences it holds. */
const noticeRegion = (el: HTMLElement) => el.querySelector<HTMLElement>(NOTICE);
const noticeSentences = (el: HTMLElement) =>
  [...(noticeRegion(el)?.querySelectorAll('p') ?? [])].map((p) => p.textContent);
const linkField = (el: HTMLElement) => el.querySelector<HTMLInputElement>('input[readonly]');

/** What a sighted reader is given: the panel's text with every visually hidden element taken out. */
function sightedText(el: HTMLElement): string {
  const clone = el.cloneNode(true) as HTMLElement;
  for (const hidden of [...clone.querySelectorAll(HIDDEN)]) hidden.remove();
  return clone.textContent ?? '';
}

/**
 * What a screen reader hears from a live region is what the region GAINS. This watches one, and
 * each call to the function it returns gives what changed since the last: the nodes added, the
 * nodes removed, and whether any text was edited in place.
 */
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
      edited: taken.some((r) => r.type === 'characterData'),
    };
  };
}

/** A request that stays out until the test answers it, one way or the other. */
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * How urgently a screen reader is told to read a region: the region's own `aria-live` if it has
 * one, else the one its role carries. `status` is polite of itself and `alert` is assertive, so a
 * region that names an `aria-live` of its own has overridden its role, and this reads that.
 */
const LIVENESS_OF_ROLE: Record<string, string> = { status: 'polite', alert: 'assertive', log: 'polite' };
const liveness = (live: Element) =>
  live.getAttribute('aria-live') ?? LIVENESS_OF_ROLE[live.getAttribute('role') ?? ''] ?? 'off';

/** A real press of Copy, then the clipboard's answer settles. The button is "Copy link" until a copy has worked, and "Copied" after. */
async function pressCopy(el: HTMLElement) {
  const target = button(el, 'Copy link') ?? button(el, 'Copied');
  await act(async () => {
    target!.click();
  });
  await settle();
}

/** Type an address into the invite form of the mounted panel and submit it, without waiting for the answer. */
function startInvite(el: HTMLElement, address = NEW_SEAT) {
  const input = el.querySelector<HTMLInputElement>('input[type="email"]')!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, address);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

/** Invite the same address again from the mounted panel (or another), and wait for the answer. */
async function inviteAgain(el: HTMLElement, address = NEW_SEAT) {
  startInvite(el, address);
  await settle();
}

/**
 * Press Remove on the admin's row (the signed-in owner's own has none) and then confirm it, without
 * waiting for the answer: the caller settles it, or answers a request it left out.
 */
function pressRemove(el: HTMLElement) {
  act(() => button(el, 'Remove')!.click());
  act(() => button(el, 'Yes, remove')!.click());
}

describe('Copy link says so in a status region, not only by renaming the button — 3.70', () => {
  afterEach(() => {
    for (const observer of observers.splice(0)) observer.disconnect();
  });

  it('the region is on the page before there is a link to copy, empty: the sentence is added to it, not made with it', () => {
    // A message made together with its region is often never read out. It is not atomic: after a
    // copy it holds the ready sentence (3.85) and the copied one beside it.
    expect(render(roomToSpareSeats)).toContain(
      '<div role="status" aria-atomic="false" data-testid="invite-link-status"></div>',
    );
    // A member has no invite form, and so no link and no region.
    expect(render(seatsWithPending, { canManage: false })).not.toContain('invite-link-status');
  });

  it('a copy that worked says "Link copied." in the region, and the button says Copied', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    // Not vacuous: the region is on the page, and holds only the link's arrival until Copy has worked.
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([READY]);

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(button(el, 'Copied')).toBeDefined();
    expect(button(el, 'Copy link')).toBeUndefined();
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('says it in a polite status region that was already on the page, and is the same one after', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    const before = region(el);
    expect(before).not.toBeNull();
    // Polite, and not merely "not assertive": `status` is polite of itself, so the region names no
    // `aria-live` of its own (or only "polite"). `assertive` would interrupt what is being read,
    // and `off` would say nothing at all.
    expect(before!.getAttribute('role')).toBe('status');
    expect([null, 'polite']).toContain(before!.getAttribute('aria-live'));
    expect(liveness(before!)).toBe('polite');

    await pressCopy(el);

    // A region made together with its text is often not read out, so this one is there first and
    // gains the sentence. It holds two paragraphs by now, the ready sentence (3.85) and this one,
    // so it is `aria-atomic="false"`: what a screen reader reads is what is added, and "Link
    // copied." does not bring "Invitation link ready…" with it.
    expect(region(el)).toBe(before);
    expect(liveness(before!)).toBe('polite');
    expect(before!.isConnected).toBe(true);
    expect(before!.getAttribute('aria-atomic')).toBe('false');
    expect(before!.querySelectorAll('p')).toHaveLength(2);
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('leaves the notice as it was: the same region, with no change to it', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    // The notice is a live region as well, and a change to it is read out whole.
    const notice = noticeRegion(el);
    expect(notice?.textContent).toContain(`${NEW_SEAT} has been invited.`);
    const text = notice!.textContent;
    const changes = watch(notice!);

    await pressCopy(el);

    // Not vacuous: the copy worked and was said.
    expect(sentences(el)).toEqual([READY, COPIED]);
    const { added, removed, edited } = changes();
    expect(added).toEqual([]);
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
    expect(noticeRegion(el)).toBe(notice);
    expect(notice!.textContent).toBe(text);
  });

  it('is for screen readers: visually hidden, so a sighted reader sees nothing new but the button’s name', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    const before = sightedText(el);
    expect(before).toContain('Copy link');

    await pressCopy(el);

    const copied = sentenceP(el, COPIED);
    expect(copied).toBeDefined();
    expect(copied!.matches(HIDDEN)).toBe(true);
    // Clipped, not taken out of what a screen reader is given.
    expect(copied!.closest('[aria-hidden="true"], [hidden]')).toBeNull();
    expect(region(el)!.closest(HIDDEN)).toBeNull();
    // The one change on screen is the button's own name, as before 3.70.
    expect(sightedText(el)).toBe(before.replace('Copy link', 'Copied'));
    expect(sightedText(el)).not.toContain('Link copied');
  });

  it('is made of the design system and nothing else: the one class is avp-visually-hidden', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await pressCopy(el);

    expect(region(el)?.hasAttribute('class')).toBe(false);
    expect([...region(el)!.querySelectorAll('p')].map((p) => p.className)).toEqual([
      'avp-visually-hidden',
      'avp-visually-hidden',
    ]);
    // The block the link arrives in is not in the static "no ad hoc styling" checks above: they
    // render no link. Checked mounted, with the sentence in it.
    expect(el.innerHTML).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(el.innerHTML).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('sits beside the block the link is in, not inside it: that block is a flex column with a gap, and a region with no height would add one gap to it', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();

    // jsdom has no layout, so this pins the structure that gives none: the region is not a flex item.
    const block = linkField(el)!.closest('.flex-col.gap-3');
    expect(block).not.toBeNull();
    expect(block!.contains(region(el))).toBe(false);
    expect(region(el)!.parentElement?.classList.contains('flex')).toBe(false);
  });

  it('a second copy is announced again: the region gains a new element with the same words', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await pressCopy(el);
    const first = sentenceP(el, COPIED);
    expect(first?.textContent).toBe(COPIED);
    const changes = watch(region(el)!);

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledTimes(2);
    const second = sentenceP(el, COPIED);
    const { added, removed } = changes();
    // The same words in the same element are no change to a live region, so nothing would be read.
    // Each copy puts a NEW element in it, and takes the last one out.
    expect(second).not.toBe(first);
    expect(first!.isConnected).toBe(false);
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(second);
    expect(removed).toHaveLength(1);
    expect(removed[0]).toBe(first);
    // One sentence of each, not more: they do not pile up.
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('a third copy is announced again too', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await pressCopy(el);
    await pressCopy(el);
    const second = sentenceP(el, COPIED);
    expect(second?.textContent).toBe(COPIED);
    const changes = watch(region(el)!);

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledTimes(3);
    expect(sentenceP(el, COPIED)).not.toBe(second);
    expect(changes().added).toHaveLength(1);
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('a copy that is refused says nothing: Copy link stays, no alert is raised, and the link is there to select', async () => {
    writeText.mockRejectedValue(new DOMException('Denied', 'NotAllowedError'));
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    // Not vacuous: the region is there to have said something.
    expect(region(el)).not.toBeNull();
    const changes = watch(region(el)!);

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY]);
    expect(changes().added).toEqual([]);
    expect(el.querySelector('[role="alert"]')).toBeNull();
    // The link is on screen and selectable, which is what the person does instead: a read-only
    // field, not a disabled one, whose text is selected when it takes focus.
    expect(linkField(el)?.value).toBe(LINK);
    expect(linkField(el)?.disabled).toBe(false);
    act(() => linkField(el)!.focus());
    expect([linkField(el)?.selectionStart, linkField(el)?.selectionEnd]).toEqual([0, LINK.length]);
  });

  it('with no clipboard to write to (a page over plain HTTP) it says nothing either', async () => {
    delete (navigator as { clipboard?: unknown }).clipboard;
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    expect(region(el)).not.toBeNull();

    await pressCopy(el);

    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY]);
    expect(el.querySelector('[role="alert"]')).toBeNull();
    expect(linkField(el)?.value).toBe(LINK);
  });

  it('a refusal after a copy that worked takes "Link copied." away with "Copied": it does not claim a copy that did not happen', async () => {
    writeText.mockResolvedValueOnce(undefined);
    writeText.mockRejectedValueOnce(new DOMException('Denied', 'NotAllowedError'));
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    await pressCopy(el);
    // Not vacuous: copied, and said.
    expect(sentences(el)).toEqual([READY, COPIED]);
    expect(button(el, 'Copied')).toBeDefined();
    const changes = watch(region(el)!);

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledTimes(2);
    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(sentences(el)).toEqual([READY]);
    // The region only loses its sentence. What a screen reader reads is what a region gains, so
    // the refusal is not read out: it says nothing, as a refusal of a first copy does.
    const { added, removed } = changes();
    expect(added).toEqual([]);
    expect(removed).toHaveLength(1);
    expect(el.querySelector('[role="alert"]')).toBeNull();
    // The link is on screen and selectable, which is what the person does instead.
    expect(linkField(el)?.value).toBe(LINK);
  });

  it('a link made after a copy starts with Copy link and nothing said of a copy: the copy was of the old link', async () => {
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(LINK2, false));
    const el = await invited();
    await pressCopy(el);
    // Not vacuous: copied, and said.
    expect(sentences(el)).toEqual([READY, COPIED]);
    expect(button(el, 'Copied')).toBeDefined();

    await inviteAgain(el);

    expect(linkField(el)?.value).toBe(LINK2);
    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([READY]);
    expect(el.textContent).not.toContain('Link copied');
  });

  it('once an invite hands back no link, nothing of a copy is left on the page', async () => {
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(null, false));
    const el = await invited();
    await pressCopy(el);
    expect(sentences(el)).toEqual([READY, COPIED]);

    await inviteAgain(el);

    expect(linkField(el)).toBeNull();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain('Link copied');
  });

  it('a removal that worked takes the link away with the region’s sentences: nothing of the link or of a copy is left', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    removeSeat.mockResolvedValue(undefined);
    const el = await invited();
    await pressCopy(el);
    // Not vacuous: the link is on the page, and a copy of it has been said.
    expect(linkField(el)?.value).toBe(LINK);
    expect(sentences(el)).toEqual([READY, COPIED]);

    // Remove asks first, and is offered on another person's row (the admin's here), never the
    // signed-in owner's own.
    act(() => button(el, 'Remove')!.click());
    await act(async () => {
      button(el, 'Yes, remove')!.click();
    });
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(el.textContent).toContain(RELEASED);
    expect(linkField(el)).toBeNull();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(button(el, 'Copy link')).toBeUndefined();
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain('Link copied');
    expect(el.innerHTML).not.toContain('INVITESENTINEL');
  });

  it('a clipboard that answers after the link was replaced does not announce a copy of the new one', async () => {
    const clip = deferred();
    writeText.mockReturnValue(clip.promise);
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(LINK2, false));
    const el = await invited();
    act(() => button(el, 'Copy link')!.click());
    // Not vacuous: the clipboard was asked for the first link and has not answered.
    expect(writeText).toHaveBeenCalledWith(LINK);
    await inviteAgain(el);
    expect(linkField(el)?.value).toBe(LINK2);

    await act(async () => clip.resolve());
    await settle();

    // The newer link was never copied: it says Copy link, and the region says only that it is ready.
    expect(linkField(el)?.value).toBe(LINK2);
    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([READY]);
  });

  it('a clipboard that answers after the link was taken away does not bring it back, or say it was copied', async () => {
    const clip = deferred();
    writeText.mockReturnValue(clip.promise);
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(null, false));
    const el = await invited();
    act(() => button(el, 'Copy link')!.click());
    expect(writeText).toHaveBeenCalledWith(LINK);
    await inviteAgain(el);
    // Not vacuous: the second invite handed back no link, so there is none on the page.
    expect(linkField(el)).toBeNull();

    await act(async () => clip.resolve());
    await settle();

    expect(linkField(el)).toBeNull();
    expect(button(el, 'Copied')).toBeUndefined();
    expect(button(el, 'Copy link')).toBeUndefined();
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    expect(el.textContent).not.toContain('Link copied');
    expect(el.innerHTML).not.toContain('INVITESENTINEL');
  });

  it('a refusal that answers after the link was replaced does not take the new link’s "Link copied." away', async () => {
    const first = deferred();
    writeText.mockReturnValueOnce(first.promise).mockResolvedValueOnce(undefined);
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(LINK2, false));
    const el = await invited();
    act(() => button(el, 'Copy link')!.click());
    // Not vacuous: the clipboard was asked for the first link and has not answered.
    expect(writeText).toHaveBeenCalledWith(LINK);
    await inviteAgain(el);
    await pressCopy(el);
    expect(writeText).toHaveBeenLastCalledWith(LINK2);
    expect(linkField(el)?.value).toBe(LINK2);
    expect(button(el, 'Copied')).toBeDefined();
    expect(sentences(el)).toEqual([READY, COPIED]);

    await act(async () => first.reject(new DOMException('Denied', 'NotAllowedError')));
    await settle();

    // That refusal was of the first link. The new one was copied, and still says so.
    expect(linkField(el)?.value).toBe(LINK2);
    expect(button(el, 'Copied')).toBeDefined();
    expect(button(el, 'Copy link')).toBeUndefined();
    expect(sentences(el)).toEqual([READY, COPIED]);
  });
});

/**
 * Checklist 3.85, the same screen: the invitation link arrives with a status, the new-seat notice
 * says whether an email was sent, the notice is a region that was on the page first, and a removal
 * the API refuses leaves the link alone.
 *
 * The link's arrival is said as a sentence in the region 3.70 made, not by a move of focus: the
 * share bar moves focus (3.33) because the button that was pressed leaves its page, and here the
 * Invite button stays where it is. A move would also be a change a sighted person sees (a ring and a
 * selected link), and a screen reader that follows focus can cut off the notice that says what was
 * not sent.
 */
describe('the arrival of the invitation link is said in that region too — 3.85', () => {
  afterEach(() => {
    for (const observer of observers.splice(0)) observer.disconnect();
  });

  it('an invite that hands back a link adds one sentence to the region that was already there, and edits nothing in place', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = mounted();
    const before = region(el);
    // Not vacuous: the region is on the page first, and says nothing.
    expect(before).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    const changes = watch(before!);

    await inviteAgain(el);

    // The link is there, and the same region has gained the sentence. What a screen reader reads
    // from a live region is what it gains: one new element, and no text edited in place.
    expect(linkField(el)?.value).toBe(LINK);
    expect(region(el)).toBe(before);
    expect(sentences(el)).toEqual([READY]);
    const { added, removed, edited } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(sentenceP(el, READY));
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
  });

  it('says that the link is ready and how long it works, in those words, with the days taken from RETENTION', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    expect(sentences(await invited())).toEqual([
      'Invitation link ready, in the field below the form. It works once and expires in 7 days.',
    ]);
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';

    // A number typed into this sentence would be a second copy of the API's invitation lifetime,
    // which `legalPages.test.tsx` holds RETENTION to. So the sentence reads the same constant.
    const held = RETENTION.invitationDays;
    const mutable = RETENTION as { invitationDays: number };
    try {
      mutable.invitationDays = 1;
      expect(sentences(await invited())).toEqual([
        'Invitation link ready, in the field below the form. It works once and expires in 1 day.',
      ]);
    } finally {
      mutable.invitationDays = held;
    }
  });

  it('is a polite status that is not atomic: a copy is read as what it adds, not together with this sentence', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();

    expect(region(el)!.getAttribute('role')).toBe('status');
    expect(liveness(region(el)!)).toBe('polite');
    // The region holds two paragraphs once a copy has worked. An atomic region would read this
    // sentence again with "Link copied.".
    expect(region(el)!.getAttribute('aria-atomic')).toBe('false');
  });

  it('says where the link is, and it is there: the one read-only field, after the form in the document and not inside it', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    expect(sentences(el)).toEqual([READY]);

    // "The field below the form" has to be one field: Email and Role are in the form, and this is
    // the only read-only one on the panel.
    const fields = el.querySelectorAll<HTMLInputElement>('input[readonly]');
    expect(fields).toHaveLength(1);
    const form = el.querySelector('form')!;
    expect(form.contains(fields[0]!)).toBe(false);
    expect(form.compareDocumentPosition(fields[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Below it on screen is what a layout shows and jsdom has none. It is a sibling after the form
    // in a block container, with nothing that reorders it, and a browser at eleven widths from 320
    // to 1328 px put its top under the form's bottom (3.85, round 2).
  });

  it('is for screen readers: visually hidden, in the design system’s class, so what a sighted reader sees does not change', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();

    const ready = sentenceP(el, READY);
    expect(ready).toBeDefined();
    expect(ready!.className).toBe('avp-visually-hidden');
    expect(ready!.matches(HIDDEN)).toBe(true);
    // Clipped, not taken out of what a screen reader is given.
    expect(ready!.closest('[aria-hidden="true"], [hidden]')).toBeNull();
    expect(el.textContent).toContain(READY);
    // The visible line says the same of the link's life, once, and that is all a sighted reader has.
    expect(sightedText(el)).not.toContain('Invitation link ready');
    expect(sightedText(el)).toContain(EMAIL_OFF_LINE);
  });

  it('a copy adds "Link copied." beside it and leaves it alone: the same element, still in place, not read again', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();
    const ready = sentenceP(el, READY);
    expect(ready).toBeDefined();
    const changes = watch(region(el)!);

    await pressCopy(el);

    expect(sentenceP(el, READY)).toBe(ready);
    expect(ready!.isConnected).toBe(true);
    const { added, removed, edited } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(sentenceP(el, COPIED));
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
  });

  it('a link made again is announced again: the region takes the old sentence out and adds a new element with the same words', async () => {
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockResolvedValueOnce(answer(LINK2, false));
    const el = await invited();
    const first = sentenceP(el, READY);
    expect(first).toBeDefined();
    const changes = watch(region(el)!);

    await inviteAgain(el);

    expect(linkField(el)?.value).toBe(LINK2);
    const second = sentenceP(el, READY);
    // The same words in the same element are no change to a live region, so nothing would be read.
    expect(second).not.toBe(first);
    expect(first!.isConnected).toBe(false);
    const { added, removed } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(second);
    expect(removed).toHaveLength(1);
    expect(removed[0]).toBe(first);
    expect(sentences(el)).toEqual([READY]);
  });

  it('an invite that hands back no link (email is set up) says nothing in the region', async () => {
    inviteSeat.mockResolvedValue(answer(null));
    const el = mounted();
    expect(region(el)).not.toBeNull();
    const changes = watch(region(el)!);

    await inviteAgain(el);

    // Not vacuous: the invite went through and the notice has been said.
    expect(noticeSentences(el)).toEqual([NEW_SEAT_NOTICE]);
    expect(linkField(el)).toBeNull();
    expect(sentences(el)).toEqual([]);
    expect(changes().added).toEqual([]);
  });

  it('is a sentence and not a move of focus: the person stays where they were, and the link is the next stop', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = mounted();
    const email = el.querySelector<HTMLInputElement>('input[type="email"]')!;
    act(() => email.focus());
    // Not vacuous: focus is in the panel, and a move of it would be seen.
    expect(document.activeElement).toBe(email);

    await inviteAgain(el);

    expect(linkField(el)?.value).toBe(LINK);
    expect(document.activeElement).not.toBe(linkField(el));
    expect(document.activeElement).not.toBe(button(el, 'Copy link'));
  });
});

describe('the new-seat notice says whether an email was sent — 3.85', () => {
  it('with email not set up the API hands the link back, and the notice says no email was sent and to send the link', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = await invited();

    expect(noticeSentences(el)).toEqual([
      'new.seat@northlight.example has been invited. They hold a seat from now, before they accept. No email was sent. Send them the invitation link yourself.',
    ]);
    // The link is on the page for it to be sent from, with the line that sits beside it.
    expect(linkField(el)?.value).toBe(LINK);
    expect(el.textContent).toContain(EMAIL_OFF_LINE);
  });

  it('with email set up the API hands nothing back, and the notice claims the invite and the seat and says nothing of an email', async () => {
    inviteSeat.mockResolvedValue(answer(null));
    const el = await invited();

    expect(noticeSentences(el)).toEqual([
      'new.seat@northlight.example has been invited. They hold a seat from now, before they accept.',
    ]);
    // The API does not say whether the provider took the message (a failed send is logged on its
    // side and the response is the same), so the web claims neither that one went nor that none did.
    expect(noticeRegion(el)!.textContent).not.toMatch(/email|sent/i);
  });

  it.each([
    ['the link handed back', LINK],
    ['no link handed back', null],
  ])('never calls the link the only way in (%s): a person with an invited seat can also accept it by signing in with Google', async (_case, acceptUrl) => {
    inviteSeat.mockResolvedValue(answer(acceptUrl));
    const el = await invited();

    expect(noticeSentences(el)).toHaveLength(1);
    expect(noticeRegion(el)!.textContent).not.toMatch(/only|sole/i);
  });
});

describe('the seat panel’s notice is said in a status region that is on the page first — 3.85', () => {
  afterEach(() => {
    for (const observer of observers.splice(0)) observer.disconnect();
  });

  it('is on the page before there is anything to say, empty, for a person who can manage seats; a member has none', () => {
    expect(render(roomToSpareSeats)).toContain('<div role="status" data-testid="seat-notice"></div>');
    // A member cannot invite or remove, so nothing can be said to them.
    expect(render(seatsWithPending, { canManage: false })).not.toContain('seat-notice');
  });

  it('is polite, and is a status and not the alert', () => {
    const el = mounted();

    expect(noticeRegion(el)!.getAttribute('role')).toBe('status');
    expect(liveness(noticeRegion(el)!)).toBe('polite');
    expect(noticeRegion(el)!.closest('[role="alert"]')).toBeNull();
  });

  it('an invite adds the notice to the region that was already there, and the paragraph is no live region of its own', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = mounted();
    const before = noticeRegion(el);
    // Not vacuous: the region is on the page first, and says nothing.
    expect(before).not.toBeNull();
    expect(noticeSentences(el)).toEqual([]);
    const changes = watch(before!);

    await inviteAgain(el);

    expect(noticeRegion(el)).toBe(before);
    expect(noticeSentences(el)).toEqual([`${NEW_SEAT_NOTICE} ${NO_EMAIL_SENTENCE}`]);
    const { added, removed, edited } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(before!.querySelector('p'));
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
    // Before 3.85 the paragraph was the live region, made together with its text.
    expect(before!.querySelector('p')!.hasAttribute('role')).toBe(false);
    expect(el.querySelectorAll('p[role="status"]')).toHaveLength(0);
  });

  it('a removal that worked says what it did in the same region', async () => {
    removeSeat.mockResolvedValue(undefined);
    const el = mounted();
    const before = noticeRegion(el);
    expect(noticeSentences(el)).toEqual([]);
    const changes = watch(before!);

    pressRemove(el);
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    expect(noticeRegion(el)).toBe(before);
    expect(noticeSentences(el)).toEqual([RELEASED]);
    expect(changes().added).toHaveLength(1);
  });

  it('the next action takes the last notice out when it starts and adds its own when it ends, so the same words twice are read twice', async () => {
    removeSeat.mockResolvedValue(undefined);
    const el = mounted();
    pressRemove(el);
    await settle();
    const first = noticeRegion(el)!.querySelector('p');
    expect(first?.textContent).toBe(RELEASED);

    const out = deferred();
    removeSeat.mockReturnValue(out.promise);
    pressRemove(el);

    // While the request is out the region is there and empty: the last sentence has gone.
    expect(noticeRegion(el)).not.toBeNull();
    expect(noticeSentences(el)).toEqual([]);
    expect(first!.isConnected).toBe(false);

    await act(async () => out.resolve());
    await settle();

    // The same words, in a new element.
    const second = noticeRegion(el)!.querySelector('p');
    expect(second?.textContent).toBe(RELEASED);
    expect(second).not.toBe(first);
    expect(noticeSentences(el)).toEqual([RELEASED]);
  });

  it('a refused invite leaves it empty and says why in the alert, as before', async () => {
    inviteSeat.mockRejectedValue(
      new ApiProblem({
        type: '/problems/seat-limit-reached',
        title: 'Seat limit reached',
        status: 409,
        detail: 'This agency is using 5 of 5 seats.',
        instance: '/api/v1/agencies/agcy_01NORTHLIGHT/invitations',
      }),
    );
    const el = mounted();

    await inviteAgain(el);

    expect(inviteSeat).toHaveBeenCalledTimes(1);
    expect(noticeRegion(el)).not.toBeNull();
    expect(noticeSentences(el)).toEqual([]);
    expect(el.querySelector('[role="alert"]')?.textContent).toBe('This agency is using 5 of 5 seats.');
  });

  it('an owner invite the API refuses shows the API’s own sentence in the alert', async () => {
    inviteSeat.mockRejectedValue(
      new ApiProblem({
        type: '/problems/permission-denied',
        title: 'Permission denied',
        status: 403,
        detail: 'Only an owner can invite someone as an owner.',
        instance: '/api/v1/agencies/agcy_01NORTHLIGHT/invitations',
      }),
    );
    const el = mounted();

    await inviteAgain(el);

    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'Only an owner can invite someone as an owner.',
    );
  });

  it('adds no gap while it is empty: it is outside the panel’s flex column, which has one, and its sentence brings the space the gap gave it', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    const el = mounted();

    // jsdom has no layout, so this pins the structure that gives none: the region is not a flex
    // item of the column the cards are in, because an empty flex item still has a gap beside it.
    const column = el.querySelector('.flex-col.gap-6');
    expect(column).not.toBeNull();
    expect(column!.querySelector('.avp-card')).not.toBeNull();
    expect(column!.contains(noticeRegion(el))).toBe(false);
    expect(noticeRegion(el)!.parentElement?.classList.contains('flex')).toBe(false);

    await inviteAgain(el);

    expect(noticeRegion(el)!.querySelector('p')!.classList.contains('mt-6')).toBe(true);
  });
});

describe('a removal that fails keeps the invitation link — 3.85', () => {
  afterEach(() => {
    for (const observer of observers.splice(0)) observer.disconnect();
  });

  const REFUSED = 'You cannot remove the last owner of an agency.';
  const refusal = () =>
    new ApiProblem({
      type: '/problems/conflict',
      title: 'Conflict',
      status: 409,
      detail: REFUSED,
      instance: `/api/v1/users/${ADMIN_ID}`,
    });

  it('the link, its Copy button and what a copy said are all still there, and the region is not touched', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    removeSeat.mockRejectedValue(refusal());
    const el = await invited();
    await pressCopy(el);
    // Not vacuous: the link is on the page and a copy of it has been said.
    expect(linkField(el)?.value).toBe(LINK);
    expect(sentences(el)).toEqual([READY, COPIED]);
    const changes = watch(region(el)!);

    pressRemove(el);
    await settle();

    expect(removeSeat).toHaveBeenCalledWith(ADMIN_ID);
    // The API's sentence is said ...
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(REFUSED);
    // ... and the link, which the API handed over once and will not hand over again, has not gone
    // with the refusal.
    expect(linkField(el)?.value).toBe(LINK);
    expect(button(el, 'Copied')).toBeDefined();
    expect(el.textContent).toContain(EMAIL_OFF_LINE);
    expect(sentences(el)).toEqual([READY, COPIED]);
    const { added, removed, edited } = changes();
    expect(added).toEqual([]);
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
  });

  it('the link can still be copied after the refusal', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    removeSeat.mockRejectedValue(refusal());
    const el = await invited();
    pressRemove(el);
    await settle();
    // Not vacuous: the removal was refused, and the link is there to copy.
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(REFUSED);
    expect(button(el, 'Copy link')).toBeDefined();

    await pressCopy(el);

    expect(writeText).toHaveBeenCalledWith(LINK);
    expect(sentences(el)).toEqual([READY, COPIED]);
  });

  it('a removal that fails for want of an answer, and not by a refusal, keeps it too', async () => {
    inviteSeat.mockResolvedValue(answer(LINK));
    removeSeat.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await invited();

    pressRemove(el);
    await settle();

    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'The change did not go through. Check your connection and try again.',
    );
    expect(linkField(el)?.value).toBe(LINK);
    expect(sentences(el)).toEqual([READY]);
  });

  it('while the removal is out the link stays, and it goes only when the removal has worked', async () => {
    const out = deferred();
    inviteSeat.mockResolvedValue(answer(LINK));
    removeSeat.mockReturnValue(out.promise);
    const el = await invited();

    pressRemove(el);

    // Out: nothing has been decided, so nothing is taken away.
    expect(button(el, 'Removing…')).toBeDefined();
    expect(linkField(el)?.value).toBe(LINK);
    expect(sentences(el)).toEqual([READY]);

    await act(async () => out.resolve());
    await settle();

    expect(el.textContent).toContain(RELEASED);
    expect(linkField(el)).toBeNull();
    expect(sentences(el)).toEqual([]);
  });
});

/**
 * Checklist 3.85, round 2: a link stays on the page until another replaces it. An invite the API
 * refuses (409 seat limit, 409 already registered, 422) or one that gets no answer used to wipe an
 * earlier link that was still valid, and the API hands a link over once and never again. Now an
 * invite that hands back a link, or none, replaces it, and a removal that works takes it away.
 * Nothing else clears it.
 */
describe('an invite that does not work leaves the earlier link alone — 3.85', () => {
  afterEach(() => {
    for (const observer of observers.splice(0)) observer.disconnect();
  });

  const OTHER = 'other.person@northlight.example';
  const problem = (type: string, title: string, status: number, detail: string) =>
    new ApiProblem({
      type,
      title,
      status,
      detail,
      instance: '/api/v1/agencies/agcy_01NORTHLIGHT/invitations',
    });

  it.each([
    [
      'the seat limit is reached (409)',
      problem('/problems/seat-limit-reached', 'Seat limit reached', 409, 'This agency is using 5 of 5 seats.'),
      'This agency is using 5 of 5 seats.',
    ],
    [
      'the address already has an account (409)',
      problem(
        '/problems/email-already-registered',
        'That email address is already registered',
        409,
        'That email address is already associated with an account.',
      ),
      'That email address is already associated with an account.',
    ],
    [
      'the address is not valid (422)',
      problem('/problems/validation-failed', 'Request validation failed', 422, 'The email address is not valid.'),
      'The email address is not valid.',
    ],
    [
      'there is no answer',
      new TypeError('Failed to fetch'),
      'The change did not go through. Check your connection and try again.',
    ],
  ])('when %s, the link, "Copied" and the region are as they were', async (_why, failure, said) => {
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockRejectedValueOnce(failure);
    const el = await invited();
    await pressCopy(el);
    // Not vacuous: the link is on the page, and a copy of it has been said.
    expect(linkField(el)?.value).toBe(LINK);
    expect(button(el, 'Copied')).toBeDefined();
    expect(sentences(el)).toEqual([READY, COPIED]);
    const ready = sentenceP(el, READY);
    const changes = watch(region(el)!);

    await inviteAgain(el, OTHER);

    expect(inviteSeat).toHaveBeenLastCalledWith('agcy_01NORTHLIGHT', OTHER, 'member');
    expect(el.querySelector('[role="alert"]')?.textContent).toBe(said);
    // The API handed that link over once and will not again, and it still works.
    expect(linkField(el)?.value).toBe(LINK);
    expect(button(el, 'Copied')).toBeDefined();
    expect(el.textContent).toContain(EMAIL_OFF_LINE);
    expect(sentences(el)).toEqual([READY, COPIED]);
    expect(sentenceP(el, READY)).toBe(ready);
    const { added, removed, edited } = changes();
    expect(added).toEqual([]);
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
  });

  it('while a second invite is out the earlier link stays, and the link that arrives replaces it, as a new element in the region', async () => {
    const out = deferred<InviteSeatResponse>();
    inviteSeat.mockResolvedValueOnce(answer(LINK)).mockReturnValueOnce(out.promise);
    const el = await invited();
    await pressCopy(el);
    const first = sentenceP(el, READY);
    expect(first).toBeDefined();
    const changes = watch(region(el)!);

    startInvite(el, OTHER);

    // Out: nothing has been decided, so nothing is taken away and nothing is said.
    expect(button(el, 'Inviting…')).toBeDefined();
    expect(linkField(el)?.value).toBe(LINK);
    expect(sentences(el)).toEqual([READY, COPIED]);
    expect(changes()).toEqual({ added: [], removed: [], edited: false });

    await act(async () => out.resolve(answer(LINK2, false)));
    await settle();

    // The new link replaces it and its copy state starts again. Its sentence is a new element, so
    // it is read: the same words in the same element would be no change to the region.
    expect(linkField(el)?.value).toBe(LINK2);
    expect(button(el, 'Copy link')).toBeDefined();
    expect(button(el, 'Copied')).toBeUndefined();
    const second = sentenceP(el, READY);
    expect(second).not.toBe(first);
    expect(first!.isConnected).toBe(false);
    expect(sentences(el)).toEqual([READY]);
    const { added } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(second);
  });
});
