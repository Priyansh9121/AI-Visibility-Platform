'use client';

/**
 * The clients screen — Epic 9.13, split out and tested in Epic 9.14.
 *
 * Pure and prop-driven, so every state is reachable by a static render. The
 * route owns fetching. That split is `DashboardView` / `dashboard/page.tsx`
 * from Epic 9.3, and it is why this screen shipped in 9.13 with no tests: its
 * states lived inside an effect the static renderer never runs.
 *
 * It is a LIST, not a management screen: no rename, no delete, no bulk action,
 * because none of those endpoints exist. It ends at the boundary of what is
 * real rather than showing a control that would fail.
 */

import type { JSX } from 'react';
import { Badge, Button, DataTable, EmptyState, ErrorState, LoadingState } from '@avp/design-system';
import type { Client, Me } from '@avp/shared-types';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';

export type ClientsState =
  | { kind: 'loading' }
  | { kind: 'ready'; clients: Client[]; more: boolean }
  | { kind: 'error'; title: string; detail: string };

const STATUS_TONE = {
  classified: 'success',
  ambiguous: 'warn',
  unclassifiable: 'danger',
  pending: 'neutral',
} as const;

const STATUS_LABEL = {
  classified: 'Identified',
  ambiguous: 'Needs review',
  unclassifiable: 'Could not read',
  pending: 'Pending',
} as const;

/** How many of these clients are in one classification state. */
function countOf(clients: readonly Client[], status: Client['classificationStatus']): number {
  return clients.filter((c) => c.classificationStatus === status).length;
}

/**
 * One header figure.
 *
 * Deliberately the same shape as `DashboardView`'s `Stat` rather than a
 * shared import: that one is a private helper inside a screen this file does
 * not otherwise touch, and hoisting it into the design system for two call
 * sites would be a component built for a coincidence. If a third screen wants
 * it, that is the point to move it.
 */
function Stat({ label, value }: { label: string; value: number }): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{label}</dt>
      <dd className="text-ui-lg font-medium text-text-primary">{value}</dd>
    </div>
  );
}

export function ClientsView({
  state,
  me,
}: {
  state: ClientsState;
  me: Me | null;
}): JSX.Element {
  return (
    <WorkspaceShell
      current="clients"
      agencyName={me?.agency.name}
      seats={me?.seats}
      wide
    >
      {state.kind === 'loading' && <LoadingState message="Loading your clients…" />}

      {state.kind === 'error' && (
        <ErrorState title={state.title} detail={state.detail} />
      )}

      {state.kind === 'ready' && (
        <div className="flex flex-col gap-8">
          <header className="flex flex-wrap items-end justify-between gap-6 border-b border-line-hairline pb-8">
            <div>
              <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
                Clients
              </p>
              <h1 className="mt-2 font-editorial text-ed-sm leading-display tracking-display text-text-primary">
                {state.clients.length === 1
                  ? 'One business'
                  : `${state.clients.length} businesses`}
              </h1>
            </div>
            {/*
              The classification split, in the dashboard header's own treatment
              — Epic 9.19. Three columns of table stretched across a Working
              screen's full width left this header carrying a single number and
              a rule, which is what made the screen read as bare.

              NOTHING NEW IS FETCHED OR COMPUTED. Every one of these counts the
              `classificationStatus` already rendered as a badge two hundred
              pixels below, on rows already in hand; the header says what the
              column says, at a glance, which is the whole job of a Working
              screen. `Pending` is folded into "waiting" alongside the
              ambiguous ones deliberately — both are rows an operator may still
              have to look at, and splitting them would put a stat on screen
              that is usually zero.
            */}
            {state.clients.length > 0 && (
              <dl className="flex flex-wrap items-end gap-8">
                <Stat label="Identified" value={countOf(state.clients, 'classified')} />
                <Stat
                  label="Awaiting review"
                  value={
                    countOf(state.clients, 'ambiguous') + countOf(state.clients, 'pending')
                  }
                />
                <Stat
                  label="Unreadable"
                  value={countOf(state.clients, 'unclassifiable')}
                />
              </dl>
            )}
          </header>

          <DataTable
            columns={[
              {
                key: 'name',
                header: 'Business',
                render: (c: Client) => (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-ui-base font-medium text-text-primary">
                      {c.brandName ?? c.name}
                    </span>
                    <span className="font-mono text-ui-xs text-text-tertiary">
                      {c.domain}
                    </span>
                  </div>
                ),
              },
              {
                key: 'industry',
                header: 'Industry',
                render: (c: Client) =>
                  c.industry ? (
                    <span className="text-ui-sm text-text-secondary">{c.industry}</span>
                  ) : (
                    // Never a dash that reads like data — the same rule the
                    // dashboard's ScoreMeter follows for a null score.
                    <span className="text-ui-sm text-text-tertiary">Not identified</span>
                  ),
              },
              {
                key: 'status',
                header: 'Classification',
                // To the far edge, the way the dashboard's Actions column is —
                // three columns spread across a Working screen's full width
                // otherwise leave the last one floating in the middle with a
                // third of the table empty beside it. `DataTable` already owns
                // this alignment; it simply was not asked for.
                align: 'end',
                render: (c: Client) => (
                  <Badge tone={STATUS_TONE[c.classificationStatus]}>
                    {STATUS_LABEL[c.classificationStatus]}
                  </Badge>
                ),
              },
            ]}
            rows={state.clients}
            rowKey={(c: Client) => c.id}
            emptyMessage={
              /*
                An agency that has just signed up lands here, and it used to be
                one grey sentence centred under three empty column headings.
                `EmptyState` gives it the treatment the rest of the product has
                and, more usefully, a way out — Compare was named in the copy
                but was not reachable from the words naming it.
              */
              <EmptyState
                // No eyebrow: an empty state nested inside a labelled section
                // would repeat the label. The dashboard's full-page one keeps
                // its eyebrow because there is no heading above it to repeat.
                title="No businesses yet"
                body="Every client here starts the same way: a website. Compare reads the site the way a buyer would, works out who it competes with, and scans from there."
                note="Anything you scan from Compare appears in this list."
                action={
                  <Button variant="primary" onClick={() => window.location.assign('/')}>
                    Scan the first business
                  </Button>
                }
              />
            }
          />

          {state.more && (
            /*
             * VERIFIED, not assumed — Epic 9.14, brief item 16.
             *
             * A previous review flagged this copy as possibly backwards —
             * if `GET /clients` sorted by id ASCENDING, the first page would
             * be the OLDEST clients and this sentence would be a lie.
             *
             * It does not. `routers/clients.py` does
             * `.order_by(Client.id.desc())` and its docstring says
             * "Cursor-paginated client list, newest first"; IDs are ULIDs, so
             * that is creation order. `test_intake.py::
             * test_list_paginates_newest_first` has asserted it since Epic 2,
             * which is to say the guarantee was already covered and only the
             * confirmation was outstanding. The copy is correct and stays.
             *
             * Said plainly rather than silently truncating. There is no paging
             * control because building one is its own piece of work.
             */
            <p className="text-ui-sm text-text-tertiary">
              Showing the most recent page only.
            </p>
          )}
        </div>
      )}
    </WorkspaceShell>
  );
}
