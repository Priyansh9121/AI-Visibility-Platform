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
import { DownloadPdfButton } from '@/components/report/DownloadPdfButton';

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
      {/*
        The one affordance a stranger DOES get — Epic 9.14.

        It is not an operator control: it hands the reader the same document
        they are already reading, in a form they can keep and forward. Epic 9.8
        minted this token because "the person a report is about has no account",
        and giving them the live URL while withholding the file would be
        backwards for a mechanism whose whole job is *send*. Nothing new is
        exposed — the PDF carries strictly the facts on this page.

        Above the report rather than inside it, for the reason the authenticated
        screen states: `ReportView` is the document that gets sent, and a
        control for sending it must not appear in what is sent.
      */}
      <div className="mb-8">
        <DownloadPdfButton
          token={token}
          subjectName={view.report.subject.brandName || view.report.subject.name}
          unscored={view.report.score === null}
        />
      </div>
      <ReportView report={view.report} publicView />
    </main>
  );
}
