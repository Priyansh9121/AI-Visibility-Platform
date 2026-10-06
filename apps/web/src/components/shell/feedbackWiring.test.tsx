// @vitest-environment jsdom

/**
 * Every screen that draws the signed-in shell hands it the feedback address — 2026-10-06.
 *
 * The link is the shell's, but the address is the page's to hand over: four screens read
 * `GET /auth/me` already (Compare, Clients, a client's space, Settings) and pass its
 * `feedbackEmail` down as it is; the dashboard takes it from the `me` its standing read already
 * makes (`useScanStanding`, so no request of its own: round 2), and the report, which reads no `me`,
 * asks through `useFeedbackEmail`. What is held here, per screen, is that the footer shows the link for an
 * address, shows nothing for none (the default), and keeps the page when the address cannot be
 * had. The last group reads the sources: the link is drawn from two places only, no public or
 * token screen mentions feedback, and no page that draws the shell can forget to hand it over.
 * Pages are mounted in jsdom with the API mocked at its edge, as `WorkspaceShell.test.tsx` does.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { clientsMe, severalClients } from '@/lib/clients/__fixtures__/clients';
import { emptyDashboard } from '@/lib/dashboard/__fixtures__/dashboards';
import { generatedFixesReport } from '@/lib/report/__fixtures__/reports';
import { noSubscription } from '@/lib/settings/__fixtures__/billing';
import { ownerMe } from '@/lib/settings/__fixtures__/seats';

const route = vi.hoisted(() => ({ pathname: null as string | null }));
vi.mock('next/navigation', () => ({ usePathname: () => route.pathname }));

const me = vi.fn();
const dashboard = vi.fn();
const report = vi.fn();
const billing = vi.fn();
const billingPlan = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      me: () => me(),
      dashboard: () => dashboard(),
      report: (id: string) => report(id),
      billing: (id: string) => billing(id),
      billingPlan: () => billingPlan(),
      logOut: async () => undefined,
    },
  };
});

// The heavy bodies, as stand-ins: what is under test is the frame round them.
vi.mock('@/components/dashboard/DashboardView', () => ({
  DashboardView: () => <div data-testid="dashboard-body">the dashboard</div>,
}));
vi.mock('@/components/report/ReportView', () => ({
  ReportView: () => <div data-testid="report-body">the report</div>,
}));
vi.mock('@/components/report/CompetitorEditor', () => ({ CompetitorEditor: () => null }));
vi.mock('@/components/report/ShareLinkBar', () => ({ ShareLinkBar: () => null }));
vi.mock('@/components/report/DownloadPdfButton', () => ({ DownloadPdfButton: () => null }));

// Imported after the mocks so each page sees the mocked modules.
const { default: Home } = await import('@/app/page');
const { default: DashboardRoute } = await import('@/app/dashboard/page');
const { default: ReportRoute } = await import('@/app/scans/[scanId]/report/page');
const { ClientsView } = await import('@/components/clients/ClientsView');
const { ClientSpace } = await import('@/components/client/ClientSpace');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ADDRESS = 'pilot-feedback@avp-pilot.example';
const SCAN_ID = 'scan_01J8Z3Q9XYZ0123456789ABCD';
const CLIENT_ID = 'clnt_01J8Z3Q9ACMEDENTAL0123456';

/** `me` as the API answers it for somebody signed in. */
const signedIn = (feedbackEmail: string | null) => ({ ...ownerMe, feedbackEmail });

const unauthorised = new ApiProblem({
  type: '/problems/authentication-required',
  title: 'Sign in',
  status: 401,
  detail: 'No session.',
  instance: '/auth/me',
});

/** The sidebar's foot, from a mounted page or a static render. */
function footerOf(html: string): string {
  const start = html.indexOf('avp-shell__footer');
  expect(start, 'the page drew no shell footer').toBeGreaterThan(-1);
  return html.slice(start, html.indexOf('</nav>', start));
}

const mailtos = (html: string) => [...html.matchAll(/href="(mailto:[^"]*)"/g)].map((m) => m[1]!);
const subjectOf = (href: string) => new URL(href).searchParams.get('subject');

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

let root: Root | null = null;

async function mount(node: ReactElement): Promise<HTMLElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(node);
  });
  await settle();
  await settle();
  return el;
}

