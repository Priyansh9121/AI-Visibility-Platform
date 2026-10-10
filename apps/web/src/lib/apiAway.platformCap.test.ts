import { describe, it, expect } from 'vitest';
import { ApiProblem } from '@/lib/api';
import { apiWasAway } from './apiAway';

const SENTENCE =
  'AVP has started as many scans as it allows in a day, so this one was not started. ' +
  'Try again after midnight UTC.';

const capProblem = new ApiProblem({
  type: '/problems/platform-cap-reached',
  title: 'Daily scan limit reached',
  status: 503,
  detail: SENTENCE,
  instance: '/api/v1/clients/clnt_1/scans',
});

describe('the platform daily scan limit is the API answering, not the API away', () => {
  it('is not away, so a screen shows the API sentence and not the unreachable one', () => {
    expect(apiWasAway(capProblem)).toBe(false);
    expect(capProblem.problem.detail).toBe(SENTENCE);
  });

  it('leaves every other 503 as away', () => {
    const other = new ApiProblem({
      type: '/problems/service-unavailable',
      title: 'Service unavailable',
      status: 503,
      detail: 'This is temporarily unavailable. Please try again shortly.',
      instance: '/x',
    });
    expect(apiWasAway(other)).toBe(true);
  });
});
