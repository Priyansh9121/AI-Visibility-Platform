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
  googleFailureFromSearch,
  googleReasonSentence,
} from './googleFailure';

const API_REASONS = [
  'denied',
  'invalid-state',
  'exchange-failed',
  'email-unverified',
  'account-suspended',
  'email-claimed',
  'invitation-expired',
  'account-unavailable',
  'unavailable',
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
    const sentences = [
      'account-suspended',
      'email-claimed',
      'invitation-expired',
      'account-unavailable',
      'email-unverified',
    ].map(googleReasonSentence);
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