beforeEach(() => {
  for (const fn of [me, dashboard, report, billing, billingPlan]) fn.mockReset();
  billingPlan.mockResolvedValue({ trialDays: 0 });
  billing.mockResolvedValue(noSubscription);
  dashboard.mockResolvedValue(emptyDashboard);
  report.mockResolvedValue(generatedFixesReport);
  window.history.replaceState(null, '', '/');
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('Compare, which reads `me` and hands the address down', () => {
  beforeEach(() => {
    route.pathname = '/';
  });

  it('shows the link for an address, in the footer, saying it is the front page', async () => {
    me.mockResolvedValue(signedIn(ADDRESS));
    const el = await mount(<Home />);
    expect(el.textContent).toContain('Start with a website.');
    const links = mailtos(footerOf(el.innerHTML));
    expect(links).toHaveLength(1);
    expect(subjectOf(links[0]!)).toBe('AVP feedback: /');
    expect(mailtos(el.innerHTML)).toHaveLength(1);
    // It rode on the read the page makes anyway: no second request.
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('shows nothing for no address: the default', async () => {
    me.mockResolvedValue(signedIn(null));
    const el = await mount(<Home />);
    expect(el.textContent).toContain('Start with a website.');
    expect(mailtos(el.innerHTML)).toEqual([]);
    expect(el.innerHTML.toLowerCase()).not.toContain('feedback');
    expect(me).toHaveBeenCalledTimes(1);
  });
});

describe('Clients and a client’s space, which are handed `me`', () => {
  const client = { id: CLIENT_ID, name: 'acme-dental.com', brandName: 'Acme Dental', domain: 'acme-dental.com' };
  const ready = { kind: 'ready' as const, clients: severalClients, more: false };

  it('Clients: the link for an address, in every state of the screen', () => {
    route.pathname = '/clients';
    for (const state of [{ kind: 'loading' as const }, ready]) {
      const html = renderToStaticMarkup(<ClientsView state={state} me={{ ...clientsMe, feedbackEmail: ADDRESS }} />);
      const links = mailtos(footerOf(html));
      expect(links).toHaveLength(1);
      expect(subjectOf(links[0]!)).toBe('AVP feedback: /clients');
      expect(mailtos(html)).toHaveLength(1);
    }
  });

  it('Clients: nothing for no address, nor while `me` has not arrived', () => {
    route.pathname = '/clients';
    for (const given of [clientsMe, { ...clientsMe, feedbackEmail: null }, null]) {
      const html = renderToStaticMarkup(<ClientsView state={{ kind: 'loading' }} me={given} />);
      expect(mailtos(html)).toEqual([]);
      expect(html.toLowerCase()).not.toContain('feedback');
    }
  });

  it('a client’s space: the link, with the shape of the page and not the client', () => {
    route.pathname = `/clients/${CLIENT_ID}/alerts`;
    const html = renderToStaticMarkup(
      <ClientSpace
        client={client}
        me={{ ...clientsMe, feedbackEmail: ADDRESS }}
        current="alerts"
        latestReportScanId={SCAN_ID}
      >
        <p>alerts</p>
      </ClientSpace>,
    );
    const links = mailtos(footerOf(html));
    expect(links).toHaveLength(1);
    expect(subjectOf(links[0]!)).toBe('AVP feedback: /clients/…/alerts');
    expect(links[0]).not.toMatch(/clnt_|01J8Z3Q9|acme/i);
  });

  it('a client’s space: nothing for no address', () => {
    route.pathname = `/clients/${CLIENT_ID}/alerts`;
    const html = renderToStaticMarkup(
      <ClientSpace client={client} me={clientsMe} current="alerts" latestReportScanId={null}>
        <p>alerts</p>
      </ClientSpace>,
    );
    expect(mailtos(html)).toEqual([]);
    expect(html.toLowerCase()).not.toContain('feedback');
  });
});

describe('the dashboard, which takes the address from the `me` its standing read already makes', () => {
  beforeEach(() => {
    route.pathname = '/dashboard';
  });

  it('shows the link for an address, and reads `me` once: the standing read and nothing more', async () => {
    me.mockResolvedValue(signedIn(ADDRESS));
    const el = await mount(<DashboardRoute />);
    expect(el.querySelector('[data-testid="dashboard-body"]')).not.toBeNull();
    const links = mailtos(footerOf(el.innerHTML));
    expect(links).toHaveLength(1);
    expect(subjectOf(links[0]!)).toBe('AVP feedback: /dashboard');
    expect(mailtos(el.innerHTML)).toHaveLength(1);
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('shows nothing for no address, and still reads `me` once: the feature off costs no request', async () => {
    me.mockResolvedValue(signedIn(null));
    const el = await mount(<DashboardRoute />);
    expect(el.querySelector('[data-testid="dashboard-body"]')).not.toBeNull();
    expect(mailtos(el.innerHTML)).toEqual([]);
    expect(el.innerHTML.toLowerCase()).not.toContain('feedback');
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('keeps the page, with no link, when the address cannot be had', async () => {
    me.mockRejectedValue(unauthorised);
    const el = await mount(<DashboardRoute />);
    expect(el.querySelector('[data-testid="dashboard-body"]')).not.toBeNull();
    expect(mailtos(el.innerHTML)).toEqual([]);
  });

  it('has no link in the loading frame, and makes no request to draw one', async () => {
    // Nothing has asked for `me` yet: the page asks for it only once it has an agency, for the standing.
    dashboard.mockReturnValue(new Promise(() => {}));
    me.mockResolvedValue(signedIn(ADDRESS));
    const el = await mount(<DashboardRoute />);
    expect(el.textContent).toContain('Loading your scans…');
    expect(mailtos(el.innerHTML)).toEqual([]);
    expect(me).not.toHaveBeenCalled();
  });
});

describe('the report, which reads no `me` and asks for the address', () => {
  const params = Object.assign(Promise.resolve({ scanId: SCAN_ID }), {
    status: 'fulfilled' as const,
    value: { scanId: SCAN_ID },
  });

  beforeEach(() => {
    route.pathname = `/scans/${SCAN_ID}/report`;
  });

  it('shows the link for an address, saying the shape of the page and not the scan', async () => {
    me.mockResolvedValue(signedIn(ADDRESS));
    const el = await mount(<ReportRoute params={params} />);
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
    const links = mailtos(footerOf(el.innerHTML));
    expect(links).toHaveLength(1);
    expect(subjectOf(links[0]!)).toBe('AVP feedback: /scans/…/report');
    expect(links[0]).not.toMatch(/scan_|01J8Z3Q9/);
    expect(mailtos(el.innerHTML)).toHaveLength(1);
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('shows nothing for no address: the default', async () => {
    me.mockResolvedValue(signedIn(null));
    const el = await mount(<ReportRoute params={params} />);
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
    expect(mailtos(el.innerHTML)).toEqual([]);
    expect(el.innerHTML.toLowerCase()).not.toContain('feedback');
  });

  it('keeps the report, with no link, when the address cannot be had', async () => {
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<ReportRoute params={params} />);
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
    expect(mailtos(el.innerHTML)).toEqual([]);
  });

  it('is in the frame when the report itself could not be loaded', async () => {
    me.mockResolvedValue(signedIn(ADDRESS));
    report.mockRejectedValue(unauthorised);
    const el = await mount(<ReportRoute params={params} />);
    expect(el.textContent).toContain('Sign in to view this report');
    expect(mailtos(footerOf(el.innerHTML))).toHaveLength(1);
  });
});

// -- the sources ---------------------------------------------------------------------------

// From the module's own path as a string: jsdom replaces `URL`, and `fileURLToPath` will not take its.
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

function sources(dir: string = SRC): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === '__fixtures__' ? [] : sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const SOURCES = sources().map((path) => ({ file: relative(SRC, path), text: readFileSync(path, 'utf8') }));
const filesSaying = (needle: RegExp) => SOURCES.filter(({ text }) => needle.test(text)).map(({ file }) => file).sort();
const count = (text: string, needle: string) => text.split(needle).length - 1;

describe('where the link can be drawn from, read off the sources', () => {
  it('reads the sources it should', () => {
    expect(SOURCES.length).toBeGreaterThan(100);
    expect(SOURCES.some(({ file }) => file === 'components/shell/WorkspaceShell.tsx')).toBe(true);
  });

  it('is drawn from the shell’s footer and from Settings, and from nowhere else', () => {
    expect(filesSaying(/<FeedbackLink\b/)).toEqual([
      'components/settings/SettingsView.tsx',
      'components/shell/WorkspaceShell.tsx',
    ]);
  });

  it('is asked for through its own hook by the report alone, the one page that reads no `me`', () => {
    expect(filesSaying(/=\s*useFeedbackEmail\(\)/)).toEqual(['app/scans/[scanId]/report/page.tsx']);
  });

  it('is taken by the dashboard from the standing read it already makes', () => {
    const dashboardPage = SOURCES.find(({ file }) => file === 'app/dashboard/page.tsx')!.text;
    expect(dashboardPage).toMatch(/feedbackEmail\s*\}\s*=\s*useScanStanding\(/);
    expect(dashboardPage).not.toContain('useFeedbackEmail');
  });

  it('is on no public or token screen, nor in any component they draw', () => {
    const PUBLIC =
      /^(app\/(share|invite|reset-password|sign-up|privacy|terms|crawler|welcome)\/|components\/(marketing|legal|auth|invite|google)\/)/;
    const mentions = SOURCES.filter(({ file }) => PUBLIC.test(file) && /feedback/i.test(readFileSync(join(SRC, file), 'utf8')));
    expect(mentions.map(({ file }) => file)).toEqual([]);
    expect(SOURCES.filter(({ file }) => PUBLIC.test(file)).length).toBeGreaterThan(10);
  });

  it('is handed over by every page that draws the shell: none can leave it out', () => {
    const users = SOURCES.filter(({ text }) => text.includes('<WorkspaceShell'));
    expect(users.length).toBeGreaterThanOrEqual(6);
    for (const { file, text } of users) {
      if (file === 'components/shell/WorkspaceShell.tsx') continue;
      expect(count(text, 'feedbackEmail='), file).toBe(count(text, '<WorkspaceShell'));
    }
  });

  it('reads the address off the response, and never off a URL, a storage or a global', () => {
    for (const file of ['lib/feedback/subject.ts', 'lib/feedback/useFeedbackEmail.ts', 'components/shell/FeedbackLink.tsx']) {
      // Code, not the comments that say what it does not do.
      const text = SOURCES.find((s) => s.file === file)!
        .text.replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      expect(text, file).not.toMatch(/location\.(href|search|hash)|localStorage|sessionStorage|document\.cookie/);
    }
  });
});
