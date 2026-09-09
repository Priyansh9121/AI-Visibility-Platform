'use client';

// Uses React hooks, so it must run on the client under the Next App Router.
// Marked at the component rather than the package level on purpose: Card,
// Badge, Table and the report primitives are pure and stay server-renderable,
// which Epic 7's server-side PDF render depends on.

import { useId, type InputHTMLAttributes, type JSX, type ReactNode } from 'react';
import { cn } from '../lib/cn.js';

/**
 * `size` and `prefix` are omitted from the base HTML attributes on purpose:
 * both exist on `InputHTMLAttributes` with incompatible meanings — `size` is a
 * character-width number, and `prefix` is the RDFa attribute typed as `string`.
 * Neither is used on a styled input, and shadowing them keeps this component's
 * own props readable at the call site.
 */
export interface TextFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  label: string;
  /** Persistent helper text. Shown below the field. */
  hint?: ReactNode;
  /** Error message. When set, the field renders in its invalid state. */
  error?: ReactNode;
  /** Rendered inside the field, before the input — e.g. a `https://` affix. */
  prefix?: ReactNode;
  size?: 'md' | 'lg';
}

/**
 * Single-line text input.
 *
 * Added in Epic 2 for the URL intake form. It lives here rather than in
 * apps/web because the design-system-only rule prohibits ad hoc styling on customer-facing
 * screens — a form input styled locally would be exactly that.
 *
 * Accessibility is wired up rather than left to the caller: the label is a
 * real `<label>` bound by id, hint and error text are linked via
 * `aria-describedby`, and the invalid state sets `aria-invalid` so it is
 * announced, not merely coloured. Error text is `role="alert"` so a screen
 * reader hears a validation failure it did not cause.
 */
export function TextField({
  label,
  hint,
  error,
  prefix,
  size = 'md',
  className,
  id,
  ...rest
}: TextFieldProps): JSX.Element {
  const generated = useId();
  const fieldId = id ?? `avp-field-${generated}`;
  const hintId = `${fieldId}-hint`;
  const errorId = `${fieldId}-error`;

  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined;

  return (
    <div className={cn('avp-field', className)}>
      <label className="avp-field__label" htmlFor={fieldId}>
        {label}
      </label>

      <div
        className={cn(
          'avp-field__control',
          `avp-field__control--${size}`,
          error != null && 'is-invalid',
        )}
      >
        {prefix != null && <span className="avp-field__prefix">{prefix}</span>}
        <input
          id={fieldId}
          className="avp-field__input"
          aria-invalid={error != null ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        />
      </div>

      {error != null && (
        <p className="avp-field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
      {error == null && hint != null && (
        <p className="avp-field__hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  );
}
