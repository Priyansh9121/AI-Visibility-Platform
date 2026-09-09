// @vitest-environment jsdom

/**
 * The sidebar's client list, driven — Epic 14.2.
 *
 * Every other test in this app renders to static markup, which is why this
 * bug lived: the list's states were all reachable statically (`navModes.test`
 * renders `ready`, `loading` and `error` by prop) and the one thing a static
 * render cannot show is a state that never ARRIVES. Opening the disclosure
 * left "Loading clients…" on screen forever on every account, because the
 * effect that fetched the list listed the list's own state among its
 * dependencies: setting `loading` inside the effect changed that dependency,
 * React ran the cleanup that flips `cancelled`, and the response was thrown
 * away on arrival. So this file mounts the real component in jsdom, clicks
 * the real button, and waits for the rows.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { severalClients } from '@/lib/clients/__fixtures__/clients';
import { scoredDashboard } from '@/lib/dashboard/__fixtures__/dashboards';

const clients = vi.fn();
const dashboard = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    clients: (...args: unknown[]) => clients(...args),
    dashboard: (...args: unknown[]) => dashboard(...args),
    logOut: async () => undefined,
  },
}));

// Imported after the mock so the shell sees the mocked module.
const { WorkspaceShell } = await import('./WorkspaceShell');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Let every pending microtask settle — the mocked fetches resolve on the next tick. */
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function mount(): { root: Root; el: HTMLDivElement } {
  const el = document.createElement('div');
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => {
    root.render(
      <WorkspaceShell current="dashboard" agencyName="Local Dev Agency 2" wide>
        <p>body</p>
      </WorkspaceShell>,
    );
  });
  return { root, el };
}

describe('the Clients disclosure loads its list', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clients.mockReset();
    dashboard.mockReset();
    clients.mockResolvedValue({ data: severalClients, nextCursor: null });
    dashboard.mockResolvedValue(scoredDashboard);
  });

  it('reaches `ready` after one click, with a row per client', async () => {
    const { root, el } = mount();
    const button = el.querySelector<HTMLButtonElement>('button[aria-controls="avp-clients-panel"]');
    expect(button, 'no Clients disclosure rendered').not.toBeNull();
    expect(button!.getAttribute('aria-expanded')).toBe('false');

    act(() => button!.click());
    expect(button!.getAttribute('aria-expanded')).toBe('true');
    // Both reads fire, once each, on the click.
    expect(clients).toHaveBeenCalledTimes(1);
    expect(dashboard).toHaveBeenCalledWith(50);

    await settle();

    const panel = el.querySelector('#avp-clients-panel')!;
    expect(panel.textContent).not.toContain('Loading clients…');
    expect(panel.textContent).not.toContain('could not be loaded');
    for (const c of severalClients) {
      expect(panel.textContent).toContain(c.brandName ?? c.name);
    }
    act(() => root.unmount());
  });

  it('shows the error state, not loading forever, when a read fails', async () => {
    clients.mockRejectedValue(new Error('down'));
    const { root, el } = mount();
    act(() => el.querySelector<HTMLButtonElement>('button[aria-controls="avp-clients-panel"]')!.click());
    await settle();
    const panel = el.querySelector('#avp-clients-panel')!;
    expect(panel.textContent).toContain('could not be loaded');
    expect(panel.textContent).not.toContain('Loading clients…');
    act(() => root.unmount());
  });

  it('fetches once, however many times the disclosure is toggled', async () => {
    const { root, el } = mount();
    const button = el.querySelector<HTMLButtonElement>('button[aria-controls="avp-clients-panel"]')!;
    act(() => button.click());
    await settle();
    act(() => button.click());
    act(() => button.click());
    await settle();
    expect(clients).toHaveBeenCalledTimes(1);
    expect(el.querySelector('#avp-clients-panel')!.textContent).toContain(severalClients[0]!.brandName ?? severalClients[0]!.name);
    act(() => root.unmount());
  });
});
