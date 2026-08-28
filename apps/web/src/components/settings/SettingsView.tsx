'use client';

/**
 * The settings screen — Epic 9.13, split out in Epic 9.14.
 *
 * Pure: every state is reachable by passing a prop, so it can be rendered to
 * static markup and asserted over. That split is the one `DashboardView` /
 * `dashboard/page.tsx` established in Epic 9.3 and `ReportView` before it, and
 * it is why this screen shipped in 9.13 with zero tests — the state that
 * mattered lived inside an effect the static renderer never runs.
 *
 * It lives in `components/` rather than beside the route because a Next.js App
 * Router page module may only export a default plus a fixed set of framework
 * fields; `next build` rejects anything else by name.
 */

import type { JSX } from 'react';
import {
  Card,
  CardBody,
  ErrorState,
  LoadingState,
  PageSection,
} from '@avp/design-system';
import type { Me, SeatList } from '@avp/shared-types';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';
import { SeatsPanel } from '@/components/settings/SeatsPanel';
import { PasswordChangePanel } from '@/components/settings/PasswordChangePanel';

/** Roles that may invite and remove. A member holds a seat; they do not grant them. */
const MANAGING_ROLES = new Set(['owner', 'admin']);

export type SettingsState =
  | { kind: 'loading' }
  | { kind: 'ready'; me: Me; seats: SeatList | null; seatsError: string | null }
  | { kind: 'error'; title: string; detail: string };

/**
 * The screen itself. Pure — every state is reachable by passing a prop.
 */
export function SettingsView({
  state,
  onChanged,
}: {
  state: SettingsState;
  onChanged: () => void | Promise<void>;
}): JSX.Element {
  const me = state.kind === 'ready' ? state.me : null;

  return (
    <WorkspaceShell current="settings" agencyName={me?.agency.name} seats={me?.seats}>
      {state.kind === 'loading' && <LoadingState message="Loading your settings…" />}

      {state.kind === 'error' && (
        <ErrorState title={state.title} detail={state.detail} />
      )}

      {state.kind === 'ready' && (
        <div className="flex flex-col gap-10">
          <PageSection
            eyebrow="Settings"
            heading="Your agency"
            lead="What the product knows about your account today."
          >
            <Card elevation="seated">
              <CardBody>
                <dl className="flex flex-col gap-4">
                  <Row label="Agency" value={state.me.agency.name} />
                  <Row label="Signed in as" value={state.me.user.email} />
                  <Row label="Role" value={state.me.user.role} />
                  <Row
                    label="Seats"
                    value={`${state.me.seats.used} of ${state.me.seats.limit}`}
                  />
                </dl>
              </CardBody>
            </Card>
          </PageSection>

          <PageSection
            eyebrow="Team"
            heading="Who can sign in"
            lead="Every seat, including invitations nobody has accepted yet — those hold a seat from the moment they are sent."
          >
            {state.seats ? (
              <SeatsPanel
                agencyId={state.me.agency.id}
                seats={state.seats}
                currentUserId={state.me.user.id}
                canManage={MANAGING_ROLES.has(state.me.user.role)}
                onChanged={onChanged}
              />
            ) : (
              <ErrorState
                title="The team list is not available"
                detail={state.seatsError ?? 'The request did not complete.'}
              />
            )}
          </PageSection>

          <PageSection
            eyebrow="Your account"
            heading="Your password"
            lead="Changing it here signs out every other device and leaves this one signed in."
          >
            <PasswordChangePanel />
          </PageSection>

          <PageSection
            eyebrow="Not built yet"
            heading="What is still missing."
            lead="Listed rather than hidden, so it is clear what is absent instead of looking for a control that is not there."
          >
            <ul className="flex flex-col gap-3">
              <Missing text="Changing your agency name, and the logo and colours a report carries — white-labelling is still name-and-slug only, and needs a written policy on which design tokens an agency may override before it is safe to open up." />
              <Missing text="Changing the seat limit. Seats can be invited and removed, but how many an agency gets is a billing question, and north-star.md §5.3 records pricing as undecided — showing a plan picker would imply a decision nobody has made." />
              <Missing text="Billing, plans and usage limits, for the same reason." />
            </ul>
          </PageSection>
        </div>
      )}
    </WorkspaceShell>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{label}</dt>
      <dd className="mt-1 text-ui-md text-text-primary">{value}</dd>
    </div>
  );
}

function Missing({ text }: { text: string }) {
  return (
    <li className="max-w-measure text-ui-base leading-prose text-text-secondary">
      {text}
    </li>
  );
}
