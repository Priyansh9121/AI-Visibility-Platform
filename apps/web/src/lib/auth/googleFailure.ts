/**
 * The front door's Google failure notice — Epic 20, widened 2026-09-21.
 *
 * The API puts one WORD in `/?google=error&reason=…` and nothing else (no
 * token, no email, no Google error text); this is where the word becomes a
 * sentence. Pure and exported so the mapping is tested directly — `Home`
 * reads the query in an effect, which a static render never runs.
 *
 * Four of these words are new on 2026-09-21. Until then the API answered
 * `account-unavailable` for a suspended account, an address linked to a
 * different Google account, and an invitation that had lapsed alike, on the
 * argument that the front door should not say which. Reversed: the person
 * reading this sentence has just proven, through Google, that they hold the
 * very address in question, so each of those sentences tells the address's
 * own holder about their own seat and tells nobody else anything. A removed
 * account stays `account-unavailable`, and `unavailable` is the API saying
 * something under it failed, not a judgement about the person.
 */
export const GOOGLE_REASONS: Record<string, string> = {
  denied: 'Google sign-in was cancelled. Nothing changed.',
  'invalid-state':
    'That Google sign-in had expired or was already used. Start it again from this page.',
  'exchange-failed': 'Google did not confirm the sign-in. Try again in a moment.',
  'email-unverified':
    'Google has not verified that email address, so it cannot be used to sign in here.',
  'account-suspended':
    "That account has been suspended. Contact the agency's owner to have it restored.",
  'email-claimed':
    'That email address is already linked to a different Google account. Sign in with the Google account you linked before, or sign in with your email and password.',
  'invitation-expired':
    'Your invitation to that agency has expired. Ask whoever invited you to send it again.',
  'account-unavailable':
    'That Google account cannot sign in here. If it is yours, sign in with your email and password, or reset the password.',
  unavailable: 'Sign-in is temporarily unavailable. Try again in a moment.',
  'not-configured': 'Google sign-in is not set up on this server yet. Sign in with your email and password.',
};

export const GOOGLE_FALLBACK = 'Google sign-in did not complete. Try again.';

/** The sentence for one reason word; the fallback for a word this build does not know. */
export function googleReasonSentence(reason: string): string {
  return GOOGLE_REASONS[reason] ?? GOOGLE_FALLBACK;
}

/**
 * Read a failure off a page's query string. Returns null unless the query
 * carries `google=error`; `reason` may be absent, in which case the fallback.
 */
export function googleFailureFromSearch(search: string): string | null {
  const params = new URLSearchParams(search);
  if (params.get('google') !== 'error') return null;
  return googleReasonSentence(params.get('reason') ?? '');
}
