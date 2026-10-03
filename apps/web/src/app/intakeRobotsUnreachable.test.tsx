// @vitest-environment jsdom

/**
 * What the intake page shows for a site whose robots.txt does not answer — 2026-10-03.
 *
 * Launch checklist 3.41 made an unreadable robots.txt its own outcome
 * (`ROBOTS_UNREACHABLE`) and gave the intake card words for it, and its last step was to
 * add such a client on the deployed app and read the card by hand. This is that step,
 * automated, and tied to the API.
 *
 * `ClassificationResult.test.tsx` renders the card GIVEN a reason code, and the API's
 * `test_robots_obeyed.py` checks the code it records; nothing tied the two, so a change
 * to the response's shape or to the code left both green with a card showing something
 * else. Both sides now read ONE checked-in file,
 * `packages/shared-types/fixtures/intake-robots-unreachable.json`: the body
 * `POST /api/v1/clients` really returns for such a site, with its ids and timestamps
 * replaced by placeholders. The API's `test_intake_robots_unreachable.py` is the source
 * of truth and writes it (`AVP_WRITE_FIXTURES=1`); THIS file never writes it.
 *
 * It mounts the REAL intake page, with the network replaced at its edge: the page asks
 * the API to add the site, the fixture is the API's answer, and what is read is the card
 * the page then shows. So the path is the one a person's browser takes — the form's
 * request, `api.createClient`, the view the page moves to, `ClassificationResult` —
 * and the card is not handed props the page would not have built. The wording is written
 * out here, not read from `strings.ts`, so a change to it is a change to this file.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ClientDetail } from '@avp/shared-types';
import { ownerMe } from '@/lib/settings/__fixtures__/seats';
import Home from './page';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// `__dirname`, not `import.meta.url`: this file runs under jsdom, where the latter is not a file URL.
const FIXTURE = join(
  __dirname,
  '../../../../packages/shared-types/fixtures/intake-robots-unreachable.json',
);
const fixtureText = readFileSync(FIXTURE, 'utf8');
const fixture = JSON.parse(fixtureText) as ClientDetail;

// The 3.41 wording, as the card says it: a heading, then one paragraph that is what
// happened followed by what the person who typed the address can do about it.
const HEADING = 'We could not read this site.';
const WHAT_HAPPENED =
  'The crawler tried to read the robots.txt of this site, or of an address the site sent it to, and did not get a usable answer. It obeys robots.txt, so without that answer it did not read the site.';
const WHAT_TO_DO = 'Check that the address is right and the site opens in a browser, then try again.';

// What the card says when robots.txt WAS read and forbids the page: the site's own word.
const FORBIDDEN_SENTENCE =
  'The robots.txt of this site, or of an address the site sent the crawler to, asks crawlers like ours not to load its home page. The crawler obeys robots.txt, so it did not read the site.';

type Call = { method: string; path: string; body: string | null };

/** The API's edge: who is signed in, and the one answer this file exists for. */
function stubTheApi(): Call[] {
  const calls: Call[] = [];
  const answer = (status: number, body: string) =>
    new Response(body, { status, headers: { 'Content-Type': 'application/json' } });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ method, path, body: typeof init?.body === 'string' ? init.body : null });
      if (method === 'GET' && path === '/api/v1/auth/me') return answer(200, JSON.stringify(ownerMe));
      if (method === 'POST' && path === '/api/v1/clients') return answer(201, fixtureText);
      // The card reads the agency's billing for the allowance beside "Run a scan"; an
      // unclassified client has no such button, and a refusal leaves the label out.
      if (/^\/api\/v1\/agencies\/[^/]+\/billing$/.test(path)) {
        return answer(
          403,
          JSON.stringify({
            type: '/problems/forbidden',
            title: 'Forbidden',
            status: 403,
            detail: 'Billing is for owners and admins.',
            instance: path,
          }),
        );
      }
      throw new Error(`the page asked for something this test does not answer: ${method} ${path}`);
    }),
  );
  return calls;
}

