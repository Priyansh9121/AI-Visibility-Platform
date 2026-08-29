import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

export interface EmptyStateProps {
  /** Small structural label above the title. Same device as PageSection's. */
  eyebrow?: string;
  /** What is not here, as a statement. "No scans yet", not "No data". */
  title: string;
  /** Why it is not here, and what fills it. One or two sentences. */
  body?: ReactNode;
  /** A quieter second line — how long something takes, what happens next. */
  note?: ReactNode;
  /** The way out. Usually a Button. Omitted when there is nothing to offer. */
  action?: ReactNode;
  /**
   * A drawing of the data that WOULD be here, from this product's own chart
   * vocabulary — see the note below on why this is not an icon slot.
   */
  figure?: ReactNode;
  className?: string;
}

/**
 * EmptyState — the one way this product says "there is nothing here yet".
 *
 * The third member of the set `LoadingState` and `ErrorState` opened in Epic
 * 9.11, and it exists for the same reason they do: before this there were four
 * of these — the dashboard's brand-new-agency card, its no-scans-yet line, the
 * clients list's empty message and the seat roster's — written out longhand
 * each time, three of them as a bare `<span>` inside a table cell. Identical
 * intent, four different treatments, which is how a product ends up looking
 * assembled rather than designed.
 *
 * WHY IT IS DASHED, AND WHY THAT IS NOT DECORATION
 * ------------------------------------------------
 * A dashed hairline already means one specific thing in this system: an
 * absence that is itself the finding. `.avp-shelf__notch` draws the rank
 * nobody is standing in, `.avp-ledger__gap-zone` outlines the points not
 * earned, and `.avp-ledger--empty` frames a scan with nothing to score. An
 * empty screen is the same fact at page scale, so it gets the same stroke
 * rather than a new one.
 *
 * WHY `figure` IS NOT AN ICON SLOT
 * --------------------------------
 * ip-safety.md #4 rules out icon packs and illustration kits, and
 * design-direction.md's house style is that a graphic here is DATA drawn as
 * illustration — the Luminance Ledger and the Answer Shelf both are. So the
 * figure a caller passes should be the shape of the data that will exist once
 * the screen is not empty: the dashboard passes an unmeasured Ledger, which is
 * the five real weighted dimensions with none of them lit. A decorative mark
 * bolted on beside the copy would be exactly the thing that rule prohibits.
 *
 * Pure and hook-free, so it stays server-renderable like Card, Badge and Table.
 */
export function EmptyState({
  eyebrow,
  title,
  body,
  note,
  action,
  figure,
  className,
}: EmptyStateProps): JSX.Element {
  return (
    <div className={cn('avp-empty', figure != null && 'avp-empty--figured', className)}>
      <div className="avp-empty__copy">
        {eyebrow != null && <p className="avp-empty__eyebrow">{eyebrow}</p>}
        <p className="avp-empty__title">{title}</p>
        {body != null && <div className="avp-empty__body">{body}</div>}
        {note != null && <p className="avp-empty__note">{note}</p>}
        {action != null && <div className="avp-empty__action">{action}</div>}
      </div>
      {figure != null && (
        <div className="avp-empty__figure">{figure}</div>
      )}
    </div>
  );
}
