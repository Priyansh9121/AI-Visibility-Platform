'use client';

/**
 * /reset-password/{token} — Epic 9.13.
 *
 * The second unauthenticated token-bearing route in this product, and it
 * follows the first. Epic 9.8's `/share/{token}` established the pattern: the
 * token is the whole credential, every refusal looks the same, and the screen
 * has exactly one thing to say when it does not work.
 *
 * **The token is NOT validated on load.** There is deliberately no "is this
 * link good?" request before the form is shown, for two reasons: a probe
 * endpoint would be a free oracle for testing guesses without committing to
 * one, and a link opened from an email should show a form, not a spinner
 * followed by a form. The single POST that sets the password is also the one
 * that checks the token.
 *
 * The screen is `components/auth/ResetPasswordView` — pure and prop-driven,
 * split out in Epic 9.14 so the refusal state can be rendered statically and
 * asserted over. This file owns the token and the request.
 */

import { use, useState, type FormEvent, type JSX } from 'react';
import { api, ApiProblem } from '@/lib/api';
import {
  ResetPasswordView,
  type ResetState,
} from '@/components/auth/ResetPasswordView';

export default function ResetPasswordRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}): JSX.Element {
  const { token } = use(params);
  const [password, setPassword] = useState('');
  const [state, setState] = useState<ResetState>({ kind: 'form' });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: 'working' });
    try {
      await api.confirmPasswordReset(token, password);
      setState({ kind: 'done' });
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 400) {
        // The link itself is no good — unknown, expired, or already used. The
        // API does not say which, and neither does this.
        setState({ kind: 'invalid', detail: err.problem.detail });
      } else if (err instanceof ApiProblem && err.status === 422) {
        // The password failed the rules. The link is still fine, so stay on
        // the form rather than sending someone back to request a new link for
        // a problem that has nothing to do with the link.
        const fields = err.fieldErrors();
        setState({
          kind: 'rejected',
          detail: fields.newPassword ?? err.problem.detail,
        });
      } else {
        setState({
          kind: 'rejected',
          detail: 'The request did not complete. Check your connection and try again.',
        });
      }
    }
  }

  return (
    <ResetPasswordView
      state={state}
      password={password}
      onPassword={setPassword}
      onSubmit={submit}
      onGoToSignIn={() => window.location.assign('/')}
    />
  );
}
