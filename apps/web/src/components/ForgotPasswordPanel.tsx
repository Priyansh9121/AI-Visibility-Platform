'use client';

/**
 * Ask for a password-reset link — Epic 9.13.
 *
 * **The confirmation is shown whatever happened.** The endpoint answers
 * identically for an address with an account and one without, and this screen
 * must not undo that: a "we couldn't find that email" here would hand back
 * exactly the account-enumeration oracle the backend was built to withhold.
 *
 * So there is one success state, its copy says "if that address has an
 * account", and the submit path has no branch that could say otherwise. The
 * only error this screen can show is the request never completing at all —
 * which is about the network, not about the address.
 */

import { useState, type FormEvent } from 'react';
import { Button, Card, CardBody, ErrorState, Reveal, TextField } from '@avp/design-system';
import { api } from '@/lib/api';

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
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.requestPasswordReset(email);
      // No branch on the response. There is nothing in it to branch on.
      setSent(true);
    } catch {
      setError('The request did not complete. Check your connection and try again.');
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
        <Card elevation="raised">
          <CardBody>
            <div className="flex flex-col gap-3">
              <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
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
              <Button variant="ghost" onClick={() => setSent(false)}>
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
              <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                Reset your password
              </h1>
              <p className="text-ui-sm leading-prose text-text-secondary">
                We will email a link that works once and expires in an hour.
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
              {busy ? 'Sending…' : 'Email me a link'}
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
