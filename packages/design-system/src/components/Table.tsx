import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { seriesStyle } from '../tokens/color.js';

export interface Column<Row> {
  key: string;
  header: ReactNode;
  /** Numeric columns right-align and use tabular figures. */
  align?: 'start' | 'end';
  width?: string;
  render: (row: Row, index: number) => ReactNode;
}

export interface DataTableProps<Row> {
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  /** Stable row key — required, so React never reorders rows by index. */
  rowKey: (row: Row, index: number) => string;
  /**
   * Marks the row representing the client/prospect under analysis. That row is
   * accented with the brand colour; every other row stays neutral. Same rule as
   * the charts — competitors are never coloured by quality.
   */
  isSubject?: (row: Row, index: number) => boolean;
  caption?: ReactNode;
  emptyMessage?: ReactNode;
  className?: string;
}

/**
 * DataTable.
 *
 * Comparison tables are the second most common surface in this product after
 * the report, so the defaults matter: tabular figures, right-aligned numerics,
 * hairline row rules rather than heavy borders, and a subject row that is
 * marked structurally (aria-current) as well as visually.
 */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  isSubject,
  caption,
  emptyMessage = 'No data yet.',
  className,
}: DataTableProps<Row>): JSX.Element {
  const subjectFill = seriesStyle('subject').fill;

  return (
    <div className={cn('avp-table-wrap', className)}>
      <table className="avp-table">
        {caption != null && <caption className="avp-table__caption">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={col.width != null ? { width: col.width } : undefined}
                className={cn(col.align === 'end' && 'avp-table__cell--end')}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="avp-table__empty">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => {
              const subject = isSubject?.(row, i) ?? false;
              return (
                <tr
                  key={rowKey(row, i)}
                  className={cn(subject && 'is-subject')}
                  aria-current={subject ? 'true' : undefined}
                  style={subject ? { boxShadow: `inset 3px 0 0 0 ${subjectFill}` } : undefined}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(col.align === 'end' && 'avp-table__cell--end')}
                    >
                      {col.render(row, i)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
