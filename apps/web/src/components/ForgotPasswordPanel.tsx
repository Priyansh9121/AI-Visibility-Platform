'use client';

/**
 * Ask for a password-reset link — Epic 9.13.
 *
 * **The confirmation is shown whatever happened.** The endpoint answers
 * identically for an address with an account and one without, and this screen
 * must not undo that: a "we couldn't find that email" here would hand back
 * exactly the account-enumeration oracle the backend was built to withhold.
 *
 * So the success copy says "if that address has an account", and the submit
 * path has no branch that could say otherwise. The only error this screen can
 * show is the request never completing at all — which is about the network,
 * not about the address.
 *
 * WHEN THIS SERVICE CANNOT SEND EMAIL (2026-09-29)
 * ------------------------------------------------
 * Production has no email provider yet — Resend needs a verified sending
 * domain — and this screen used to say "a reset link is on its way" anyway.
 * The response now carries `emailConfigured`, and with it false the second
 * state says what is true: no email was sent, sign in with Google, or ask the
 * operator. That is the one branch on the response, and it is on a fact about
 * the DEPLOYMENT: the API returns the same body for every address in both
 * modes, so it still tells nobody whether an account exists. The form itself
 * promises no email, because until the response arrives this screen does not
 * know whether one can be sent — and the web must not guess.
 */

import { useState, type FormEvent } from 'react';
import {
  Button,
  Card,
  CardBody,
  ErrorState,
  GoogleSignInButton,
  Reveal,
  TextField,
} from '@avp/design-system';
import { api, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import { OPERATOR } from '@/lib/legal/operator';

/** What the API said it did: emailed a link, or could not email at all. */
type Sent = 'emailed' | 'email-off';

/**
 * Epic 9.16: the card arrives rather than being already there.
 *
 * A single `Reveal` on the card itself — no stagger, because there is one
 * object here and a sequence needs at least two. These screens were flagged as
 * bare rather than as static, and a plain fade-and-rise answers that without
 * anything structural changing.
 *
 * The `Reveal` REPLACES the outer wrapper rather than nesting inside it, so no
 * box is added and the layout classes stay exactly where they were.
 */
export function ForgotPasswordPanel({
  onBackToSignIn,
  animate = true,
}: {
  onBackToSignIn: () => void;
  /** Turn the arrival off — tests and static renders. */
  animate?: boolean;
}) {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState<Sent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestPasswordReset(email);
      // The one branch, and it is on the deployment, never on the address:
      // every address gets the same body, which says whether email is on.
      setSent(result.emailConfigured ? 'emailed' : 'email-off');
    } catch {
      setError('The request did not complete. Check your connection and try again.');
    } finally {
      setBusy(false);
    }
  }

  if (sent === 'email-off') {
    return (
      <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <div className="flex flex-col gap-3">
              <h1 className="font-display text-ed-xs leading-display text-text-primary">
                No email was sent
              </h1>
              <p className="text-ui-base leading-prose text-text-secondary">
                Password reset by email isn&rsquo;t available yet. Sign in with Google if your
                account uses this address, or email{' '}
                <a
                  href={`mailto:${OPERATOR.email}`}
                  className="text-text-primary underline decoration-line-strong underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-600 hover:decoration-beacon-400"
                >
                  {OPERATOR.email}
                </a>{' '}
                and we&rsquo;ll reset it by hand.
              </p>
            </div>
            {/*
              Google first: on the same address its callback links the
              password account and signs the person in, which is the whole
              reset, done by Google instead of by a link.
            */}
            <div className="mt-5 flex flex-col gap-2">
              <GoogleSignInButton href={GOOGLE_SIGN_IN_URL} label="Sign in with Google" fullWidth />
              <Button variant="secondary" fullWidth onClick={onBackToSignIn}>
                Back to sign in
              </Button>
            </div>
          </CardBody>
        </Card>
      </Reveal>
    );
  }

  if (sent === 'emailed') {
    return (
      <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <div className="flex flex-col gap-3">
              <h1 className="font-display text-ed-xs leading-display text-text-primary">
                Check your email
              </h1>
              <p className="text-ui-base leading-prose text-text-secondary">
                If that address has an account, a reset link is on its way. The link
                works once and expires in an hour.
              </p>
              <p className="text-ui-sm leading-prose text-text-tertiary">
                Nothing arrived? It may be the wrong address, or the message may be
                in spam. You can ask again — a new link retires the old one.
              </p>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <Button variant="secondary" onClick={onBackToSignIn}>
                Back to sign in
              </Button>
              <Button variant="ghost" onClick={() => setSent(null)}>
                Use a different address
              </Button>
            </div>
          </CardBody>
        </Card>
      </Reveal>
    );
  }

  return (
    <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
      {error != null && <ErrorState title="Could not send the request" detail={error} />}

      <Card elevation="raised">
        <CardBody>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h1 className="font-display text-ed-xs leading-display text-text-primary">
                Reset your password
              </h1>
              <p className="text-ui-sm leading-prose text-text-secondary">
                Enter the address you sign in with.
              </p>
            </div>

            <TextField
              label="Email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={busy}
            />

            <Button type="submit" variant="primary" fullWidth disabled={busy}>
              {busy ? 'Requesting a reset…' : 'Continue'}
            </Button>
          </form>
        </CardBody>
      </Card>

      <p className="text-ui-sm leading-prose text-text-tertiary">
        Remembered it?{' '}
        <Button variant="ghost" size="sm" onClick={onBackToSignIn}>
          Sign in
        </Button>
      </p>
    </Reveal>
  );
}
