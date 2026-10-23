/**
 * The Google failure notice's word-to-sentence mapping — 2026-09-21. Every
 * reason the API can put in `/?google=error&reason=…` has its own sentence,
 * the four causes that used to share one are told apart, and an unknown
 * word falls back rather than rendering nothing.
 */
import { describe, it, expect } from 'vitest';
import {
  GOOGLE_FALLBACK,
  GOOGLE_REASONS,
  GOOGLE_REASONS_SIGNED_IN,
  googleFailureFromSearch,
  googleFailureReason,
  googleReasonSentence,
  takeGoogleFailure,
} from './googleFailure';

const API_REASONS = [
  'denied',
  'invalid-state',
  'browser-mismatch',
  'exchange-failed',
  'email-unverified',
  'account-suspended',
  'email-claimed',
  'invitation-expired',
  'account-unavailable',
  'unavailable',
  'rate-limited',
  'busy',
  'not-configured',
];

describe('googleReasonSentence', () => {
  it('knows every reason the API emits, each with a sentence', () => {
    for (const reason of API_REASONS) {
      expect(GOOGLE_REASONS[reason], reason).toBeTruthy();
      expect(googleReasonSentence(reason)).not.toBe(GOOGLE_FALLBACK);
    }
  });

  it('tells the four causes that used to share a sentence apart', () => {
    // Not point-free: `map` would hand the index to `signedInAs` and test the
    // signed-in words instead (2026-09-29, caught by the typecheck).
    const sentences = [
      'account-suspended',
      'email-claimed',
      'invitation-expired',
      'account-unavailable',
      'email-unverified',
    ].map((reason) => googleReasonSentence(reason));
    expect(new Set(sentences).size).toBe(sentences.length);
    expect(googleReasonSentence('account-suspended')).toContain('suspended');
    expect(googleReasonSentence('email-claimed')).toContain('different Google account');
    expect(googleReasonSentence('invitation-expired')).toContain('invitation');
    expect(googleReasonSentence('email-unverified')).toContain('not verified');
  });

  it('every sentence is a sentence, not a code', () => {
    for (const sentence of Object.values(GOOGLE_REASONS)) {
      expect(sentence).toMatch(/\.$/);
      expect(sentence).not.toMatch(/[a-z]-[a-z]+-[a-z]/);
    }
  });

  it('falls back for a word this build does not know, and for none', () => {
    expect(googleReasonSentence('something-new')).toBe(GOOGLE_FALLBACK);
    expect(googleReasonSentence('')).toBe(GOOGLE_FALLBACK);
  });
});

describe('googleFailureFromSearch', () => {
  it('renders nothing unless the query says google=error', () => {
    expect(googleFailureFromSearch('')).toBeNull();
    expect(googleFailureFromSearch('?reason=denied')).toBeNull();
    expect(googleFailureFromSearch('?google=ok&reason=denied')).toBeNull();
  });

  it('maps the reason, and falls back when the reason is missing', () => {
    expect(googleFailureFromSearch('?google=error&reason=denied')).toBe(
      GOOGLE_REASONS.denied,
    );
    expect(googleFailureFromSearch('?google=error')).toBe(GOOGLE_FALLBACK);
  });
});

