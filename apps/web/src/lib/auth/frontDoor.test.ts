/**
 * The front door's forms, addressable by URL — 2026-09-29.
 */
import { describe, it, expect } from 'vitest';
import { frontDoorHref, takeFrontDoorForm } from './frontDoor';

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

describe('frontDoorHref', () => {
  it('names the view on `/`', () => {
    expect(frontDoorHref('sign-in')).toBe('/?view=sign-in');
    expect(frontDoorHref('sign-up')).toBe('/?view=sign-up');
  });
});

describe('takeFrontDoorForm', () => {
  it.each(['sign-in', 'sign-up'] as const)('takes %s and clears the query', (form) => {
    const { replaced, win } = fakeWindow(`?view=${form}`);
    expect(takeFrontDoorForm(win)).toBe(form);
    expect(replaced).toEqual([[null, '', '/']]);
  });

  it('ignores any other view, and leaves the address bar alone', () => {
    for (const search of ['?view=forgot', '?view=', '?utm_source=newsletter', '']) {
      const { replaced, win } = fakeWindow(search);
      expect(takeFrontDoorForm(win), search).toBeNull();
      expect(replaced, search).toEqual([]);
    }
  });
});
