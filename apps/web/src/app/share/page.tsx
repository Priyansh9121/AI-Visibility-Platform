'use client';

/**
 * /share#{token} — the public report, Epic 9.8.
 *
 * The token rides in the URL FRAGMENT, which no server or Referer ever sees.
 * The fragment stays in the address bar, so the page can be reloaded,
 * bookmarked and forwarded. `/share/{token}` (the older path form) redirects here.
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
 *
 * Two exceptions, and neither says anything about the token. A 429 means this NETWORK has asked
 * too often (the API limits the share routes per source address, before it looks the token up);
 * a 503 means the service is unavailable for now. Each has its own screen with a Reload, because
 * "ask whoever sent it for a current one" would be wrong advice for either.
 */

import { useEffect, useState } from 'react';
import { Button, ErrorState, LoadingState } from '@avp/design-system';
import type { Report } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { useFragmentToken } from '@/lib/fragmentToken';
import { ReportView } from '@/components/report/ReportView';
import { DownloadPdfButton } from '@/components/report/DownloadPdfButton';

type View =
  | { kind: 'loading' }
  | { kind: 'ready'; report: Report }
  | { kind: 'gone' }
  | { kind: 'limited' }
  | { kind: 'unavailable' };

// A 429 (this network asked too often) and a 503 (unavailable for now) say nothing about the
// link, so neither sends the reader to ask for a new one. Both offer a Reload.

function ReloadButton() {
  return (
    <Button variant="secondary" onClick={() => window.location.reload()}>
      Reload
    </Button>
  );
}

export default function PublicReportRoute() {
  // undefined until mounted: server and first client render show the same
  // loading state.
  const token = useFragmentToken(false);
  const [view, setView] = useState<View>({ kind: 'loading' });

  useEffect(() => {
    if (token === undefined) return;
    if (token === '') {
      setView({ kind: 'gone' });
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const report = await api.publicReport(token);
        if (!cancelled) setView({ kind: 'ready', report });
      } catch (err) {
        if (err instanceof ApiProblem && (err.status === 429 || err.status === 503)) {
          if (!cancelled) setView({ kind: err.status === 429 ? 'limited' : 'unavailable' });
          return;
        }
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
      <main className="mx-auto max-w-page px-6 py-18">
        <LoadingState message="Loading the report…" />
      </main>
    );
  }

  if (view.kind === 'limited') {
    return (
      <main className="mx-auto max-w-page px-6 py-18">
        <ErrorState
          title="Too many requests from your network"
          detail="Wait a few minutes, then reload this page."
          action={<ReloadButton />}
        />
      </main>
    );
  }

  if (view.kind === 'unavailable') {
    return (
      <main className="mx-auto max-w-page px-6 py-18">
        <ErrorState
          title="This is temporarily unavailable"
          detail="Please try again shortly."
          action={<ReloadButton />}
        />
      </main>
    );
  }

  if (view.kind === 'gone') {
    return (
      <main className="mx-auto max-w-page px-6 py-18">
        <ErrorState
          title="This report link is not available"
          detail="The link may be incomplete, expired or withdrawn. Ask whoever sent it for a current one."
        />
      </main>
    );
  }

  return (
    /*
      The desk is the whole page here — Epic 14.1. A stranger opening this
      link gets a document on a light neutral surface, edge to edge, rather
      than a white column on the app's ground. The report's measure is
      `--avp-report-width`, 72rem since Epic 17; its tokens are untouched.
    */
    <main className="avp-report-frame avp-report-frame--page">
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
      <div className="mx-auto mb-8 max-w-report">
        <DownloadPdfButton
          token={token ?? ''}
          subjectName={view.report.subject.brandName || view.report.subject.name}
          unscored={view.report.score === null}
        />
      </div>
      <ReportView report={view.report} publicView />
    </main>
  );
}
