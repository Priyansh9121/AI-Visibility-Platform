'use client';

/**
 * Seat management — Epic 9.14.
 *
 * Settings has said "Inviting or removing seats. The invitations table, the
 * seat-limit service and session revocation all exist; the HTTP endpoints do
 * not" since Epic 9.13. They do now, and this is the screen over them.
 *
 * SHAPED ON `CompetitorEditor`, WHICH IS THIS CODEBASE'S ANSWER TO THIS PROBLEM
 * ----------------------------------------------------------------------------
 * "Manage a list against a mutating endpoint" already has a precedent here, and
 * it was followed rather than re-invented: the component takes the current list
 * as a prop, owns its own form state, calls the API itself, maps `ApiProblem`
 * to a sentence, and hands the parent an `onChanged` callback to re-read with.
 * The parent owns fetching; this owns the interaction.
 *
 * Where it deliberately DIFFERS from CompetitorEditor: that endpoint replaces a
 * set wholesale, so its editor builds a draft and saves once. These endpoints
 * are per-row — one invite, one removal — so there is no draft to abandon and
 * every action takes effect immediately. A staged "save your seat changes"
 * button would imply an atomicity the API does not offer.
 *
 * The no-competitor-reference rules: derived from the data model and the goal, and from
 * the precedent above. No competitor's seat or team screen was opened, looked
 * at, or used as a reference at any point.
 *
 * WHAT THIS SCREEN SAYS OUT LOUD
 * ------------------------------
 * Removing a seat ends that person's sessions everywhere, not just their
 * subscription. That is a real consequence of pressing a small button, so the
 * confirmation says it rather than leaving it to be discovered.
 *
 * And, since 2026-09-29, that this service cannot send email yet. Production
 * has no email provider (Resend needs a verified sending domain), so an
 * invitation used to reach nobody while this screen implied it had been sent.
 * Now the API hands the link back in `acceptUrl` when — and only when — it
 * could not email it, and this screen shows it once, with a Copy link button,
 * for the owner to send themselves. Its presence is the API saying email is
 * off; the screen never works that out for itself.
 *
 * A copy is said as well as shown (3.70). The button's new name ("Copied") is
 * not reliably announced by a screen reader, so a copy that worked also says
 * "Link copied." in a status region, visually hidden with the design system's
 * own `avp-visually-hidden`, as the share bar's Copy does (3.52). A copy that
 * is refused says nothing: the link is on screen and selectable, and nothing
 * here claims a copy that did not happen. So one that is refused after a copy
 * that worked takes "Link copied." away and puts the button back to "Copy
 * link", as the share bar's does.
 *
 * WHAT IS SAID WHEN THE LINK ARRIVES, AND WHEN NOTHING WAS SENT (3.85)
 * ---------------------------------------------------------------------
 * The link block mounts with its text, and a screen reader mostly does not read
 * what arrives that way. So the same region says that the link is ready, where
 * it is and how long it works, as a sentence of its own that a copy leaves
 * alone: the region holds two paragraphs by then, so it is not atomic, as the
 * share bar's is (3.52). A sentence, not a move of focus: the Invite button
 * stays where it is, a move is something a sighted person sees (a ring, a
 * selected link), and a screen reader that follows focus can cut off the notice
 * below. "In the field below the form" is where the one read-only field is: it
 * follows the form in the document and sits under it at every width (a browser
 * checked 320 to 1328 px), and a test pins the order, so moving the block means
 * changing the sentence.
 *
 * The new-seat notice says "No email was sent" when the API handed the link
 * back, and only then. The API returns the link only when email is not
 * configured (`accept_url=None if settings.email_configured else invite_url`),
 * so its presence is the API saying that nothing was sent. With email on, the
 * response does not say whether the provider took the message: a failed send is
 * logged on the API's side and the response is the same. So the notice claims
 * the invite and the seat and no email, either way. The re-invite notice with
 * email on is no different: it says the send was tried and how to try again
 * (inviting the same address re-sends, the API's own stated recovery), and
 * never that a link "has been sent". Nor does any notice call the link the only
 * way in: a person with an invited seat who signs in with Google using that
 * address is accepted as well, with no link (`google_oauth.py`).
 *
 * The notice is said in a status region that is on the page from the first
 * render, and gains its sentence, as the link's is. Before, the paragraph was
 * the region and was made together with its text.
 *
 * The link stays until something replaces it. A removal that fails, and an
 * invite the API refuses or that gets no answer, leave it where it was: the API
 * hands it over once and will not again. An invite that hands back a link, or
 * none, replaces it, and a removal that worked clears it.
 */

