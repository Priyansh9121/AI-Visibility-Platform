/**
 * F4 — `/clients/sources` (no client id) was a 404 in production.
 *
 * Every client screen draws its frame while the client is still loading, and
 * on failure, with a placeholder whose `id` is `''`. `ClientNav` joined that
 * to a section path (`/clients/` + '' + `/sources`), and the browser followed
 * (or prefetched) `/clients/sources`. A nav item either links somewhere real
 * or is not in the sidebar, so with no id the section links are not drawn.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientNav, type ShellClient } from '@/components/shell/sidebars';
import {
  ClientOverviewView,
  ClientRankingsView,
  ClientSourcesView,
} from './ClientDetailView';
import { CLIENT_NAV } from './clientNav';
import { clientsMe } from '@/lib/clients/__fixtures__/clients';

const shellClient = (id: string): ShellClient => ({
  id,
  name: 'Acme',
  brandName: null,
  domain: 'acme.example',
  section: 'sources',
  latestReportScanId: null,
});

/** Every href in the markup that points into the clients tree. */
const clientHrefs = (html: string): string[] =>
  [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]!).filter((h) => h.startsWith('/clients'));

describe('ClientNav without a client id', () => {
  for (const id of ['', '   ']) {
    it(`draws no client link for id ${JSON.stringify(id)}`, () => {
      const html = renderToStaticMarkup(<ClientNav client={shellClient(id)} />);
      expect(clientHrefs(html)).toEqual([]);
      expect(html).not.toContain('/clients/sources');
      expect(html).not.toContain('/clients//');
    });
  }

  it('still draws every section link, under the id, when there is one', () => {
    const html = renderToStaticMarkup(<ClientNav client={shellClient('clnt_1')} />);
    const hrefs = clientHrefs(html);
    for (const cluster of CLIENT_NAV) {
      for (const item of cluster.items) {
        if (item.path === null) continue;
        expect(hrefs).toContain(`/clients/clnt_1${item.path}`);
      }
    }
  });
});

describe('the real client screens before the client is known', () => {
  const views = {
    overview: ClientOverviewView,
    sources: ClientSourcesView,
    rankings: ClientRankingsView,
  };
  for (const [name, View] of Object.entries(views)) {
    for (const state of [
      { kind: 'loading' as const },
      { kind: 'error' as const, title: 'No such client', detail: 'It does not exist.' },
    ]) {
      it(`${name} (${state.kind}) links to no /clients/<section> path`, () => {
        const html = renderToStaticMarkup(<View state={state} me={clientsMe} />);
        expect(html).not.toContain('/clients/sources');
        expect(html).not.toMatch(/href="\/clients\/(sources|rankings|sentiment|technical|gaps|prompts|alerts|crawler|competitors)"/);
        expect(clientHrefs(html).every((h) => h === '/clients')).toBe(true);
      });
    }
  }
});
