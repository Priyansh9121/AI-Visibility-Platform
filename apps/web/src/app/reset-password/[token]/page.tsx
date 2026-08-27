'use client';

/**
 * /reset-password/{token} — Epic 9.13.
 *
 * The second unauthenticated token-bearing route in this product, and it
 * follows the first. Epic 9.8's `/share/{token}` established the pattern: the
 * token is the whole credential, every refusal looks the same, and the screen
 * has exactly one thing to say when it does not work.
 *
 * **The token is NOT validated on load.** There is deliberately no "is this
 * link good?" request before the form is shown, for two reasons: a probe
 * endpoint would be a free oracle for testing guesses without committing to
 * one, and a link opened from an email should show a form, not a spinner
 * followed by a form. The single POST that sets the password is also the one
 * that checks the token.
 */

import { use, useState, type FormEvent } from 'react';
import { Button, Card, CardBody, ErrorState, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

type State =
  | { kind: 'form' }
  | { kind: 'working' }
  | { kind: 'done' }
  | { kind: 'rejected'; detail: string }
  | { kind: 'invalid'; detail: string };

export default function ResetPasswordRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [password, setPassword] = useState('');
  const [state, setState] = useState<State>({ kind: 'form' });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: 'working' });
    try {
      await api.confirmPasswordReset(token, password);
      setState({ kind: 'done' });
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 400) {
        // The link itself is no good — unknown, expired, or already used. The
        // API does not say which, and neither does this.
        setState({ kind: 'invalid', detail: err.problem.detail });
      } else if (err instanceof ApiProblem && err.status === 422) {
        // The password failed the rules. The link is still fine, so stay on
        // the form rather than sending someone back to request a new link for
        // a problem that has nothing to do with the link.
        const fields = err.fieldErrors();
        setState({
          kind: 'rejected',
          detail: fields.newPassword ?? err.problem.detail,
        });
      } else {
        setState({
          kind: 'rejected',
          detail: 'The request did not complete. Check your connection and try again.',
        });
      }
    }
  }

  if (state.kind === 'done') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <div className="mx-auto flex max-w-form flex-col gap-4">
          <Card elevation="raised">
            <CardBody>
              <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                Password changed
              </h1>
              <p className="mt-3 text-ui-base leading-prose text-text-secondary">
                Every session that was open has been signed out, on every device.
                Sign in with your new password.
              </p>
              <div className="mt-5">
                <Button variant="primary" onClick={() => window.location.assign('/')}>
                  Sign in
                </Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </main>
    );
  }

  if (state.kind === 'invalid') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <div className="mx-auto flex max-w-form flex-col gap-4">
          <ErrorState
            title="This reset link is not valid"
            detail={state.detail}
            action={
              <Button variant="secondary" onClick={() => window.location.assign('/')}>
                Ask for a new link
              </Button>
            }
          />
        </div>
      </main>
    );
  }

  const busy = state.kind === 'working';

  return (
    <main className="mx-auto max-w-report px-6 py-18">
      <div className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <form onSubmit={submit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                  Choose a new password
                </h1>
                <p className="text-ui-sm leading-prose text-text-secondary">
                  Setting it signs out every other session.
                </p>
              </div>

              <TextField
                label="New password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                error={state.kind === 'rejected' ? state.detail : undefined}
                hint="At least 12 characters. A phrase you can remember beats a short, clever one."
                disabled={busy}
              />

              <Button type="submit" variant="primary" fullWidth disabled={busy}>
                {busy ? 'Setting your password…' : 'Set new password'}
              </Button>
            </form>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
