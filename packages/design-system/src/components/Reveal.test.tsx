// @vitest-environment jsdom
//
// The ONLY file in this package that asks for a DOM, and it asks per-file
// rather than by config on purpose: the other 125 assertions here are static
// renders that run faster and more predictably in `node`, and switching the
// whole package to jsdom to test one component would have changed the
// environment under all of them for no reason.
//
// A DOM is genuinely required. This component's contract is "starts hidden,
// becomes revealed when an observer says so" — the interesting half only
// happens in an effect, which `renderToStaticMarkup` never runs. A test that
// could only see the static output would assert the hidden state and call it
// covered, which is precisely the shape of test that passes while a marketing
// page renders blank.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { Reveal, RevealGroup } from './Reveal.js';
import { PageSection } from './marketing/PageSection.js';

// React 19 wants this flag set before act() is used.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * A fake IntersectionObserver whose callbacks this test fires by hand.
 *
 * Mocked the way this codebase mocks every other browser/network seam — a
 * small hand-written stub that records what it was asked to do, rather than a
 * generic auto-mock. What matters here is WHEN the callback runs, and a stub
 * that fires on demand is the only way to control that.
 */
class FakeObserver {
  static instances: FakeObserver[] = [];
  readonly observed: Element[] = [];
  disconnected = false;

  constructor(private readonly callback: IntersectionObserverCallback) {
    FakeObserver.instances.push(this);
  }

  observe(el: Element): void {
    this.observed.push(el);
  }

  disconnect(): void {
    this.disconnected = true;
  }

  unobserve(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  /** Fire the callback as a real observer does for an element in view. */
  fire(isIntersecting = true): void {
    this.callback(
      this.observed.map((target) => ({ target, isIntersecting })) as IntersectionObserverEntry[],
      this as unknown as IntersectionObserver,
    );
  }
}

function setReducedMotion(reduce: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('prefers-reduced-motion'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}

let container: HTMLDivElement;
let root: Root;

function mount(node: React.ReactNode): HTMLDivElement {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(node);
  });
  return container;
}

