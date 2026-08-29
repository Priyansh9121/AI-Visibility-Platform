'use client';

/**
 * /invite/{token} — the screen. Epic 9.14.
 *
 * The THIRD unauthenticated token-bearing route in this product, and it follows
 * the first two rather than inventing a third set of rules. Epic 9.8's
 * `/share/{token}` established them and Epic 9.13's `/reset-password/{token}`
 * confirmed them: the token is the whole credential, every refusal looks the
 * same, and the screen has exactly one thing to say when it does not work.
 *
 * **The token is NOT validated on load**, for the two reasons the reset screen
 * gives verbatim: a probe endpoint would be a free oracle for testing guesses
 * without committing to one, and a link opened from an email should show a
 * form rather than a spinner followed by a form. The single POST that sets the
 * password is also the one that checks the token.
 *
 * WHERE IT DEPARTS FROM THE RESET SCREEN, AND WHY
 * -----------------------------------------------
 * Accepting signs you in; resetting does not. The reset screen sends you back
 * to sign in because you proved control of an inbox, not knowledge of a
 * password, and one deliberate sign-in confirms you hold the new one. Here
 * there is no prior password to have proved anything about — this is a first
 * password on a seat nobody has used — so it lands in the workspace, which is
 * what sign-up already does for the same reason.
 *
 * It also asks for a name. The person who sent the invitation knew an email
 * address; until this form is submitted, the seat list shows that address,
 * because it is the only fact anyone has.
 *
 * WHY THIS IS NOT IN THE PAGE FILE
 * -------------------------------
 * A Next.js App Router page module may only export a default plus a fixed set
 * of framework fields — `next build` rejects anything else by name, which is
 * how the first draft of this was caught. So the pure view lives here, in
 * `components/`, exactly where `DashboardView` and `ReportView` already live,
 * and the route is the thin thing that fetches and holds state.
 */

import { type FormEvent, type JSX } from 'react';
import { Button, Card, CardBody, ErrorState, Reveal, TextField } from '@avp/design-system';

export type InviteState =
  | { kind: 'form' }
  | { kind: 'working' }
  | { kind: 'rejected'; field: 'password' | 'fullName' | null; detail: string }
  | { kind: 'invalid'; detail: string };

/**
 * The screen itself. Pure, so every state is reachable by passing a prop —
 * including the refusal, which no static render could otherwise get to.
 */
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
export function AcceptInvitationView({
  state,
  fullName,
  password,
  onFullName,
  onPassword,
  onSubmit,
  animate = true,
}: {
  state: InviteState;
  fullName: string;
  password: string;
  onFullName: (value: string) => void;
  onPassword: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  /** Turn the arrival off — tests and static renders. */
  animate?: boolean;
}): JSX.Element {
  if (state.kind === 'invalid') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
          <ErrorState
            title="This invitation link is not valid"
            detail={state.detail}
            action={
              <Button variant="secondary" onClick={() => window.location.assign('/')}>
                Go to sign in
              </Button>
            }
          />
        </Reveal>
      </main>
    );
  }

  const busy = state.kind === 'working';
  const rejected = state.kind === 'rejected' ? state : null;

  return (
    <main className="mx-auto max-w-report px-6 py-18">
      <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <form onSubmit={onSubmit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                  Join the workspace
                </h1>
                <p className="text-ui-sm leading-prose text-text-secondary">
                  Choose a password and you are in. The seat is already yours — this
                  link is how you claim it.
                </p>
              </div>

              <TextField
                label="Your name"
                autoComplete="name"
                required
                value={fullName}
                onChange={(e) => onFullName(e.target.value)}
                error={rejected?.field === 'fullName' ? rejected.detail : undefined}
                hint="How you will appear on your agency's seat list."
                disabled={busy}
                maxLength={200}
              />

              <TextField
                label="Password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => onPassword(e.target.value)}
                error={rejected?.field === 'password' ? rejected.detail : undefined}
                hint="At least 12 characters. A phrase you can remember beats a short, clever one."
                disabled={busy}
              />

              {rejected?.field === null && (
                <p role="alert" className="text-ui-sm leading-prose text-danger">
                  {rejected.detail}
                </p>
              )}

              <Button type="submit" variant="primary" fullWidth disabled={busy}>
                {busy ? 'Setting up your seat…' : 'Join'}
              </Button>
            </form>
          </CardBody>
        </Card>

        <p className="text-ui-sm leading-prose text-text-tertiary">
          Links work once and expire seven days after they are sent. If this one has
          run out, ask whoever invited you to send another.
        </p>
      </Reveal>
    </main>
  );
}
