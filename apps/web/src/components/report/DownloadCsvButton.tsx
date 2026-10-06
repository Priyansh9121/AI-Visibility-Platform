'use client';

/**
 * "Download figures (CSV)" — F3, 2026-10-06, on the signed-in report and nowhere else.
 *
 * ONE ROUTE, AND NO TOKEN
 * -----------------------
 * `DownloadPdfButton` serves two screens and takes a `scanId` or a `token`. This takes a `scanId`
 * and whether the scan is unscored, and nothing else: a share link opens the report and its PDF,
 * never this file, and a component that cannot be given a token cannot be drawn for a stranger.
 * The report page draws it only when the report itself says the export is available
 * (`csvExportAvailable`: the API's setting is on and the scan is finished), and `/share` does not
 * import it, which a source test pins.
 *
 * HOW IT DOWNLOADS
 * ----------------
 * As the PDF does, and for the reason `saveBlob` gives: a fetch with the session cookie, handed to
 * the browser as a blob under the name the server chose (`<client>-ai-visibility-<date>.csv`). A
 * refusal arrives as one plain sentence under the button, where a plain link would have left the
 * page or saved the refusal as a file: a 409 (a scan that has no figures yet) as the API words
 * it, and a 404, which is the switch turned off after this page loaded, as one fixed sentence
 * and never the API's "Not Found". The bytes are not decoded on the way, so the byte-order mark
 * that makes a spreadsheet read UTF-8 survives.
 *
 * WHAT IT SAYS THE FILE IS
 * ------------------------
 * Some of the report's figures, and not the report: no narrative and nothing the engines wrote,
 * and none of its other tables (who got named, the engines' agreement, the citation totals, a
 * fix's explanation). The lead beside the button says what the file holds, with the cap on cited
 * domains (the report's two tables show 12 each, and the file is built from the same figures); a
 * second paragraph below it says what the file does not have, and that the scores, shares and
 * sentiment keep two decimal places where the page rounds some of them. The lead stays beside the
 * button, as the PDF button's does, and the row is aligned to the top of it. Those are
 * exactly the figures written to two places; the points are one place, as the page shows them,
 * and the sentence says nothing of them. Each thing it names is in the file
 * (`services/report_csv.py`), and the API's tests pin the labels and the places.
 *
 * For a scan the file calls "Not scored" it says so before the click, as `DownloadPdfButton` does,
 * because the file then has no overall score and no dimension scores. The page tells it by the
 * file's own rule (`fileSaysNotScored`), which is wider than the PDF button's `score === null`.
 */

import { useId, useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api, ApiProblem, saveBlob } from '@/lib/api';

export interface DownloadCsvButtonProps {
  /** The scan whose report this is. There is no token form of this control. */
  scanId: string;
  /**
   * True when the file will say "Not scored": a scan never scored, one with too little data, or a
   * score with no composite (`fileSaysNotScored`, which the page calls). That is wider than the PDF
   * button's own `score === null`.
   */
  unscored?: boolean | undefined;
}

/** Used only when the browser does not expose the server's `Content-Disposition` name. */
const FALLBACK_NAME = 'ai-visibility.csv';

/** A 404 here is the switch turned off after the page loaded, so it is not "Not Found". */
const NOT_AVAILABLE = 'The figures file is not available for this report.';

// Some of the report's figures, not the report's: the file has none of its other tables (who got
// named, the engines' agreement, the citation totals, a fix's explanation), so the lead says "Some
// of" and the second paragraph says what is missing. Each thing named is in the file
// (`services/report_csv.py`).
const LEAD = 'Some of this report’s figures, one fact to a row, to open in a spreadsheet: ';
const WHAT_IT_HOLDS =
  'the scan’s date and whether it was complete or partial, the overall score, each dimension’s score (or why it was left out), share of voice, whether each engine named the client and where, the domains cited (up to 12 in each of the report’s two tables) and the fix list.';
const WHAT_IT_HOLDS_UNSCORED =
  'the scan’s date and whether it was complete or partial, share of voice, whether each engine named the client and where, the domains cited (up to 12 in each of the report’s two tables) and the fix list. This scan has no score, so the file says “Not scored” and has no dimension scores.';
// The second paragraph's first sentence names its subject ("The file"): it follows the lead, and in
// the unscored variant a scan's "no score", so "It" would read as the scan.
const WHAT_IT_LACKS =
  'The file does not have the weights, who else the answers named, the rivals’ mention rate, citation strength or average position, or the audit’s checks.';
// The scores, the shares and the sentiment are written to two places (the API's tests pin which
// figures those are); the points are one place, as the page shows them, and this says nothing of them.
const NUMBERS = 'Scores, shares and sentiment keep two decimal places; the page rounds some of them.';

export function DownloadCsvButton({
  scanId,
  unscored = false,
}: DownloadCsvButtonProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const errorId = useId();

  async function download() {
    // aria-disabled keeps focus on the button while busy; a second click is ignored.
    if (busy) return;
    setBusy(true);
    setDone(false);
    setError(null);
    try {
      const blob = await api.reportCsv(scanId);
      saveBlob(blob, blob instanceof File && blob.name ? blob.name : FALLBACK_NAME);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiProblem
          ? err.status === 404
            ? NOT_AVAILABLE
            : err.problem.detail || err.problem.title
          : 'The figures could not be prepared. Try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-start gap-3">
        <Button
          variant="secondary"
          onClick={download}
          aria-disabled={busy ? true : undefined}
          aria-describedby={error != null ? errorId : undefined}
        >
          {busy ? 'Preparing…' : 'Download figures (CSV)'}
        </Button>
        <span role="status" className="sr-only">
          {busy ? 'Preparing the CSV…' : done ? 'CSV downloaded' : ''}
        </span>
        <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
          {`${LEAD}${unscored ? WHAT_IT_HOLDS_UNSCORED : WHAT_IT_HOLDS}`}
        </p>
      </div>
      <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
        {`${WHAT_IT_LACKS} ${NUMBERS}`}
      </p>

      {error != null && (
        <p id={errorId} role="alert" className="text-ui-sm leading-prose text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
