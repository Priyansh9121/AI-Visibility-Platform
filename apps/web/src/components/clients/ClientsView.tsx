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
import { Badge, DataTable, ErrorState, LoadingState } from '@avp/design-system';
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
              <span className="text-ui-base text-text-secondary">
                No businesses yet. Use Compare to scan the first one.
              </span>
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
