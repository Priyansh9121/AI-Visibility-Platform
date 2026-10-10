// @vitest-environment jsdom

/**
 * The intake form shows each refusal a classification can now meet, in the API's own words — F3.
 *
 * `POST /clients` with `classify` is counted per agency per day (a 429) and per platform per day
 * (a 503 `platform-cap-reached`), and fails closed when it cannot count (a 503
 * `service-unavailable`). Each is shown above the form, as the other refusals are, and none is
 * "we could not reach the server". Nothing was created, so the address field is left alone. There
 * is no reclassify button in the web app, so this form is the one screen that meets them.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { IntakeForm } from './IntakeForm';
import { API_AWAY_SENTENCE } from '@/lib/apiAway';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

async function submitRefused(status: number, type: string, title: string, detail: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(
        JSON.stringify({ type: `/problems/${type}`, title, status, detail, instance: '/x' }),
        { status, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    ),
  );
  const onFailed = vi.fn();
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(<IntakeForm onClassified={() => {}} onStarted={() => {}} onFailed={onFailed} />),
  );
  const input = el.querySelector('input')!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
      input,
      'northaven-dental.com',
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    el.querySelector('form')!.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    );
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
  return {
    text: el.textContent ?? '',
    alert: el.querySelector('.avp-errorstate')?.textContent ?? null,
    field: el.querySelector('.avp-field')!.outerHTML,
    onFailed,
  };
}

describe('a classification the API refuses is explained in its own words', () => {
  it.each([
    [
      '429, the agency’s day',
      429,
      'rate-limited',
      'Too many requests',
      'This agency has classified as many sites as it can today. Try again after midnight UTC.',
    ],
    [
      '503, the platform’s day',
      503,
      'platform-cap-reached',
      'Daily limit reached',
      'AVP has classified as many sites as it allows in a day. Try again after midnight UTC.',
    ],
    [
      '503, it could not count',
      503,
      'service-unavailable',
      'Service unavailable',
      'This is temporarily unavailable. Please try again shortly.',
    ],
  ])('%s', async (_why, status, type, title, detail) => {
    const { text, alert, field, onFailed } = await submitRefused(status, type, title, detail);
    expect(alert).toContain(detail);
    expect(text).not.toContain(API_AWAY_SENTENCE);
    expect(field).not.toContain('avp-field__error');
    expect(field).not.toContain('aria-invalid');
    expect(onFailed).toHaveBeenCalled();
  });

  it('still reads a 5xx that is not the API’s own as the server being away', async () => {
    const { text } = await submitRefused(502, 'unknown', 'Bad gateway', 'The server returned 502.');
    expect(text).toContain(API_AWAY_SENTENCE);
  });
});
