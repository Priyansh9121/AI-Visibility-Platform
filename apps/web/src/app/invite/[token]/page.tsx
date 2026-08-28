'use client';

/**
 * /invite/{token} — accept a seat invitation. Epic 9.14.
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
 */

import { use, useState, type FormEvent, type JSX } from 'react';
import { api, ApiProblem } from '@/lib/api';
import {
  AcceptInvitationView,
  type InviteState,
} from '@/components/invite/AcceptInvitationView';

export default function AcceptInvitationRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}): JSX.Element {
  const { token } = use(params);
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [state, setState] = useState<InviteState>({ kind: 'form' });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: 'working' });
    try {
      await api.acceptInvitation(token, fullName, password);
      // Signed in already — the response set the cookie. Straight to the
      // dashboard rather than a "you're in" card nobody needs to read.
      window.location.assign('/dashboard');
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 400) {
        // The link itself is no good — unknown, expired, already used,
        // revoked, or the seat is gone. The API does not say which, and
        // neither does this.
        setState({ kind: 'invalid', detail: err.problem.detail });
      } else if (err instanceof ApiProblem && err.status === 422) {
        // The form failed the rules. The link is still fine, so stay on it
        // rather than sending someone to ask for a new invitation over a
        // problem that has nothing to do with the invitation.
        const fields = err.fieldErrors();
        setState({
          kind: 'rejected',
          field: fields.password ? 'password' : fields.fullName ? 'fullName' : null,
          detail: fields.password ?? fields.fullName ?? err.problem.detail,
        });
      } else {
        setState({
          kind: 'rejected',
          field: null,
          detail: 'The request did not complete. Check your connection and try again.',
        });
      }
    }
  }

  return (
    <AcceptInvitationView
      state={state}
      fullName={fullName}
      password={password}
      onFullName={setFullName}
      onPassword={setPassword}
      onSubmit={submit}
    />
  );
}
