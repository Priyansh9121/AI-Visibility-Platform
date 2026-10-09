/**
 * The probable-rivals card on the Competitors screen — F2 (2026-10-09).
 *
 * The card reads its own state (`RivalSuggestions.test.tsx` mounts it and holds what it says and
 * does), so the screen's job is only to seat it: for a client that has been scanned, BELOW the field
 * (it is an offer, not what the page was opened for, and it draws only after its own read, so below
 * the field it moves nothing a reader is already reading), after whatever the field is in each of its
 * states; never for a client with no scan, and never while the client itself is loading or failed.
 * The card is replaced by a marker here so a static render can see where it sits.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Client, ClientHistory, Report } from '@avp/shared-types';

vi.mock('@/components/client/RivalSuggestions', () => ({
  RivalSuggestions: ({ clientId }: { clientId: string }) => (
    <div data-testid="rival-suggestions" data-client={clientId} />
  ),
}));

import { ClientCompetitorsView, type FieldState } from './ClientCompetitorsView';
import type { ClientDetailState } from './ClientDetailView';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { noScanHistory, threeScanHistory } from '@/lib/client/__fixtures__/history';
import { helpscoutReport, noCompetitorSetReport } from '@/lib/report/__fixtures__/reports';

const CLIENT = identifiedClient as Client;
const ready = (history: ClientHistory = threeScanHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});
const render = (field: FieldState, state: ClientDetailState = ready()) =>
  renderToStaticMarkup(<ClientCompetitorsView state={state} field={field} me={clientsMe} />);

const MARKER = 'data-testid="rival-suggestions"';

describe('where the card sits', () => {
  it('is seated for a client that has been scanned, with that client’s id', () => {
    const html = render({ kind: 'ready', report: helpscoutReport as unknown as Report });
    expect((html.match(/data-testid="rival-suggestions"/g) ?? []).length).toBe(1);
    expect(html).toContain(`data-client="${CLIENT.id}"`);
  });

  it('is below the field: an offer drawn after its own read moves nothing the comparison is read on', () => {
    const html = render({ kind: 'ready', report: helpscoutReport as unknown as Report });
    const at = html.indexOf(MARKER);
    expect(at).toBeGreaterThan(html.indexOf('Competitors</h1>'));
    const above = html.slice(0, at);
    const below = html.slice(at);
    // The field is above the card, from its heading to its last block, and none of it is below.
    expect(above).toContain('The field, dimension by dimension');
    expect(above).toContain('avp-ledger');
    expect(above).toContain('Who leads on what');
    expect(below).not.toContain('The field, dimension by dimension');
    expect(below).not.toContain('avp-ledger');
    expect(below).not.toContain('Who leads on what');
    expect(below).not.toContain('No rival leads');
  });

  it.each([
    ['while the latest scan is read', { kind: 'loading' } as FieldState, 'Reading the latest scan'],
    [
      'when the latest scan has no report',
      { kind: 'ready', report: null } as FieldState,
      'The latest scan has no report to read',
    ],
    [
      'when the scan has no competitor set',
      { kind: 'ready', report: noCompetitorSetReport as unknown as Report } as FieldState,
      'No competitors in this scan',
    ],
    [
      'when the scan could not be read',
      { kind: 'error', title: 'The latest scan could not be read', detail: 'x' } as FieldState,
      'The latest scan could not be read',
    ],
  ])('is still seated %s, below what the field says: a rival can be added to a scan that has none', (_when, field, said) => {
    const html = render(field);
    expect(html).toContain(MARKER);
    expect(html.indexOf(said)).toBeGreaterThan(-1);
    expect(html.indexOf(MARKER)).toBeGreaterThan(html.indexOf(said));
  });
});

describe('where it is not', () => {
  it('is not seated for a client that has never been scanned', () => {
    expect(render({ kind: 'ready', report: null }, ready(noScanHistory))).not.toContain(MARKER);
  });

  it('is not seated while the client loads, or when it failed to load', () => {
    const field: FieldState = { kind: 'ready', report: null };
    expect(render(field, { kind: 'loading' })).not.toContain(MARKER);
    expect(render(field, { kind: 'error', title: 'Gone', detail: 'x' })).not.toContain(MARKER);
  });
});
