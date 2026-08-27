'use client';

import { useState, type FormEvent } from 'react';
import { Button, Card, CardBody, ErrorState, TextField } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';

/**
 * Create an agency — Epic 9.12.
 *
 * Closes the first of the two dead ends Epic 9.11 named: `POST /auth/sign-up`
 * has existed since Epic 1.3 and nothing in `apps/web` had ever called it, so
 * the landing page's call to action led to a form only existing users could
 * use.
 *
 * WHY A SEPARATE COMPONENT RATHER THAN A MODE OF SignInPanel
 * -----------------------------------------------------------
 * Four fields against two, a different password rule, a different error map and
 * different copy. A shared component with a `mode` prop would branch in about
 * six places and grow a seventh with every change. The two screens share their
 * VISUAL pattern by reusing the same primitives — `Card`, `TextField`,
 * `ErrorState`, the editorial heading — which is what the design system is for.
 *
 * WHICH ERRORS GO WHERE, decided before the submit handler was written
 * --------------------------------------------------------------------
 * * `409 email-already-registered` -> the EMAIL field. It is a fact about one
 *   input, and it has an obvious fix that belongs next to the box being fixed.
 *   Same precedent SignInPanel set in 9.11 by moving credential failures off
 *   the page and onto the field.
 * * `422 validation-failed` -> whichever field the API names, via
 *   `ApiProblem.fieldErrors()`, which already parses the RFC 9457 `errors`
 *   array. The server owns the password rule (12-256 characters, at least 5
 *   distinct); restating it in the browser would create a second definition
 *   that drifts. The hint below the field describes it; the server enforces it.
 * * anything else — unreachable API, a 500 — is environmental, belongs to no
 *   field, and renders as an `ErrorState` above the card.
 *
 * ip-safety.md #2: every element from `@avp/design-system`; width from the
 * `max-w-form` token added in 9.11.
 */
export function SignUpPanel({
  onSignedUp,
  onSwitchToSignIn,
}: {
  onSignedUp: () => void;
  onSwitchToSignIn: () => void;
}) {
  const [agencyName, setAgencyName] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await api.signUp({ agencyName, fullName, email, password });
      // 201 sets the session cookie, so there is no second sign-in step.
      onSignedUp();
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 409) {
        setFieldErrors({ email: err.problem.detail });
      } else if (err instanceof ApiProblem && err.status === 422) {
        const named = err.fieldErrors();
        if (Object.keys(named).length > 0) setFieldErrors(named);
        else setError(err.problem.detail);
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
      {error != null && <ErrorState title="Could not create your agency" detail={error} />}

      <Card elevation="raised">
        <CardBody>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h1 className="font-editorial text-ed-xs leading-display text-text-primary">
                Create your agency
              </h1>
              <p className="text-ui-sm leading-prose text-text-secondary">
                You are the first seat, and the owner. Clients and scans you add
                belong to this agency and to nobody else.
              </p>
            </div>

            <div className="flex flex-col gap-4">
              <TextField
                label="Agency name"
                autoComplete="organization"
                required
                value={agencyName}
                onChange={(e) => setAgencyName(e.target.value)}
                error={fieldErrors.agencyName}
                hint="This is the name that appears on every report you send."
                disabled={busy}
              />
              <TextField
                label="Your name"
                autoComplete="name"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                error={fieldErrors.fullName}
                disabled={busy}
              />
              <TextField
                label="Email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                error={fieldErrors.email}
                disabled={busy}
              />
              <TextField
                label="Password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                error={fieldErrors.password}
                // Describes the server's rule rather than re-implementing it.
                // api-contracts.md: 12-256 characters, NIST SP 800-63B, no
                // composition requirements — deliberately no "one capital and
                // a symbol", which pushes people toward predictable swaps.
                hint="At least 12 characters. A phrase you can remember beats a short, clever one."
                disabled={busy}
              />
            </div>

            <Button type="submit" variant="primary" fullWidth disabled={busy}>
              {busy ? 'Creating your agency…' : 'Create agency'}
            </Button>
          </form>
        </CardBody>
      </Card>

      <p className="text-ui-sm leading-prose text-text-tertiary">
        Already have an account?{' '}
        <Button variant="ghost" size="sm" onClick={onSwitchToSignIn}>
          Sign in
        </Button>
      </p>
    </div>
  );
}
