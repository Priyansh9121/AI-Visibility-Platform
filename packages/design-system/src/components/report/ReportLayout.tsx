import type { HTMLAttributes, JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Report layout primitives.
 *
 * ip-safety.md #3 requires primary screens to be structured as a NARRATIVE
 * REPORT — score, biggest gap, proof, fix, pitch — rather than a metrics-tile
 * dashboard. These primitives make that structure the path of least resistance:
 * a report is assembled from ordered Beats, and the spacing between them
 * (--avp-beat) is larger than any spacing inside them, so the five-part shape
 * is legible from the page thumbnail.
 */

/** The five narrative beats, in order. The type makes skipping one a compile error. */
export type BeatId = 'score' | 'gap' | 'proof' | 'fix' | 'pitch';

export const BEAT_SEQUENCE: readonly BeatId[] = ['score', 'gap', 'proof', 'fix', 'pitch'];

const BEAT_EYEBROW: Record<BeatId, string> = {
  score: 'Where you stand',
  gap: 'The biggest gap',
  proof: 'The evidence',
  fix: 'What to change',
  pitch: 'The opportunity',
};

export interface ReportPageProps extends HTMLAttributes<HTMLElement> {
  children: ReactNode;
  /** White-label surface for Epic 7 — agency logo and name, never ours. */
  brand?: ReactNode;
}

/** The measured column the whole report lives in. Matches the PDF export width. */
export function ReportPage({ children, brand, className, ...rest }: ReportPageProps): JSX.Element {
  return (
    <article className={cn('avp-report', className)} {...rest}>
      {brand != null && <div className="avp-report__brand">{brand}</div>}
      {children}
    </article>
  );
}

export interface ReportHeaderProps {
  subject: string;
  subtitle?: ReactNode;
  meta?: ReactNode;
  className?: string;
}

export function ReportHeader({ subject, subtitle, meta, className }: ReportHeaderProps): JSX.Element {
  return (
    <header className={cn('avp-report__header', className)}>
      <h1 className="avp-report__title">{subject}</h1>
      {subtitle != null && <p className="avp-report__subtitle">{subtitle}</p>}
      {meta != null && <div className="avp-report__meta">{meta}</div>}
    </header>
  );
}

export interface BeatProps {
  id: BeatId;
  /** The finding, stated as a claim. Not a section label. */
  heading: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * One narrative beat.
 *
 * The heading is a CLAIM, not a category. "You are absent from 71% of buying
 * prompts" does the work; "Mention Rate" does not. The eyebrow supplies the
 * structural label so the heading is free to argue.
 */
export function Beat({ id, heading, children, className }: BeatProps): JSX.Element {
  const step = BEAT_SEQUENCE.indexOf(id) + 1;
  return (
    <section className={cn('avp-beat', `avp-beat--${id}`, className)} aria-labelledby={`beat-${id}`}>
      <div className="avp-beat__eyebrow">
        <span className="avp-beat__step">{String(step).padStart(2, '0')}</span>
        <span className="avp-beat__kicker">{BEAT_EYEBROW[id]}</span>
      </div>
      <h2 className="avp-beat__heading" id={`beat-${id}`}>
        {heading}
      </h2>
      <div className="avp-beat__body">{children}</div>
    </section>
  );
}

/** Long-form narrative text, capped at the reading measure. */
export function Prose({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div className={cn('avp-prose', className)} {...rest}>
      {children}
    </div>
  );
}

export interface EvidenceProps {
  /** What was asked — a prompt we generated, so it is our own text. */
  prompt: string;
  /** Engine name. A fact. */
  engine: string;
  /**
   * Structured findings ONLY. ip-safety.md #7: never pass raw answer text or
   * competitor copy through here. Mentions, positions, and cited domains are
   * facts; the engine's prose is not ours to republish.
   */
  findings: readonly { label: string; value: ReactNode }[];
  className?: string;
}

/**
 * Evidence block — the receipt behind a claim.
 *
 * Deliberately shaped so it CANNOT carry a paragraph of scraped answer text:
 * the API is a prompt string plus label/value facts. The constraint is enforced
 * by the prop types, not by a reviewer noticing.
 */
export function Evidence({ prompt, engine, findings, className }: EvidenceProps): JSX.Element {
  return (
    <div className={cn('avp-evidence', className)}>
      <div className="avp-evidence__head">
        <span className="avp-evidence__engine">{engine}</span>
        <p className="avp-evidence__prompt">{prompt}</p>
      </div>
      <dl className="avp-evidence__findings">
        {findings.map((f) => (
          <div key={f.label} className="avp-evidence__finding">
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export interface FixItem {
  id: string;
  title: string;
  detail?: ReactNode;
  priority: 'high' | 'medium' | 'low';
  effort: 'S' | 'M' | 'L';
  /** Points recoverable, from the ledger gap calculation. */
  pointsUpside?: number;
}

/**
 * Prioritised fix list — the fix beat.
 *
 * Ordered by the caller (Epic 8 supplies the ranking); this primitive renders
 * the shape and shows the recoverable-points figure that ties each fix back to
 * the ledger, so the report never asserts a fix matters without saying by how
 * much.
 */
export function FixList({ items, className }: { items: readonly FixItem[]; className?: string }): JSX.Element {
  return (
    <ol className={cn('avp-fixlist', className)}>
      {items.map((item) => (
        <li key={item.id} className="avp-fixlist__item">
          <div className="avp-fixlist__main">
            <h3 className="avp-fixlist__title">{item.title}</h3>
            {item.detail != null && <div className="avp-fixlist__detail">{item.detail}</div>}
          </div>
          <div className="avp-fixlist__tags">
            <span className={cn('avp-tag', `avp-tag--priority-${item.priority}`)}>
              {item.priority} priority
            </span>
            <span className="avp-tag">{`effort ${item.effort}`}</span>
            {item.pointsUpside != null && (
              <span className="avp-tag avp-tag--upside">{`+${item.pointsUpside.toFixed(1)} pts`}</span>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
