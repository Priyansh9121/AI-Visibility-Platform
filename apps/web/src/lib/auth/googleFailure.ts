/**
 * The front door's Google failure notice — Epic 20, widened 2026-09-21, and
 * given a second voice on 2026-09-29 for a reader who is still signed in.
 *
 * The API puts one WORD in `/?google=error&reason=…` and nothing else (no
 * token, no email, no Google error text); this is where the word becomes a
 * sentence. Pure and exported so the mapping is tested directly — `Home`
 * reads the query in an effect, which a static render never runs (its own
 * test mounts it in jsdom for the part only a mount can show).
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
 *
 * `busy` is new on 2026-10-22: the API's ceiling on Google sign-in starts for
 * the whole deployment (hardening A6 part 2). Like `rate-limited` it is not
 * about this person, and unlike it, it is not about their network.
 */
export const GOOGLE_REASONS: Record<string, string> = {
  denied: 'Google sign-in was cancelled. Nothing changed.',
  'invalid-state':
    'That Google sign-in had expired or was already used. Start it again from this page.',
  'browser-mismatch':
    'That Google sign-in could not be matched to this browser. Start it again from this page.',
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
  'rate-limited':
    'Too many sign-in attempts from this network. Wait a few minutes and try again.',
  busy: 'Google sign-in is busy right now. Wait a few minutes and try again, or sign in with your email and password.',
  unavailable: 'Sign-in is temporarily unavailable. Try again in a moment.',
  'not-configured': 'Google sign-in is not set up on this server yet. Sign in with your email and password.',
};

/**
 * The same words, for somebody still SIGNED IN — 2026-09-29.
 *
 * Since the free scan began to need a Google sign-in, the scan-limit notice
 * sends a signed-in user to Google to link this account (Continue with
 * Google, on `SCAN_LIMIT_VERIFY_REQUIRED`), so a failure now lands on `/`
 * with the session still live. The sentences above were written for the
 * sign-in page — "sign in with your email and password", "start it again
 * from this page" — which is not where this reader is, and not what they
 * were doing: they were linking, not signing in. `denied` and
 * `exchange-failed` already read true for them and have no entry here.
 *
 * The session proves this person's own account is active and not deleted,
 * so a refusal about an account's state — `email-claimed`,
 * `account-suspended`, `invitation-expired`, `account-unavailable` — is about
 * the account at the address of the Google account they CHOSE: somebody
 * else's row here, not theirs. The way out is the same for all four, so each
 * ends with it, naming this sign-in's own address — the one Google has to be
 * signed in as for the link to happen.
 */
export const GOOGLE_REASONS_SIGNED_IN: Record<string, (address: string) => string> = {
  'invalid-state': (address) =>
    `That Google sign-in had expired or was already used, so nothing was linked. Continue with Google again and choose ${address}.`,
  'browser-mismatch': (address) =>
    `That Google sign-in could not be matched to this browser, so nothing was linked. Continue with Google again and choose ${address}.`,
  'email-unverified': (address) =>
    `Google has not verified the address on the Google account you chose, so nothing was linked. Continue with Google again and choose ${address}.`,
  'account-suspended': (address) =>
    `The Google account you chose belongs to a suspended account here, so nothing was linked. Continue with Google again and choose ${address}.`,
  'email-claimed': (address) =>
    `The address on the Google account you chose already belongs to another account here, so nothing was linked. Continue with Google again and choose ${address}.`,
  'invitation-expired': (address) =>
    `The Google account you chose has an expired invitation to another agency here, so nothing was linked. Continue with Google again and choose ${address}.`,
  'account-unavailable': (address) =>
    `The Google account you chose cannot be used here, so nothing was linked. Continue with Google again and choose ${address}.`,
  busy: () =>
    'Google sign-in is busy right now, so nothing was linked. Wait a few minutes and try again.',
  unavailable: () =>
    'Google sign-in is temporarily unavailable, so nothing was linked. Try again in a moment.',
  'not-configured': () =>
    'Google sign-in is not set up on this server yet, so this account cannot be linked to Google.',
};

export const GOOGLE_FALLBACK = 'Google sign-in did not complete. Try again.';

/**
 * The sentence for one reason word; the fallback for a word this build does
 * not know. `signedInAs` is the signed-in user's own address, when there is
 * one: it picks the signed-in wording where a reason has one.
 *
 * The word comes off the address bar, so it is anything a person or a link
 * puts there. Each map is read only through `Object.hasOwn`: a plain object
 * also answers to the names it inherits (`constructor`, `toString`,
 * `__proto__`), which are not reasons and must give the fallback.
 */
export function googleReasonSentence(reason: string, signedInAs?: string): string {
  if (signedInAs !== undefined && Object.hasOwn(GOOGLE_REASONS_SIGNED_IN, reason)) {
    return GOOGLE_REASONS_SIGNED_IN[reason]!(signedInAs);
  }
  if (Object.hasOwn(GOOGLE_REASONS, reason)) return GOOGLE_REASONS[reason]!;
  return GOOGLE_FALLBACK;
}

/**
 * The reason WORD off a page's query string. Null unless the query carries
 * `google=error`; the empty string when `reason` is absent, which reads as
 * the fallback. A word rather than a sentence, because which sentence it
 * becomes waits on whether the session check that follows succeeds.
 */
export function googleFailureReason(search: string): string | null {
  const params = new URLSearchParams(search);
  if (params.get('google') !== 'error') return null;
  return params.get('reason') ?? '';
}

/** Read a failure off a page's query string, as a sentence. See `googleFailureReason`. */
export function googleFailureFromSearch(search: string, signedInAs?: string): string | null {
  const reason = googleFailureReason(search);
  return reason === null ? null : googleReasonSentence(reason, signedInAs);
}

/**
 * Take the reason off the address bar: read it, then clear the query, so a
 * reload or a back-press does not show the notice twice. The window is a
 * parameter so the clearing is tested, not assumed.
 */
export function takeGoogleFailure(
  win: { location: Pick<Location, 'search' | 'pathname'>; history: Pick<History, 'replaceState'> },
): string | null {
  const reason = googleFailureReason(win.location.search);
  if (reason === null) return null;
  win.history.replaceState(null, '', win.location.pathname);
  return reason;
}
