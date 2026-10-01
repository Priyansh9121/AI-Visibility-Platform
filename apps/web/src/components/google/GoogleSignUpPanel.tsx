'use client';

import { useState, type FormEvent, type JSX } from 'react';
import { Button, Card, CardBody, ErrorState, Reveal, TextField } from '@avp/design-system';
import type { GooglePending } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';

export type GoogleSignUpState =
  | { kind: 'loading' }
  | { kind: 'ask'; pending: GooglePending }
  | { kind: 'no-code' }
  | { kind: 'expired' };

/**
 * What each refusal says. Both are the same card with the same way out.
 *
 * `no-code` is built like /invite's and /reset-password's refusal ("This
 * invitation link is not valid", "This page no longer has the link's code (it
 * is removed from the address bar once read)."): the same title shape and the
 * same first sentence, then the action that fits a code that came from Google
 * and not from an email, which is to start again from the sign-in page. The
 * title does not say the sign-in was used: the page cannot know that (the peek
 * spends nothing, and a typed address never had a ticket).
 */
const REFUSALS = {
  'no-code': {
    title: 'This sign-in link is not valid',
    detail:
      "This page no longer has the link's code (it is removed from the address bar once read). Start again from the sign-in page.",
  },
  expired: {
    title: 'That Google sign-in has expired',
    detail: 'It works for ten minutes and once. Start again from the sign-in page.',
  },
} as const;

/**
 * The Google half of "Create your agency" — Epic 20.
 *
 * Google has verified who this is; what it cannot say is what the agency is
 * called or how the person writes their own name. So this asks the two
 * things `SignUpPanel` asks that Google cannot answer, shows the address the
 * account will be under, and asks for no password — that is the point.
 *
 * Pure and prop-driven like `SignUpPanel`: the route owns the ticket and the
 * fetch, this owns the form. Two states are refusals, and the way out of both
 * is the front door. `no-code` is a page with no ticket to ask about, typically
 * a reload (the ticket is taken out of the address bar once read). `expired` is
 * the API refusing the ticket (unknown, already used, or past its ten minutes)
 * or not answering. Which one applies is the route's call.
 */
export function GoogleSignUpPanel({
  state,
  ticket,
  onSignedUp,
  animate = true,
}: {
  state: GoogleSignUpState;
  ticket: string;
  onSignedUp: () => void;
  animate?: boolean;
}): JSX.Element {
  const [agencyName, setAgencyName] = useState('');
  const [fullName, setFullName] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (state.kind === 'loading') {
    return (
      <p className="mx-auto max-w-form text-ui-sm text-text-tertiary" role="status">
        Checking your Google sign-in…
      </p>
    );
  }

  if (state.kind === 'no-code' || state.kind === 'expired') {
    const { title, detail } = REFUSALS[state.kind];
    return (
      <div className="mx-auto max-w-form">
        <ErrorState
          title={title}
          detail={detail}
          action={
            <Button variant="secondary" onClick={() => window.location.assign('/')}>
              Back to sign in
            </Button>
          }
        />
      </div>
    );
  }

  const name = fullName ?? state.pending.suggestedName;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await api.completeGoogleSignUp({ ticket, agencyName, fullName: name });
      onSignedUp();
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 422) {
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
    <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
      {error != null && <ErrorState title="Could not create your agency" detail={error} />}

      <Card elevation="raised">
        <CardBody>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <h1 className="font-display text-ed-xs leading-display text-text-primary">
                Name your agency
              </h1>
              <p className="text-ui-sm leading-prose text-text-secondary">
                Google confirmed <span className="font-medium text-text-primary">{state.pending.email}</span>.
                You are the first seat, and the owner. No password is needed; you
                will sign in with Google.
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
                value={name}
                onChange={(e) => setFullName(e.target.value)}
                error={fieldErrors.fullName}
                disabled={busy}
              />
            </div>

            <Button type="submit" variant="primary" fullWidth disabled={busy}>
              {busy ? 'Creating your agency…' : 'Create agency'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </Reveal>
  );
}
