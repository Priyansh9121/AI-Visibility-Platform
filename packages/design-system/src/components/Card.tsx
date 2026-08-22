import type { HTMLAttributes, JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';

/** Only levels that survive print are offered to product code. */
export type CardElevation = 'flat' | 'seated' | 'raised';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  elevation?: CardElevation;
  /** Renders the lit-from-within selected state. */
  selected?: boolean;
  children: ReactNode;
}

/**
 * Card.
 *
 * Elevation is borders and tone, plus at most a hard 1px offset — never a
 * blurred halo. Blurred shadows vanish when the report is printed, and this
 * product's main artifact is a PDF a client reads on paper.
 *
 * `lifted` and `overlay` are intentionally NOT exposed here: they belong to
 * transient popovers and modals, which never appear in an export.
 */
export function Card({
  elevation = 'seated',
  selected = false,
  className,
  children,
  ...rest
}: CardProps): JSX.Element {
  return (
    <div
      className={cn('avp-card', `avp-card--${elevation}`, selected && 'is-selected', className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div className={cn('avp-card__header', className)} {...rest}>
      {children}
    </div>
  );
}

export function CardTitle({ className, children, ...rest }: HTMLAttributes<HTMLHeadingElement>): JSX.Element {
  return (
    <h3 className={cn('avp-card__title', className)} {...rest}>
      {children}
    </h3>
  );
}

export function CardBody({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div className={cn('avp-card__body', className)} {...rest}>
      {children}
    </div>
  );
}

export function CardFooter({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div className={cn('avp-card__footer', className)} {...rest}>
      {children}
    </div>
  );
}
