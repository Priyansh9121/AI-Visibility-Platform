// @vitest-environment jsdom

/**
 * /privacy and /terms, mounted — 2026-09-29.
 *
 * Google's review, and any prospect, reaches these pages signed out, so what
 * matters is that they render with no session at all and never ask who is
 * signed in: a session call is the thing that could redirect them. The real
 * route components in jsdom, with the API client mocked at its edge and
 * watched (`WorkspaceShell.test.tsx`'s pattern), effects and all.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, type ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const me = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, me: (...args: unknown[]) => me(...args) } };
});

// Imported after the mock so every module under the routes sees it.
const { default: PrivacyRoute } = await import('./privacy/page');
const { default: TermsRoute } = await import('./terms/page');
const { MarketingHeader } = await import('@/components/marketing/MarketingHeader');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;

async function mount(Component: ComponentType): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<Component />));
  await settle();
  return el;
}

beforeEach(() => {
  me.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('both routes render signed out and never ask who is signed in', () => {
  it.each([
    ['/privacy', PrivacyRoute, 'How this service handles personal information.'],
    ['/terms', TermsRoute, 'The terms of using this service.'],
  ] as const)('%s', async (_path, Route, title) => {
    const el = await mount(Route);
    expect(el.querySelector('h1')?.textContent).toBe(title);
    expect(el.querySelector('footer a[href="/privacy"]')).not.toBeNull();
    expect(me).not.toHaveBeenCalled();
  });
});

describe('the shared header, away from the landing page', () => {
  it('opens the sign-in and sign-up forms on `/` by URL', async () => {
    const went: string[] = [];
    const el = await mount(() => <MarketingHeader navigate={(href) => went.push(href)} />);
    const button = (label: string) =>
      [...el.querySelectorAll('button')].find((b) => b.textContent === label)!;
    act(() => button('Log in').click());
    act(() => button('Get started free').click());
    expect(went).toEqual(['/?view=sign-in', '/?view=sign-up']);
    expect(el.querySelector('a')?.getAttribute('href')).toBe('/');
  });

  it('on the landing page, still hands both buttons to the page’s own view machine', async () => {
    const calls: string[] = [];
    const el = await mount(() => (
      <MarketingHeader
        sections={{ productId: 'product', pricingId: 'pricing' }}
        onLogIn={() => calls.push('log in')}
        onGetStarted={() => calls.push('get started')}
        navigate={() => calls.push('navigated')}
      />
    ));
    for (const b of el.querySelectorAll('button')) act(() => b.click());
    expect(calls).toEqual(['log in', 'get started']);
    expect(el.querySelector('a')?.getAttribute('href')).toBe('#top');
  });
});
