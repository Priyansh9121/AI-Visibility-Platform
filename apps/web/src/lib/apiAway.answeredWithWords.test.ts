import { describe, it, expect } from 'vitest';
import { ApiProblem } from '@/lib/api';
import { apiAnsweredWithWords, apiWasAway } from './apiAway';

const problem = (type: string, status: number) =>
  new ApiProblem({ type, title: 't', status, detail: 'A sentence.', instance: '/x' });

describe('apiAnsweredWithWords: the API said something to the person', () => {
  it('counts every refusal apiWasAway does not', () => {
    for (const status of [402, 429]) {
      expect(apiAnsweredWithWords(problem('/problems/rate-limited', status))).toBe(true);
    }
    expect(apiAnsweredWithWords(problem('/problems/platform-cap-reached', 503))).toBe(true);
  });

  it('also counts the API’s own service-unavailable 503, which apiWasAway still reads as away', () => {
    const unavailable = problem('/problems/service-unavailable', 503);
    expect(apiWasAway(unavailable)).toBe(true);
    expect(apiAnsweredWithWords(unavailable)).toBe(true);
  });

  it('does not count a network error, a proxy 5xx or a non-error', () => {
    expect(apiAnsweredWithWords(new TypeError('Failed to fetch'))).toBe(false);
    expect(apiAnsweredWithWords(problem('/problems/unknown', 502))).toBe(false);
    expect(apiAnsweredWithWords(problem('/problems/unknown', 0))).toBe(false);
    expect(apiAnsweredWithWords('boom')).toBe(false);
  });
});
