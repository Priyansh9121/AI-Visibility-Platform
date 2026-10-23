'use client';

import { useState, type JSX, type Ref } from 'react';
import { Button, Card, CardBody, Reveal } from '@avp/design-system';
import { api } from '@/lib/api';
import { apiWasAway } from '@/lib/apiAway';

/**
 * "You've been invited to join {agency}. Join, or start your own agency?" — launch checklist 3.126.
 *
 * Where Google's callback sends an address that has an invitation waiting. The callback used to
 * accept that invitation by itself, so whoever invited somebody else's address took that person's
 * first Google sign-in into their own agency; now it writes nothing and this asks.
 *
 * The sentence and the two labels are the founder's, word for word, and there is nothing else on
 * the view: no other copy, no explanation. Every other thing a person can meet here is a sentence
 * or a state the page already had, and the route shows it: a ticket the API refuses, or a seat or
 * invitation that is no longer good, is the "can no longer be used" card (`onFailed(false)`); an
 * API that does not answer is the "could not reach the server" card with its Try again
 * (`onFailed(true)`). So this component never writes a sentence about a failure.
 *
 * "Join" is the primary button: it is what the invitation was for. Both are disabled while a press
 * is in flight, with no change of label (a "Joining…" would be a sentence nobody gave), so a
 * second press cannot spend the ticket twice. After a success the view is replaced or the page
 * navigates away, so the buttons stay disabled; after a failure the route replaces the view.
 *
 * Pure like its panel: the route owns what each outcome does. The heading takes a ref and
 * `tabIndex={-1}` so the panel can hand focus to it when the view arrives after a "Try again" or a
 * "Start your own agency" (the other states' convention); a first visit takes none.
 */
export function GoogleInvitationChoice({
  agency,
  ticket,
  onJoined,
  onOwnAgency,
  onFailed,
  headingRef,
  animate = true,
}: {
  /** The inviting agency's name, from the peek. */
  agency: string;
  ticket: string;
  /** "Join" worked: the seat is active and the session cookie is set. */
  onJoined: () => void;
  /** "Start your own agency" worked: the ticket for the agency-name step. */
  onOwnAgency: (ticket: string) => void;
  /** Either answer failed. `away` is true when the API did not answer (no reply, or a 5xx). */
  onFailed: (away: boolean) => void;
  headingRef?: Ref<HTMLHeadingElement>;
  animate?: boolean;
}): JSX.Element {
  const [busy, setBusy] = useState(false);

  async function join() {
    if (busy) return;
    setBusy(true);
    try {
      await api.joinWithGoogle(ticket);
      onJoined();
    } catch (err) {
      onFailed(apiWasAway(err));
      setBusy(false);
    }
  }

  async function startOwn() {
    if (busy) return;
    setBusy(true);
    try {
      const next = await api.declineGoogleInvitation(ticket);
      onOwnAgency(next.ticket);
    } catch (err) {
      onFailed(apiWasAway(err));
      setBusy(false);
    }
  }

  return (
    <Reveal animate={animate} className="mx-auto flex max-w-form flex-col gap-4">
      <Card elevation="raised">
        <CardBody>
          <div className="flex flex-col gap-5">
            {/* The agency's name is the inviter's to choose (2 to 200 characters, spaces or none), so
                the heading may break anywhere: a name like a domain would otherwise run past the
                card and give the page a sideways scroll (WCAG 1.4.10). */}
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="font-display text-ed-xs leading-display text-text-primary break-words"
            >
              {`You've been invited to join ${agency}. Join, or start your own agency?`}
            </h1>
            <div className="flex flex-wrap gap-3">
              <Button variant="primary" disabled={busy} onClick={() => void join()}>
                Join
              </Button>
              <Button variant="secondary" disabled={busy} onClick={() => void startOwn()}>
                Start your own agency
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
    </Reveal>
  );
}
