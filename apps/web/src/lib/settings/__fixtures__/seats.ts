/**
 * Seat-management fixtures — Epic 9.14.
 *
 * Shaped to match what `GET /agencies/{id}/seats` actually returns, including
 * the two things about it that are easy to render wrongly: an INVITED member
 * whose `fullName` IS their email address (the backend refuses to invent a
 * display name from the local part), and the same person appearing in both
 * `members` and `invitations`.
 */

import type { Me, SeatList } from '@avp/shared-types';

const OWNER = {
  id: 'user_01OWNER',
  email: 'dana@northlight.example',
  fullName: 'Dana Whitfield',
  role: 'owner' as const,
  status: 'active' as const,
  lastLoginAt: '2026-08-28T09:00:00Z',
  createdAt: '2026-08-20T05:29:02Z',
};

const ADMIN = {
  id: 'user_01ADMIN',
  email: 'sam@northlight.example',
  fullName: 'Sam Okafor',
  role: 'admin' as const,
  status: 'active' as const,
  lastLoginAt: '2026-08-27T14:11:00Z',
  createdAt: '2026-08-24T11:02:00Z',
};

/** An invited seat. `fullName` is the address, exactly as the API sends it. */
const PENDING = {
  id: 'user_01PENDING',
  email: 'rae@northlight.example',
  fullName: 'rae@northlight.example',
  role: 'member' as const,
  status: 'invited' as const,
  lastLoginAt: null,
  createdAt: '2026-08-28T09:12:00Z',
};

export const ownerOnlySeats: SeatList = {
  members: [OWNER],
  invitations: [],
  seats: { used: 1, limit: 3 },
};

export const seatsWithPending: SeatList = {
  members: [OWNER, ADMIN, PENDING],
  invitations: [
    {
      id: 'invt_01PENDING',
      email: 'rae@northlight.example',
      role: 'member',
      expiresAt: '2026-09-04T09:12:00Z',
      createdAt: '2026-08-28T09:12:00Z',
    },
  ],
  seats: { used: 3, limit: 3 },
};

export const roomToSpareSeats: SeatList = {
  members: [OWNER, ADMIN],
  invitations: [],
  seats: { used: 2, limit: 5 },
};

export const ownerMe: Me = {
  user: OWNER,
  agency: {
    id: 'agcy_01NORTHLIGHT',
    name: 'Northlight Partners',
    slug: 'northlight-partners',
    seatLimit: 3,
    createdAt: '2026-08-20T05:29:02Z',
  },
  seats: { used: 3, limit: 3 },
};

export const memberMe: Me = {
  ...ownerMe,
  user: {
    id: 'user_01MEMBER',
    email: 'kit@northlight.example',
    fullName: 'Kit Alvarez',
    role: 'member',
    status: 'active',
    lastLoginAt: '2026-08-28T08:00:00Z',
    createdAt: '2026-08-26T09:00:00Z',
  },
};

export const OWNER_ID = OWNER.id;
export const ADMIN_ID = ADMIN.id;
export const PENDING_ID = PENDING.id;
