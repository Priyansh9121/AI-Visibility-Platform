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
 */

import type { BillingStatus, ProblemDetail } from '@avp/shared-types';
import { formatDay } from '@/lib/dates';

/** The remaining-count label beside a "scan" control, or null when no gate applies. */
export function allowanceLabel(billing: BillingStatus): string | null {
  const { scanLimit, scansUsedThisPeriod: used, periodResetsAt } = billing;
  if (scanLimit === null || scanLimit === undefined) return null;
  if (!billing.isActive) {
    return used >= scanLimit ? 'Free scan used' : `${scanLimit} free scan`;
  }
  const base = `${used} of ${scanLimit} scans this month`;
  // At the cap there is no tier above this one to offer; say when it resets.
  if (used >= scanLimit && periodResetsAt) return `${base} · resets ${formatDay(periodResetsAt)}`;
  return base;
}

export type ScanLimitCode = 'SCAN_LIMIT_FREE_USED' | 'SCAN_LIMIT_MONTHLY';

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
  if (code !== 'SCAN_LIMIT_FREE_USED' && code !== 'SCAN_LIMIT_MONTHLY') return null;
  return {
    code,
    detail: problem.detail,
    scansUsed: Number(problem['scansUsed'] ?? 0),
    scanLimit: Number(problem['scanLimit'] ?? 0),
    periodResetsAt: typeof problem['periodResetsAt'] === 'string' ? problem['periodResetsAt'] : null,
  };
}
