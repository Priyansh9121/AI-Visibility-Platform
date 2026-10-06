// @vitest-environment jsdom

/**
 * The "Send feedback" link — 2026-10-06.
 *
 * One anchor, drawn only for an address the API sent, whose subject names the shape of the page
 * the user is on (`lib/feedback/subject.test.ts` holds that rule against every route). What is
 * held here is the component: nothing for no address, one link for one, the right subject from
 * the router's pathname, and a name a screen reader and a voice user can say. The name is "Send
 * feedback by email": the words on screen first (WCAG 2.5.3, Label in Name, so a voice user who says
 * what they read is understood), then a visually hidden "by email" so it is not a surprise to a
 * screen-reader user that it opens a mail app. The link never wraps and has a target 24 px tall
 * (WCAG 2.5.8) in the sidebar, where the strip it sits in is narrow.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

// The router's pathname, which a test sets. `null` is what `usePathname` answers outside the router.
const route = vi.hoisted(() => ({ pathname: null as string | null }));
vi.mock('next/navigation', () => ({ usePathname: () => route.pathname }));

const { FeedbackLink, FEEDBACK_LABEL, FEEDBACK_HIDDEN_NAME } = await import('./FeedbackLink');

const ADDRESS = 'pilot-feedback@avp-pilot.example';

const draw = (address: unknown, look?: 'quiet' | 'inline') =>
  renderToStaticMarkup(
    <FeedbackLink address={address as string | null | undefined} {...(look ? { look } : {})} />,
  );

/** The one anchor in some markup, as a DOM element. */
function anchors(html: string): HTMLAnchorElement[] {
  const holder = document.createElement('div');
  holder.innerHTML = html;
  return [...holder.querySelectorAll('a')];
}

/** What a sighted user reads: the link's text with anything visually hidden left out. */
function visibleText(link: HTMLElement): string {
  const copy = link.cloneNode(true) as HTMLElement;
  for (const hidden of copy.querySelectorAll('.avp-visually-hidden')) hidden.remove();
  return copy.textContent ?? '';
}

const classesOf = (link: HTMLElement) => link.className.split(/\s+/).filter(Boolean);

beforeEach(() => {
  route.pathname = null;
});

describe('no address, no link', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['empty', ''],
    ['blank', '   '],
    ['not an address', 'pilot.example'],
    ['two addresses', 'a@pilot.example, b@pilot.example'],
    ['an address with a header in it', 'a@pilot.example?bcc=b@pilot.example'],
    ['a number', 25],
    ['an object', { feedbackEmail: ADDRESS }],
  ])('%s draws nothing at all', (_what, address) => {
    expect(draw(address)).toBe('');
  });
});

describe('an address draws one link', () => {
  it('is an anchor to a mailto of the address', () => {
    const [link, ...others] = anchors(draw(ADDRESS));
    expect(others).toEqual([]);
    expect(link!.getAttribute('href')).toMatch(/^mailto:pilot-feedback@avp-pilot\.example\?subject=/);
  });

  it('says "Send feedback" on screen and is called "Send feedback by email"', () => {
    const [link] = anchors(draw(ADDRESS));
    expect(visibleText(link!)).toBe('Send feedback');
    expect(FEEDBACK_LABEL).toBe('Send feedback');
    // The name a screen reader gives it is its whole text: the visible words, then " by email".
    expect(link!.textContent).toBe('Send feedback by email');
    expect(FEEDBACK_HIDDEN_NAME).toBe(' by email');
  });

  it('has the words on screen at the start of its name, so what a voice user reads is what works', () => {
    const [link] = anchors(draw(ADDRESS));
    expect(link!.textContent!.startsWith(visibleText(link!))).toBe(true);
  });

  it('adds "by email" in one visually hidden span and nothing else: no icon, no second label', () => {
    const html = draw(ADDRESS);
    expect(html).toContain('>Send feedback<span class="avp-visually-hidden"> by email</span></a>');
    const [link] = anchors(html);
    expect(link!.children).toHaveLength(1);
    const [hidden] = link!.children;
    expect(hidden!.tagName).toBe('SPAN');
    expect(hidden!.className).toBe('avp-visually-hidden');
    expect(hidden!.textContent).toBe(' by email');
    for (const attribute of ['aria-label', 'aria-labelledby', 'aria-hidden', 'title']) {
      expect(link!.hasAttribute(attribute), attribute).toBe(false);
      expect(hidden!.hasAttribute(attribute), attribute).toBe(false);
    }
  });

  it('is a real link in the tab order, that opens no window and downloads nothing', () => {
    const [link] = anchors(draw(ADDRESS));
    for (const attribute of ['tabindex', 'target', 'download', 'rel']) {
      expect(link!.hasAttribute(attribute), attribute).toBe(false);
    }
  });

  it('is underlined, so a link is not told from the text round it by colour alone', () => {
    for (const look of ['quiet', 'inline'] as const) {
      expect(classesOf(anchors(draw(ADDRESS, look))[0]!), look).toContain('underline');
    }
  });

  it('never wraps, in either look: "Send feedback" is one line in the narrow sidebar strip', () => {
    for (const look of ['quiet', 'inline'] as const) {
      expect(classesOf(anchors(draw(ADDRESS, look))[0]!), look).toContain('whitespace-nowrap');
    }
  });

  it('is the box its hidden "by email" is positioned against, in both looks', () => {
    // `.avp-visually-hidden` is absolute. Against the page it sat outside the narrow strip's own
    // scroller and gave the whole page a sideways scroll at 360 px and narrower (measured).
    for (const look of ['quiet', 'inline'] as const) {
      expect(classesOf(anchors(draw(ADDRESS, look))[0]!), look).toContain('relative');
    }
  });

  it('has a target at least 24 px tall in the sidebar: the line and 4 px above and below it', () => {
    // WCAG 2.5.8. The sidebar's text is 13 px on a 17.55 px line, so the line alone is too short.
    const classes = classesOf(anchors(draw(ADDRESS))[0]!);
    expect(classes).toContain('py-1');
    expect(classes).toContain('text-ui-sm');
  });

  it('has a look for the sidebar and one for a sentence, and the sidebar’s is the default', () => {
    expect(draw(ADDRESS)).toBe(draw(ADDRESS, 'quiet'));
    expect(draw(ADDRESS, 'inline')).not.toBe(draw(ADDRESS, 'quiet'));
  });
});

describe('the subject follows the page the router says the user is on', () => {
  const subjectOf = (html: string) =>
    new URL(anchors(html)[0]!.getAttribute('href')!).searchParams.get('subject');

  it.each([
    ['/', 'AVP feedback: /'],
    ['/dashboard', 'AVP feedback: /dashboard'],
    ['/settings', 'AVP feedback: /settings'],
    ['/clients', 'AVP feedback: /clients'],
    ['/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/alerts', 'AVP feedback: /clients/…/alerts'],
    ['/scans/scan_01J8Z3Q9XYZ0123456789ABCD/report', 'AVP feedback: /scans/…/report'],
  ])('on %s it says %s', (pathname, subject) => {
    route.pathname = pathname;
    expect(subjectOf(draw(ADDRESS))).toBe(subject);
  });

  it('says only that it is feedback when there is no router to ask', () => {
    route.pathname = null;
    expect(subjectOf(draw(ADDRESS))).toBe('AVP feedback');
  });

  it('never carries an id, a token or a client’s name, whatever the path was', () => {
    route.pathname = '/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/alerts';
    const html = draw(ADDRESS);
    expect(html).not.toMatch(/clnt_|01J8Z3Q9|ACMEDENTAL/);
  });
});
