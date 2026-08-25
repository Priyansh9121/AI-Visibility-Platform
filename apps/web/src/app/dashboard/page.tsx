'use client';

/**
 * /dashboard — the agency's recent scans (Epic 9.3), polling since Epic 9.7.
 *
 * Client-rendered and cookie-authenticated, the pattern Epic 2's intake screen
 * established and Epic 7's report screen followed: the session is an httpOnly
 * cookie, so the browser is what holds it.
 *
 * Fetching, re-run and polling state live here; DashboardView stays pure so it
 * can be rendered to static markup and asserted over. The polling RULES live in
 * `lib/dashboard/polling.ts`, framework-free so they can be tested with fake
 * timers — see that module's header.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';
import type { Dashboard } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { DashboardView } from '@/components/dashboard/DashboardView';
import { type Poller, createPoller, settled, shouldPoll } from '@/lib/dashboard/polling';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; dashboard: Dashboard }
  | { kind: 'error'; title: string; detail: string };

const EXPIRED = {
  kind: 'error' as const,
  title: 'Sign in to see your dashboard',
  detail: 'Scans are scoped to the agency that ran them.',
};

export default function DashboardRoute() {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [rerunning, setRerunning] = useState<ReadonlySet<string>>(new Set());
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [pollProblem, setPollProblem] = useState<string | null>(null);
  const [live, setLive] = useState(false);

  /**
   * Scans this browser session started.
   *
   * A ref, not state: the poller reads it on every decision, and a value
   * captured in a closure would go stale exactly when it matters — between
   * pressing Re-run and the next tick.
   */
  const watching = useRef<Set<string>>(new Set());
  const poller = useRef<Poller | null>(null);

  /**
   * One place where a freshly read dashboard takes effect.
   *
   * Order matters: prune finished scans from the watch set BEFORE asking the
   * poller to re-decide, or it would keep polling for a scan that has landed.
   */
  const apply = useCallback((dashboard: Dashboard) => {
    for (const id of settled(dashboard, watching.current)) watching.current.delete(id);
    setView({ kind: 'ready', dashboard });
    setLive(shouldPoll(dashboard, watching.current));
    poller.current?.sync(dashboard);
  }, []);

  // Built before the first load effect runs, so a dashboard arriving from any
  // source has something to hand itself to.
  useEffect(() => {
    poller.current = createPoller({
      fetchDashboard: () => api.dashboard(),
      getWatching: () => watching.current,
      onDashboard: apply,
      onUnauthorized: () => {
        setLive(false);
        setView(EXPIRED);
      },
      onTrouble: setPollProblem,
    });
    // A standard cleanup is all the App Router needs: this is a client
    // component, so navigating away unmounts it and this runs. It also covers
    // StrictMode's develop-time mount/unmount/mount, which simply builds a
    // fresh poller the second time.
    return () => {
      poller.current?.stop();
      poller.current = null;
      setLive(false);
    };
  }, [apply]);

  // A backgrounded tab keeps its timer but does no work. Refocusing polls at
  // once rather than making the user wait out an interval on a stale view.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'visible') poller.current?.pokeNow();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  const load = useCallback(async () => {
    apply(await api.dashboard());
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const dashboard = await api.dashboard();
        if (!cancelled) apply(dashboard);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiProblem && err.status === 401) {
          setView(EXPIRED);
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
  }, [apply]);

  /**
   * Start a scan.
   *
   * Since Epic 9.5 this resolves as soon as the scan is QUEUED rather than
   * blocking for the whole ~303s run, and since Epic 9.6 a concurrent second
   * request adopts the first's scan instead of buying another — so the
   * in-flight marking below is a courtesy against double-clicks, not the
   * defence it used to be.
   *
   * The scan is added to the watch set before the refresh, which is what makes
   * polling start. There is only ONE polling mechanism; pressing Re-run does
   * not start a second one. It supplies the other of the two conditions the
   * poller starts on — the scan is still QUEUED at this point and would not
   * qualify on status alone (the executor promotes it ~1.1ms later, after this
   * refresh has already read it), and the watch entry dissolves the moment the
   * scan reaches a terminal status.
   */
  const rerun = useCallback(
    async (clientId: string) => {
      setRerunError(null);
      setRerunning((current) => new Set(current).add(clientId));
      try {
        const queued = await api.runScan(clientId);
        watching.current.add(queued.id);
        await load();
      } catch (err) {
        setRerunError(
          err instanceof ApiProblem
            ? err.problem.detail
            : 'The scan could not be started. It may still be running on the server.',
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
        live={live}
        pollProblem={pollProblem}
      />
    </main>
  );
}
