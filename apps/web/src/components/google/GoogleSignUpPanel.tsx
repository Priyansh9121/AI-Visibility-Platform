'use client';

import { useEffect, useRef, useState, type FormEvent, type JSX } from 'react';
import { Button, Card, CardBody, ErrorState, Reveal, TextField } from '@avp/design-system';
import type { GooglePending } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { apiWasAway, API_AWAY_SENTENCE } from '@/lib/apiAway';

export type GoogleSignUpState =
  | { kind: 'loading' }
  | { kind: 'ask'; pending: GooglePending }
  | { kind: 'no-code' }
  | { kind: 'expired' }
  /** The API did not answer (network error, 5xx). The ticket may be fine. */
  | { kind: 'unreachable' };

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
    title: 'That Google sign-in can no longer be used',
    detail: 'It works once, for ten minutes, in the browser that started it. Start again from the sign-in page.',
  },
} as const;

const UNREACHABLE = {
  title: 'We could not reach the server',
  detail: 'Check your connection and try again. The sign-in link works for ten minutes.',
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
 * the API refusing the ticket (unknown, already used, past its ten minutes, or
 * opened in a browser that did not start the sign-in).
 * An API that does not answer, or answers 5xx, is not a refusal and is its own
 * state, `unreachable`, whose way out is Try again. Which one applies is the
 * route's call.
 */
export function GoogleSignUpPanel({
  state,
  ticket,
  onSignedUp,
  onRetry,
  animate = true,
}: {
  state: GoogleSignUpState;
  ticket: string;
  onSignedUp: () => void;
  /** Re-runs the ticket check. The check only peeks; it does not spend the ticket. */
  onRetry?: () => void;
  animate?: boolean;
}): JSX.Element {
  const [agencyName, setAgencyName] = useState('');
  const [fullName, setFullName] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // FOCUS, THROUGH "TRY AGAIN". Pressing it replaces the card that holds the
  // button with the "Checking…" line, and that with whatever the peek finds, so
  // without this every step drops focus on <body> and a keyboard or screen-reader
  // user starts again from the top of the page. Each step hands it to what is
  // there: the line, then the form's heading; the buttons of the cards that can
  // follow take it as they mount (`autoFocus`). Only after a retry: a first
  // visit has nothing to carry and takes no focus.
  const [retried, setRetried] = useState(false);
  const checking = useRef<HTMLParagraphElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!retried) return;
    if (state.kind === 'loading') checking.current?.focus();
    else if (state.kind === 'ask') heading.current?.focus();
  }, [retried, state.kind]);

  if (state.kind === 'loading') {
    return (
      <p
        ref={checking}
        tabIndex={-1}
        className="mx-auto max-w-form text-ui-sm text-text-secondary"
        role="status"
      >
        Checking your Google sign-in…
      </p>
    );
  }

  if (state.kind === 'unreachable') {
    return (
      <div className="mx-auto max-w-form">
        <ErrorState
          title={UNREACHABLE.title}
          detail={UNREACHABLE.detail}
          action={
            <Button
              variant="secondary"
              autoFocus={retried}
              onClick={() => {
                setRetried(true);
                onRetry?.();
              }}
            >
              Try again
            </Button>
          }
        />
      </div>
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
            <Button
              variant="secondary"
              autoFocus={retried}
              onClick={() => window.location.assign('/')}
            >
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
      } else if (err instanceof ApiProblem && !apiWasAway(err)) {
        // The API refused, and its own sentence says why.
        setError(err.problem.detail);
      } else {
        // No answer, or a 5xx (a proxy's 502 reaches here as an ApiProblem
        // carrying that status): nothing the API said is about this form.
        setError(API_AWAY_SENTENCE);
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
              <h1
                ref={heading}
                tabIndex={-1}
                className="font-display text-ed-xs leading-display text-text-primary"
              >
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