/** Let promises settle, a bounded number of turns, until `done` says so. */
async function settleUntil(done: () => boolean, turns = 200): Promise<void> {
  for (let turn = 0; turn < turns && !done(); turn += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

const resultCard = (): Element | null =>
  [...page.querySelectorAll('.avp-card')].find((c) => c.querySelector('.avp-badge')) ?? null;

let root: Root | null = null;
let page: HTMLDivElement;
let calls: Call[];
let card: Element;

beforeAll(async () => {
  calls = stubTheApi();
  page = document.createElement('div');
  document.body.appendChild(page);
  root = createRoot(page);
  act(() => root!.render(<Home />));

  // Signed in, the page opens on the intake form. Type the site's address and submit it,
  // as a person does: the native setter and an `input` event, then the form's `submit`.
  await settleUntil(() => page.querySelector('input') !== null);
  const input = page.querySelector('input');
  if (input === null) throw new Error(`the intake form never appeared: ${page.textContent}`);
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
      input,
      fixture.domain,
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => {
    page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  // The page shows a card while it reads the site ("Reading the site") and another for
  // the result; the result's is the one with a status badge.
  await settleUntil(() => resultCard() !== null);
  // No card, no test: with nothing to read, every "does not say" below would pass.
  const shown = resultCard();
  if (shown === null) throw new Error(`the page never showed a result card: ${page.textContent}`);
  card = shown;
});

afterAll(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

const text = () => card.textContent ?? '';
const paragraphs = () => [...card.querySelectorAll('p')].map((p) => p.textContent);

/**
 * What a reader meets, one piece per text node with a space between, so that no word is
 * glued to the next the way `textContent` glues them ("Could not classifyWe could not").
 * A code printed under the sentence would otherwise sit against the last letter before it,
 * where a pattern that looks for a whole word cannot see it.
 */
function spokenText(el: Element): string {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const pieces: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    pieces.push(node.textContent ?? '');
  }
  return pieces.join(' ');
}

describe('the response the card is shown for is the one the API returns for a robots.txt that does not answer', () => {
  it('is an unclassifiable client whose reason is the unreachable code, and not a refusal by the site', () => {
    // Held here as well as in the API's test, so a fixture regenerated from some other
    // outcome cannot leave this file checking a card nobody asked about.
    expect(fixture.classificationStatus).toBe('unclassifiable');
    expect(fixture.classificationReasonCode).toBe('ROBOTS_UNREACHABLE');
    expect(fixture.industry).toBeNull();
  });

  it('reaches the page by the request a person makes: their address, with the classification on', () => {
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      {
        method: 'POST',
        path: '/api/v1/clients',
        body: JSON.stringify({ url: fixture.domain, classify: true }),
      },
    ]);
  });
});

describe('the card says what happened, in the 3.41 words', () => {
  it('has the heading, and under it what the crawler did and then what the person can do, in one paragraph', () => {
    const all = paragraphs();
    const body = `${WHAT_HAPPENED} ${WHAT_TO_DO}`;
    expect(all).toContain(HEADING);
    expect(all).toContain(body);
    expect(all.indexOf(body)).toBe(all.indexOf(HEADING) + 1);
  });

  it('names the site, and says it could not be classified', () => {
    expect(text()).toContain(fixture.domain);
    expect(card.querySelector('.avp-badge')?.textContent).toBe('Could not classify');
  });

  it('offers no scan, and a way onward', () => {
    expect(text()).not.toContain('Run a scan');
    expect(text()).toContain('Go to your scans');
    expect(text()).toContain('Scan another site');
  });
});

describe('the card prints no code', () => {
  // `ROBOTS_UNREACHABLE` is the machine's word for what the sentence says. A reason code
  // looks like WORDS_WITH_UNDERSCORES or, for one word, TIMEOUT: a word of capitals,
  // digits and underscores, three or more long.
  const A_CODE = /\b[A-Z][A-Z0-9_]{2,}\b/;

  it('neither its own, nor any other, in the words or in the markup', () => {
    expect(fixture.classificationReasonCode).toBeTruthy();
    expect(text()).not.toContain(fixture.classificationReasonCode!);
    expect(card.innerHTML).not.toContain(fixture.classificationReasonCode!);
    for (const other of ['ROBOTS_DISALLOWED', 'FETCH_FAILED', 'BROWSER_ERROR', 'TIMEOUT', 'ADDRESS_NOT_PUBLIC']) {
      expect(card.innerHTML, other).not.toContain(other);
    }
    expect(spokenText(card).match(A_CODE)).toBeNull();
    expect(text()).not.toContain('Reason:');
  });
});

describe('the card does not say the site asks crawlers to stay out', () => {
  // The words for a robots.txt that WAS read and forbids the page. Nothing was read here.
  it('has none of the ROBOTS_DISALLOWED wording', () => {
    expect(text()).not.toContain(FORBIDDEN_SENTENCE);
    for (const words of ['stay out', 'asks crawlers', 'not to load', 'did not load', 'usually means']) {
      expect(text(), words).not.toContain(words);
    }
  });
});
