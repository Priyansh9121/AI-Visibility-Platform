/**
 * The scan allowance, as the API states it — 2026-09-28.
 *
 * One free scan per agency for life, forty a month on the plan. The numbers
 * come from `GET /agencies/{id}/billing` (`scanLimit`, `scansUsedThisPeriod`,
 * `periodResetsAt`) and are never typed here: the API's
 * `services/scan_allowance.py` owns them, and a label built from anything
 * else would be a second copy that drifts. `scanLimit` is null when billing
 * is not configured on the deployment, in which case there is no gate and
 * no label.
 *
 * Since 2026-09-29 the free scan needs a Google sign-in. Whether THIS user
 * needs one is `freeScanRequiresGoogle`, and it is the API's answer: it is
 * about the requesting user and not the agency, and working it out here
 * would be a second copy of that rule.
 */

import type { BillingStatus, ProblemDetail, UserRole } from '@avp/shared-types';
import { formatDay } from '@/lib/dates';

/**
 * Who changes what the agency pays: the owner alone (founder decision of 2026-10-11, launch
 * checklist 3.130). The API refuses Checkout and the billing portal to an admin and to a member
 * (`RequireOwner`), and an admin still READS the status. Named here once, so the Settings panel and
 * the scan-limit notice cannot disagree about who is offered Subscribe and Manage billing.
 */
export function canChangeBilling(role: UserRole): boolean {
  return role === 'owner';
}

/**
 * What an admin is told in place of the controls they cannot use. The founder's sentence, exactly
 * (3.130): a plain line, not an alert, because nothing has gone wrong.
 */
export const ASK_AN_OWNER_TO_MANAGE_BILLING = 'Ask an owner to manage billing.';

/**
 * Whether this agency has no subscription and its free scan is gone: the
 * reading `allowanceLabel` uses for "Free scan used". For an agency with no
 * subscription the API reports `scanLimit` 1 and `scansUsedThisPeriod` as the
 * count of scans ever started (`services/scan_allowance.py`). False when no
 * gate applies (`scanLimit` null) and for any active subscription.
 */
export function freeScanUsed(billing: BillingStatus): boolean {
  const { scanLimit, scansUsedThisPeriod: used } = billing;
  if (scanLimit === null || scanLimit === undefined) return false;
  return !billing.isActive && used >= scanLimit;
}

/**
 * The subscription statuses a Checkout can start from: no subscription, or one
 * that is over. Any other status is live at Stripe, and the API refuses a
 * second Checkout for it with a 409 (`TERMINAL_STATUSES` in
 * `services/billing.py`). Named here once.
 */
const CHECKOUT_STATUSES: ReadonlySet<string | null> = new Set([null, 'canceled', 'incomplete_expired']);

export function canCheckout(subscriptionStatus: string | null | undefined): boolean {
  return CHECKOUT_STATUSES.has(subscriptionStatus ?? null);
}

/** The remaining-count label beside a "scan" control, or null when no gate applies. */
export function allowanceLabel(billing: BillingStatus): string | null {
  const { scanLimit, scansUsedThisPeriod: used, periodResetsAt } = billing;
  if (scanLimit === null || scanLimit === undefined) return null;
  // Before the count, as the API checks it: a scan started by this user is
  // refused `SCAN_LIMIT_VERIFY_REQUIRED` whatever the count says, and the
  // label says what pressing the button will meet.
  if (billing.freeScanRequiresGoogle) return 'Free scan needs Google sign-in';
  if (!billing.isActive) {
    return freeScanUsed(billing) ? 'Free scan used' : `${scanLimit} free scan`;
  }
  const base = `${used} of ${scanLimit} scans this month`;
  // At the cap there is no tier above this one to offer; say when it resets.
  if (used >= scanLimit && periodResetsAt) return `${base} · resets ${formatDay(periodResetsAt)}`;
  return base;
}

export type ScanLimitCode =
  | 'SCAN_LIMIT_FREE_USED'
  | 'SCAN_LIMIT_MONTHLY'
  | 'SCAN_LIMIT_VERIFY_REQUIRED';

/** A `402 /problems/scan-limit-reached`, narrowed. */
export interface ScanLimitProblem {
  code: ScanLimitCode;
  detail: string;
  scansUsed: number;
  scanLimit: number;
  periodResetsAt: string | null;
}

/** Whether a problem is the allowance refusing to start a scan. */
export function scanLimitProblem(problem: ProblemDetail): ScanLimitProblem | null {
  if (problem.status !== 402 || !problem.type.endsWith('/scan-limit-reached')) return null;
  const code = problem['code'];
  if (
    code !== 'SCAN_LIMIT_FREE_USED' &&
    code !== 'SCAN_LIMIT_MONTHLY' &&
    code !== 'SCAN_LIMIT_VERIFY_REQUIRED'
  ) {
    return null;
  }
  return {
    code,
    detail: problem.detail,
    scansUsed: Number(problem['scansUsed'] ?? 0),
    scanLimit: Number(problem['scanLimit'] ?? 0),
    periodResetsAt: typeof problem['periodResetsAt'] === 'string' ? problem['periodResetsAt'] : null,
  };
}
