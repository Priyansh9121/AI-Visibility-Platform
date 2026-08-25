'use client';

/**
 * /dashboard — the agency's recent scans (Epic 9.3).
 *
 * Client-rendered and cookie-authenticated, the pattern Epic 2's intake screen
 * established and Epic 7's report screen followed: the session is an httpOnly
 * cookie, so the browser is what holds it.
 *
 * Fetching and re-run state live here; DashboardView is pure so it can be
 * rendered to static markup and asserted over. See that component's header for
 * why this screen does not poll a running scan.
 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';
import type { Dashboard } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { DashboardView } from '@/components/dashboard/DashboardView';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; dashboard: Dashboard }
  | { kind: 'error'; title: string; detail: string };

export default function DashboardRoute() {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [rerunning, setRerunning] = useState<ReadonlySet<string>>(new Set());
  const [rerunError, setRerunError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setView({ kind: 'ready', dashboard: await api.dashboard() });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const dashboard = await api.dashboard();
        if (!cancelled) setView({ kind: 'ready', dashboard });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiProblem && err.status === 401) {
          setView({
            kind: 'error',
            title: 'Sign in to see your dashboard',
            detail: 'Scans are scoped to the agency that ran them.',
          });
        } else {
          setView({
            kind: 'error',
            title: 'The dashboard could not be loaded',
            detail:
              err instanceof ApiProblem ? err.problem.detail : 'The request did not complete.',
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Start a scan.
   *
   * The request does not resolve until the scan has finished — Epic 9.2
   * measured 361.3s — so the client is marked in-flight for the whole wait and
   * the dashboard is reloaded once it lands. Marking it here is also the only
   * double-submit guard available: the server cannot see its own uncommitted
   * scan, so two clicks would buy two scans.
   */
  const rerun = useCallback(
    async (clientId: string) => {
      setRerunError(null);
      setRerunning((current) => new Set(current).add(clientId));
      try {
        await api.runScan(clientId);
        await load();
      } catch (err) {
        setRerunError(
          err instanceof ApiProblem
            ? err.problem.detail
            : 'The scan did not complete. It may still be running on the server.',
        );
      } finally {
        setRerunning((current) => {
          const next = new Set(current);
          next.delete(clientId);
          return next;
        });
      }
    },
    [load],
  );

  if (view.kind === 'loading') {
    return (
      <main className="mx-auto max-w-app px-6 py-18">
        <p className="text-ui-base text-text-tertiary">Loading your scans…</p>
      </main>
    );
  }

  if (view.kind === 'error') {
    return (
      <main className="mx-auto max-w-app px-6 py-18">
        <Card elevation="seated">
          <CardBody>
            <p className="text-ui-md font-medium text-text-primary">{view.title}</p>
            <p className="mt-2 text-ui-base leading-prose text-text-secondary">{view.detail}</p>
            <div className="mt-5">
              <Button variant="secondary" onClick={() => window.location.assign('/')}>
                Back to intake
              </Button>
            </div>
          </CardBody>
        </Card>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-app px-6 py-18">
      <DashboardView
        dashboard={view.dashboard}
        onRerun={rerun}
        rerunning={rerunning}
        rerunError={rerunError}
      />
    </main>
  );
}
