'use client';

/**
 * The two faces of the sidebar — Epic 13. Pure and prop-driven, so both
 * modes can be rendered statically and asserted over; `WorkspaceShell` owns
 * the state and the fetch.
 *
 * AGENCY MODE is the sidebar every screen had since Epic 9.13, with one
 * change: Clients has grown from a link into a disclosure that lists every
 * client, each with the ramp dot and figure the clients screen never had.
 * The clients screen itself is the first row inside — so the destination the
 * label names is still one click away, and 9.13's rule that no item is dead
 * still holds.
 *
 * CLIENT MODE is that client's map. Its head names the client and the way
 * back; its items are `CLIENT_NAV`, the one table the strip read since Epic
 * B.1, clusters and cluster-relative accents included — the reasoning for
 * those lives in `clientNav.ts` and is unchanged by moving them.
 *
 * Icons are Lucide (the licensed-assets rule). Fixed by SECTION, never by position,
 * for the same reason the accents are: an operator who has learnt where the
 * bell is should not have to re-learn it because somebody reordered the list.
 */

import type { JSX } from 'react';
import {
  NavDisclosure,
  NavGroup,
  NavHead,
  NavItem,
  NavPanel,
  NavScore,
  NavSlot,
  NavSubItem,
} from '@avp/design-system';
import {
  BarChart3,
  Bell,
  Bot,
  Building2,
  FileText,
  Gauge,
  LayoutGrid,
  Link2,
  MessageSquare,
  Settings,
  Telescope,
  Terminal,
  TrendingUp,
  Users,
  Wrench,
} from 'lucide-react';
import { CLIENT_NAV, type ClientSection } from '@/components/client/clientNav';
import type { SidebarClient } from '@/lib/shell/clientList';

export type ShellSection = 'dashboard' | 'compare' | 'clients' | 'settings';

/** A destination's accent, fixed by name — see `WorkspaceShell`'s note. */
export const ACCENT: Record<ShellSection, number> = {
  dashboard: 0,
  compare: 1,
  clients: 2,
  settings: 3,
};

export type ClientListState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; rows: SidebarClient[]; more: boolean }
  | { kind: 'error' };

const ICON = { width: 16, height: 16, 'aria-hidden': true } as const;

export function AgencyNav({
  current,
  clientsOpen,
  onToggleClients,
  clients,
}: {
  current: ShellSection;
  clientsOpen: boolean;
  onToggleClients: () => void;
  clients: ClientListState;
}): JSX.Element {
  return (
    <>
      <NavItem
        href="/dashboard"
        label="Dashboard"
        accent={ACCENT.dashboard}
        current={current === 'dashboard'}
        icon={<BarChart3 {...ICON} />}
      />
      <NavDisclosure
        label="Clients"
        accent={ACCENT.clients}
        expanded={clientsOpen}
        onToggle={onToggleClients}
        controls="avp-clients-panel"
        current={current === 'clients'}
        icon={<Building2 {...ICON} />}
      />
      <NavPanel id="avp-clients-panel" open={clientsOpen} label="Clients">
        <NavSlot>
          <NavSubItem href="/clients" label="All clients" current={current === 'clients'} />
        </NavSlot>
        {clients.kind === 'loading' && (
          <NavSlot>
            <span className="avp-nav__subitem" role="status">
              Loading clients…
            </span>
          </NavSlot>
        )}
        {clients.kind === 'error' && (
          <NavSlot>
            <span className="avp-nav__subitem">The list could not be loaded.</span>
          </NavSlot>
        )}
        {clients.kind === 'ready' &&
          clients.rows.map((row) => (
            <NavSlot key={row.id}>
              <NavSubItem
                href={`/clients/${row.id}`}
                label={row.label}
                {...(row.score === undefined ? {} : { score: row.score, note: 'no reading' })}
              />
            </NavSlot>
          ))}
        {clients.kind === 'ready' && clients.more && (
          <NavSlot>
            <NavSubItem href="/clients" label="More on the clients screen" />
          </NavSlot>
        )}
      </NavPanel>
      <NavItem
        href="/"
        label="Compare"
        accent={ACCENT.compare}
        note="Scan a new business"
        current={current === 'compare'}
        icon={<Telescope {...ICON} />}
      />
      <NavItem
        href="/settings"
        label="Settings"
        accent={ACCENT.settings}
        current={current === 'settings'}
        icon={<Settings {...ICON} />}
      />
    </>
  );
}

/** What the sidebar knows about the selected client — all of it from the route's own page. */
export interface ShellClient {
  id: string;
  name: string;
  brandName: string | null;
  domain: string;
  section: ClientSection;
  /** The scan the Report item opens; `null` renders no Report item at all. */
  latestReportScanId: string | null;
  /**
   * The latest composite. `null` is "never scored"; `undefined` is "the
   * screen has not loaded it yet", which renders nothing rather than a claim.
   */
  latestScore?: number | null | undefined;
}

const SECTION_ICON: Record<ClientSection, (props: typeof ICON) => JSX.Element> = {
  overview: (p) => <Gauge {...p} />,
  sources: (p) => <Link2 {...p} />,
  rankings: (p) => <TrendingUp {...p} />,
  sentiment: (p) => <MessageSquare {...p} />,
  technical: (p) => <Wrench {...p} />,
  crawler: (p) => <Bot {...p} />,
  gaps: (p) => <LayoutGrid {...p} />,
  prompts: (p) => <Terminal {...p} />,
  alerts: (p) => <Bell {...p} />,
  // Epic 13. The section the icon was imported for before it existed.
  competitors: (p) => <Users {...p} />,
};

export function ClientNav({ client }: { client: ShellClient }): JSX.Element {
  const base = `/clients/${client.id}`;
  return (
    <>
      {CLIENT_NAV.map((cluster) => (
        <NavGroup key={cluster.key} label={cluster.label}>
          {cluster.items.map((item) => {
            if (item.section === null) {
              // The Report resolves to a SCAN, and with no scan it is not
              // rendered at all rather than rendered dead — NavItem's rule.
              return client.latestReportScanId == null ? null : (
                <NavSlot key="report">
                  <NavItem
                    href={`/scans/${client.latestReportScanId}/report`}
                    label={item.label}
                    icon={<FileText {...ICON} />}
                    external
                  />
                </NavSlot>
              );
            }
            const Icon = SECTION_ICON[item.section];
            return (
              <NavSlot key={item.section}>
                <NavItem
                  href={`${base}${item.path ?? ''}`}
                  label={item.label}
                  current={client.section === item.section}
                  icon={<Icon {...ICON} />}
                  {...(item.accent != null ? { accent: item.accent } : {})}
                />
              </NavSlot>
            );
          })}
        </NavGroup>
      ))}
    </>
  );
}

export function ClientHead({ client }: { client: ShellClient }): JSX.Element {
  return (
    <NavHead
      back={{ href: '/clients', label: 'All clients' }}
      title={client.brandName ?? client.name}
      subtitle={client.domain}
    >
      {client.latestScore === undefined ? null : <NavScore score={client.latestScore} />}
    </NavHead>
  );
}
