'use client';

/**
 * Change your password while signed in — Epic 9.14.
 *
 * Settings has said "Changing your password while signed in. The reset-by-email
 * flow works; an authenticated change is a different endpoint and is not built"
 * since Epic 9.13. It is built now, and this is the form.
 *
 * WHAT THIS SCREEN SAYS BEFORE THE CLICK, NOT AFTER
 * -------------------------------------------------
 * Changing a password here signs out every OTHER device and leaves this one
 * working. That is a real consequence — somebody's phone stops working — and it
 * is also usually the reason they came to this form. Either way it belongs
 * above the button rather than in a toast afterwards.
 *
 * WHY THE CURRENT PASSWORD FIELD EXISTS ON A PAGE YOU ARE ALREADY SIGNED IN TO
 * ---------------------------------------------------------------------------
 * The server requires it, so the form must collect it. The form also SAYS why,
 * because "why is it asking me this, I'm already logged in" is the obvious
 * reaction and the answer is a good one: a session proves someone got in once,
 * not that they are still the account holder. An unlocked laptop presents a
 * perfectly valid session.
 *
 * ip-safety.md #1: derived from the endpoint and the user goal. No competitor's
 * account screen was referenced.
 */

import { useState, type FormEvent, type JSX } from 'react';
import { Button, Card, CardBody, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

export type PasswordChangeState =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'done' }
  | { kind: 'failed'; field: 'currentPassword' | 'newPassword' | null; detail: string };

export interface PasswordChangePanelProps {
  /** Set in tests to render a state the static renderer cannot reach. */
  initialState?: PasswordChangeState;
}

export function PasswordChangePanel({
  initialState = { kind: 'idle' },
}: PasswordChangePanelProps): JSX.Element {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [state, setState] = useState<PasswordChangeState>(initialState);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: 'working' });
    try {
      await api.changePassword(current, next);
      setCurrent('');
      setNext('');
      setState({ kind: 'done' });
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 401) {
        // The API says only "that is not your current password" and nothing
        // about why. This screen repeats exactly that and adds nothing —
        // "no account found" would be a sentence the server refused to write,
        // and there is no such account to find: the caller is signed in.
        setState({
          kind: 'failed',
          field: 'currentPassword',
          detail: err.problem.detail,
        });
      } else if (err instanceof ApiProblem && err.status === 422) {
        const fields = err.fieldErrors();
        setState({
          kind: 'failed',
          field: fields.newPassword ? 'newPassword' : null,
          detail: fields.newPassword ?? err.problem.detail,
        });
      } else {
        setState({
          kind: 'failed',
          field: null,
          detail: 'The change did not go through. Check your connection and try again.',
        });
      }
    }
  }

  const busy = state.kind === 'working';
  const failed = state.kind === 'failed' ? state : null;

  return (
    <Card elevation="seated">
      <CardBody>
        <form onSubmit={submit} className="flex max-w-form flex-col gap-5">
          <div className="flex flex-col gap-2">
            <h3 className="text-ui-md font-medium text-text-primary">Change your password</h3>
            <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
              You will stay signed in here. Every other device signs out immediately —
              which is the point, if you think someone else has your password.
            </p>
          </div>

          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            error={failed?.field === 'currentPassword' ? failed.detail : undefined}
            hint="Asked for because being signed in is not proof of who you are — an unlocked laptop is a valid session too."
            disabled={busy}
          />

          <TextField
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            value={next}
            onChange={(e) => setNext(e.target.value)}
            error={failed?.field === 'newPassword' ? failed.detail : undefined}
            hint="At least 12 characters. A phrase you can remember beats a short, clever one."
            disabled={busy}
          />

          {failed?.field === null && (
            <p role="alert" className="text-ui-sm leading-prose text-danger">
              {failed.detail}
            </p>
          )}

          {state.kind === 'done' && (
            <p role="status" className="max-w-measure text-ui-sm leading-prose text-text-secondary">
              Your password has been changed. Every other device has been signed out; this
              one is still signed in.
            </p>
          )}

          <div>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || current.trim() === '' || next.trim() === ''}
            >
              {busy ? 'Changing…' : 'Change password'}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