import { useState, type FormEvent, type JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  CardBody,
  DataTable,
  SelectField,
  TextField,
} from '@avp/design-system';
import type { BadgeTone, Column } from '@avp/design-system';
import type { PendingInvitation, SeatList, User, UserRole } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { formatDay } from '@/lib/dates';
import { RETENTION, counted } from '@/lib/legal/retention';

const ROLE_LABEL: Record<UserRole, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
};

/**
 * What each seat status means, in the words a person would use.
 *
 * `invited` is the one that matters: the seat is consumed and the person has
 * not arrived. Showing it as "Pending" rather than hiding it is the whole
 * reason the count and the roster can disagree with a naive reading.
 */
const STATUS_LABEL: Record<string, string> = {
  active: 'Active',
  invited: 'Invited',
  suspended: 'Suspended',
};

const STATUS_TONE: Record<string, BadgeTone> = {
  active: 'success',
  invited: 'neutral',
  suspended: 'warn',
};

/**
 * Said in a status region once a copy has worked (3.70). The button's new name ("Copied") is not
 * reliably announced, and a copy that is refused says nothing: the link is on screen and
 * selectable, and nothing on the panel claims a copy that did not happen.
 */
const LINK_COPIED_SENTENCE = 'Link copied.';

/**
 * Said in the same status region when an invitation link arrives (3.85), as the share bar says
 * "Link ready" of its own (3.33). The line beside the link is not live, so a screen reader is not
 * told that a link has appeared, where it is, or how long it works, unless this says so. Visually
 * hidden: a sighted reader has that line and the field. The days are `RETENTION`'s, read at render,
 * as that line's are.
 *
 * "In the field below the form" is literal: the link is the one read-only field on the panel, in a
 * block that follows the form in the document and sits under it at every width. If that block is
 * ever moved, this sentence is wrong, and a test that pins the order will say so.
 */
function linkReadySentence(): string {
  return `Invitation link ready, in the field below the form. It works once and expires in ${counted(RETENTION.invitationDays, 'day')}.`;
}

/**
 * What the new-seat notice adds when the API handed the link back (3.85). The API does that only
 * when it could not email the link, so there was no email, and "has been invited" alone would read
 * as if there was one. It does not call the link the only way in, which it is not: signing in with
 * Google as the invited address is accepted too.
 */
const NO_EMAIL_SENTENCE = 'No email was sent. Send them the invitation link yourself.';

/** An invitation link the API handed back because it could not email it. */
interface HandedLink {
  email: string;
  url: string;
  // How many times Copy has worked on this link in a row (3.70): 0 until it has, and again once a
  // copy of it is refused. Above 0 the button says "Copied" and the status region says "Link
  // copied.". The count is that sentence's key, so each copy is a new element in the region, which
  // a screen reader announces, where the same words in the same element are no change.
  copies: number;
}

export interface SeatsPanelProps {
  agencyId: string;
  seats: SeatList;
  /** The signed-in user's id, so their own row cannot offer a remove button. */
  currentUserId: string;
  /** The signed-in user's role. Only an owner can invite someone as an owner; the API refuses an admin. */
  currentUserRole: UserRole;
  /** Whether the caller may invite and remove. Members may not. */
  canManage: boolean;
  /** Re-read the roster after a change. */
  onChanged: () => void | Promise<void>;
  /** Set in tests to render a state the static renderer cannot reach. */
  initialError?: string | null;
}

