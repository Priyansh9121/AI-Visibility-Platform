/**
 * The "Send feedback" subject, held to every route the app has — 2026-10-06.
 *
 * The subject names the SHAPE of the page the user was on and never its values, so that no id,
 * token or client name can reach an email the user sends. The load-bearing tests here read the
 * route tree off the disk (`src/app`), the way `providers.test.ts` reads the hosts: every page
 * the app has is rendered into a path with realistic ids, tokens, share tokens and a client's
 * name in each dynamic slot, and the subject is held to say the shape and none of the values.
 * The list of fixed names is held to the same tree both ways, so it can neither miss a route nor
 * carry a value.
 */

import { describe, it, expect } from 'vitest';
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ELIDED,
  ROUTE_SEGMENTS,
  SUBJECT_PREFIX,
  feedbackAddress,
  feedbackMailto,
  feedbackSubject,
  routeShape,
} from './subject';

// -- the route tree, read from the disk ----------------------------------------------------

const APP = fileURLToPath(new URL('../../app', import.meta.url));

/** Every folder under `src/app`, as a trail of folder names, and whether it holds a page. */
function folders(dir: string = APP, trail: string[] = []): { trail: string[]; page: boolean }[] {
  const found: { trail: string[]; page: boolean }[] = [];
  const names = readdirSync(dir);
  found.push({ trail, page: names.includes('page.tsx') });
  for (const name of names) {
    if (statSync(join(dir, name)).isDirectory()) {
      found.push(...folders(join(dir, name), [...trail, name]));
    }
  }
  return found;
}

const FOLDERS = folders();
const ROUTES = FOLDERS.filter((folder) => folder.page).map((folder) => folder.trail);
const isDynamic = (segment: string) => /^\[.+\]$/.test(segment);
const STATIC_SEGMENTS = new Set(
  FOLDERS.flatMap((folder) => folder.trail).filter((segment) => !isDynamic(segment)),
);

// -- realistic values for every dynamic slot ----------------------------------------------

const SHARE_TOKEN = 'k7Qe3vJ0h2pX9mTz5RbLw1YcNaU8dGfSoIiV4tHjE6A';
const INVITE_TOKEN = 'Zr2LwQ9sT7vB1nXkF5yHcM3pJdA8eUgOiN6tYhR4aKE';
const RESET_TOKEN = 'p0Ho8WqD3zNfXe5yRb2LtJmV7cKaS1uGiT9wAhE4rZB';
const VALUES: Record<string, readonly string[]> = {
  // A client's id, and, in the last two, what must never be in a path but is tried anyway: a
  // client's domain and its name.
  clientId: ['clnt_01J8Z3Q9ACMEDENTAL0123456', 'clnt_01AAA', 'acme-dental.com', 'Acme%20Dental%20Group'],
  scanId: ['scan_01J8Z3Q9XYZ0123456789ABCD', 'scan_01AAA'],
  token: [SHARE_TOKEN, INVITE_TOKEN, RESET_TOKEN],
};
const ROUNDS = [0, 1, 2, 3];

function valueFor(param: string, round: number): string {
  const options = VALUES[param];
  if (options === undefined) throw new Error(`a dynamic slot with no values here: ${param}`);
  return options[round % options.length]!;
}

/** The longest run an id or a token would make; no word of the app's own is this long. */
const LOOKS_LIKE_A_VALUE = /[A-Za-z0-9_-]{16,}/;

