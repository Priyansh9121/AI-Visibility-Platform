import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

export interface LoadingStateProps {
  /** What is being waited on, as a sentence. "Assembling the report". */
  message: string;
  /**
   * The steps actually happening, when they are known and worth naming.
   *
   * Omit when the wait is a single request. A list of invented steps is worse
   * than no list: it implies progress the screen cannot actually observe.
   */
  steps?: readonly string[];
  /** How long this usually takes, if it is long enough that a person wonders. */
  hint?: ReactNode;
  className?: string;
}

/**
 * LoadingState — the one way this product says "wait".
 *
 * Before Epic 9.11 there were four of these, one per route, each a bare
 * paragraph with slightly different words and no shared treatment. They were
 * identical markup with different strings, which is how a product ends up
 * looking assembled rather than designed.
 *
 * **There is deliberately no spinner and no progress bar.** The rule the intake
 * screen set in Epic 2 and the dashboard restated in Epic 9.7 holds everywhere:
 * this product cannot measure real progress on any of its long operations — the
 * scan phases are not exposed by any endpoint — and a bar that fills on a timer
 * is a lie the user eventually catches. Naming the work is what makes a wait
 * tolerable, so that is what this does.
 *
 * `aria-live="polite"` and `role="status"` because a screen reader gets no
 * benefit from a visual change it is never told about.
 *
 * Pure and hook-free, so it stays server-renderable like Card and Badge.
 */
export function LoadingState({
  message,
  steps,
  hint,
  className,
}: LoadingStateProps): JSX.Element {
  return (
    <div className={cn('avp-loading', className)} role="status" aria-live="polite">
      <p className="avp-loading__message">
        {/* Alive, not progressing — see the stylesheet's note. Epic 16.3. */}
        <span className="avp-loading__pulse" aria-hidden="true" />
        {message}
      </p>
      {steps != null && steps.length > 0 && (
        <ol className="avp-loading__steps">
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}
      {hint != null && <p className="avp-loading__hint">{hint}</p>}
    </div>
  );
}