describe('for somebody still signed in — 2026-09-29', () => {
  // Since 2026-09-29 the scan-limit notice sends a SIGNED-IN user to Google to
  // link the account, so these words are read on the intake screen, not the
  // sign-in page, by someone who was linking rather than signing in.
  const ADDRESS = 'dana@northlight.example';

  it('no sentence sends them to sign in, or to a page they are not on', () => {
    for (const reason of API_REASONS) {
      const sentence = googleReasonSentence(reason, ADDRESS);
      expect(sentence, reason).not.toMatch(/sign in with your email and password/i);
      expect(sentence, reason).not.toMatch(/you linked before/i);
      expect(sentence, reason).not.toMatch(/from this page/i);
      // Their own account is active — the session says so — so a suspension
      // is somebody else's, and "contact the owner" is not their next step.
      expect(sentence, reason).not.toMatch(/contact the agency/i);
    }
  });

  it("email-claimed: the way out is the Google account for this sign-in's own address", () => {
    const sentence = googleReasonSentence('email-claimed', ADDRESS);
    expect(sentence).toContain('already belongs to another account here');
    expect(sentence).toContain(`Continue with Google again and choose ${ADDRESS}.`);
    expect(sentence).not.toBe(GOOGLE_REASONS['email-claimed']);
    expect(googleFailureFromSearch('?google=error&reason=email-claimed', ADDRESS)).toBe(sentence);
  });

  it('rate-limited: one plain sentence, same signed in or out, from the query string', () => {
    const sentence =
      'Too many sign-in attempts from this network. Wait a few minutes and try again.';
    expect(googleReasonSentence('rate-limited')).toBe(sentence);
    expect(googleReasonSentence('rate-limited', ADDRESS)).toBe(sentence);
    expect(googleFailureFromSearch('?google=error&reason=rate-limited')).toBe(sentence);
  });

  it('browser-mismatch: one sentence signed out, and a linking sentence signed in', () => {
    expect(googleReasonSentence('browser-mismatch')).toBe(
      'That Google sign-in could not be matched to this browser. Start it again from this page.',
    );
    expect(googleReasonSentence('browser-mismatch', ADDRESS)).toBe(
      `That Google sign-in could not be matched to this browser, so nothing was linked. Continue with Google again and choose ${ADDRESS}.`,
    );
    expect(googleFailureFromSearch('?google=error&reason=browser-mismatch')).toBe(
      GOOGLE_REASONS['browser-mismatch'],
    );
  });

  it("the refusals about somebody else's account share the way out and are still told apart", () => {
    const sentences = [
      'email-claimed',
      'account-suspended',
      'invitation-expired',
      'account-unavailable',
      'email-unverified',
    ].map((reason) => googleReasonSentence(reason, ADDRESS));
    expect(new Set(sentences).size).toBe(sentences.length);
    for (const sentence of sentences) {
      expect(sentence).toContain('nothing was linked');
      expect(sentence).toContain(`choose ${ADDRESS}.`);
    }
  });

  it('a reason that already reads true signed in keeps one sentence for both', () => {
    for (const reason of ['denied', 'exchange-failed']) {
      expect(GOOGLE_REASONS_SIGNED_IN[reason]).toBeUndefined();
      expect(googleReasonSentence(reason, ADDRESS)).toBe(GOOGLE_REASONS[reason]);
    }
  });

  it('signed out, every sentence is what it was', () => {
    for (const reason of API_REASONS) {
      expect(googleReasonSentence(reason)).toBe(GOOGLE_REASONS[reason]);
    }
  });

  it('every variant is a sentence, for a word the API emits; an unknown word still falls back', () => {
    for (const [reason, variant] of Object.entries(GOOGLE_REASONS_SIGNED_IN)) {
      expect(API_REASONS).toContain(reason);
      expect(variant(ADDRESS)).toMatch(/\.$/);
      expect(variant(ADDRESS)).not.toMatch(/[a-z]-[a-z]+-[a-z]/);
    }
    expect(googleReasonSentence('something-new', ADDRESS)).toBe(GOOGLE_FALLBACK);
  });
});