export function SeatsPanel({
  agencyId,
  seats,
  currentUserId,
  currentUserRole,
  canManage,
  onChanged,
  initialError = null,
}: SeatsPanelProps): JSX.Element {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<UserRole>('member');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  // Only the invite response ever carries it, so this is the one time it can
  // be shown. A removal that worked clears it, and an invite that hands back a link (or none)
  // replaces it; an invite the API refuses leaves it alone (3.85). Re-inviting mints a fresh one.
  const [link, setLink] = useState<HandedLink | null>(null);

  const full = seats.seats.used >= seats.seats.limit;

  async function invite(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    // An earlier link is not cleared here (3.85). The API hands a link over once and never again,
    // so an invite it refuses (409 seat limit or address already registered, 422) or one that gets
    // no answer must not take away a link that still works. The answer below replaces it, with the
    // new link or with none.
    try {
      const result = await api.inviteSeat(agencyId, email.trim(), role);
      const handed = result.acceptUrl ?? null;
      // The API distinguishes a new seat from a re-issued link, so this does
      // too. Reporting "seat taken" when the count did not move would be a
      // small lie the seat counter immediately contradicts. And a link the
      // API handed back was not sent anywhere, so this does not say it was.
      //
      // For a new seat the invite and the seat are certain. A link handed back means no email
      // went, and the notice says so (3.85). With none handed back the API does not say whether
      // the provider took the message (it tries the send and swallows a failure), so the new-seat
      // notice claims no email, and a re-invite says the send was tried and what to do if the
      // email does not arrive: invite the same address again, which re-sends.
      setNotice(
        result.seatConsumed
          ? `${result.user.email} has been invited. They hold a seat from now, before they accept.${
              handed != null ? ` ${NO_EMAIL_SENTENCE}` : ''
            }`
          : handed != null
            ? `${result.user.email} already had a pending invitation. The old link no longer works — send them the new one.`
            : `${result.user.email} already had a pending invitation. The old link no longer works. We tried to email a new one; if it does not arrive, invite the same address again.`,
      );
      setLink(handed != null ? { email: result.user.email, url: handed, copies: 0 } : null);
      setEmail('');
      await onChanged();
    } catch (err) {
      setError(problemText(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(handed: HandedLink) {
    // A copy that worked adds one, and that puts "Link copied." in the status region (3.70). One
    // that is refused sets the count back to 0: the sentence goes and the button says Copy link
    // again, so the panel never claims a copy that did not happen, as the share bar's does (3.52).
    //
    // The answer is applied to the link that is on the page now, and to nothing else: the clipboard
    // can answer after an invite or a removal has replaced or cleared it, and the answer is not
    // about that link. `handed` is the link as it was when Copy was pressed, so it says only which.
    const mark = (worked: boolean) =>
      setLink((now) =>
        now !== null && now.url === handed.url
          ? { ...now, copies: worked ? now.copies + 1 : 0 }
          : now,
      );
    try {
      await navigator.clipboard.writeText(handed.url);
      mark(true);
    } catch {
      // As in `ShareLinkBar`: clipboard access is refused in some browsers and
      // over plain HTTP. The link is on screen and selectable, so it gets
      // copied by hand instead — nothing worth interrupting anyone over. Said
      // nowhere (3.70): "Link copied." is only for a copy that happened, and a
      // refusal takes back what an earlier copy of this link said.
      mark(false);
    }
  }

  async function remove(userId: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.removeSeat(userId);
      // The link goes with a removal that worked, and not before (3.85): the API hands it over
      // once and will not again, so one it refused to remove must not take the link with it.
      setLink(null);
      setConfirming(null);
      setNotice('That seat has been released and their sessions have been signed out.');
      await onChanged();
    } catch (err) {
      setError(problemText(err));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<User>[] = [
    {
      key: 'person',
      header: 'Person',
      render: (u: User) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-ui-base font-medium text-text-primary">
            {/*
              An invited seat's `fullName` IS its email address — the backend
              stores the address rather than guessing a display name from the
              local part. Rendering it twice would look like a bug, so the
              second line is suppressed when they are the same string.
            */}
            {u.fullName}
          </span>
          {u.fullName !== u.email && (
            <span className="font-mono text-ui-xs text-text-tertiary">{u.email}</span>
          )}
        </div>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      render: (u: User) => (
        <span className="text-ui-sm text-text-secondary">{ROLE_LABEL[u.role]}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (u: User) => (
        <Badge tone={STATUS_TONE[u.status] ?? 'neutral'}>
          {STATUS_LABEL[u.status] ?? u.status}
        </Badge>
      ),
    },
    {
      key: 'action',
      header: '',
      // To the far edge — Epic 9.19. Settings became a full-width Working
      // screen in that pass, and a Remove button that had sat next to its row
      // was suddenly stranded in the middle of a very wide table with nothing
      // to its right.
      align: 'end',
      render: (u: User) => {
        if (!canManage) return null;
        if (u.id === currentUserId) {
          // Not a disabled button. The API refuses this with a 409 and the
          // reason is not "you lack permission" — it is that the session
          // making the request is the one that would be revoked. Saying so is
          // more use than a greyed-out control with a tooltip.
          return <span className="text-ui-xs text-text-tertiary">This is you</span>;
        }
        if (confirming === u.id) {
          return (
            <span className="flex flex-wrap items-center gap-2">
              <Button variant="danger" size="sm" disabled={busy} onClick={() => remove(u.id)}>
                {busy ? 'Removing…' : 'Yes, remove'}
              </Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirming(null)}>
                Cancel
              </Button>
            </span>
          );
        }
        return (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirming(u.id)}>
            Remove
          </Button>
        );
      },
    },
  ];

  return (
    <div>
      <div className="flex flex-col gap-6">
        <Card elevation="seated">
          <CardBody>
            <div className="flex flex-wrap items-baseline justify-between gap-4">
              <p className="text-ui-md font-medium text-text-primary">
                {`${seats.seats.used} of ${seats.seats.limit} seats in use`}
              </p>
              <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
                A pending invitation holds a seat before it is accepted, so an agency
                cannot invite past its limit and settle up later.
              </p>
            </div>

            <div className="mt-5">
              <DataTable
                columns={columns}
                rows={seats.members}
                rowKey={(u: User) => u.id}
                isSubject={(u: User) => u.id === currentUserId}
                emptyMessage={
                  <span className="text-ui-base text-text-secondary">
                    No seats. That should not be possible — an agency always has an owner.
                  </span>
                }
              />
            </div>
          </CardBody>
        </Card>

        {confirming !== null && (
          <Card elevation="seated">
            <CardBody>
              <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
                Removing a seat signs that person out everywhere immediately — not at
                their next sign-in, and not when a token expires. Their scans and reports
                stay exactly where they are. You can invite the same address again later.
              </p>
            </CardBody>
          </Card>
        )}

        {canManage && (
          <Card elevation="seated">
            <CardBody>
              <form onSubmit={invite} className="flex flex-col gap-4">
                <div>
                  <h3 className="text-ui-md font-medium text-text-primary">Invite someone</h3>
                  <p className="mt-1 max-w-measure text-ui-sm leading-prose text-text-tertiary">
                    An invitation is a link that works once and expires in seven days.
                    Opening it lets the person choose a password and sign in.
                  </p>
                </div>

                {/*
                  The email field is capped at the form measure rather than left
                  to `flex-1` — Epic 9.19. On the widened screen an unbounded
                  field ran nearly the whole viewport for an address that is
                  never that long, and it no longer matched the password form
                  two sections below, which has used `--avp-form-width` since
                  Epic 9.11.
                */}
                <div className="flex flex-wrap items-end gap-3">
                  <div className="min-w-0 flex-1 basis-full sm:basis-auto sm:max-w-form">
                    <TextField
                      label="Email"
                      type="email"
                      autoComplete="off"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      disabled={busy || full}
                      maxLength={320}
                    />
                  </div>
                  <SelectField
                    label="Role"
                    value={role}
                    disabled={busy || full}
                    onChange={(e) => setRole(e.target.value as UserRole)}
                    options={[
                      { value: 'member', label: 'Member' },
                      { value: 'admin', label: 'Admin' },
                      ...(currentUserRole === 'owner'
                        ? [{ value: 'owner', label: 'Owner' }]
                        : []),
                    ]}
                  />
                  <Button
                    type="submit"
                    variant="primary"
                    disabled={busy || full || email.trim() === ''}
                  >
                    {busy ? 'Inviting…' : 'Invite'}
                  </Button>
                </div>

                {full && (
                  // Said, not merely disabled. A greyed-out button with no
                  // explanation is how a limit becomes a support ticket. There is
                  // no upgrade link because there is no billing surface to link
                  // to — north-star.md §5.3's pricing is still undecided, and a
                  // plan picker would imply a decision nobody has made.
                  <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
                    Every seat is in use. Remove one to invite someone else — changing the
                    seat limit is not something this screen can do yet.
                  </p>
                )}
              </form>

              {link != null && (
                // Outside the form, so nothing here can submit it.
                <div className="mt-5 flex flex-col gap-3 border-t border-line-hairline pt-5">
                  <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
                    Email isn&rsquo;t set up yet — send this link yourself. It works once and
                    expires in {counted(RETENTION.invitationDays, 'day')}.
                  </p>
                  {/*
                    A read-only field, as `ShareLinkBar` does it: selectable
                    where the clipboard is refused, with its label bound.
                  */}
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="min-w-0 flex-1 basis-full sm:basis-auto">
                      <TextField
                        label={`Invitation link for ${link.email}`}
                        readOnly
                        value={link.url}
                        onFocus={(e) => e.currentTarget.select()}
                      />
                    </div>
                    <Button variant="secondary" onClick={() => void copy(link)}>
                      {link.copies > 0 ? 'Copied' : 'Copy link'}
                    </Button>
                  </div>
                </div>
              )}

              {/*
                Where a copy is said (3.70) and where the link's arrival is (3.85), because the
                button's new name, and a block that mounts with its text, are not reliably read out.
                The region is on the page before there is a link and gains its sentences when there
                is one, as the share bar's does (3.52): a message made together with its region is
                often never read. It is not inside the block above, which is a flex column with a
                gap: a region with no height of its own would still add one gap under the field.

                It holds two paragraphs once a link has been copied, so it is not atomic: what a
                screen reader reads is what is added, and "Link copied." does not bring the ready
                sentence with it. The ready sentence is made when the link is, and its key is the
                link, so a different link's sentence is a new element and is read, where the same
                words in the same element would be no change to the region. An earlier link stays
                until another replaces it, so an invite the API refuses, a copy, and a removal that
                fails all leave the sentence alone.

                The copied sentence's key is the number of copies, so a second copy takes the
                sentence out and puts a new one in. The same words in the same element would be no
                change to the region, and nothing would be read. A copy that is refused takes the
                sentence away and adds nothing, so it says nothing.
              */}
              <div role="status" aria-atomic="false" data-testid="invite-link-status">
                {link !== null && (
                  <>
                    <p key={link.url} className="avp-visually-hidden">
                      {linkReadySentence()}
                    </p>
                    {link.copies > 0 && (
                      <p key={link.copies} className="avp-visually-hidden">
                        {LINK_COPIED_SENTENCE}
                      </p>
                    )}
                  </>
                )}
              </div>
            </CardBody>
          </Card>
        )}

        {seats.invitations.length > 0 && (
          <Card elevation="seated">
            <CardBody>
              <h3 className="text-ui-md font-medium text-text-primary">Invitations outstanding</h3>
              <ul className="mt-3 flex flex-col gap-2">
                {seats.invitations.map((invite: PendingInvitation) => (
                  <li
                    key={invite.id}
                    className="flex flex-wrap items-baseline justify-between gap-3 text-ui-sm"
                  >
                    <span className="font-mono text-text-primary">{invite.email}</span>
                    <span className="text-text-tertiary">
                      {`${ROLE_LABEL[invite.role]} · link expires ${formatDay(invite.expiresAt)}`}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 max-w-measure text-ui-sm leading-prose text-text-tertiary">
                Inviting the same address again makes a fresh link and retires this one. It
                does not take a second seat.
              </p>
            </CardBody>
          </Card>
        )}

        {error != null && (
          <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
            {error}
          </p>
        )}
      </div>

      {/*
        Where the notice is said (3.85). The paragraph used to be the live region and was made
        together with its text, which a screen reader mostly does not read. Now the region is on
        the page from the first render and gains the sentence. Each action empties it when it
        starts and fills it when it ends, so the same words twice are a new element each time.

        Outside the column above, which has a gap: an empty region in it would still add one gap
        under the last card. The sentence brings that space itself, as `mt-6`, the column's own
        `gap-6`. For a person who can manage seats only: nobody else can invite or remove, so
        nothing is said to them.
      */}
      {canManage && (
        <div role="status" data-testid="seat-notice">
          {notice != null && (
            <p className="mt-6 max-w-measure text-ui-sm leading-prose text-text-secondary">
              {notice}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The API's own sentence, or a plain fallback.
 *
 * `seat-limit-reached` carries `seatsUsed` and `seatLimit` and its `detail`
 * already names both, so there is nothing to add — re-authoring it here would
 * be a second copy of a message the backend chose deliberately.
 */
function problemText(err: unknown): string {
  if (err instanceof ApiProblem) {
    return err.problem.detail || err.problem.title;
  }
  return 'The change did not go through. Check your connection and try again.';
}
