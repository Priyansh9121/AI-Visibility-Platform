import type { JSX, ReactNode } from 'react';
import { Card, CardBody } from '../Card.js';
import { cn } from '../../lib/cn.js';

export interface ErrorStateProps {
  /** What went wrong, as a statement a person can act on. Not a code. */
  title: string;
  /** One or two sentences of detail. Often the API's own problem detail. */
  detail?: ReactNode;
  /** A machine code, shown small and last. Only when one genuinely exists. */
  code?: string | null;
  /** A way out — usually a Button. Omitted when there is nothing to offer. */
  action?: ReactNode;
  className?: string;
}

/**
 * ErrorState — the one way this product says "that did not work".
 *
 * Before Epic 9.11 there were five of these: three full-page ones (report,
 * dashboard, share) and two inline ones (the dashboard's poll-failure and
 * re-run cards), all the same Card + title + detail + optional button, written
 * out longhand each time.
 *
 * **The title is a statement, never a status code.** "No report for that scan"
 * tells someone what happened; "404" tells them the shape of our routing. The
 * machine code is available in `code` when one exists, rendered small and last,
 * for the case where someone needs to quote it back to us.
 *
 * **It does not decide what to say.** Callers pass copy, because only the
 * caller knows whether a 404 means "that scan is not yours" or "that link has
 * been withdrawn" — and collapsing those into one generic message is how error
 * screens stop being useful. The component owns the treatment, not the words.
 *
 * Seated rather than raised: an error is not a thing to lift off the page, and
 * the report and dashboard already settled on seated for exactly this.
 */
export function ErrorState({
  title,
  detail,
  code,
  action,
  className,
}: ErrorStateProps): JSX.Element {
  return (
    <Card elevation="seated" className={cn('avp-errorstate', className)}>
      <CardBody>
        <div role="alert">
          <p className="avp-errorstate__title">{title}</p>
          {detail != null && <p className="avp-errorstate__detail">{detail}</p>}
          {code != null && code !== '' && (
            <p className="avp-errorstate__code">Reference: {code}</p>
          )}
        </div>
        {action != null && <div className="avp-errorstate__action">{action}</div>}
      </CardBody>
    </Card>
  );
}
