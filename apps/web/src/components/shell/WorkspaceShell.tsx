'use client';

/**
 * The signed-in frame — Epic 9.13, and since Epic 13 a frame with TWO MODES.
 *
 * Wraps `AppShell` with this product's actual destinations so no screen has to
 * restate them. It frames what Epics 7 / 9.8 / 9.9 / 9.10 settled and changes
 * none of it.
 *
 * **Every item in the sidebar leads somewhere real.** There is no disabled nav
 * item and no item whose click does nothing: Settings leads to a screen that
 * states plainly what is not built yet, which is a destination rather than a
 * stub. The Clients row opens a list, and the clients screen is the first row
 * inside it.
 *
 * `/share/{token}` deliberately does NOT use this — a stranger reading a
 * report should not be shown the sending agency's workspace navigation.
 *
 * IT IS IN COLOUR NOW — Epic 9.24
 * -------------------------------
 * Each destination carries a fixed index into the Working-screen accent layer
 * (`BENCH_ACCENTS`). The indices are written out by name in `sidebars.tsx`
 * rather than derived from array position, because they are IDENTITY: Clients
 * has been violet since that epic and must still be violet after someone
 * reorders the sidebar or inserts a fifth destination between two existing
 * ones.
 *
 * TWO MODES — Epic 13
 * -------------------
 * Epic 9.20 kept this sidebar agency-wide and put a client's own navigation in
 * a strip inside the content area, on the argument that depth about one client
 * cannot live here "without inventing a global selected client the rest of
 * the product does not have". Epic 13 reverses that on the founder's decision;
 * the build log entry "Epic 13 — the sidebar becomes the map" records the
 * original argument and why it changed.
 *
 * What survives of the argument is the rule it was protecting: an item in
 * this sidebar is always about ONE thing. So the sidebar switches modes rather
 * than mixing levels. Given a `client`, it is that client's map — the head
 * names the client and the way back, the items are `CLIENT_NAV`. Given none,
 * it is the agency's, exactly as before plus the client list.
 *
 * THE SELECTED CLIENT IS THE URL, NOT A STORE
 * -------------------------------------------
 * `client` arrives as a prop from `ClientSpace`, which every screen under
 * `/clients/[clientId]/*` already renders with the record its route loaded.
 * Nothing here reads `useParams`, nothing is kept in context, and a screen
 * whose URL names no client has no selected client. That keeps both modes
 * renderable to static markup, which is how they are tested.
 *
 * This is chrome only. design-direction.md §0 puts every screen behind this
 * shell in the Working column; the report it links to is Presenting and is
 * untouched, which `reportIsolation.test.ts` enforces rather than promises.
 */

import { useCallback, useEffect, useState, type JSX, type ReactNode } from 'react';
import { AppShell, Button } from '@avp/design-system';
import { api } from '@/lib/api';
import { latestScores, sidebarClients } from '@/lib/shell/clientList';
import {
  AgencyNav,
  ClientHead,
  ClientNav,
  type ClientListState,
  type ShellClient,
  type ShellSection,
} from '@/components/shell/sidebars';

export type { ShellSection, ShellClient };

/**
 * Whether the Clients list was left open — remembered for the session.
 *
 * The list is opened tens of times a day by an operator moving between
 * clients, and a disclosure that closed itself on every navigation would be a
 * click tax on the most-used path in the product. Session storage, not local:
 * a fresh visit starts closed, which is the calmer default for a first look.
 * Every access is guarded — a private window or a blocked store must never
 * cost the sidebar.
 */
const OPEN_KEY = 'avp.sidebar.clients';

function readOpen(): boolean {
  try {
    return window.sessionStorage.getItem(OPEN_KEY) === '1';
  } catch {
    return false;
  }
}

function writeOpen(open: boolean): void {
  try {
    window.sessionStorage.setItem(OPEN_KEY, open ? '1' : '0');
  } catch {
    // Nothing to do: the preference simply is not remembered.
  }
}

export function WorkspaceShell({
  current,
  agencyName,
  seats,
  wide = false,
  client,
  children,
}: {
  current: ShellSection;
  /**
   * Omitted while the screen is still loading — the frame renders regardless,
   * so navigation is usable before the data arrives.
   *
   * Explicitly `| undefined` because this project runs
   * `exactOptionalPropertyTypes`, under which `?:` alone rejects a value that
   * is present-but-undefined — which is exactly what `me?.agency.name` is.
   */
  agencyName?: string | undefined;
  seats?: { used: number; limit: number } | undefined;
  wide?: boolean | undefined;
  /** Present on every screen inside one client's space; absent everywhere else. */
  client?: ShellClient | undefined;
  children: ReactNode;
}): JSX.Element {
  // Starts closed on the server and on first paint, then adopts the remembered
  // state — reading storage during render would disagree with the server
  // markup and React would refuse the hydration.
  const [clientsOpen, setClientsOpen] = useState(current === 'clients');
  const [list, setList] = useState<ClientListState>({ kind: 'idle' });

  useEffect(() => {
    if (current !== 'clients' && readOpen()) setClientsOpen(true);
  }, [current]);

  useEffect(() => {
    if (!clientsOpen || list.kind !== 'idle') return;
    let cancelled = false;
    setList({ kind: 'loading' });
    void (async () => {
      try {
        // Two reads in parallel, both of which already exist: the identities,
        // and the recent scans the scores are read off. See lib/shell/clientList
        // for what that derivation can and cannot claim.
        const [page, dashboard] = await Promise.all([api.clients(), api.dashboard(50)]);
        if (cancelled) return;
        setList({
          kind: 'ready',
          rows: sidebarClients(page.data, latestScores(dashboard)),
          more: page.nextCursor !== null,
        });
      } catch {
        if (!cancelled) setList({ kind: 'error' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clientsOpen, list.kind]);

  const toggleClients = useCallback(() => {
    setClientsOpen((open) => {
      writeOpen(!open);
      return !open;
    });
  }, []);

  return (
    <AppShell
      wide={wide}
      brand={
        client ? (
          <ClientHead client={client} />
        ) : (
          <>
            <span className="text-ui-2xs font-semibold uppercase tracking-caps text-text-tertiary">
              Agency
            </span>
            <span className="font-display text-ed-2xs font-semibold leading-display tracking-display text-text-primary">
              {agencyName ?? '—'}
            </span>
          </>
        )
      }
      nav={
        client ? (
          <ClientNav client={client} />
        ) : (
          <AgencyNav
            current={current}
            clientsOpen={clientsOpen}
            onToggleClients={toggleClients}
            clients={list}
          />
        )
      }
      footer={
        <div className="flex flex-col gap-3">
          {client && agencyName != null && (
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{agencyName}</p>
          )}
          {seats && (
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
              {`${seats.used} of ${seats.limit} seats`}
            </p>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await api.logOut();
              window.location.assign('/');
            }}
          >
            Sign out
          </Button>
        </div>
      }
    >
      {children}
    </AppShell>
  );
}
