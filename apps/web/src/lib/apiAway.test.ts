import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect } from 'vitest';
import { ApiProblem } from '@/lib/api';
import { apiWasAway, API_AWAY_SENTENCE } from './apiAway';

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

describe('the sentence a screen shows when the API was away', () => {
  it('says what happened and what to do, and names no developer detail', () => {
    expect(API_AWAY_SENTENCE).toBe(
      'We could not reach the server. Check your connection and try again.',
    );
    expect(API_AWAY_SENTENCE).not.toMatch(/\d{4}|\bAPI\b/);
  });
});

describe('no screen tells a customer about a developer port', () => {
  it('no non-test source file under apps/web/src mentions "port 8000"', () => {
    const root = join(__dirname, '..');
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
          if (/port 8000/i.test(readFileSync(full, 'utf8'))) offenders.push(full);
        }
      }
    };
    walk(root);
    expect(offenders).toEqual([]);
  });
});
