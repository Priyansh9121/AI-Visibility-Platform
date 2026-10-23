// @vitest-environment jsdom

/**
 * The "Check who has access" notice through the Settings route — A6 part 4, launch checklist 3.123.
 *
 * `SeatsPanel.accessReview.test.tsx` holds the notice itself. What only the route can show is that
 * it is wired to the page: the seat list the route reads carries the review into the Team section,
 * the notice is the first thing in it, and "Keep these people" ends with the route reading
 * everything again, so the notice is gone because the API stopped sending it and not because the
 * screen forgot it. The real route and screen, the API mocked at its edge.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { BillingPlan, BillingStatus, SeatList } from '@avp/shared-types';
import {
  adminMe,
  memberMe,
  ownerMe,
  seatsWithAccessReview,
  seatsWithPending,
} from '@/lib/settings/__fixtures__/seats';
import { noSubscriptionTrialOffered } from '@/lib/settings/__fixtures__/billing';

const me = vi.fn();
const seats = vi.fn<() => Promise<SeatList>>();
const billing = vi.fn<() => Promise<BillingStatus>>();
const billingPlan = vi.fn<() => Promise<BillingPlan>>();
const keepAccessReview = vi.fn<() => Promise<void>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      me: () => me(),
      seats: () => seats(),
      billing: () => billing(),
      billingPlan: () => billingPlan(),
      keepAccessReview: () => keepAccessReview(),
    },
  };
});

// Imported after the mock so the route sees it.
const { default: SettingsRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HEADING = 'Check who has access';
const KEEP = 'Keep these people';

let root: Root | null = null;

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function arrive(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<SettingsRoute />);
  });
  await flush();
  return el;
}

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

/** The Team section: the one whose heading says who can sign in. */
const teamSection = (el: HTMLElement) =>
  [...el.querySelectorAll('section')].find((s) => s.textContent?.includes('Who can sign in'));

beforeEach(() => {
  for (const mock of [me, seats, billing, billingPlan, keepAccessReview]) mock.mockReset();
  me.mockResolvedValue(ownerMe);
  seats.mockResolvedValue(seatsWithAccessReview);
  billing.mockResolvedValue(noSubscriptionTrialOffered);
  billingPlan.mockResolvedValue({ trialDays: 30 });
  keepAccessReview.mockResolvedValue(undefined);
});

afterEach(async () => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('the Team section opens with the notice when the seat list carries a review', () => {
  it('shows it first in Team, above the roster, to an owner', async () => {
    const el = await arrive();
    const team = teamSection(el)!;
    expect(team).toBeDefined();
    const notice = team.querySelector('[data-testid="access-review"]');
    expect(notice?.querySelector('h3')?.textContent).toBe(HEADING);
    const text = team.textContent ?? '';
    expect(text.indexOf(HEADING)).toBeLessThan(text.indexOf('seats in use'));
    // Only the Team section has it.
    expect(el.querySelectorAll('[data-testid="access-review"]')).toHaveLength(1);
  });

  it('shows it to an admin, who can manage seats too', async () => {
    me.mockResolvedValue(adminMe);
    const el = await arrive();
    expect(el.textContent).toContain(HEADING);
  });

  it('shows nothing when the seat list carries none', async () => {
    seats.mockResolvedValue(seatsWithPending);
    const el = await arrive();
    expect(el.textContent).toContain('seats in use');
    expect(el.textContent).not.toContain(HEADING);
    expect(button(el, KEEP)).toBeUndefined();
  });

  it('shows a member nothing: their seat list is refused, as it always was', async () => {
    me.mockResolvedValue(memberMe);
    seats.mockRejectedValue(
      new ApiProblem({
        type: '/problems/permission-denied',
        title: 'Forbidden',
        status: 403,
        detail: 'This action requires one of: admin, owner. Your role is member.',
        instance: '/api/v1/agencies/x/seats',
      }),
    );
    const el = await arrive();
    expect(el.textContent).toContain('The team list is not available');
    expect(el.textContent).not.toContain(HEADING);
  });
});

describe('Keep these people ends with the route reading everything again', () => {
  it('asks the API to keep, reads the page again, and the notice is gone because the API stopped sending it', async () => {
    const el = await arrive();
    expect(me).toHaveBeenCalledTimes(1);
    expect(seats).toHaveBeenCalledTimes(1);
    // What the API answers once the review is closed.
    seats.mockResolvedValue(seatsWithPending);

    await act(async () => {
      button(el, KEEP)!.click();
    });
    await flush();

    expect(keepAccessReview).toHaveBeenCalledTimes(1);
    expect(me).toHaveBeenCalledTimes(2);
    expect(seats).toHaveBeenCalledTimes(2);
    expect(el.textContent).not.toContain(HEADING);
    expect(button(el, KEEP)).toBeUndefined();
    // Nobody was removed by keeping them: the roster still holds every person.
    expect(el.textContent).toContain('Sam Okafor');
    expect(el.textContent).toContain('rae@northlight.example');
  });

  it('a refusal leaves the notice, and the page is not read again', async () => {
    keepAccessReview.mockRejectedValue(
      new ApiProblem({
        type: '/problems/authentication-required',
        title: 'Unauthorized',
        status: 401,
        detail: 'Session is expired or invalid.',
        instance: '/api/v1/auth/me/access-review/keep',
      }),
    );
    const el = await arrive();
    await act(async () => {
      button(el, KEEP)!.click();
    });
    await flush();

    // Every page that mounts the shell has one standing, empty `role="alert"` in the sidebar
    // footer (the sign-out sentence's region), before `<main>`: the notice's own is looked for
    // inside the notice.
    const notice = el.querySelector('[data-testid="access-review"]')!;
    const alert = notice.querySelector('[role="alert"]')!;
    expect(alert.textContent).toBe('Session is expired or invalid.');
    // Said in the notice, under the button that was pressed.
    expect(notice.contains(alert)).toBe(true);
    // And it is the first alert the page draws after the shell's own.
    expect(el.querySelector('main [role="alert"]')).toBe(alert);
    expect(el.textContent).toContain(HEADING);
    expect(me).toHaveBeenCalledTimes(1);
    expect(seats).toHaveBeenCalledTimes(1);
  });
});
