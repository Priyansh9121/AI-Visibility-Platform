'use client';

// Uses React hooks, so it must run on the client under the Next App Router.
// Marked at the component rather than the package level, exactly as TextField
// is: Card, Badge, Table and the report primitives stay server-renderable.

import { useId, type JSX, type ReactNode, type SelectHTMLAttributes } from 'react';
import { cn } from '../lib/cn.js';

export interface SelectOption {
  value: string;
  label: string;
}

/**
 * `size` is omitted from the base attributes for the reason `TextFieldProps`
 * gives for the same shadowing: it exists on `SelectHTMLAttributes` as a
 * visible-row count, which is meaningless on a styled single-line control.
 */
export interface SelectFieldProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size' | 'children'> {
  label: string;
  options: readonly SelectOption[];
  hint?: ReactNode;
  error?: ReactNode;
  size?: 'md' | 'lg';
}

/**
 * Single-choice select.
 *
 * Added in Epic 9.14 for the seat-invitation role picker, and added HERE rather
 * than in `apps/web` for the reason the design-system-only rule states and `TextField`'s
 * docstring already spells out: a form control styled locally is precisely the
 * ad hoc styling that rule prohibits. The first draft of the seat panel did
 * hand-assemble a `<select>` out of `avp-field__*` classes, which is the same
 * mistake in a thinner disguise — reaching into another component's internals
 * to borrow its look. Epic 9.13 set the precedent by building four primitives
 * into the system rather than beside it.
 *
 * A native `<select>`, deliberately, rather than a custom listbox. The native
 * control comes with keyboard behaviour, screen-reader semantics, and the
 * platform's own touch picker; a bespoke one has to reimplement all three and
 * usually reimplements two. There is no design requirement here that the native
 * control cannot meet.
 *
 * Accessibility is wired up rather than left to the caller — the same
 * arrangement `TextField` uses: a real bound `<label>`, `aria-describedby` for
 * hint and error, `aria-invalid` on the invalid state, and `role="alert"` on
 * the error so a screen reader hears a validation failure it did not cause.
 */
export function SelectField({
  label,
  options,
  hint,
  error,
  size = 'md',
  className,
  id,
  ...rest
}: SelectFieldProps): JSX.Element {
  const generated = useId();
  const fieldId = id ?? `avp-select-${generated}`;
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
        <select
          id={fieldId}
          className="avp-field__input avp-field__select"
          aria-invalid={error != null ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
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
