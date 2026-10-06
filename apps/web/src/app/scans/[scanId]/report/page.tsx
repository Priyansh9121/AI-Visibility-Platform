'use client';

/**
 * /scans/{scanId}/report — the narrative report screen (Epic 7).
 *
 * Client-rendered and cookie-authenticated, matching the pattern Epic 2's
 * intake screen established: the session is an httpOnly cookie, so the browser
 * is what holds it.
 */

import { use, useEffect, useState } from 'react';
import { Button, ErrorState, LoadingState } from '@avp/design-system';
import type { Report } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { useFeedbackEmail } from '@/lib/feedback/useFeedbackEmail';
import { ReportView } from '@/components/report/ReportView';
import { CompetitorEditor } from '@/components/report/CompetitorEditor';
import { ShareLinkBar } from '@/components/report/ShareLinkBar';
import { DownloadPdfButton } from '@/components/report/DownloadPdfButton';
import { DownloadCsvButton } from '@/components/report/DownloadCsvButton';
import { fileSaysNotScored } from '@/lib/report/csvScore';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';

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
  // This page reads no `me`, so the footer's "Send feedback" link asks for its address here
  // (2026-10-06): null, and no link, until it is told one.
  const feedbackEmail = useFeedbackEmail();

  // No reload after the competitor editor saves. A saved list is for the client's later scans
  // (the API writes its next list), so the scan on screen, its figures and everything derived from
  // them are what they were; the editor says so itself.
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
      <WorkspaceShell current="dashboard" feedbackEmail={feedbackEmail} wide>
        <LoadingState message="Assembling the report…" />
      </WorkspaceShell>
    );
  }

  if (view.kind === 'error') {
    return (
      <WorkspaceShell current="dashboard" feedbackEmail={feedbackEmail} wide>
        <ErrorState
          title={view.title}
          detail={view.detail}
          action={
            <Button variant="secondary" onClick={() => window.location.assign('/')}>
              Back to intake
            </Button>
          }
        />
      </WorkspaceShell>
    );
  }

  return (
    /*
      The document is placed on a desk — Epic 14.1 — and the document is
      72rem wide — Epic 17.

      Epic 14.1 fixed the framing: a fixed white column dropped onto the
      workspace ground read as a clipped phone screenshot, so
      `.avp-report-frame` gave it a light neutral desk and a real edge. It
      kept the page at design-direction.md's 52rem Presenting measure, and
      the founder then looked at it live twice and found a well-fitted desk
      around a narrow page is still a narrow page. Epic 17 reversed the
      width table's row for this route and `/share`, explicitly,
      in the table.

      `wide` is deliberate now: the shell runs at the Working measure and
      the FRAME caps itself at the report width plus the desk's own gutter,
      so the desk still hugs the page — the mistake 14.1 corrected cannot
      recur, because the hug no longer depends on which shell measure the
      route happens to be on. `reportWidth.test.ts` asserts both halves.

      Route-level framing and width only — the report's identity, its
      beats and its tokens are untouched, which ReportView.test.tsx and
      reportIsolation.test.ts keep asserting.
    */
    <WorkspaceShell current="dashboard" feedbackEmail={feedbackEmail} wide>
      {/*
        Operator chrome, deliberately ABOVE the report rather than inside it.
        ReportView is the document that gets sent; anything to do with sending
        it must not be part of what is sent. It is also why this is not passed
        as a slot — a slot would put it inside the rendered page.
      */}
      <div className="mx-auto max-w-report">
        <ShareLinkBar
          scanId={scanId}
          scanStatus={view.report.scanStatus}
          shareExpiresAt={view.report.shareExpiresAt}
        />
        <div className="mt-4 mb-6 flex flex-col gap-3">
          <DownloadPdfButton
            scanId={scanId}
            subjectName={view.report.subject.brandName || view.report.subject.name}
            unscored={view.report.score === null}
          />
          {/*
            The figures as a spreadsheet (F3), only when the report says it is available: the
            API's switch is on and the scan is finished. `=== true`, so a report from an API that
            has no such field yet draws nothing. One wrapper for both downloads, so they sit one
            above the other with the same gap. The control is told whether the scan is unscored by
            the rule the file uses for "Not scored" (never scored, too little data, or no
            composite), which is wider than the PDF control's `score === null`; the PDF control is
            told what it always was. Operator chrome like the PDF, above the document and not in
            it, and on this screen only: `/share` does not import the control.
          */}
          {view.report.csvExportAvailable === true && (
            <DownloadCsvButton scanId={scanId} unscored={fileSaysNotScored(view.report.score)} />
          )}
        </div>
      </div>
      <div className="avp-report-frame">
        <ReportView
          report={view.report}
          competitorEditor={
            <CompetitorEditor
              clientId={view.report.subject.clientId}
              competitors={view.report.competitorSet?.competitors ?? []}
              setMissing={view.report.competitorSet === null}
              scanFinished={!['queued', 'running'].includes(view.report.scanStatus)}
            />
          }
        />
      </div>
    </WorkspaceShell>
  );
}