beforeEach(() => {
  FakeObserver.instances = [];
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  setReducedMotion(false);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe('a single Reveal', () => {
  it('starts hidden', () => {
    const el = mount(<Reveal>content</Reveal>).firstElementChild!;
    expect(el.className).toContain('avp-reveal');
    expect(el.className).not.toContain('avp-reveal--revealed');
  });

  it('reveals once its observer says it is on screen', () => {
    const host = mount(<Reveal>content</Reveal>);
    expect(FakeObserver.instances).toHaveLength(1);

    act(() => FakeObserver.instances[0]!.fire());

    expect(host.firstElementChild!.className).toContain('avp-reveal--revealed');
  });

  it('ignores a callback that says it is NOT on screen', () => {
    // An observer fires immediately after observe() for every element,
    // on-screen or not. Treating that first callback as "arrived" would
    // reveal the whole page at once and make the scroll trigger a no-op.
    const host = mount(<Reveal>content</Reveal>);
    act(() => FakeObserver.instances[0]!.fire(false));
    expect(host.firstElementChild!.className).not.toContain('avp-reveal--revealed');
  });

  it('stops observing once it has revealed', () => {
    mount(<Reveal>content</Reveal>);
    act(() => FakeObserver.instances[0]!.fire());
    expect(FakeObserver.instances[0]!.disconnected).toBe(true);
  });

  it('does not un-reveal when the element scrolls back off screen', () => {
    const host = mount(<Reveal>content</Reveal>);
    const observer = FakeObserver.instances[0]!;
    act(() => observer.fire(true));
    act(() => observer.fire(false));
    expect(host.firstElementChild!.className).toContain('avp-reveal--revealed');
  });

  it('renders the element the caller asks for', () => {
    const host = mount(
      <ul>
        <Reveal as="li">one</Reveal>
      </ul>,
    );
    expect(host.querySelector('li')).not.toBeNull();
    expect(host.querySelector('li')!.className).toContain('avp-reveal');
  });
});

describe('prefers-reduced-motion', () => {
  it('renders already revealed, with no observer created at all', () => {
    setReducedMotion(true);
    const host = mount(<Reveal>content</Reveal>);

    expect(host.firstElementChild!.className).toContain('avp-reveal--revealed');
    // Not "revealed quickly" — never observed. There is nothing to wait for.
    expect(FakeObserver.instances).toHaveLength(0);
  });

  it('reveals a whole group immediately, with no stagger index emitted', () => {
    setReducedMotion(true);
    const host = mount(
      <RevealGroup>
        <Reveal index={0}>a</Reveal>
        <Reveal index={1}>b</Reveal>
        <Reveal index={2}>c</Reveal>
      </RevealGroup>,
    );

    const items = host.querySelectorAll('.avp-reveal');
    expect(items).toHaveLength(3);
    for (const item of items) {
      expect(item.className).toContain('avp-reveal--revealed');
    }
  });
});

describe('when the browser cannot observe', () => {
  it('reveals immediately rather than leaving the page blank', () => {
    // The failure mode this guards is not "no animation". It is a marketing
    // page that renders nothing, because every revealed thing starts at
    // opacity 0. Content visibility must never depend on an optional API.
    vi.stubGlobal('IntersectionObserver', undefined);
    const host = mount(<Reveal>content</Reveal>);
    expect(host.firstElementChild!.className).toContain('avp-reveal--revealed');
  });
});

describe('a RevealGroup', () => {
  it('observes itself once, not each child', () => {
    mount(
      <RevealGroup>
        <Reveal index={0}>a</Reveal>
        <Reveal index={1}>b</Reveal>
        <Reveal index={2}>c</Reveal>
      </RevealGroup>,
    );
    // Three children, ONE observer. Observing each separately would make the
    // stagger depend on scroll speed instead of being an authored rhythm.
    expect(FakeObserver.instances).toHaveLength(1);
  });

  it('reveals every child together when it arrives', () => {
    const host = mount(
      <RevealGroup>
        <Reveal index={0}>a</Reveal>
        <Reveal index={1}>b</Reveal>
      </RevealGroup>,
    );
    act(() => FakeObserver.instances[0]!.fire());

    for (const item of host.querySelectorAll('.avp-reveal')) {
      expect(item.className).toContain('avp-reveal--revealed');
    }
  });

  it('offsets each child by its index, in CSS rather than in milliseconds', () => {
    const host = mount(
      <RevealGroup>
        <Reveal index={0}>a</Reveal>
        <Reveal index={1}>b</Reveal>
        <Reveal index={2}>c</Reveal>
      </RevealGroup>,
    );
    const items = [...host.querySelectorAll('.avp-reveal')] as HTMLElement[];

    // Index 0 emits nothing — the default is already 0, and an inline
    // `--avp-reveal-index: 0` is noise in every first child on the page.
    expect(items[0]!.getAttribute('style')).toBeNull();
    expect(items[1]!.style.getPropertyValue('--avp-reveal-index')).toBe('1');
    expect(items[2]!.style.getPropertyValue('--avp-reveal-index')).toBe('2');

    // No duration or delay is computed in JavaScript anywhere.
    for (const item of items) {
      expect(item.getAttribute('style') ?? '').not.toMatch(/\d+ms/);
    }
  });

  it('takes a step override as a custom property, not as arithmetic', () => {
    const host = mount(
      <RevealGroup step={40}>
        <Reveal index={1}>a</Reveal>
      </RevealGroup>,
    );
    const group = host.firstElementChild as HTMLElement;
    expect(group.style.getPropertyValue('--avp-stagger-reveal')).toBe('40ms');
  });
});

describe('animate={false}', () => {
  it('renders the finished state with no observer', () => {
    const host = mount(<Reveal animate={false}>content</Reveal>);
    expect(host.firstElementChild!.className).toContain('avp-reveal--revealed');
    expect(FakeObserver.instances).toHaveLength(0);
  });

  it('is what a static render produces, so tests assert finished markup', () => {
    const markup = renderToStaticMarkup(<Reveal animate={false}>content</Reveal>);
    expect(markup).toContain('avp-reveal--revealed');
  });
});

describe('PageSection stagger is opt-in', () => {
  it('emits no reveal markup by default, so Settings is untouched', () => {
    // Five sections of Settings and one of Welcome are built from this
    // component. A default of `true` would have animated screens this brief
    // deliberately leaves alone.
    const markup = renderToStaticMarkup(
      <PageSection eyebrow="Team" heading="Who can sign in" lead="Every seat.">
        body
      </PageSection>,
    );
    expect(markup).not.toContain('avp-reveal');
    expect(markup).toContain('avp-section');
  });

  it('staggers its four parts in order when asked', () => {
    const host = mount(
      <PageSection stagger eyebrow="Pricing" heading="One plan" lead="A lead.">
        body
      </PageSection>,
    );
    const items = [...host.querySelectorAll('.avp-reveal')] as HTMLElement[];
    expect(items).toHaveLength(4);
    expect(items[1]!.style.getPropertyValue('--avp-reveal-index')).toBe('1');
    expect(items[3]!.style.getPropertyValue('--avp-reveal-index')).toBe('3');
  });

  it('closes the gap a missing part would leave in the sequence', () => {
    // Indices are handed out as parts are rendered, not fixed per slot, so a
    // section with no eyebrow does not begin with an unexplained pause.
    const host = mount(
      <PageSection stagger heading="One plan" lead="A lead.">
        body
      </PageSection>,
    );
    const items = [...host.querySelectorAll('.avp-reveal')] as HTMLElement[];
    expect(items).toHaveLength(3);
    expect(items[0]!.getAttribute('style')).toBeNull();
    expect(items[1]!.style.getPropertyValue('--avp-reveal-index')).toBe('1');
    expect(items[2]!.style.getPropertyValue('--avp-reveal-index')).toBe('2');
  });
});
