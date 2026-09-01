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
 *
 * **A row now LEADS SOMEWHERE — Epic 9.20.** The sentence above used to be the
 * whole story, and it was also the problem: every row was inert, so nothing
 * about one client had anywhere to live and the product had no depth anywhere.
 * `/clients/{id}` is that place. The list itself is unchanged in what it
 * manages — still no rename, still no delete — it simply is not a dead end.
 */

import type { JSX } from 'react';
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  StatRow,
  StatTile,
} from '@avp/design-system';
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

/*
 * The private `Stat` helper that used to live here is gone — Epic 9.24.
 *
 * Its own comment set the condition for moving it: "two call sites would be a
 * component built for a coincidence. If a third screen wants it, that is the
 * point to move it." A third screen wanted it, so it moved, and it is
 * `StatTile` in the design system now. Nothing about the decision was revisited
 * — the condition was simply met.
 */

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
          <header className="flex flex-col gap-6 border-b border-line-hairline pb-8">
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
              <StatRow>
                <StatTile
                  label="Identified"
                  value={countOf(state.clients, 'classified')}
                  accent={0}
                />
                <StatTile
                  label="Awaiting review"
                  value={
                    countOf(state.clients, 'ambiguous') + countOf(state.clients, 'pending')
                  }
                  accent={1}
                  emphasis={
                    countOf(state.clients, 'ambiguous') + countOf(state.clients, 'pending') > 0
                  }
                />
                <StatTile
                  label="Unreadable"
                  value={countOf(state.clients, 'unclassifiable')}
                  accent={2}
                  emphasis={countOf(state.clients, 'unclassifiable') > 0}
                />
                {/*
                  A fourth figure the list already knows and never said — Epic
                  9.24. `industry` is rendered per row and is null for clients
                  whose classification did not resolve one, so "how many of
                  these do we not know the industry of" is a question the
                  screen could always answer and did not.

                  Still nothing fetched: same rows, same fields.
                */}
                <StatTile
                  label="Industry known"
                  value={`${state.clients.filter((c) => c.industry).length} / ${state.clients.length}`}
                  accent={3}
                />
              </StatRow>
            )}
          </header>

          <DataTable
            columns={[
              {
                key: 'name',
                header: 'Business',
                render: (c: Client) => (
                  /*
                    An anchor on the NAME rather than a click handler on the
                    row. A whole-row handler is not middle-clickable, not
                    bookmarkable and invisible to a screen reader, which is the
                    same reasoning `NavItem` gives for being an `<a>`; and a row
                    that navigates on click also swallows text selection, which
                    an operator copying a domain would notice immediately.
                  */
                  <div className="flex flex-col gap-0.5">
                    <a
                      href={`/clients/${c.id}`}
                      className="text-ui-base font-medium text-text-primary underline decoration-line-strong underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-700 hover:decoration-beacon-400"
                    >
                      {c.brandName ?? c.name}
                    </a>
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
