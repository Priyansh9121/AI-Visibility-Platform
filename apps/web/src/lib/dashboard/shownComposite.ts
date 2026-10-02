/**
 * The composite a dashboard surface may show for a scan — checklist 3.24.
 *
 * A FAILED scan has no score on screen, whatever the payload carries: the API
 * sends null for one, and this keeps every reader of `compositeScore` (the row
 * meter, the portfolio median, the sidebar, the getting-started checklist)
 * agreeing if a value ever reaches them.
 */
export function shownComposite(scan: {
  status: string;
  compositeScore?: string | null | undefined;
}): string | null {
  return scan.status === 'failed' ? null : (scan.compositeScore ?? null);
}
