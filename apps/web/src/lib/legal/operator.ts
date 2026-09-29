/**
 * Who runs the service, for the privacy and terms pages — 2026-09-29.
 *
 * Supplied by the founder on 2026-09-29, in answer to the brief that built
 * those pages; none of it is in the repository anywhere else. The ABN is
 * checked against the ATO's checksum by `legalPages.test.tsx`, so a typo here
 * fails the suite rather than going live.
 *
 * DRAFTS FOR THE FOUNDER'S REVIEW. Both pages were written by an agent that
 * is not a lawyer, from what the code does, and should be reviewed by one
 * before real customers pay. The draft status lives here and in the build
 * log, never on the rendered pages.
 */

export const OPERATOR = {
  name: 'Priyansh Ranpura',
  // As the founder described the business: an individual trading on their
  // own account, not a company.
  trading: 'a sole trader',
  city: 'Melbourne',
  state: 'Victoria',
  country: 'Australia',
  abn: '89 415 092 182',
  email: 'priyansh.ranpura9@gmail.com',
} as const;

/** When the privacy and terms pages last changed, as an ISO date. */
export const LEGAL_UPDATED = '2026-09-29';
