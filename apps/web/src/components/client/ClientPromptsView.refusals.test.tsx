// @vitest-environment jsdom

/**
 * The Prompts tab's ask form shows each refusal the API can now give, in the API's own words — F3.
 *
 * Asking a question is a paid call, so the API answers with a 402 (needs a plan), a 429 (this
 * client's hour is used), a 503 `platform-cap-reached` (AVP's day is spent) or a 503
 * `service-unavailable` (it could not count, so it did not ask). The form mounts with `fetch`
 * stubbed at the edge, submits a question, and reads the alert. None of them is "the server is
 * away", and none is replaced by a generic line.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { PromptWorkbench } from './ClientPromptsView';
import { API_AWAY_SENTENCE } from '@/lib/apiAway';
import { identifiedClient } from '@/lib/clients/__fixtures__/clients';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HISTORY = { data: [], runsRemaining: 30, runsPerHour: 30, maxPromptChars: 500 };

const problem = (status: number, type: string, title: string, detail: string) =>
  new Response(
    JSON.stringify({ type: `/problems/${type}`, title, status, detail, instance: '/x' }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

async function ask(
  refusal: () => Response,
  role: 'owner' | 'admin' | 'member' | null = null,
): Promise<{ alert: string; el: HTMLElement }> {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === 'POST'
        ? refusal()
        : new Response(JSON.stringify(HISTORY), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
    ),
  );
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => root!.render(<PromptWorkbench client={identifiedClient} role={role} />));
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
  const input = el.querySelector('input')!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
      input,
      'best help desk for small teams',
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    el.querySelector('form')!.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    );
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
  const alert = el.querySelector('.avp-errorstate')?.textContent ?? '';
  expect(el.textContent).not.toContain(API_AWAY_SENTENCE);
  expect(el.textContent).not.toContain('The run did not complete');
  // A refusal leaves the form usable: the person can edit the question and try again.
  expect(el.querySelector('input')!.disabled).toBe(false);
  const run = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Run this prompt');
  expect(run).toBeDefined();
  expect(run!.disabled).toBe(false);
  return { alert, el };
}

describe('a refused question says why, in the API’s words', () => {
  it.each([
    [
      '402, no plan',
      402,
      'subscription-required',
      'Subscription required',
      'Asking your own questions needs a plan. Start one in Settings, under Billing.',
    ],
    [
      '429, the client’s hour',
      429,
      'rate-limited',
      'Too many requests',
      'This client has run 30 prompts in the last hour, which is the limit of 30. Each run asks every engine, so the limit is there to keep ad-hoc testing from costing more than a scan. Try again shortly.',
    ],
    [
      '503, the platform’s day',
      503,
      'platform-cap-reached',
      'Daily limit reached',
      'AVP has answered as many questions as it allows in a day. Try again after midnight UTC.',
    ],
    [
      '503, it could not count',
      503,
      'service-unavailable',
      'Service unavailable',
      'This is temporarily unavailable. Please try again shortly.',
    ],
  ])('%s', async (_why, status, type, title, detail) => {
    const { alert } = await ask(() => problem(status, type, title, detail));
    expect(alert).toContain('That prompt did not run');
    expect(alert).toContain(detail);
  });
});

describe('a 402 points an owner or admin at Billing, and leaves a member with the sentence', () => {
  const NEEDS_PLAN = 'Asking your own questions needs a plan. Start one in Settings, under Billing.';
  const refuse = () => problem(402, 'subscription-required', 'Subscription required', NEEDS_PLAN);

  it.each(['owner', 'admin'] as const)('%s gets a keyboard-reachable link to Billing', async (role) => {
    const { alert, el } = await ask(refuse, role);
    expect(alert).toContain(NEEDS_PLAN);
    const link = el.querySelector<HTMLAnchorElement>('a[href="/settings#billing"]');
    expect(link).not.toBeNull();
    expect(link!.textContent).toBe('Go to Billing in Settings');
    expect(link!.getAttribute('tabindex')).toBeNull(); // a real anchor: in the tab order
  });

  it.each([['a member', 'member' as const], ['a role not known', null]])(
    '%s gets the sentence alone',
    async (_who, role) => {
      const { alert, el } = await ask(refuse, role);
      expect(alert).toContain(NEEDS_PLAN);
      expect(el.querySelector('a[href="/settings#billing"]')).toBeNull();
    },
  );

  it('offers no link for a refusal that is not about the plan', async () => {
    const { el } = await ask(
      () => problem(429, 'rate-limited', 'Too many requests', 'Try again shortly.'),
      'owner',
    );
    expect(el.querySelector('a[href="/settings#billing"]')).toBeNull();
  });
});
