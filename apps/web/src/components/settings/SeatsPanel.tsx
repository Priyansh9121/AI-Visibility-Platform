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

export interface SeatsPanelProps {
  agencyId: string;
  seats: SeatList;
  /** The signed-in user's id, so their own row cannot offer a remove button. */
  currentUserId: string;
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

  const full = seats.seats.used >= seats.seats.limit;

  async function invite(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.inviteSeat(agencyId, email.trim(), role);
      // The API distinguishes a new seat from a re-issued link, so this does
      // too. Reporting "seat taken" when the count did not move would be a
      // small lie the seat counter immediately contradicts.
      setNotice(
        result.seatConsumed
          ? `${result.user.email} has been invited. They hold a seat from now, before they accept.`
          : `${result.user.email} already had a pending invitation. A new link has been sent and the old one no longer works.`,
      );
      setEmail('');
      await onChanged();
    } catch (err) {
      setError(problemText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(userId: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.removeSeat(userId);
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
                  They get a link that works once and expires in seven days. Opening it
                  lets them choose a password and sign in.
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
                    { value: 'owner', label: 'Owner' },
                  ]}
                />
                <Button
                  type="submit"
                  variant="primary"
                  disabled={busy || full || email.trim() === ''}
                >
                  {busy ? 'Sending…' : 'Send invitation'}
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
              Inviting the same address again sends a fresh link and retires this one. It
              does not take a second seat.
            </p>
          </CardBody>
        </Card>
      )}

      {notice != null && (
        <p role="status" className="max-w-measure text-ui-sm leading-prose text-text-secondary">
          {notice}
        </p>
      )}

      {error != null && (
        <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
          {error}
        </p>
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
