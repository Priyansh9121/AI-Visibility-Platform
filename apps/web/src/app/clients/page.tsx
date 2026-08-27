'use client';

/**
 * /clients — every business this agency has added. Epic 9.13.
 *
 * `GET /clients` has existed since Epic 2 and nothing in the browser called it,
 * the same way `sign-up` sat unused until Epic 9.12. This is wiring, not a new
 * capability.
 *
 * It is a LIST, not a management screen: there is no rename, no delete and no
 * bulk action, because none of those endpoints exist. It ends at the boundary
 * of what is real rather than showing a control that would fail.
 */

import { useEffect, useState } from 'react';
import { Badge, DataTable, ErrorState, LoadingState } from '@avp/design-system';
import type { Client, Me } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';

type View =
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

export default function ClientsRoute() {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [identity, page] = await Promise.all([api.me(), api.clients()]);
        if (cancelled) return;
        setMe(identity);
        setView({ kind: 'ready', clients: page.data, more: page.nextCursor !== null });
      } catch (err) {
        if (cancelled) return;
        setView({
          kind: 'error',
          title:
            err instanceof ApiProblem && err.status === 401
              ? 'Sign in to see your clients'
              : 'Your clients could not be loaded',
          detail:
            err instanceof ApiProblem
              ? err.problem.detail
              : 'The request did not complete.',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <WorkspaceShell
      current="clients"
      agencyName={me?.agency.name}
      seats={me?.seats}
      wide
    >
      {view.kind === 'loading' && <LoadingState message="Loading your clients…" />}

      {view.kind === 'error' && (
        <ErrorState title={view.title} detail={view.detail} />
      )}

      {view.kind === 'ready' && (
        <div className="flex flex-col gap-8">
          <header className="flex flex-wrap items-end justify-between gap-6 border-b border-line-hairline pb-8">
            <div>
              <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
                Clients
              </p>
              <h1 className="mt-2 font-editorial text-ed-sm leading-display tracking-display text-text-primary">
                {view.clients.length === 1
                  ? 'One business'
                  : `${view.clients.length} businesses`}
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
            rows={view.clients}
            rowKey={(c: Client) => c.id}
            emptyMessage={
              <span className="text-ui-base text-text-secondary">
                No businesses yet. Use Compare to scan the first one.
              </span>
            }
          />

          {view.more && (
            // Said plainly rather than silently truncating. There is no
            // paging control because building one is its own piece of work.
            <p className="text-ui-sm text-text-tertiary">
              Showing the most recent page only.
            </p>
          )}
        </div>
      )}
    </WorkspaceShell>
  );
}
