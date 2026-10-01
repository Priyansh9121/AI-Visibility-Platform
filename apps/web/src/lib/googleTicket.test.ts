/**
 * /sign-up/google reads its ticket from the URL fragment and takes it out of
 * the address bar. A legacy `?ticket=` link (sent in the deploy window before
 * this change) is moved out of the query the same way, once.
 */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { takeGoogleTicket } from './googleTicket';

const HERE = fileURLToPath(new URL('.', import.meta.url));

function fakeWindow(hash: string, search: string) {
  const replaceState = vi.fn();
  return {
    replaceState,
    win: {
      location: { hash, search, pathname: '/sign-up/google' },
      history: { replaceState },
    },
  };
}

describe('takeGoogleTicket', () => {
  it('reads the fragment and clears the address bar with null state', () => {
    const { win, replaceState } = fakeWindow('#ticket=abc_DEF-1', '');
    expect(takeGoogleTicket(win)).toBe('abc_DEF-1');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/sign-up/google');
  });

  it('no fragment and no query is the empty (expired) state, and touches nothing', () => {
    const { win, replaceState } = fakeWindow('', '');
    expect(takeGoogleTicket(win)).toBe('');
    expect(replaceState).not.toHaveBeenCalled();
  });

  it('a malformed or bare fragment is empty', () => {
    expect(takeGoogleTicket(fakeWindow('#', '').win)).toBe('');
    expect(takeGoogleTicket(fakeWindow('#ticket=', '').win)).toBe('');
    expect(takeGoogleTicket(fakeWindow('#ticket=%E0%A4%A', '').win)).toBe('');
    expect(takeGoogleTicket(fakeWindow('#other=abc', '').win)).toBe('');
  });

  it('the fragment wins over a query ticket, and both leave the address bar', () => {
    const { win, replaceState } = fakeWindow('#ticket=fromhash', '?ticket=fromquery');
    expect(takeGoogleTicket(win)).toBe('fromhash');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/sign-up/google');
  });

  it('LEGACY: a ?ticket= link is read once and moved out of the query', () => {
    const { win, replaceState } = fakeWindow('', '?ticket=old_TICKET-9');
    expect(takeGoogleTicket(win)).toBe('old_TICKET-9');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/sign-up/google');
  });
});

describe('the completion page', () => {
  const src = readFileSync(join(HERE, '../app/sign-up/google/page.tsx'), 'utf8');

  it('takes the ticket via takeGoogleTicket and never reads location.search itself', () => {
    expect(src).toContain('takeGoogleTicket(window)');
    expect(src).not.toContain('location.search');
    expect(src).not.toContain('URLSearchParams');
  });

  it('peeks with the POST client and keeps the expired state for a missing ticket', () => {
    expect(src).toContain('api.googlePending(');
    expect(src).toContain("setState({ kind: 'expired' })");
  });

  it('the helper reads location.hash and is the only place a legacy query is read', () => {
    const helper = readFileSync(join(HERE, 'googleTicket.ts'), 'utf8');
    expect(helper).toContain('loc.hash');
    expect(helper).toContain('LEGACY');
  });
});
