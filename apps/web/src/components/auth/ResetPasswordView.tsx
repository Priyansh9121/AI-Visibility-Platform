'use client';

/**
 * The reset-password screen — Epic 9.13, split out and tested in Epic 9.14.
 *
 * Pure and prop-driven so every state is reachable by a static render —
 * including the refusal, which is the one that matters here and which no static
 * render of the route could ever get to.
 *
 * THE PROPERTY THIS SCREEN EXISTS TO PRESERVE
 * -------------------------------------------
 * The backend folds unknown, expired, already-used and
 * belonging-to-an-inactive-user into ONE `400` with ONE detail string, so that
 * a guesser cannot learn which kind of wrong their guess was.
 * `test_password_reset.py` asserts that at the API boundary. A screen that
 * branched on the reason — or that had two refusal states, or that said
 * "expired" where the API said only "not valid" — would hand the distinction
 * straight back, and no backend test would notice.
 *
 * So there is exactly one `invalid` state, its title is fixed, and its detail
 * is the API's own sentence passed through verbatim.
 *
 * **A password that fails the RULES is a different state.** The link is still
 * good, so it stays on the form rather than sending someone to ask for a new
 * link over a problem that has nothing to do with the link.
 */

import type { FormEvent, JSX } from 'react';
import { Button, Card, CardBody, ErrorState, TextField } from '@avp/design-system';

export type ResetState =
  | { kind: 'form' }
  | { kind: 'working' }
  | { kind: 'done' }
  | { kind: 'rejected'; detail: string }
  | { kind: 'invalid'; detail: string };

export function ResetPasswordView({
  state,
  password,
  onPassword,
  onSubmit,
  onGoToSignIn,
}: {
  state: ResetState;
  password: string;
  onPassword: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onGoToSignIn: () => void;
}): JSX.Element {
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
                <Button variant="primary" onClick={onGoToSignIn}>
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
              <Button variant="secondary" onClick={onGoToSignIn}>
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
            <form onSubmit={onSubmit} className="flex flex-col gap-5">
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
                onChange={(e) => onPassword(e.target.value)}
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
