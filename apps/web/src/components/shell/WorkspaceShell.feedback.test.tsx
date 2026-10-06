/**
 * "Send feedback" in the signed-in footer — 2026-10-06.
 *
 * The link is drawn at the foot of the sidebar when the page tells the shell an address (`me`'s
 * `feedbackEmail`), and only then. The default is the point: with `FEEDBACK_EMAIL` unset the API
 * says null, and the shell must draw exactly what it drew before the feature existed. A static
 * render, like the shell's other placement tests; `WorkspaceShell.test.tsx` mounts the real
 * component for what only a mount can show.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ReactNode } from 'react';

const route = vi.hoisted(() => ({ pathname: null as string | null }));
vi.mock('next/navigation', () => ({ usePathname: () => route.pathname }));

vi.mock('@/lib/api', () => ({ api: { logOut: async () => undefined } }));

const { WorkspaceShell } = await import('./WorkspaceShell');

const ADDRESS = 'pilot-feedback@avp-pilot.example';
const SEATS = { used: 2, limit: 3 };
const CLIENT = {
  id: 'clnt_01J8Z3Q9ACMEDENTAL0123456',
  name: 'acme-dental.com',
  brandName: 'Acme Dental',
  domain: 'acme-dental.com',
  section: 'alerts' as const,
  latestReportScanId: 'scan_01J8Z3Q9XYZ0123456789ABCD',
  latestScore: 61,
};

type Props = Parameters<typeof WorkspaceShell>[0];
const draw = (props: Partial<Props> = {}, body: ReactNode = <p>the page</p>) =>
  renderToStaticMarkup(
    <WorkspaceShell current="dashboard" agencyName="Northlight Partners" seats={SEATS} {...props}>
      {body}
    </WorkspaceShell>,
  );

/** The sidebar's foot, as markup. */
function footer(html: string): string {
  const start = html.indexOf('avp-shell__footer');
  expect(start, 'no footer').toBeGreaterThan(-1);
  return html.slice(start, html.indexOf('</nav>', start));
}

/** Every mailto anchor: [whole, href, inner markup]. The inner markup holds the hidden "by email" span. */
const links = (html: string) => [...html.matchAll(/<a [^>]*href="(mailto:[^"]*)"[^>]*>(.*?)<\/a>/g)];
/** What a sighted user reads of some markup: the visually hidden spans dropped, then the tags. */
const visible = (inner: string) =>
  inner.replace(/<span class="avp-visually-hidden">[^<]*<\/span>/g, '').replace(/<[^>]+>/g, '');
/** What a screen reader says of it: every tag dropped. */
const name = (inner: string) => inner.replace(/<[^>]+>/g, '');

beforeEach(() => {
  route.pathname = '/dashboard';
});

describe('with an address, the footer offers the link', () => {
  it('draws one mailto link, in the footer and nowhere else on the page', () => {
    const html = draw({ feedbackEmail: ADDRESS });
    const [only, ...rest] = links(html);
    expect(rest).toEqual([]);
    expect(only![1]).toMatch(/^mailto:pilot-feedback@avp-pilot\.example\?subject=/);
    expect(visible(only![2]!)).toBe('Send feedback');
    expect(name(only![2]!)).toBe('Send feedback by email');
    expect(links(footer(html))).toHaveLength(1);
    expect(html.slice(0, html.indexOf('avp-shell__footer'))).not.toContain('mailto:');
    expect(html.slice(html.indexOf('<main'))).not.toContain('mailto:');
  });

  it('sits between the seat count and Sign out', () => {
    const foot = footer(draw({ feedbackEmail: ADDRESS }));
    const seats = foot.indexOf('2 of 3 seats');
    const feedback = foot.indexOf('Send feedback');
    const signOut = foot.indexOf('Sign out');
    expect(seats).toBeGreaterThan(-1);
    expect(seats).toBeLessThan(feedback);
    expect(feedback).toBeLessThan(signOut);
  });

  it('draws it as the sidebar’s link: on one line, 24 px tall, in secondary text', () => {
    const anchor = footer(draw({ feedbackEmail: ADDRESS })).match(/<a [^>]*href="mailto:[^>]*>/)![0];
    const classes = anchor.match(/class="([^"]*)"/)![1]!.split(/\s+/);
    expect(classes).toEqual(
      expect.arrayContaining(['whitespace-nowrap', 'py-1', 'self-start', 'underline', 'relative']),
    );
    expect(classes).toContain('text-text-secondary');
  });

  it('says the page the user is on, by its shape', () => {
    route.pathname = '/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/alerts';
    const href = links(draw({ feedbackEmail: ADDRESS, client: CLIENT }))[0]![1]!;
    expect(new URL(href).searchParams.get('subject')).toBe('AVP feedback: /clients/…/alerts');
    expect(href).not.toMatch(/clnt_|01J8Z3Q9|acme/i);
  });

  it('is in the client’s sidebar too, where the footer also names the agency', () => {
    route.pathname = '/clients/clnt_01J8Z3Q9ACMEDENTAL0123456/alerts';
    const html = draw({ feedbackEmail: ADDRESS, client: CLIENT });
    expect(links(footer(html))).toHaveLength(1);
    expect(footer(html)).toContain('Northlight Partners');
  });

  it('is there while the page is still loading its own data: the frame draws regardless', () => {
    const html = draw({ feedbackEmail: ADDRESS, agencyName: undefined, seats: undefined }, null);
    expect(links(footer(html))).toHaveLength(1);
    expect(footer(html)).toContain('Sign out');
  });
});

describe('with no address the shell is exactly what it was', () => {
  const without = draw();

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['empty', ''],
    ['blank', '   '],
    ['not an address', 'pilot.example'],
  ])('%s draws byte for byte what omitting it does', (_what, value) => {
    expect(draw({ feedbackEmail: value })).toBe(without);
  });

  it('has nothing about feedback, and no mailto, in any mode', () => {
    for (const html of [without, draw({ client: CLIENT }), draw({ wide: true })]) {
      expect(html).not.toContain('mailto:');
      expect(html.toLowerCase()).not.toContain('feedback');
    }
  });

  it('keeps the footer’s other parts: the seats and Sign out', () => {
    expect(footer(without)).toContain('2 of 3 seats');
    expect(footer(without)).toContain('Sign out');
  });
});
