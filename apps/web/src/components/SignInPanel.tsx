'use client';

import { useState, type FormEvent } from 'react';
import { Button, Card, CardBody, ErrorState, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

/**
 * Sign in.
 *
 * **Scope: this signs an existing operator in.** Creating an account is
 * `SignUpPanel`, which Epic 9.12 added and which this panel now links to —
 * until then there was no sign-up anywhere in `apps/web` and this paragraph
 * said so. Password reset and invitation-accept are still not built.
 *
 * `onSwitchToSignUp` is REQUIRED rather than optional, for the same reason
 * `IntakeForm.onFailed` is (Epic 9.11): an optional callback lets a caller
 * silently drop the wiring and strand the user, and the compiler is a cheaper
 * guard than a test this repo has no DOM library to write.
 *
 * ip-safety.md #2: every element from `@avp/design-system`. The width comes
 * from `max-w-form`, a token added in 9.11 to retire the hardcoded
 * `max-w-[26rem]` that used to live here — the only arbitrary Tailwind value
 * this component had, and exactly the off-system styling #2 prohibits.
 */
export function SignInPanel({
  onSignedIn,
  onSwitchToSignUp,
}: {
  onSignedIn: () => void;
  onSwitchToSignUp: () => void;
}) {
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
        No account yet?{' '}
        <Button variant="ghost" size="sm" onClick={onSwitchToSignUp}>
          Create your agency
        </Button>
      </p>
    </div>
  );
}
