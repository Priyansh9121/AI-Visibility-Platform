'use client';

/**
 * /share/{token} — the public report, Epic 9.8.
 *
 * **The only screen in this product a stranger can reach.** It exists because
 * Epic 9's acceptance criterion is that an agency can "generate and *send*" a
 * report, and the person a report is about has no account.
 *
 * It renders the SAME `ReportView` the authenticated screen renders — not a
 * cut-down copy. A second rendering path would be a second place for the
 * narrative to drift and for an operator affordance to reappear. What differs
 * is what is NOT passed:
 *
 *   - no `competitorEditor` slot, so no editing affordance exists
 *   - `publicView`, which drops the pitch beat's operator-only CTA
 *   - no re-run control and no link back to the dashboard
 *
 * This is a document a prospect opens, not a partial dashboard.
 *
 * Every failure is the same failure. The API answers 404 for a malformed
 * token, an unknown token and a revoked one alike, so this screen has exactly
 * one thing to say and cannot leak which kind of wrong a URL was.
 */

import { use, useEffect, useState } from 'react';
import { ErrorState, LoadingState } from '@avp/design-system';
import type { Report } from '@avp/shared-types';
import { api } from '@/lib/api';
import { ReportView } from '@/components/report/ReportView';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; report: Report }
  | { kind: 'gone' };

export default function PublicReportRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [view, setView] = useState<View>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const report = await api.publicReport(token);
        if (!cancelled) setView({ kind: 'ready', report });
      } catch {
        // Every error collapses to one state on purpose. Distinguishing a 404
        // from a 500 here would tell a guesser which of their guesses was
        // shaped like a real token.
        if (!cancelled) setView({ kind: 'gone' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (view.kind === 'loading') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <LoadingState message="Loading the report…" />
      </main>
    );
  }

  if (view.kind === 'gone') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <ErrorState
          title="This report link is not available"
          detail="The link may be incomplete, or it may have been withdrawn. Ask whoever sent it for a current one."
        />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-report px-6 py-18">
      <ReportView report={view.report} publicView />
    </main>
  );
}
