'use client';

/**
 * "Download PDF" — Epic 9.14, on both report screens.
 *
 * One component, two callers, because there is one document. The authenticated
 * report passes a `scanId`; the public share page passes a `token`. Both reach
 * the same `render_report_pdf` over the same `build_report` payload, so a
 * prospect and the agency that sent it to them get byte-identical files — which
 * a backend test asserts directly.
 *
 * WHY IT SAYS WHAT IT IS DOWNLOADING
 * ----------------------------------
 * "Download PDF" alone invites the reasonable assumption that a PDF is a
 * fuller, more official artefact than the page — an export with extras. It is
 * not: it is the same five beats, the same numbers, and the same degraded
 * states. When the scan behind it is unscored, the PDF says "Not scored" too,
 * and the copy here says so before the click rather than letting someone
 * discover it in a file they have already sent to a client.
 *
 * The design-from-the-data-model rule: derived from what the endpoint does. No competitor's export
 * control was looked at.
 */

import { useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api, ApiProblem, saveBlob } from '@/lib/api';

export interface DownloadPdfButtonProps {
  /**
   * The authenticated route. Exactly one of these two is supplied.
   *
   * Explicitly `| undefined` because this project runs
   * `exactOptionalPropertyTypes`, under which `?:` alone rejects a value that
   * is present-but-undefined — which is exactly what a caller choosing between
   * the two routes passes. Same reason `WorkspaceShell` spells its optionals
   * out.
   */
  scanId?: string | undefined;
  /** The share-token route, for a reader with no account. */
  token?: string | undefined;
  /** Used for the fallback filename only; the server names the file. */
  subjectName: string;
  /** True when the scan behind this report has no score. */
  unscored?: boolean | undefined;
}

export function DownloadPdfButton({
  scanId,
  token,
  subjectName,
  unscored = false,
}: DownloadPdfButtonProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const blob = token
        ? await api.publicReportPdf(token)
        : await api.reportPdf(scanId ?? '');
      // The server sets the real filename in Content-Disposition; a blob URL
      // cannot read it back, so this mirrors the server's own rule rather than
      // inventing a different one.
      saveBlob(blob, `${slug(subjectName)}-ai-visibility.pdf`);
    } catch (err) {
      setError(
        err instanceof ApiProblem
          ? err.problem.detail || err.problem.title
          : 'The PDF could not be prepared. Try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={download} disabled={busy}>
          {busy ? 'Preparing…' : 'Download PDF'}
        </Button>
        <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
          {unscored
            ? // Said before the click. A PDF that turns out to say "Not scored"
              // after it has been forwarded to a client is worse than one that
              // was never downloaded.
              'The same report as this page — including that this scan has no score yet. The PDF will say so rather than showing a number.'
            : 'The same report as this page, as a file you can send on.'}
        </p>
      </div>

      {error != null && (
        <p role="alert" className="text-ui-sm leading-prose text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

/** Mirrors the server's `[^A-Za-z0-9._-] -> '-'` filename rule. */
export function slug(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 60) || 'report';
}
