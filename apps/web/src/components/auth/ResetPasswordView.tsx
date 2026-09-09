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
import { Button, Card, CardBody, ErrorState, Reveal, TextField } from '@avp/design-system';

export type ResetState =
  | { kind: 'form' }
  | { kind: 'working' }
  | { kind: 'done' }
  | { kind: 'rejected'; detail: string }
  | { kind: 'invalid'; detail: string };

/**
 * Epic 9.16: the card arrives rather than being already there.
 *
 * The same single `Reveal` the other four auth surfaces got — no stagger,
 * because there is one object here and a sequence needs at least two.
 *
 * The `Reveal` REPLACES the card column rather than nesting inside it, so no
 * box is added and the layout classes stay on the element they were already
 * on. It does NOT replace the `<main>`: that is a landmark element a screen
 * reader navigates by, and `Reveal` renders none of the elements it could
 * legitimately be.
 *
 * **`animate` threads through this VIEW, not the route.** This screen follows
 * the pure-view-plus-fetching-route split (`SettingsView`, `DashboardView`,
 * `ReportView`) rather than the self-contained-panel shape `SignInPanel` uses,
 * so the wrapper being replaced lives here and the route passes nothing —
 * taking the `true` default, which is what a browser should get.
 *
 * Every branch is wrapped, not only the form. A refusal is a card arriving too,
 * and `ForgotPasswordPanel` already set that precedent with its two states.
 */
export function ResetPasswordView({
  state,
  password,
  onPassword,
  onSubmit,
  onGoToSignIn,
  animate = true,
}: {
  state: ResetState;
  password: string;
  onPassword: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onGoToSignIn: () => void;
  /** Turn the arrival off — tests and static renders. */
  animate?: boolean;
}): JSX.Element {
  if (state.kind === 'done') {
    return (
      <main className="mx-auto max-w-page px-6 py-18">
        <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
          <Card elevation="raised">
            <CardBody>
              <h1 className="font-display text-ed-xs leading-display text-text-primary">
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
        </Reveal>
      </main>
    );
  }

  if (state.kind === 'invalid') {
    return (
      <main className="mx-auto max-w-page px-6 py-18">
        <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
          <ErrorState
            title="This reset link is not valid"
            detail={state.detail}
            action={
              <Button variant="secondary" onClick={onGoToSignIn}>
                Ask for a new link
              </Button>
            }
          />
        </Reveal>
      </main>
    );
  }

  const busy = state.kind === 'working';

  return (
    <main className="mx-auto max-w-page px-6 py-18">
      <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <form onSubmit={onSubmit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <h1 className="font-display text-ed-xs leading-display text-text-primary">
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
      </Reveal>
    </main>
  );
}
