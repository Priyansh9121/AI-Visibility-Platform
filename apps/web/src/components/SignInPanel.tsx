'use client';

import { useState, type FormEvent } from 'react';
import { Button, Card, CardBody, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

/**
 * Minimal sign-in.
 *
 * Deliberately minimal: Epic 2's scope is the intake form, and this exists only
 * because intake requires an authenticated session. The real account and seat
 * management screens are a later epic.
 */
export function SignInPanel({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.logIn(email, password);
      onSignedIn();
    } catch (err) {
      setError(
        err instanceof ApiProblem ? err.problem.detail : 'Could not reach the API.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card elevation="raised" className="mx-auto max-w-[26rem]">
      <CardBody>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <h1 className="font-editorial text-ed-2xs text-text-primary">Sign in</h1>
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={error ?? undefined}
          />
          <Button type="submit" variant="primary" fullWidth disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}
