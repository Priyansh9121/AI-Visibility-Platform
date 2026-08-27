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
 */

import type { JSX, ReactNode } from 'react';
import { AppShell, Button, NavItem } from '@avp/design-system';
import { BarChart3, Building2, Settings, Telescope } from 'lucide-react';
import { api } from '@/lib/api';

export type ShellSection = 'dashboard' | 'compare' | 'clients' | 'settings';

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
            current={current === 'dashboard'}
            icon={<BarChart3 width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/"
            label="Compare"
            note="Scan a new business"
            current={current === 'compare'}
            icon={<Telescope width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/clients"
            label="Clients"
            current={current === 'clients'}
            icon={<Building2 width={16} height={16} aria-hidden="true" />}
          />
          <NavItem
            href="/settings"
            label="Settings"
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
