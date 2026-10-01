// @vitest-environment jsdom

/**
 * The Billing section can be linked to — 2026-10-01.
 *
 * `ScanLimitNotice` sends an agency whose checkout was refused to
 * `/settings#billing`, where Manage billing is. Settings draws its sections only
 * after its requests come back, so when the page arrives there is no Billing
 * section yet and the browser's own scroll to a fragment finds nothing to scroll
 * to. The screen scrolls once the section exists, and moves keyboard focus there
 * with it. Mounted rather than rendered to static markup, because an effect and
 * the DOM it reads are the whole subject.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { SettingsView, type SettingsState } from './SettingsView';
import { ownerMe, seatsWithPending } from '@/lib/settings/__fixtures__/seats';
import { noSubscription } from '@/lib/settings/__fixtures__/billing';
import { BILLING_SECTION_ID, BILLING_SETTINGS_HREF } from '@/lib/settings/sections';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const READY: SettingsState = {
  kind: 'ready',
  me: ownerMe,
  seats: seatsWithPending,
  seatsError: null,
  billing: noSubscription,
  billingError: null,
};

const scrollIntoView = vi.fn();
let root: Root | null = null;

/** Mount the real screen; `show` swaps its state, as the route does when its requests land. */
function mount(state: SettingsState): { show: (next: SettingsState) => void } {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  const show = (next: SettingsState) =>
    act(() => root!.render(<SettingsView state={next} onChanged={() => {}} />));
  show(state);
  return { show };
}

beforeEach(() => {
  scrollIntoView.mockReset();
  // jsdom lays nothing out and scrolls nothing, and has no `scrollIntoView`.
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    value: scrollIntoView,
    configurable: true,
    writable: true,
  });
  window.location.hash = '';
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  window.location.hash = '';
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe('the anchor', () => {
  it('is the one the notice links to', () => {
    // Two literals on purpose: the id here and the href in ScanLimitNotice's
    // own test must be changed together, deliberately.
    expect(BILLING_SECTION_ID).toBe('billing');
    expect(BILLING_SETTINGS_HREF).toBe('/settings#billing');
  });

  it('is on the Billing section, which holds the heading and the panel', () => {
    mount(READY);
    expect(document.querySelectorAll('#billing')).toHaveLength(1);
    const section = document.getElementById('billing')!;
    expect(section.querySelector('h2')?.textContent).toBe('What you pay');
    expect(section.textContent).toContain('No subscription');
    // Only that section: not the one before it, nor the one after.
    expect(section.textContent).not.toContain('Your password');
    expect(section.textContent).not.toContain('What is still missing.');
  });

  it('is not there while the page is still loading', () => {
    mount({ kind: 'loading' });
    expect(document.getElementById('billing')).toBeNull();
  });
});

describe('arriving at /settings#billing', () => {
  it('scrolls to the section once the page is ready, and not before', () => {
    window.location.hash = '#billing';
    const view = mount({ kind: 'loading' });
    expect(scrollIntoView).not.toHaveBeenCalled();
    view.show(READY);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.contexts[0]).toBe(document.getElementById('billing'));
  });

  it('scrolls once: reading its state again does not pull the page back', () => {
    window.location.hash = '#billing';
    const view = mount({ kind: 'loading' });
    view.show(READY);
    view.show({ ...READY });
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it('does nothing for no fragment, or for another one', () => {
    for (const hash of ['', '#team']) {
      scrollIntoView.mockReset();
      window.location.hash = hash;
      const view = mount({ kind: 'loading' });
      view.show(READY);
      expect(scrollIntoView, `hash "${hash}"`).not.toHaveBeenCalled();
      act(() => root?.unmount());
      root = null;
      document.body.innerHTML = '';
    }
  });

  it('does nothing when the page failed to load', () => {
    window.location.hash = '#billing';
    mount({ kind: 'error', title: 'Your settings could not be loaded', detail: 'x' });
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});

describe('arriving at /settings#billing by keyboard', () => {
  // Scrolling moves the view, not the place Tab starts from. Without focus
  // going to the section too, somebody who followed "Open Settings" from the
  // keyboard would tab on from the top of the page, a whole screen away from
  // Manage billing.
  const spyOnFocus = () => vi.spyOn(HTMLElement.prototype, 'focus');
  let focus: ReturnType<typeof spyOnFocus>;

  /** Something that already has focus, as a control the person was using would. */
  function somethingElseHasFocus(): HTMLButtonElement {
    const other = document.createElement('button');
    document.body.appendChild(other);
    other.focus();
    return other;
  }

  beforeEach(() => {
    focus = spyOnFocus();
  });

  afterEach(() => {
    focus.mockRestore();
  });

  it('takes focus with the scroll, and does not scroll a second time to do it', () => {
    window.location.hash = '#billing';
    const view = mount({ kind: 'loading' });
    expect(document.activeElement).toBe(document.body);
    view.show(READY);
    const section = document.getElementById('billing')!;
    expect(document.activeElement).toBe(section);
    expect(focus.mock.contexts).toContain(section);
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it('can take it, and is not a tab stop of its own', () => {
    // `tabindex="-1"`: focusable by script, never reached by Tab, so nothing
    // changes for anybody who did not arrive at the fragment.
    mount(READY);
    expect(document.getElementById('billing')!.getAttribute('tabindex')).toBe('-1');
  });

  it.each(['', '#team'])('with the fragment "%s", focus stays where it was', (hash) => {
    window.location.hash = hash;
    const other = somethingElseHasFocus();
    const view = mount({ kind: 'loading' });
    view.show(READY);
    expect(document.activeElement).toBe(other);
    expect(focus.mock.contexts).not.toContain(document.getElementById('billing'));
  });

  it('a loading or failed screen takes no focus', () => {
    window.location.hash = '#billing';
    const view = mount({ kind: 'loading' });
    expect(document.activeElement).toBe(document.body);
    view.show({ kind: 'error', title: 'Your settings could not be loaded', detail: 'x' });
    expect(document.activeElement).toBe(document.body);
  });
});
