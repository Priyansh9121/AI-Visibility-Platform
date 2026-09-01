'use client';

/**
 * The signed-in frame — Epic 9.13.
 *
 * Wraps `AppShell` with this product's actual destinations so no screen has to
 * restate them. It frames what Epics 7 / 9.8 / 9.9 / 9.10 settled and changes
 * none of it.
 *
 * **Every item in the sidebar leads somewhere real.** There is no disabled nav
 * item and no item whose click does nothing: Settings leads to a screen that
 * states plainly what is not built yet, which is a destination rather than a
 * stub. Clients reads an endpoint that has existed since Epic 2.
 *
 * `/share/{token}` deliberately does NOT use this — a stranger reading a
 * report should not be shown the sending agency's workspace navigation.
 *
 * IT IS IN COLOUR NOW — Epic 9.24
 * -------------------------------
 * Each destination carries a fixed index into the Working-screen accent layer
 * (`BENCH_ACCENTS`). The indices are written out below rather than derived from
 * array position, because they are IDENTITY: Clients has been violet since this
 * epic and must still be violet after someone reorders the sidebar or inserts a
 * fifth destination between two existing ones. An operator who has learnt where
 * the violet icon is should not have to re-learn it because the markup moved.
 *
 * This is chrome only. design-direction.md §0 puts every screen behind this
 * shell in the Working column; the report it links to is Presenting and is
 * untouched, which `reportIsolation.test.ts` enforces rather than promises.
 */

import type { JSX, ReactNode } from 'react';
import { AppShell, Button, NavItem } from '@avp/design-system';
import { BarChart3, Building2, Settings, Telescope } from 'lucide-react';
import { api } from '@/lib/api';

export type ShellSection = 'dashboard' | 'compare' | 'clients' | 'settings';

/**
 * A destination's accent, fixed by name.
 *
 * Not the array index. See the module note — these are stable identities, and
 * a lookup keyed by name survives reordering where a position does not.
 */
const ACCENT: Record<ShellSection, number> = {
  dashboard: 0,
  compare: 1,
  clients: 2,
  settings: 3,
};

export function WorkspaceShell({
  current,
  agencyName,
  seats,
  wide = false,
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
  children: ReactNode;
}): JSX.Element {
  return (
    <AppShell
      wide={wide}
      brand={
        <>
          <span className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
            Agency
          </span>
          <span className="font-editorial text-ed-2xs leading-display text-text-primary">
            {agencyName ?? '—'}
          </span>
        </>
      }
      nav={
        <>
          <NavItem
            href="/dashboard"
            label="Dashboard"
            accent={ACCENT.dashboard}
            current={current === 'dashboard'}
            icon={<BarChart3 width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/"
            label="Compare"
            accent={ACCENT.compare}
            note="Scan a new business"
            current={current === 'compare'}
            icon={<Telescope width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/clients"
            label="Clients"
            accent={ACCENT.clients}
            current={current === 'clients'}
            icon={<Building2 width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/settings"
            label="Settings"
            accent={ACCENT.settings}
            current={current === 'settings'}
            icon={<Settings width={16} height={16} aria-hidden="true" />}
          />
        </>
      }
      footer={
        <div className="flex flex-col gap-3">
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
