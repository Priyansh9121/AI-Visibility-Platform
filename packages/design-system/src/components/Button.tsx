import type { ButtonHTMLAttributes, JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Lucide icon element (MIT) or custom-drawn SVG only — the licensed-assets rule. */
  iconStart?: ReactNode;
  iconEnd?: ReactNode;
  fullWidth?: boolean;
}

/**
 * Button.
 *
 * Focus is a beacon ring + halo, never a lift or an offset outline: emphasis in
 * this system is light, not elevation. Hover darkens rather than raising, for
 * the same reason.
 */
export function Button({
  variant = 'secondary',
  size = 'md',
  iconStart,
  iconEnd,
  fullWidth = false,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps): JSX.Element {
  return (
    <button
      type={type}
      className={cn(
        'avp-btn',
        `avp-btn--${variant}`,
        `avp-btn--${size}`,
        fullWidth && 'avp-btn--full',
        className,
      )}
      {...rest}
    >
      {iconStart != null && <span className="avp-btn__icon">{iconStart}</span>}
      {children != null && <span className="avp-btn__label">{children}</span>}
      {iconEnd != null && <span className="avp-btn__icon">{iconEnd}</span>}
    </button>
  );
}