describe('busy: Google sign-in is at its ceiling for everyone — A6 part 2, 2026-10-22', () => {
  // The founder's two sentences, word for word, written out here rather than read back from the
  // maps: a reworded sentence has to come through this file.
  const SIGNED_OUT =
    'Google sign-in is busy right now. Wait a few minutes and try again, or sign in with your email and password.';
  const SIGNED_IN =
    'Google sign-in is busy right now, so nothing was linked. Wait a few minutes and try again.';
  const ADDRESS = 'dana@northlight.example';

  it('signed out: its own sentence, from the word and from the query string', () => {
    expect(googleReasonSentence('busy')).toBe(SIGNED_OUT);
    expect(GOOGLE_REASONS.busy).toBe(SIGNED_OUT);
    expect(googleFailureFromSearch('?google=error&reason=busy')).toBe(SIGNED_OUT);
  });

  it('signed in, linking: its own sentence, from the word and from the query string', () => {
    expect(googleReasonSentence('busy', ADDRESS)).toBe(SIGNED_IN);
    expect(GOOGLE_REASONS_SIGNED_IN.busy?.(ADDRESS)).toBe(SIGNED_IN);
    expect(googleFailureFromSearch('?google=error&reason=busy', ADDRESS)).toBe(SIGNED_IN);
  });

  it('is not the per-network sentence, and the two readers hear different things', () => {
    expect(SIGNED_OUT).not.toBe(SIGNED_IN);
    expect(googleReasonSentence('busy')).not.toBe(googleReasonSentence('rate-limited'));
    expect(googleReasonSentence('busy', ADDRESS)).not.toBe(googleReasonSentence('rate-limited', ADDRESS));
  });
});

describe('a word the address bar made up — I7, 2026-10-22', () => {
  // Names a plain object answers to without having them as keys; `__proto__` is the one that is
  // also an accessor. None is a reason, and every one must give the fallback, signed out and in.
  const INHERITED = ['constructor', 'toString', '__proto__', 'hasOwnProperty', 'valueOf'];
  const ADDRESS = 'dana@northlight.example';

  it.each(INHERITED)('%s, signed out, is the fallback', (name) => {
    expect(googleReasonSentence(name)).toBe(GOOGLE_FALLBACK);
    expect(googleFailureFromSearch(`?google=error&reason=${name}`)).toBe(GOOGLE_FALLBACK);
  });

  it.each(INHERITED)('%s, signed in, is the fallback', (name) => {
    expect(googleReasonSentence(name, ADDRESS)).toBe(GOOGLE_FALLBACK);
    expect(googleFailureFromSearch(`?google=error&reason=${name}`, ADDRESS)).toBe(GOOGLE_FALLBACK);
  });

  it('none of them is a key of either map, and every key still gives its own sentence', () => {
    for (const name of INHERITED) {
      expect(Object.hasOwn(GOOGLE_REASONS, name), name).toBe(false);
      expect(Object.hasOwn(GOOGLE_REASONS_SIGNED_IN, name), name).toBe(false);
    }
    for (const reason of Object.keys(GOOGLE_REASONS)) {
      expect(googleReasonSentence(reason), reason).toBe(GOOGLE_REASONS[reason]);
    }
    for (const [reason, variant] of Object.entries(GOOGLE_REASONS_SIGNED_IN)) {
      expect(googleReasonSentence(reason, ADDRESS), reason).toBe(variant(ADDRESS));
    }
  });
});

describe('taking the reason off the address bar', () => {
  function fakeWindow(search: string) {
    const replaced: unknown[][] = [];
    return {
      replaced,
      win: {
        location: { search, pathname: '/' },
        history: { replaceState: (...args: unknown[]) => void replaced.push(args) },
      },
    };
  }

  it('reads the word, not the sentence — the sentence waits on the session check', () => {
    expect(googleFailureReason('?google=error&reason=email-claimed')).toBe('email-claimed');
    expect(googleFailureReason('?google=error')).toBe('');
    expect(googleFailureReason('?reason=denied')).toBeNull();
  });

  it('takes it and clears the query, so a reload does not show it twice', () => {
    const { replaced, win } = fakeWindow('?google=error&reason=denied');
    expect(takeGoogleFailure(win)).toBe('denied');
    expect(replaced).toEqual([[null, '', '/']]);
  });

  it('leaves the address bar alone when there is nothing to take', () => {
    const { replaced, win } = fakeWindow('?utm_source=newsletter');
    expect(takeGoogleFailure(win)).toBeNull();
    expect(replaced).toEqual([]);
  });
});
