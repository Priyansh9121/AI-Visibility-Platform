'use client';

// Uses hooks, so it must run on the client under the Next App Router. Marked
// at the component rather than the package level on purpose, as ScoreDisplay
// and Reveal are: the report primitives stay pure and server-renderable.

import { useEffect, useRef, useState, type CSSProperties, type JSX, type ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { Button } from './Button.js';

export interface ChecklistStep {
  key: string;
  /** The step, as a verb phrase: "Add a client". Never "Step 1". */
  label: string;
  done: boolean;
  /**
   * What is true about this step right now — "2 clients", "After the first
   * scan finishes". A fact about the account, not an instruction.
   */
  detail?: ReactNode;
  /**
   * The way to do it. Usually a Button. Omitted when the step follows from
   * another one and there is nothing to press.
   */
  action?: ReactNode;
}

export interface ChecklistProps {
  /** "Getting started". */
  title: string;
  steps: readonly ChecklistStep[];
  /** One sentence under the title while steps remain. */
  lead?: ReactNode;
  /** The title once every step is lit. */
  completeTitle?: string;
  /** The sentence once every step is lit. */
  completeLead?: ReactNode;
  /** Closes the card. When omitted there is no close control at all. */
  onDismiss?: (() => void) | undefined;
  /** The close control's label while steps remain. */
  dismissLabel?: string;
  /** The close control's label once every step is lit. */
  completeDismissLabel?: string;
  className?: string;
}

/**
 * Checklist — a set of steps that light up as an account becomes real.
 *
 * WHAT IT IS, IN THIS PRODUCT'S OWN TERMS
 * ---------------------------------------
 * The palette's organising idea is that visibility is luminance: the Ledger
 * lights a bar to the height of a score, the meter lights a track to its
 * length, and the score reveal dissolves dim-to-lit. A checklist is the same
 * idea at account scale. Each step is a lamp — a small slab, void and dashed
 * until it is done, lit in the beacon accent once it is — and the count at
 * the head says how many are lit. When all of them are, the card itself reads
 * as lit from within, the same `selected` treatment a Card takes, because a
 * complete account is the selected state of an agency.
 *
 * The dashed void is not decoration. A dashed stroke already means one thing
 * here: an absence that is itself the finding (`EmptyState`, the shelf's
 * notch, the ledger's gap zone). An unlit step is that fact at step scale.
 *
 * WHEN IT MOVES, AND WHEN IT DELIBERATELY DOES NOT
 * ------------------------------------------------
 * It performs no entrance. A checklist sits on a Working screen an operator
 * opens many times in their first week, and design-direction.md §4 excludes
 * arrival motion from that screen for exactly that reason. What moves is
 * what actually changes while the page is open — the line Epic 9.19 drew:
 *
 *   - A lamp that goes from unlit to lit dissolves over the reveal duration
 *     and curve. It is a CSS transition on the `is-done` class, so a lamp
 *     that is already lit at first paint paints lit and moves nothing; only
 *     a step completing under the reader's eyes — a polled scan landing, a
 *     score arriving — is seen to light.
 *   - The moment the LAST step lights, once per agency, the five lamps take
 *     one breath in sequence, left to right, and then the card lights. The
 *     breath is the live badge's own keyframe run once, a reveal long, one
 *     stagger step apart; the card's wash is the selected ring and wash on
 *     the reveal timing. No value here is new. This is gated by JavaScript
 *     on the transition from incomplete to complete during this mount, so a
 *     page that loads already complete performs nothing.
 *
 * Reduced motion collapses every duration and delay at the base layer, and
 * the breath keyframe ends fully lit, so the same markup is correct there.
 *
 * ACCESSIBILITY: the lamps are `aria-hidden`; each step carries its state in
 * words for a screen reader, and the current step is `aria-current="step"`.
 * The numeral is hidden from assistive tech because `lead` says the same
 * thing in a sentence.
 */
export function Checklist({
  title,
  steps,
  lead,
  completeTitle = 'Everything is lit',
  completeLead,
  onDismiss,
  dismissLabel = 'Hide this',
  completeDismissLabel = 'Done',
  className,
}: ChecklistProps): JSX.Element {
  const lit = steps.filter((s) => s.done).length;
  const complete = steps.length > 0 && lit === steps.length;
  const currentKey = steps.find((s) => !s.done)?.key ?? null;

  // The one-shot completion choreography. `wasComplete` starts at the value
  // the component MOUNTED with, so an already-complete checklist never
  // "completes" on load; only a change from false to true during this
  // mount turns the class on, and it is turned off again if the account
  // un-completes (a share link revoked while the page is open).
  const [completing, setCompleting] = useState(false);
  const wasComplete = useRef(complete);
  useEffect(() => {
    if (complete && !wasComplete.current) setCompleting(true);
    if (!complete) setCompleting(false);
    wasComplete.current = complete;
  }, [complete]);

  const headingId = `avp-checklist-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  return (
    <section
      className={cn(
        'avp-checklist',
        complete && 'is-complete',
        completing && 'is-completing',
        className,
      )}
      style={{ '--avp-checklist-count': steps.length } as CSSProperties}
      aria-labelledby={headingId}
    >
      <header className="avp-checklist__head">
        <p className="avp-checklist__count" aria-hidden="true">
          <span className="avp-checklist__lit">{lit}</span>
          <span className="avp-checklist__of">/{steps.length}</span>
        </p>
        <div className="avp-checklist__copy">
          <h2 className="avp-checklist__title" id={headingId}>
            {complete ? completeTitle : title}
          </h2>
          {(complete ? completeLead : lead) != null && (
            <p className="avp-checklist__lead">{complete ? completeLead : lead}</p>
          )}
        </div>
        {onDismiss != null && (
          <div className="avp-checklist__dismiss">
            <Button
              size="sm"
              variant={complete ? 'primary' : 'ghost'}
              onClick={onDismiss}
            >
              {complete ? completeDismissLabel : dismissLabel}
            </Button>
          </div>
        )}
      </header>

      <ol className="avp-checklist__steps">
        {steps.map((step, index) => {
          const current = step.key === currentKey;
          return (
            <li
              key={step.key}
              className={cn(
                'avp-checklist__step',
                step.done ? 'is-done' : current ? 'is-current' : 'is-pending',
              )}
              style={{ '--avp-checklist-index': index } as CSSProperties}
              aria-current={current ? 'step' : undefined}
            >
              <span className="avp-checklist__lamp" aria-hidden="true" />
              <div className="avp-checklist__body">
                <span className="avp-checklist__label">
                  <span className="avp-visually-hidden">{step.done ? 'Done: ' : 'To do: '}</span>
                  {step.label}
                </span>
                {step.detail != null && (
                  <span className="avp-checklist__detail">{step.detail}</span>
                )}
              </div>
              {!step.done && step.action != null && (
                <div className="avp-checklist__action">{step.action}</div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
