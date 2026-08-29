'use client';

// Uses hooks and IntersectionObserver, so it must run on the client under the
// Next App Router. Marked at the component rather than the package level on
// purpose, exactly as LuminanceLedger is: Card, Badge, Table and the report
// primitives stay pure and server-renderable, which the PDF path depends on.

import {
  createContext,
  useContext,
  type CSSProperties,
  type JSX,
  type ReactNode,
} from 'react';
import { cn } from '../lib/cn.js';
import { useRevealOnIntersect } from '../lib/useRevealOnIntersect.js';

/**
 * REVEAL — content arrives rather than being already there.
 *
 * `design-direction.md` §4 settled the vocabulary this works in: *emphasis is
 * light, not lift.* Nothing here bounces, scales, slides in from the side, or
 * parallaxes. A revealed element fades up by one spacing step over the same
 * `duration-reveal` / `ease-reveal` the score already uses, and stops. The page
 * gains a sense of being composed in front of you; it does not gain personality.
 *
 * That token reuse is the point rather than a convenience. §4 defines
 * `600ms cubic-bezier(0.22, 1, 0.36, 1)` as *the reveal* — the dim-to-lit
 * dissolve that states the product's metaphor. A second, different reveal
 * timing for text would have meant the page and its signature chart moving to
 * two different rhythms, which is the difference between a product that reads
 * as authored and one that reads as assembled.
 *
 * THE THING THIS COMPONENT MUST NOT DO
 * ------------------------------------
 * Hide content. Everything below starts at `opacity: 0`, which means every path
 * that could fail to reveal is a blank page rather than a still one. There are
 * four independent guarantees against that, and only one of them is JavaScript:
 *
 *   - `@media (prefers-reduced-motion: reduce)` paints `.avp-reveal` fully
 *     revealed in CSS, with no transition. Not a faster animation — no
 *     animation, and no dependence on this component running.
 *   - `@media (scripting: none)` does the same for a visitor with JavaScript
 *     disabled.
 *   - `useRevealOnIntersect` reveals immediately if `IntersectionObserver` is
 *     missing, or if it has nothing to observe.
 *   - `animate={false}` starts revealed, which is what every static render and
 *     every test gets.
 *
 * ip-safety.md #1 and #5: fade-and-rise-on-scroll is a generic technique with
 * nothing proprietary to derive it from. Nobody's site, markup or stylesheet
 * was inspected, measured or referenced. The timing comes from this project's
 * own `design-direction.md` §4 and the tokens Epic 0 shipped.
 */

/** The elements a reveal wrapper is allowed to be. */
export type RevealElement = 'div' | 'p' | 'li' | 'section' | 'span' | 'article';

interface RevealGroupValue {
  revealed: boolean;
}

/**
 * Set by `RevealGroup` so its children reveal together rather than each
 * running its own observer.
 *
 * The stagger between them is NOT in here — it is a CSS custom property, so
 * that no millisecond value is ever computed in JavaScript. See the group.
 */
const RevealGroupContext = createContext<RevealGroupValue | null>(null);

export interface RevealProps {
  children: ReactNode;
  /**
   * Position in a staggered group. Multiplies the group's step.
   *
   * Only meaningful inside a `RevealGroup`; a standalone `Reveal` reveals
   * alone and has nothing to be staggered against.
   */
  index?: number | undefined;
  /** The element to render. `div` unless the surrounding markup demands otherwise. */
  as?: RevealElement | undefined;
  /**
   * Whether to animate at all. `false` renders the final state immediately —
   * what tests, static renders and the print path get.
   */
  animate?: boolean | undefined;
  className?: string | undefined;
}

/**
 * One element that fades and rises into place when it reaches the viewport.
 *
 * Standalone it owns an observer. Inside a `RevealGroup` it uses the group's,
 * and offsets itself by `index`.
 */
export function Reveal({
  children,
  index = 0,
  as: Element = 'div',
  animate = true,
  className,
}: RevealProps): JSX.Element {
  const group = useContext(RevealGroupContext);
  // A grouped child must not observe itself: the group is what decides when
  // the set arrives, and five children with five observers would reveal at
  // five slightly different moments as each crossed the fold.
  const own = useRevealOnIntersect<HTMLElement>(animate && group === null);
  const revealed = group !== null ? group.revealed : own.revealed;

  return (
    <Element
      ref={group === null ? (own.ref as never) : undefined}
      className={cn('avp-reveal', revealed && 'avp-reveal--revealed', className)}
      style={index > 0 ? ({ '--avp-reveal-index': index } as CSSProperties) : undefined}
    >
      {children}
    </Element>
  );
}

export interface RevealGroupProps {
  children: ReactNode;
  /** The element to render. */
  as?: RevealElement | 'ol' | 'ul' | undefined;
  /**
   * Milliseconds between siblings. Defaults to the `stagger-reveal` token.
   *
   * Supplied as an override of that custom property rather than as a number
   * this component multiplies, so the default lives in the token layer and
   * there is no duration literal anywhere in this file.
   */
  step?: number | undefined;
  animate?: boolean | undefined;
  className?: string | undefined;
}

/**
 * A set of `Reveal`s that arrive together, one step apart.
 *
 * ONE OBSERVER FOR THE SET, NOT ONE EACH. The group is the thing that enters
 * view; the children are its parts. Observing each child separately would make
 * the stagger depend on scroll speed — fast scrolling would collapse it, slow
 * scrolling would stretch it — instead of being a fixed, authored rhythm.
 */
export function RevealGroup({
  children,
  as: Element = 'div',
  step,
  animate = true,
  className,
}: RevealGroupProps): JSX.Element {
  const { ref, revealed } = useRevealOnIntersect<HTMLElement>(animate);

  return (
    <RevealGroupContext.Provider value={{ revealed }}>
      <Element
        ref={ref as never}
        className={cn('avp-reveal-group', className)}
        style={
          step === undefined
            ? undefined
            : ({ '--avp-stagger-reveal': `${step}ms` } as CSSProperties)
        }
      >
        {children}
      </Element>
    </RevealGroupContext.Provider>
  );
}
