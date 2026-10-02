/**
 * Why a scan ended partial or failed, in one plain sentence.
 *
 * The codes are the API's (`services/scan_executor.py`, `scan_runner.py`) and
 * are never shown. Each sentence also says whether the scan used up one of the
 * agency's scans, because that is the question a failed scan raises.
 *
 * `status` is typed loosely (a string) because `ReportOut.scanStatus` is one.
 * Anything that is not partial or failed has nothing to explain and returns
 * null.
 *
 * Two screens call it, both the agency's own: the dashboard's scan list
 * (`ScanSummary.errorCode`) and the signed-in report (`Report.scanErrorCode`).
 * The public `/share` report never does — "It counts as one of your scans" is
 * wrong for a prospect — which `ReportView`'s `publicView` and
 * `app/share/scanOutcomeGate.test.ts` keep true.
 */

/**
 * How long a scan takes, in the one sentence every screen that says so uses — launch
 * checklist 3.39 (the intake card's started state, the dashboard's empty state and the
 * getting-started checklist). The screens promised a scan inside 300 seconds, and that
 * was not true: read from the dev database on 2026-10-03, 9 of 25 finished scans took longer
 * (succeeded: median 4.7 minutes, longest 5.8; partial: median 3.9, longest 7.6), a
 * scan's audit could wait up to `SCAN_AUDIT_WAIT_S` for the crawler's one browser and then
 * run (300 s when those scans ran, 120 s since launch checklist 3.35), and a scan is
 * allowed up to `MAX_SCAN_DURATION` (2,400 s).
 *
 * No number, then: any figure here is one the next slow scan breaks. "A few minutes" is
 * what that range supports (21 of the 25 took between two and six minutes), and "usually"
 * keeps it true of the four that did not (the shortest took under one minute and the
 * longest 7.6). One constant so the screens cannot say it several ways; the landing page
 * runs the same words into its own sentence (`LandingView`).
 */
export const SCAN_DURATION_SENTENCE = 'A scan usually takes a few minutes.';

const COUNTS = 'It counts as one of your scans.';
const FREE = 'It does not use up one of your scans.';

const FAILED: Record<string, string> = {
  SCAN_DEADLINE_EXCEEDED: `This scan ran out of time before enough prompts were answered. ${FREE}`,
  ALL_ENGINE_CALLS_FAILED: `None of the AI engines answered, so there are no results. ${FREE}`,
  EXECUTION_FAILED: `This scan stopped because of an error on our side. ${FREE}`,
  EXECUTOR_LOST: `This scan stopped when our server restarted. ${FREE}`,
  EXECUTOR_NEVER_STARTED: `This scan did not start because our server restarted. ${FREE}`,
};

export function scanOutcomeSentence(
  status: string,
  errorCode?: string | null,
): string | null {
  if (status === 'partial') {
    return errorCode === 'SCAN_DEADLINE_EXCEEDED'
      ? `This scan ran out of time. These results use the prompts that finished. ${COUNTS}`
      : `Some engines did not answer. These results use the answers that came back. ${COUNTS}`;
  }
  if (status === 'failed') {
    return errorCode && Object.hasOwn(FAILED, errorCode)
      ? FAILED[errorCode]!
      : `This scan did not finish. ${FREE}`;
  }
  return null;
}
