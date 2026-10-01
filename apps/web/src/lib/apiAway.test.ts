import { describe, it, expect } from 'vitest';
import { ApiProblem } from '@/lib/api';
import { apiWasAway } from './apiAway';

const problem = (status: number) =>
  new ApiProblem({
    type: '/problems/unknown',
    title: 'Request failed',
    status,
    detail: `The server returned ${status}.`,
    instance: '/x',
  });

describe('apiWasAway: the API did not answer, as against the API saying no', () => {
  it.each([
    ['a network error', new TypeError('Failed to fetch')],
    ['something thrown that is not an error at all', 'boom'],
    ['an answer with no status', problem(0)],
    ['a 500', problem(500)],
    ['a 502 from a proxy', problem(502)],
    ['a 503', problem(503)],
    ['a 599', problem(599)],
  ])('%s: away', (_what, failure) => {
    expect(apiWasAway(failure)).toBe(true);
  });

  it.each([400, 401, 402, 403, 404, 409, 422, 429, 499])('a %i is the API refusing: not away', (status) => {
    expect(apiWasAway(problem(status))).toBe(false);
  });
});
