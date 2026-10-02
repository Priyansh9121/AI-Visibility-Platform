// @vitest-environment jsdom
/**
 * URL intake — Epic 2, with the loading/error collision fixed in Epic 9.11.
 */

import { describe, it, expect, vi } from 'vitest';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { IntakeForm } from './IntakeForm';

const render = () =>
  renderToStaticMarkup(
    <IntakeForm onClassified={() => {}} onStarted={() => {}} onFailed={() => {}} />,
  );

describe('the form is built from the design system', () => {
  it('renders a real labelled field and a primary action', () => {
    const out = render();
    expect(out).toContain('avp-field');
    expect(out).toContain('Website address');
    expect(out).toContain('Identify this business');
  });

  it('tells the user what will actually be read', () => {
    expect(render()).toContain('we read the homepage and a few key pages');
  });

  it('emits no arbitrary-value or raw-palette utility', () => {
    const out = render();
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('shows no error before anything is submitted', () => {
    expect(render()).not.toContain('avp-field__error');
  });
});

describe('a failed submission cannot leave the page mid-flight', () => {
  it('makes onFailed a REQUIRED prop, so the wiring cannot be dropped', () => {
    // The defect this guards, found in a browser: `onStarted` moved the page
    // into "working" and nothing moved it back, so a rejected submit rendered
    // the field error with "Reading the site" still sitting underneath it.
    // Both components were individually correct in isolation, which is why no
    // unit test caught it.
    //
    // The guarantee is enforced by the TYPE, not by this assertion: omitting
    // `onFailed` is a compile error, and `tsc --noEmit` runs in the same gate
    // as these tests. This repo has no DOM-driving test library (adding one
    // would need a licence review under the dependency-licensing rule), so a required prop
    // is both the stronger guard and the cheaper one.
    //
    // @ts-expect-error - onFailed is required; removing it must not compile.
    const missing = <IntakeForm onClassified={() => {}} onStarted={() => {}} />;
    expect(missing).toBeTruthy();
  });
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('when the API does not answer, the person is told in plain words', () => {
  const AWAY = 'We could not reach the server. Check your connection and try again.';

  async function submitWith(fetchImpl: () => Promise<Response>) {
    vi.stubGlobal('fetch', vi.fn(fetchImpl));
    const onFailed = vi.fn();
    const el = document.createElement('div');
    document.body.appendChild(el);
    const root = createRoot(el);
    act(() =>
      root.render(
        <IntakeForm onClassified={() => {}} onStarted={() => {}} onFailed={onFailed} />,
      ),
    );
    const input = el.querySelector('input')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
        input,
        'northaven-dental.com',
      );
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => {
      el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });
    const text = el.textContent ?? '';
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
    return { text, onFailed };
  }

  it('a network failure shows the shared sentence, no port number, and still ends the working state', async () => {
    const { text, onFailed } = await submitWith(() => Promise.reject(new TypeError('Failed to fetch')));
    expect(text).toContain(AWAY);
    expect(text).not.toContain('8000');
    expect(text).not.toContain('Could not reach the API');
    expect(onFailed).toHaveBeenCalled();
  });

  it('a 5xx without a problem body shows the shared sentence', async () => {
    const { text } = await submitWith(() => Promise.resolve(new Response('<html>Bad gateway</html>', { status: 502 })));
    expect(text).toContain(AWAY);
    expect(text).not.toContain('The server returned 502');
  });

  it('a 4xx problem still shows its own detail', async () => {
    const body = {
      type: '/problems/validation',
      title: 'Invalid',
      status: 422,
      detail: 'That address is not a website.',
      instance: '/api/v1/clients',
    };
    const { text } = await submitWith(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 422,
          headers: { 'Content-Type': 'application/problem+json' },
        }),
      ),
    );
    expect(text).toContain('That address is not a website.');
    expect(text).not.toContain(AWAY);
  });
});
