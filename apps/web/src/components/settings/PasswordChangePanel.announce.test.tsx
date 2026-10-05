// @vitest-environment jsdom

/**
 * The success message of the password form is said in a status region that is on the page first —
 * checklist 3.85, the same family as 3.70's "Link copied." and 3.52's share bar.
 *
 * A message made together with its region is often never read out, because a screen reader
 * announces what a live region gains and mostly not a region that arrives with its text in it. So
 * the region is there from the first render, empty, and the sentence is added to it when the change
 * has worked. `PasswordChangePanel.test.tsx` renders the states statically; this mounts the form in
 * jsdom, the API module mocked at its edge, because "added to a region that was already there" is
 * something only a mounted form can show.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ApiProblem } from '@/lib/api';

const changePassword = vi.fn<(currentPassword: string, newPassword: string) => Promise<void>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      changePassword: (currentPassword: string, newPassword: string) =>
        changePassword(currentPassword, newPassword),
    },
  };
});

// Imported after the mock so the panel sees the mocked module.
const { PasswordChangePanel } = await import('./PasswordChangePanel');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const DONE =
  'Your password has been changed. Every other device has been signed out; this one is still signed in.';
const REGION = '[data-testid="password-status"]';
const CURRENT = 'the-old-password-1';
const NEXT = 'a-new-password-of-length';

let root: Root | null = null;
const observers: MutationObserver[] = [];

beforeEach(() => {
  changePassword.mockReset();
});

afterEach(() => {
  for (const observer of observers.splice(0)) observer.disconnect();
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

/** Let every pending microtask settle: the mocked request resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

function mounted(): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<PasswordChangePanel />));
  return el;
}

const region = (el: HTMLElement) => el.querySelector<HTMLElement>(REGION);
const sentences = (el: HTMLElement) =>
  [...(region(el)?.querySelectorAll('p') ?? [])].map((p) => p.textContent);

/** Type into a controlled field the way a browser does: set the value, then announce it. */
function type(el: HTMLElement, autocomplete: string, value: string) {
  const input = el.querySelector<HTMLInputElement>(`input[autocomplete="${autocomplete}"]`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** Fill both fields and submit, without waiting for the answer. */
function submit(el: HTMLElement) {
  type(el, 'current-password', CURRENT);
  type(el, 'new-password', NEXT);
  act(() => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

/** What a screen reader hears from a live region is what the region GAINS. */
function watch(target: Element) {
  const records: MutationRecord[] = [];
  const observer = new MutationObserver((batch) => records.push(...batch));
  observer.observe(target, { childList: true, subtree: true, characterData: true });
  observers.push(observer);
  return () => {
    records.push(...observer.takeRecords());
    const taken = records.splice(0, records.length);
    return {
      added: taken.flatMap((r) => [...r.addedNodes]),
      removed: taken.flatMap((r) => [...r.removedNodes]),
      edited: taken.some((r) => r.type === 'characterData'),
    };
  };
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('the status region is on the page before there is anything to say', () => {
  it('is there from the first render, empty, and a polite status', () => {
    const el = mounted();

    expect(region(el)).not.toBeNull();
    expect(region(el)!.getAttribute('role')).toBe('status');
    // `status` is polite of itself: no `aria-live` of its own, or only "polite". `assertive` would
    // interrupt what is being read.
    expect([null, 'polite']).toContain(region(el)!.getAttribute('aria-live'));
    expect(region(el)!.textContent).toBe('');
    expect(sentences(el)).toEqual([]);
  });

  it('adds no gap while it is empty: it is not a flex item of the form, which has one', () => {
    const el = mounted();

    // jsdom has no layout, so this pins the structure that gives none. The form is a flex column
    // with a gap, and an empty flex item still has a gap beside it, so the region sits in the
    // wrapper that holds the submit button, which was a flex item already.
    const form = el.querySelector('form')!;
    expect(form.classList.contains('flex-col')).toBe(true);
    expect(form.classList.contains('gap-5')).toBe(true);
    expect(region(el)!.parentElement).not.toBe(form);
    expect(region(el)!.parentElement?.parentElement).toBe(form);
    expect(region(el)!.parentElement?.contains(el.querySelector('button[type="submit"]'))).toBe(true);
  });
});

describe('a change that worked says so in that region', () => {
  it('adds the sentence to the region that was already there, as one new element and no edited text', async () => {
    changePassword.mockResolvedValue(undefined);
    const el = mounted();
    const before = region(el);
    // Not vacuous: the region is there first, and says nothing.
    expect(before).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    const changes = watch(before!);

    submit(el);
    await settle();

    expect(changePassword).toHaveBeenCalledWith(CURRENT, NEXT);
    expect(region(el)).toBe(before);
    expect(sentences(el)).toEqual([DONE]);
    const { added, removed, edited } = changes();
    expect(added).toHaveLength(1);
    expect(added[0]).toBe(before!.querySelector('p'));
    expect(removed).toEqual([]);
    expect(edited).toBe(false);
    // Before 3.85 the paragraph was the live region, made together with its text.
    expect(before!.querySelector('p')!.hasAttribute('role')).toBe(false);
    expect(el.querySelectorAll('p[role="status"]')).toHaveLength(0);
    // The fields are empty again, as before.
    expect(el.querySelector<HTMLInputElement>('input[autocomplete="current-password"]')!.value).toBe('');
    expect(el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!.value).toBe('');
  });

  it('keeps the space the message had: the form’s gap above it and below it', async () => {
    changePassword.mockResolvedValue(undefined);
    const el = mounted();

    submit(el);
    await settle();

    // The paragraph is a flex item no longer, so the gap under it is its own margin: the same
    // `gap-5` the form puts between the rest of its parts.
    expect(region(el)!.querySelector('p')!.classList.contains('mb-5')).toBe(true);
  });

  it('a second change is announced again: the message is taken out when the next press starts, and the same words come back as a new element', async () => {
    changePassword.mockResolvedValueOnce(undefined);
    const el = mounted();
    submit(el);
    await settle();
    const first = region(el)!.querySelector('p');
    expect(first?.textContent).toBe(DONE);

    const out = deferred();
    changePassword.mockReturnValueOnce(out.promise);
    submit(el);

    // While the request is out the region is there and empty: the last message has gone.
    expect(region(el)).not.toBeNull();
    expect(sentences(el)).toEqual([]);
    expect(first!.isConnected).toBe(false);

    await act(async () => out.resolve());
    await settle();

    const second = region(el)!.querySelector('p');
    expect(second?.textContent).toBe(DONE);
    expect(second).not.toBe(first);
    expect(sentences(el)).toEqual([DONE]);
  });
});

describe('a change that did not work says nothing in that region', () => {
  it('a wrong current password goes on its field, and the region stays empty', async () => {
    changePassword.mockRejectedValue(
      new ApiProblem({
        type: '/problems/invalid-credentials',
        title: 'Not your password',
        status: 401,
        detail: 'That is not your current password.',
        instance: '/api/v1/auth/change-password',
      }),
    );
    const el = mounted();
    const changes = watch(region(el)!);

    submit(el);
    await settle();

    expect(el.textContent).toContain('That is not your current password.');
    expect(sentences(el)).toEqual([]);
    expect(changes().added).toEqual([]);
  });

  it('a failure with no answer is the alert, and the region stays empty', async () => {
    changePassword.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = mounted();
    // Not vacuous: the region is there to have said something.
    expect(region(el)).not.toBeNull();

    submit(el);
    await settle();

    expect(el.querySelector('[role="alert"]')?.textContent).toBe(
      'The change did not go through. Check your connection and try again.',
    );
    expect(sentences(el)).toEqual([]);
  });
});
