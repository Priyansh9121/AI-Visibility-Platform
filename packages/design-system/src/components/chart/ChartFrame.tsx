import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

export interface ChartFrameProps {
  /** Short, declarative. Reads as a finding, not a chart title. */
  title?: ReactNode;
  caption?: ReactNode;
  /**
   * Accessible description of what the chart shows. Required — a chart without
   * one is unusable to a screen reader and unusable in an exported PDF's
   * accessibility tree, which some enterprise clients audit.
   */
  ariaLabel: string;
  /** Screen-reader table equivalent. Rendered visually hidden. */
  dataTable?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * Shared shell for every chart in the system: title, caption, accessible
 * description, and a visually-hidden data-table equivalent.
 *
 * Recharts-based charts (product-spec.md §5.1) mount inside this frame too, so
 * they inherit the same accessibility contract rather than each chart
 * reinventing it.
 */
export function ChartFrame({
  title,
  caption,
  ariaLabel,
  dataTable,
  children,
  className,
}: ChartFrameProps): JSX.Element {
  return (
    <figure className={cn('avp-chart-frame', className)}>
      {title != null && <figcaption className="avp-chart-frame__title">{title}</figcaption>}
      <div className="avp-chart-frame__plot" role="img" aria-label={ariaLabel}>
        {children}
      </div>
      {caption != null && <p className="avp-chart-frame__caption">{caption}</p>}
      {dataTable != null && <div className="avp-visually-hidden">{dataTable}</div>}
    </figure>
  );
}
