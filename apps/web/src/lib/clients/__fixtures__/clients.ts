/**
 * Client-list fixtures — Epic 9.14 (Part E backfill).
 *
 * Shaped to match `GET /clients`, including the two rows that are easy to
 * render wrongly: one that classification could not read (`industry: null`,
 * which must not become a bare dash) and one still pending.
 */

import type { Client, Me } from '@avp/shared-types';

function client(overrides: Partial<Client> & Pick<Client, 'id' | 'name' | 'domain'>): Client {
  return {
    agencyId: 'agcy_01NORTHLIGHT',
    kind: 'client',
    brandName: null,
    industry: null,
    industryNiche: null,
    classificationStatus: 'classified',
    classificationReasonCode: null,
    industryConfidence: null,
    industryConfidenceScore: null,
    classifiedAt: null,
    classifierModel: null,
    createdAt: '2026-08-27T09:00:00Z',
    updatedAt: '2026-08-27T09:00:00Z',
    ...overrides,
  } as Client;
}

export const identifiedClient = client({
  id: 'clnt_01AAA',
  name: 'northaven-dental.com',
  domain: 'northaven-dental.com',
  brandName: 'Northaven Dental',
  industry: 'Dental practice',
  classificationStatus: 'classified',
});

/** Classification could not read the site. `industry` is null. */
export const unreadableClient = client({
  id: 'clnt_01BBB',
  name: 'quietbrook.io',
  domain: 'quietbrook.io',
  industry: null,
  classificationStatus: 'unclassifiable',
  classificationReasonCode: 'CRAWL_FAILED',
});

export const ambiguousClient = client({
  id: 'clnt_01CCC',
  name: 'meridian-labs.com',
  domain: 'meridian-labs.com',
  brandName: 'Meridian Labs',
  industry: 'Software',
  classificationStatus: 'ambiguous',
  industryConfidence: 'low',
});

export const pendingClient = client({
  id: 'clnt_01DDD',
  name: 'not-yet-read.com',
  domain: 'not-yet-read.com',
  classificationStatus: 'pending',
});

export const oneClient = [identifiedClient];
export const severalClients = [
  identifiedClient,
  unreadableClient,
  ambiguousClient,
  pendingClient,
];

export const clientsMe: Me = {
  user: {
    id: 'user_01OWNER',
    email: 'dana@northlight.example',
    fullName: 'Dana Whitfield',
    role: 'owner',
    status: 'active',
    lastLoginAt: '2026-08-28T09:00:00Z',
    createdAt: '2026-08-20T05:29:02Z',
  },
  agency: {
    id: 'agcy_01NORTHLIGHT',
    name: 'Northlight Partners',
    slug: 'northlight-partners',
    seatLimit: 3,
    createdAt: '2026-08-20T05:29:02Z',
  },
  seats: { used: 2, limit: 3 },
};
