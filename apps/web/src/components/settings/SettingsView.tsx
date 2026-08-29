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
import type { BillingStatus, Me, SeatList } from '@avp/shared-types';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';
import { SeatsPanel } from '@/components/settings/SeatsPanel';
import { PasswordChangePanel } from '@/components/settings/PasswordChangePanel';
import { BillingPanel } from '@/components/settings/BillingPanel';

/** Roles that may invite and remove. A member holds a seat; they do not grant them. */
const MANAGING_ROLES = new Set(['owner', 'admin']);

export type SettingsState =
  | { kind: 'loading' }
  | {
      kind: 'ready';
      me: Me;
      seats: SeatList | null;
      seatsError: string | null;
      /** Null when the caller may not read billing — a member gets a 403. */
      billing: BillingStatus | null;
      billingError: string | null;
    }
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
    /*
     * WIDE — Epic 9.19, and it is a correction rather than a preference.
     *
     * design-direction.md section 0 splits every screen into Working and
     * Presenting, and the report measure belongs to the second: it is the
     * width a document is read at, and it is why `--avp-report-width` exists.
     * Settings is a Working screen by that document's own definition — an
     * operator's account and seat roster, never printed, never handed to a
     * prospect — and it was the only one of the three still rendering at the
     * document measure, stranding a third of the viewport beside a seat table
     * that wanted the room. The dashboard and clients have been `wide` since
     * Epic 9.13; this makes the three consistent.
     *
     * Nothing inside grows unbounded as a result. Every panel already caps its
     * own prose at `--avp-measure` and its forms at `--avp-form-width`, so what
     * the extra width buys is table columns and the agency grid below, not
     * 90rem-long lines of text.
     */
    <WorkspaceShell current="settings" agencyName={me?.agency.name} seats={me?.seats} wide>
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
                {/*
                  Four one-line facts. Stacked, they were a tall thin column
                  with the whole width of the card empty beside them; a grid
                  reads in one pass, which is what the Working context asks
                  for. The columns step 1 -> 2 -> 4 so the labels never end up
                  further from their values than they are tall.
                */}
                <dl className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-4">
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
            eyebrow="Billing"
            heading="What you pay"
            lead="One plan, $29 a month, 3 seats. Nothing in the product is behind it — subscribing is how you pay for this, not how you unlock it."
          >
            <BillingPanel
              agencyId={state.me.agency.id}
              billing={state.billing}
              billingError={state.billingError}
            />
          </PageSection>

          <PageSection
            eyebrow="Not built yet"
            heading="What is still missing."
            lead="Listed rather than hidden, so it is clear what is absent instead of looking for a control that is not there."
          >
            {/*
              Numbered rather than bulleted, in the report's fix-list idiom —
              a leading-zero mono ordinal and a hairline between rows. Three
              paragraphs of tertiary prose read as small print that has been
              left over; the same three as a numbered ledger read as a list
              somebody keeps. It is the same device `.avp-fixlist__item` uses
              on the report, which is the closest thing this product has to a
              house treatment for "an ordered list of things to change".
            */}
            <ol className="flex flex-col">
              {MISSING.map((text, index) => (
                <Missing key={text} index={index + 1} text={text} />
              ))}
            </ol>
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

/**
 * What Settings still cannot do, listed rather than hidden.
 *
 * Lifted out of the JSX so the list is one thing to keep true rather than
 * three call sites — two entries have already been removed as the features
 * behind them shipped (Epic 9.14's seats, Epic 9.15's billing).
 */
const MISSING: readonly string[] = [
  'Changing your agency name, and the logo and colours a report carries — white-labelling is still name-and-slug only, and needs a written policy on which design tokens an agency may override before it is safe to open up.',
  'Changing the seat limit. Seats can be invited and removed, but three is what every plan includes and there is only one plan, so there is no number here to change yet.',
  'Usage limits and any view of how much you have used. Scans are not metered in either direction — there is no cap to hit and no figure to look at.',
];

function Missing({ index, text }: { index: number; text: string }) {
  return (
    <li className="flex gap-4 border-t border-line-hairline py-4 first:border-t-0 first:pt-0">
      <span className="font-mono text-ui-xs text-text-tertiary">
        {String(index).padStart(2, '0')}
      </span>
      <span className="max-w-measure text-ui-base leading-prose text-text-secondary">
        {text}
      </span>
    </li>
  );
}
