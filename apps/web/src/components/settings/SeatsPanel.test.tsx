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

const inviteSeat =
  vi.fn<(agencyId: string, email: string, role?: UserRole) => Promise<InviteSeatResponse>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      inviteSeat: (agencyId: string, email: string, role?: UserRole) =>
        inviteSeat(agencyId, email, role),
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

const LINK = 'https://app.example/invite/INVITESENTINELtoken0123456789';
const NEW_SEAT = 'new.seat@northlight.example';
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

/** Mount the panel as an owner with room to spare, invite NEW_SEAT, and wait for the answer. */
async function invited(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(
      <SeatsPanel
        agencyId="agcy_01NORTHLIGHT"
        seats={roomToSpareSeats}
        currentUserId={OWNER_ID}
        canManage
        onChanged={() => {}}
      />,
    ),
  );
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
    expect(el.textContent).not.toContain('/invite/');
    expect(el.querySelector('input[readonly]')).toBeNull();
    expect(button(el, 'Copy link')).toBeUndefined();
  });

  it('a re-invite still says the new link was sent, because it was', async () => {
    inviteSeat.mockResolvedValue(answer(null, false));
    const el = await invited();
    expect(el.textContent).toContain('A new link has been sent and the old one no longer works.');
  });
});
