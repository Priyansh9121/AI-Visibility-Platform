'use client';

import { useState, type FormEvent } from 'react';
import { Button, Card, CardBody, ErrorState, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

/**
 * Sign in.
 *
 * **Scope, stated plainly: this signs an existing operator in and does nothing
 * else.** There is no sign-up form, no password reset and no invitation accept
 * in `apps/web` — `POST /auth/sign-up` exists on the API and has since Epic 1.3,
 * but nothing in the browser calls it. That is a missing FEATURE rather than a
 * rough edge, and Epic 9.11 deliberately did not build it: a landing page whose
 * call to action leads here is only complete once a stranger can actually get
 * an account, and that deserves its own brief. Named in build-log Epic 9.11.
 *
 * ip-safety.md #2: every element from `@avp/design-system`. The width comes
 * from `max-w-form`, a token added in 9.11 to retire the hardcoded
 * `max-w-[26rem]` that used to live here — the only arbitrary Tailwind value
 * this component had, and exactly the off-system styling #2 prohibits.
 */
export function SignInPanel({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [credentialError, setCredentialError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCredentialError(null);
    try {
      await api.logIn(email, password);
      onSignedIn();
    } catch (err) {
      // A rejected credential belongs ON the field; anything else does not.
      // Every failure used to land under "Password", so an unreachable API
      // read as a wrong password and sent people to reset something that was
      // never wrong.
      if (err instanceof ApiProblem && (err.status === 401 || err.status === 422)) {
        setCredentialError(err.problem.detail);
      } else if (err instanceof ApiProblem) {
        setError(err.problem.detail);
      } else {
        setError('The API did not respond. Check that it is running on port 8000.');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-form flex-col gap-4">
      {error != null && (
        <ErrorState title="Could not sign you in" detail={error} />
      )}

      <Card elevation="raised">
        <CardBody>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                Sign in
              </h1>
              <p className="text-ui-sm leading-prose text-text-secondary">
                Reports and scans are scoped to your agency.
              </p>
            </div>

            <div className="flex flex-col gap-4">
              <TextField
                label="Email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={busy}
              />
              <TextField
                label="Password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                error={credentialError ?? undefined}
                disabled={busy}
              />
            </div>

            <Button type="submit" variant="primary" fullWidth disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardBody>
      </Card>

      <p className="text-ui-sm leading-prose text-text-tertiary">
        No account yet? Access is by conversation while this is in pilot — reach out
        and we will set your agency up.
      </p>
    </div>
  );
}
