// @vitest-environment jsdom

/**
 * `useFeedbackEmail` — the address, for a page that reads no `me` of its own — 2026-10-06.
 *
 * Most pages read `GET /auth/me` already (they show the agency's name and its seats in the
 * sidebar) and hand its `feedbackEmail` to the shell. The dashboard and the report read no
 * `me`, so they ask for it through this hook, once, when they mount. It answers null until it
 * knows, null for an API that has no such field yet, and null for any failure: the link is for a
 * page that was told an address, never for one that guessed. Mounted in jsdom, the API mocked at
 * its edge.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const me = vi.fn();
vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, me: () => me() } };
});

const { useFeedbackEmail } = await import('./useFeedbackEmail');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ADDRESS = 'pilot-feedback@avp-pilot.example';

function Probe() {
  return <p data-testid="address">{JSON.stringify(useFeedbackEmail())}</p>;
}

let root: Root | null = null;

async function mount(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<Probe />);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return el;
}

const said = (el: HTMLElement) =>
  JSON.parse(el.querySelector('[data-testid="address"]')!.textContent!) as string | null;

beforeEach(() => {
  me.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the address, from the one read of /auth/me', () => {
  it('is the address the API sent', async () => {
    me.mockResolvedValue({ feedbackEmail: ADDRESS });
    expect(said(await mount())).toBe(ADDRESS);
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('is null until the answer comes', async () => {
    let answer: (value: unknown) => void = () => {};
    me.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => {
      root!.render(<Probe />);
    });
    expect(said(el)).toBeNull();
    await act(async () => {
      answer({ feedbackEmail: ADDRESS });
    });
    expect(said(el)).toBe(ADDRESS);
  });

  it.each([
    ['the API says null: not set', { feedbackEmail: null }],
    ['an API with no such field yet', {}],
    ['an empty string', { feedbackEmail: '' }],
    ['something that is not an address', { feedbackEmail: 'pilot.example' }],
    ['two addresses', { feedbackEmail: 'a@pilot.example, b@pilot.example' }],
    ['a number', { feedbackEmail: 25 }],
    ['no body at all', undefined],
  ])('is null for %s', async (_what, body) => {
    me.mockResolvedValue(body);
    expect(said(await mount())).toBeNull();
  });

  it.each([
    ['a 401', new ApiProblem({ type: '/problems/x', title: 'x', status: 401, detail: 'x', instance: '/x' })],
    ['a server error', new ApiProblem({ type: '/problems/x', title: 'x', status: 500, detail: 'x', instance: '/x' })],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])('is null, and unhandled nowhere, for %s', async (_what, failure) => {
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on('unhandledRejection', onUnhandled);
    try {
      me.mockRejectedValue(failure);
      const el = await mount();
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(said(el)).toBeNull();
      expect(unhandled).toEqual([]);
    } finally {
      process.off('unhandledRejection', onUnhandled);
    }
  });

  it('is null when the API module has no such call at all', async () => {
    me.mockImplementation(() => {
      throw new TypeError('api.me is not a function');
    });
    expect(said(await mount())).toBeNull();
  });

  it('asks once however often the page draws again, and not after it has gone', async () => {
    let answer: (value: unknown) => void = () => {};
    me.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => {
      root!.render(<Probe />);
    });
    await act(async () => {
      root!.render(<Probe />);
    });
    expect(me).toHaveBeenCalledTimes(1);
    // The page is gone before the answer comes: nothing is set on it, and nothing complains.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    act(() => root!.unmount());
    root = null;
    await act(async () => {
      answer({ feedbackEmail: ADDRESS });
    });
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});
