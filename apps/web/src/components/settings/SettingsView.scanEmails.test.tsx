/**
 * The scan-finished email switch on Settings — F1. Drawn only when the API says the feature is on
 * and email is configured (`me.scanEmails.available`); with it off, the default, Settings is
 * exactly what it was: no section, no word about these emails.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SettingsView, type SettingsState } from './SettingsView';
import { ownerMe, seatsWithPending } from '@/lib/settings/__fixtures__/seats';
import { noSubscription } from '@/lib/settings/__fixtures__/billing';

vi.mock('next/navigation', () => ({ usePathname: () => '/settings' }));

const ready = (available: boolean, enabled: boolean): SettingsState => ({
  kind: 'ready',
  me: { ...ownerMe, scanEmails: { available, enabled } },
  seats: seatsWithPending,
  seatsError: null,
  billing: noSubscription,
  billingError: null,
});

const render = (state: SettingsState) =>
  renderToStaticMarkup(<SettingsView state={state} onChanged={() => {}} />);

describe('unavailable (the default)', () => {
  const html = render(ready(false, true));
  it('draws no section and says nothing about these emails', () => {
    expect(html).not.toContain('Email when a scan finishes');
    expect(html).not.toContain('scan-emails-status');
  });
  it('is the same screen whatever the stored choice is', () => {
    expect(render(ready(false, false))).toBe(html);
  });
});

describe('an answer from an older API, with no scanEmails at all', () => {
  it('draws Settings as if the switch were unavailable, and does not throw', () => {
    const { scanEmails: _gone, ...older } = { ...ownerMe, scanEmails: { available: true, enabled: true } };
    const state = { ...ready(false, true), me: older as unknown as typeof ownerMe };
    const html = render(state);
    expect(html).not.toContain('Email when a scan finishes');
    expect(html).toBe(render(ready(false, true)));
  });
});

describe('available', () => {
  it('draws the section after the password and before Billing, with the exact lead', () => {
    const html = render(ready(true, true));
    const at = html.indexOf('Email when a scan finishes');
    expect(at).toBeGreaterThan(html.indexOf('Your password'));
    expect(at).toBeLessThan(html.indexOf('avp-section__eyebrow">Billing<'));
    expect(html).toContain(
      'When a scan you started finishes, we email you a link to its report, or tell you it did not finish. If the scan raised alerts, a second email says how many.',
    );
  });

  it('shows the stored choice: On pressed, or Off pressed', () => {
    const pressed = (html: string) =>
      [...html.matchAll(/aria-pressed="(true|false)"[^>]*><span class="avp-btn__label">(On|Off)<\/span>/g)].map(
        (m) => `${m[2]}:${m[1]}`,
      );
    expect(pressed(render(ready(true, true)))).toEqual(['On:true', 'Off:false']);
    expect(pressed(render(ready(true, false)))).toEqual(['On:false', 'Off:true']);
    expect(render(ready(true, false))).toContain('Off: you will not get these emails.');
  });
});
