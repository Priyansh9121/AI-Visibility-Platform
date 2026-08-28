/**
 * Seat management — Epic 9.14.
 *
 * The assertions here are the ones `test_seat_endpoints.py` proves at the API
 * boundary, re-checked at the point they reach a person. A backend that is
 * careful about the difference between a seat consumed and a link re-issued is
 * worth nothing if the screen reports both as "invited"; a backend that refuses
 * to invent a display name is worth nothing if the screen invents one anyway.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SeatsPanel } from './SeatsPanel';
import {
  ADMIN_ID,
  OWNER_ID,
  ownerOnlySeats,
  roomToSpareSeats,
  seatsWithPending,
} from '@/lib/settings/__fixtures__/seats';
import type { SeatList } from '@avp/shared-types';

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
    expect(html).not.toContain('Send invitation');
    expect(html).not.toContain('Invite someone');
  });
});

describe('the invite form', () => {
  it('collects an email and a role', () => {
    const html = render(roomToSpareSeats);
    expect(html).toContain('>Email<');
    expect(html).toContain('>Role<');
    expect(html).toContain('Send invitation');
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
    expect(render(seatsWithPending)).toMatch(/Send invitation[\s\S]{0,80}?$|disabled/);
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
