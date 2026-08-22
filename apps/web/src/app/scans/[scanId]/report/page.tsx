'use client';

/**
 * /scans/{scanId}/report — the narrative report screen (Epic 7).
 *
 * Client-rendered and cookie-authenticated, matching the pattern Epic 2's
 * intake screen established: the session is an httpOnly cookie, so the browser
 * is what holds it.
 */

import { use, useCallback, useEffect, useState } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';
import type { Report } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { ReportView } from '@/components/report/ReportView';
import { CompetitorEditor } from '@/components/report/CompetitorEditor';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; report: Report }
  | { kind: 'error'; title: string; detail: string };

export default function ReportPageRoute({
  params,
}: {
  params: Promise<{ scanId: string }>;
}) {
  const { scanId } = use(params);
  const [view, setView] = useState<View>({ kind: 'loading' });

  /**
   * Re-read the report from the API.
   *
   * Correcting the competitor set changes Share of Voice's inputs, so the whole
   * document has to come back rather than the table being patched in place —
   * the gap beat, the fix list and the pitch are all derived from figures the
   * correction can move.
   */
  const reload = useCallback(async () => {
    setView({ kind: 'ready', report: await api.report(scanId) });
  }, [scanId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const report = await api.report(scanId);
        if (!cancelled) setView({ kind: 'ready', report });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiProblem && err.status === 401) {
          setView({
            kind: 'error',
            title: 'Sign in to view this report',
            detail: 'Reports are scoped to the agency that ran the scan.',
          });
        } else if (err instanceof ApiProblem && err.status === 404) {
          setView({
            kind: 'error',
            title: 'No report for that scan',
            detail: 'The scan does not exist, or it belongs to another agency.',
          });
        } else {
          setView({
            kind: 'error',
            title: 'The report could not be loaded',
            detail:
              err instanceof ApiProblem ? err.problem.detail : 'The request did not complete.',
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [scanId]);

  if (view.kind === 'loading') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <p className="text-ui-base text-text-tertiary">Assembling the report…</p>
      </main>
    );
  }

  if (view.kind === 'error') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
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
    <main className="mx-auto max-w-report px-6 py-18">
      <ReportView
        report={view.report}
        competitorEditor={
          view.report.competitorSet ? (
            <CompetitorEditor
              clientId={view.report.subject.clientId}
              competitors={view.report.competitorSet.competitors}
              onSaved={reload}
            />
          ) : undefined
        }
      />
    </main>
  );
}
