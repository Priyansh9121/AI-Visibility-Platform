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
    const html = el.innerHTML;
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
    return { text, html, onFailed };
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

  it('the sentence is about the connection, so it is not drawn on the address field — launch checklist 3.39', async () => {
    const { html } = await submitWith(() => Promise.reject(new TypeError('Failed to fetch')));
    expect(html).toContain('avp-errorstate');
    expect(html).not.toContain('avp-field__error');
    expect(html).not.toContain('aria-invalid');
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

describe('an error that is not about the address is not drawn on the address — launch checklist 3.39', () => {
  // The field shows what is wrong with the address: nothing typed, a 409 (`invalid-url`, or
  // already tracked) and a 422 on the `url`. Everything else the server says is shown at
  // the top of the form, above a field that stays as it was.
  const problem = (status: number, type: string, title: string, detail: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify({ type: `/problems/${type}`, title, status, detail, instance: '/api/v1/clients' }),
        { status, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    );
  const BUSY_AGENCY = 'Your agency already has a site being read. Try again in a minute.';
  const BUSY_CRAWLER = 'The crawler is busy with other pages right now. Try again in a minute.';

  async function submitOnce(fetchImpl: () => Promise<Response>, typed = 'northaven-dental.com') {
    vi.stubGlobal('fetch', vi.fn(fetchImpl));
    const onFailed = vi.fn();
    const el = document.createElement('div');
    document.body.appendChild(el);
    const root = createRoot(el);
    act(() =>
      root.render(<IntakeForm onClassified={() => {}} onStarted={() => {}} onFailed={onFailed} />),
    );
    const input = el.querySelector('input')!;
    const type = (value: string) =>
      act(() => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    type(typed);
    const submit = async () => {
      act(() => {
        el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      });
      await act(async () => {
        for (let i = 0; i < 6; i += 1) await Promise.resolve();
      });
    };
    await submit();
    const snapshot = () => ({
      html: el.innerHTML,
      text: el.textContent ?? '',
      field: el.querySelector('.avp-field')!.outerHTML,
      fieldText: el.querySelector('.avp-field')!.textContent ?? '',
      alert: el.querySelector('.avp-errorstate')?.textContent ?? null,
    });
    return { snapshot, submit, type, onFailed, done: () => {
      act(() => root.unmount());
      document.body.innerHTML = '';
      vi.unstubAllGlobals();
    } };
  }

  it.each([
    ['the agency already has a site being read', BUSY_AGENCY],
    ['the crawler is busy', BUSY_CRAWLER],
  ])('a 429, %s, is shown above the form with the address field left alone', async (_why, detail) => {
    const form = await submitOnce(() => problem(429, 'rate-limited', 'Too many requests', detail));
    const { html, field, fieldText, alert } = form.snapshot();
    form.done();
    expect(alert).toContain(detail);
    expect(alert).toContain('Could not start reading the site');
    expect(fieldText).not.toContain(detail);
    expect(html).toContain('role="alert"');
    // The address is fine, so the field says nothing is wrong with it: no error text, no
    // invalid state, no `aria-invalid`, and the hint it always carries is still there.
    expect(field).not.toContain('avp-field__error');
    expect(field).not.toContain('is-invalid');
    expect(field).not.toContain('aria-invalid');
    expect(field).toContain('Paste any page on the site');
    expect(html.split(detail).length - 1).toBe(1);
    expect(form.onFailed).toHaveBeenCalled();
  });

  it('the alert comes before the field, at the top of the form', async () => {
    const form = await submitOnce(() => problem(429, 'rate-limited', 'Too many requests', BUSY_AGENCY));
    const { html } = form.snapshot();
    form.done();
    expect(html.indexOf('avp-errorstate')).toBeGreaterThan(-1);
    expect(html.indexOf('avp-errorstate')).toBeLessThan(html.indexOf('avp-field'));
  });

  it.each([
    [401, 'authentication-required', 'Authentication required', 'Authentication required'],
    [403, 'origin-not-allowed', 'Origin not allowed', 'Origin not allowed'],
    [403, 'permission-denied', 'No permission', 'You do not have permission to perform this action'],
  ])('a %i (%s) is not about the address either: above the form, the field clean', async (status, type, title, detail) => {
    const form = await submitOnce(() => problem(status, type, title, detail));
    const { field, fieldText, alert } = form.snapshot();
    form.done();
    expect(alert).toContain(detail);
    expect(field).not.toContain('avp-field__error');
    expect(field).not.toContain('aria-invalid');
    expect(fieldText).not.toContain(detail);
  });

  it.each([
    ['a 409 that says it is not a website address', 409, 'invalid-url', 'That does not look like a website address', "'northaven' does not contain a public domain name. Enter a website address such as example.com."],
    ['a 409 that says the address is already tracked', 409, 'conflict', 'Conflicting request', 'northaven-dental.com is already tracked by this agency.'],
    ['a 422 on the address', 422, 'validation-failed', 'Request validation failed', 'That address is not a website.'],
  ])('%s is about the address: on the field, invalid and announced, with no alert above the form', async (_what, status, type, title, detail) => {
    const form = await submitOnce(() => problem(status, type, title, detail));
    const { html, field, fieldText, alert } = form.snapshot();
    form.done();
    expect(field).toContain('avp-field__error');
    expect(field).toContain('is-invalid');
    expect(field).toContain('aria-invalid="true"');
    expect(field).toContain('role="alert"');
    expect(fieldText).toContain(detail);
    expect(alert).toBeNull();
    expect(html).not.toContain('avp-errorstate');
    expect(form.onFailed).toHaveBeenCalled();
  });

  it('nothing typed is about the address: on the field, with no request and no alert above the form', async () => {
    const fetchSpy = vi.fn(() => Promise.reject(new TypeError('should not be called')));
    const form = await submitOnce(fetchSpy, '   ');
    const { html, field, alert } = form.snapshot();
    form.done();
    expect(field).toContain('Enter a website address to scan.');
    expect(field).toContain('aria-invalid="true"');
    expect(alert).toBeNull();
    expect(html).not.toContain('avp-errorstate');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('an empty submit clears the alert an earlier attempt left above the form, and says only what is wrong with the address', async () => {
    // Nothing is sent on an empty submit, so what the last attempt's 429 said is not about it:
    // left up, it sat above a field that also said "Enter a website address", two errors for one press.
    const form = await submitOnce(() => problem(429, 'rate-limited', 'Too many requests', BUSY_AGENCY));
    expect(form.snapshot().alert).toContain(BUSY_AGENCY);

    form.type('   ');
    await form.submit();
    const { html, field, fieldText, alert } = form.snapshot();
    form.done();
    expect(alert).toBeNull();
    expect(html).not.toContain('avp-errorstate');
    expect(html).not.toContain(BUSY_AGENCY);
    expect(fieldText).toContain('Enter a website address to scan.');
    expect(field).toContain('aria-invalid="true"');
  });

  it('a new attempt clears what the last one said, from the field and from above the form', async () => {
    let attempt = 0;
    const form = await submitOnce(() => {
      attempt += 1;
      return attempt === 1
        ? problem(429, 'rate-limited', 'Too many requests', BUSY_AGENCY)
        : problem(409, 'invalid-url', 'That does not look like a website address', 'That is not a website.');
    });
    expect(form.snapshot().alert).toContain(BUSY_AGENCY);

    await form.submit();
    const second = form.snapshot();
    expect(second.alert).toBeNull();
    expect(second.html).not.toContain(BUSY_AGENCY);
    expect(second.field).toContain('That is not a website.');

    attempt = 0;
    await form.submit();
    const third = form.snapshot();
    form.done();
    expect(third.alert).toContain(BUSY_AGENCY);
    expect(third.field).not.toContain('That is not a website.');
    expect(third.field).not.toContain('aria-invalid');
  });
});