function decodeSafely(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

describe('the route tree the tests read is the one the app has', () => {
  it('finds the app’s pages, and no folder kind this reading would get wrong', () => {
    expect(ROUTES.length).toBeGreaterThan(20);
    // Route groups (a), parallel routes @slot and private folders _x change what a folder name
    // means in a URL. None exist; if one does, this reading must be taught about it first.
    const unusual = FOLDERS.flatMap((folder) => folder.trail).filter((name) => /^[(@_]/.test(name));
    expect(unusual).toEqual([]);
  });

  it('knows the dynamic slots by name', () => {
    const slots = new Set(ROUTES.flat().filter(isDynamic));
    expect([...slots].sort()).toEqual(['[clientId]', '[scanId]', '[token]']);
  });
});

describe('ROUTE_SEGMENTS is the app’s own fixed names, exactly', () => {
  it('has every fixed folder name of the route tree', () => {
    const missing = [...STATIC_SEGMENTS].filter((segment) => !ROUTE_SEGMENTS.includes(segment));
    expect(missing, 'a route folder the subject would turn into "…": add it').toEqual([]);
  });

  it('has nothing that is not a fixed folder name of the route tree', () => {
    const extra = ROUTE_SEGMENTS.filter((segment) => !STATIC_SEGMENTS.has(segment));
    expect(extra, 'an entry that is not one of the app’s folders: a value must never be listed').toEqual([]);
  });

  it('lists nothing twice, and no entry is long enough to be an id or a token', () => {
    expect(new Set(ROUTE_SEGMENTS).size).toBe(ROUTE_SEGMENTS.length);
    for (const segment of ROUTE_SEGMENTS) expect(segment).not.toMatch(LOOKS_LIKE_A_VALUE);
  });
});

describe('every route the app has, with real values in every dynamic slot', () => {
  const cases = ROUTES.flatMap((trail) =>
    ROUNDS.map((round) => {
      const values: string[] = [];
      const path =
        '/' +
        trail
          .map((segment) => {
            if (!isDynamic(segment)) return segment;
            const value = valueFor(segment.slice(1, -1), round);
            values.push(value);
            return value;
          })
          .join('/');
      const shape = '/' + trail.map((segment) => (isDynamic(segment) ? ELIDED : segment)).join('/');
      return { path, shape, values };
    }),
  );

  it('has cases to run, with values in them', () => {
    expect(cases.length).toBeGreaterThan(80);
    expect(cases.filter((c) => c.values.length > 0).length).toBeGreaterThan(40);
  });

  it.each(cases.map((c) => [c.path, c] as const))('%s says its shape and none of its values', (_path, c) => {
    expect(routeShape(c.path)).toBe(c.shape);
    const subject = feedbackSubject(c.path);
    expect(subject).toBe(`${SUBJECT_PREFIX}: ${c.shape}`);

    const href = feedbackMailto('pilot@avp-pilot.example', c.path);
    expect(href).not.toBeNull();
    for (const value of c.values) {
      for (const form of [value, encodeURIComponent(value), decodeSafely(value)]) {
        expect(subject).not.toContain(form);
        expect(href).not.toContain(form);
      }
    }
    expect(subject).not.toMatch(LOOKS_LIKE_A_VALUE);
    expect(subject).not.toMatch(/clnt_|scan_|invt_|user_|agcy_/);
  });
});

describe('the subject, by example', () => {
  const E = ELIDED;
  it.each([
    ['/', `${SUBJECT_PREFIX}: /`],
    ['/dashboard', `${SUBJECT_PREFIX}: /dashboard`],
    ['/settings', `${SUBJECT_PREFIX}: /settings`],
    ['/clients', `${SUBJECT_PREFIX}: /clients`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456', `${SUBJECT_PREFIX}: /clients/${E}`],
    // The brief's own example: a client's page, whatever the client is called.
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/report', `${SUBJECT_PREFIX}: /clients/${E}/report`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/alerts', `${SUBJECT_PREFIX}: /clients/${E}/alerts`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/competitors', `${SUBJECT_PREFIX}: /clients/${E}/competitors`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/crawler', `${SUBJECT_PREFIX}: /clients/${E}/crawler`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/gaps', `${SUBJECT_PREFIX}: /clients/${E}/gaps`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/prompts', `${SUBJECT_PREFIX}: /clients/${E}/prompts`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/rankings', `${SUBJECT_PREFIX}: /clients/${E}/rankings`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/sentiment', `${SUBJECT_PREFIX}: /clients/${E}/sentiment`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/sources', `${SUBJECT_PREFIX}: /clients/${E}/sources`],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/technical', `${SUBJECT_PREFIX}: /clients/${E}/technical`],
    ['/scans/scan_01J8Z3Q9XYZ0123456789ABCD/report', `${SUBJECT_PREFIX}: /scans/${E}/report`],
    ['/sign-up/google', `${SUBJECT_PREFIX}: /sign-up/google`],
    ['/welcome', `${SUBJECT_PREFIX}: /welcome`],
    ['/privacy', `${SUBJECT_PREFIX}: /privacy`],
  ])('%s', (path, subject) => {
    expect(feedbackSubject(path)).toBe(subject);
  });

  it('says nothing of the page when it has no pathname to go on', () => {
    for (const none of [null, undefined]) expect(feedbackSubject(none)).toBe(SUBJECT_PREFIX);
  });
});

describe('tokens, in a path and in a fragment', () => {
  it.each([
    ['share', SHARE_TOKEN],
    ['invite', INVITE_TOKEN],
    ['reset-password', RESET_TOKEN],
  ])('/%s: the legacy path form and the fragment form', (name, token) => {
    // The old path form puts the token in a segment; the new one puts it after `#`.
    expect(feedbackSubject(`/${name}/${token}`)).toBe(`${SUBJECT_PREFIX}: /${name}/${ELIDED}`);
    expect(feedbackSubject(`/${name}#${token}`)).toBe(`${SUBJECT_PREFIX}: /${name}`);
    expect(feedbackSubject(`/${name}/#${token}`)).toBe(`${SUBJECT_PREFIX}: /${name}`);
    expect(feedbackMailto('pilot@avp-pilot.example', `/${name}/${token}`)).not.toContain(token);
    expect(feedbackMailto('pilot@avp-pilot.example', `/${name}#${token}`)).not.toContain(token);
  });
});

describe('the query and the fragment are dropped', () => {
  it.each([
    ['/clients/clnt_1/alerts?ticket=SECRETTICKET&email=owner%40acme.example', '/clients/…/alerts'],
    ['/scans/scan_1/report?token=SECRETTOKEN#beat-3', '/scans/…/report'],
    ['/settings#billing', '/settings'],
    ['/settings?session=SECRETSESSION', '/settings'],
    ['/?code=SECRETCODE&state=SECRETSTATE', '/'],
    ['/dashboard?', '/dashboard'],
    ['/dashboard#', '/dashboard'],
  ])('%s', (path, shape) => {
    const subject = feedbackSubject(path);
    expect(subject).toBe(`${SUBJECT_PREFIX}: ${shape}`);
    expect(subject).not.toMatch(/SECRET|owner|acme|beat-3|billing/);
  });
});

describe('a client’s name and domain are never in it', () => {
  it.each([
    '/clients/Acme Dental Group/alerts',
    '/clients/acme-dental.com/alerts',
    '/clients/Acme%20Dental%20Group/alerts',
    '/clients/Northaven Dental/report',
    '/clients/ünïcode-brand/sources',
  ])('%s', (path) => {
    const subject = feedbackSubject(path);
    expect(subject).toBe(`${SUBJECT_PREFIX}: /clients/${ELIDED}/${path.split('/').pop()}`);
    expect(subject).not.toMatch(/acme|northaven|dental|ünï|%20/i);
  });
});

describe('a path that is not one of the app’s', () => {
  it.each([
    ['', '/'],
    ['/', '/'],
    ['//', '/'],
    ['///clients///', '/clients'],
    ['clients/clnt_1/alerts', '/clients/…/alerts'],
    ['/clients/clnt_1/alerts/', '/clients/…/alerts'],
    ['/Clients/clnt_1', '/…/…'],
    ['/CLIENTS', '/…'],
    ['/clients/%2e%2e/settings', '/clients/…/settings'],
    ['/clients/../settings', '/clients/…/settings'],
    ['/cli%65nts', '/…'],
    ['/clients/…/alerts', '/clients/…/alerts'],
    ['/nothing-here/at-all', '/…/…'],
    ['/api/v1/auth/me', '/…/…/…/…'],
  ])('%j says %s', (path, shape) => {
    expect(routeShape(path)).toBe(shape);
  });

  it('says a very long segment, and a token in the wrong place, as "…" too', () => {
    expect(routeShape('/' + 'x'.repeat(5000))).toBe('/…');
    const odd = '/clients/' + 'a'.repeat(300) + '/' + SHARE_TOKEN + '/alerts';
    expect(feedbackSubject(odd)).toBe(`${SUBJECT_PREFIX}: /clients/${ELIDED}/${ELIDED}/alerts`);
  });

  it('keeps a word of the app’s own wherever it stands, which is harmless: it is a word, not a value', () => {
    // The list is of names, not of positions. An id or a token is never one of these words.
    expect(routeShape('/clients/settings')).toBe('/clients/settings');
    expect(routeShape('/settings/clients')).toBe('/settings/clients');
  });
});

describe('the link', () => {
  const ADDRESS = 'pilot-feedback@avp-pilot.example';

  it('is mailto, the address and the encoded subject, and nothing else', () => {
    expect(feedbackMailto(ADDRESS, '/clients/clnt_1/alerts')).toBe(
      'mailto:pilot-feedback@avp-pilot.example?subject=AVP%20feedback%3A%20%2Fclients%2F%E2%80%A6%2Falerts',
    );
  });

  it('round-trips through a URL parser to the subject and no other header', () => {
    const url = new URL(feedbackMailto(ADDRESS, '/scans/scan_1/report?x=1')!);
    expect(url.protocol).toBe('mailto:');
    expect(decodeURIComponent(url.pathname)).toBe(ADDRESS);
    expect([...url.searchParams.keys()]).toEqual(['subject']);
    expect(url.searchParams.get('subject')).toBe('AVP feedback: /scans/…/report');
    expect(url.hash).toBe('');
  });

  it('is just the prefix when there is no pathname', () => {
    expect(feedbackMailto(ADDRESS, null)).toBe(
      'mailto:pilot-feedback@avp-pilot.example?subject=AVP%20feedback',
    );
    expect(feedbackMailto(ADDRESS, undefined)).toBe(feedbackMailto(ADDRESS, null));
  });

  it('encodes a plus and a non-ASCII address, and keeps the one @', () => {
    expect(feedbackMailto('a+tag@avp-pilot.example', '/')).toContain('mailto:a%2Btag@avp-pilot.example?');
    const url = new URL(feedbackMailto('üser@bücher.example', '/')!);
    expect(decodeURIComponent(url.pathname)).toBe('üser@bücher.example');
  });

  it('draws nothing for an address that is not one address, whatever it would have injected', () => {
    for (const sneaky of [
      'a@avp-pilot.example?bcc=spy@elsewhere.example',
      'a@avp-pilot.example&cc=spy@elsewhere.example',
      'a@avp-pilot.example,spy@elsewhere.example',
      'a@avp-pilot.example;spy@elsewhere.example',
      'a@avp-pilot.example\nbcc: spy@elsewhere.example',
      'a@avp-pilot.example\r\nbcc: spy@elsewhere.example',
      'Pilot <a@avp-pilot.example>',
      'a@avp-pilot.example#spy',
    ]) {
      expect(feedbackMailto(sneaky, '/dashboard'), sneaky).toBeNull();
    }
  });

  it('keeps what is left of an address that has a percent sign or a colon from becoming a header', () => {
    // `%` and `:` are allowed in an address; encoded, they are only characters.
    const href = feedbackMailto('a%0Abcc:spy@avp-pilot.example', '/dashboard')!;
    expect(href).toBe(
      'mailto:a%250Abcc%3Aspy@avp-pilot.example?subject=AVP%20feedback%3A%20%2Fdashboard',
    );
    expect(href.split('?')).toHaveLength(2);
    expect(href).not.toMatch(/[\s,&]|%0A/i);
    expect([...new URL(href).searchParams.keys()]).toEqual(['subject']);
  });
});

describe('the address the API sent', () => {
  it.each([
    ['pilot-feedback@avp-pilot.example', 'pilot-feedback@avp-pilot.example'],
    ['  pilot-feedback@avp-pilot.example  ', 'pilot-feedback@avp-pilot.example'],
    ['Feedback+AVP@pilot.example', 'Feedback+AVP@pilot.example'],
    ['üser@bücher.example', 'üser@bücher.example'],
    ["o'brien@pilot.example", "o'brien@pilot.example"],
  ])('%j is the address %j', (given, kept) => {
    expect(feedbackAddress(given)).toBe(kept);
  });

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a number', 25],
    ['a boolean', true],
    ['an object', { email: 'a@pilot.example' }],
    ['a list', ['a@pilot.example']],
    ['empty', ''],
    ['blank', '   '],
    ['no at sign', 'pilot.example'],
    ['two at signs', 'a@@pilot.example'],
    ['two at signs, apart', 'a@b@pilot.example'],
    ['nothing before the at sign', '@pilot.example'],
    ['nothing after it', 'a@'],
    ['a space inside', 'a b@pilot.example'],
    ['two addresses, a comma', 'a@pilot.example, b@pilot.example'],
    ['two addresses, a semicolon', 'a@pilot.example;b@pilot.example'],
    ['a name and angle brackets', 'Pilot <a@pilot.example>'],
    ['a second header', 'a@pilot.example?bcc=b@pilot.example'],
    ['a joined header', 'a@pilot.example&cc=b@pilot.example'],
    ['a fragment', 'a@pilot.example#frag'],
    ['a newline', 'a@pilot.example\nbcc: b@pilot.example'],
    ['a tab', 'a@pilot.example\tb'],
    ['a quote', 'a"b@pilot.example'],
    ['a backslash', 'a\\b@pilot.example'],
    ['a control character', 'a\u0007b@pilot.example'],
  ])('is no address: %s', (_what, given) => {
    expect(feedbackAddress(given)).toBeNull();
  });
});
