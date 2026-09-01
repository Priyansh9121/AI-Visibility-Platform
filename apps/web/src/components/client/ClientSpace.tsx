'use client';

/**
 * The frame every screen inside ONE client's space shares — Epic 9.20.
 *
 * THIS IS NEW STRUCTURE, NOT A NEW SCREEN
 * ---------------------------------------
 * Until this epic a client row went nowhere: `ClientsView` said so in its own
 * comment ("a LIST, not a management screen"), and it rendered no links at all.
 * There was no place for anything about one client to live, which is why the
 * product had no depth anywhere — not because the depth was cut, but because
 * there was no room with a door on it.
 *
 * The agency sidebar stays exactly as it is. It is agency-wide — every item in
 * it is about the whole account across every client at once — and depth about
 * one client cannot go there without either changing what those items mean or
 * inventing a global "selected client" the rest of the product does not have.
 * So this is a second level of navigation, nested inside the first, and
 * `LocalNav` is the primitive that says so.
 *
 * WHERE EACH DESTINATION GOES
 * ---------------------------
 * - **Overview** (`/clients/{id}`) — where a client row lands. It exists
 *   because a nav has to have a current item and a space has to have a front
 *   door; it carries the client's identity, its latest reading and its scan
 *   history.
 * - **Report** — a LINK OUT to `/scans/{latest}/report`, the document that
 *   already exists. Deliberately not a copy of the report rendered inside this
 *   frame: the report is a Presenting-context document at `--avp-report-width`
 *   and putting it inside a wide Working shell with a nav strip above it would
 *   change the artefact. A real path into it is what the brief asked for and
 *   what this is.
 * - **Sources** / **Rankings** — trends over the client's whole history.
 * - **Technical** — Epic 9.22. The audit has existed since Epic 6 and only
 *   ever appeared folded into the report's fix beat; this is the same stored
 *   data read on its own, with the four weighted components its sub-score is
 *   actually made of.
 *
 * Nothing about this frame assumes a fixed number of items.
 */

import type { JSX, ReactNode } from 'react';
import { LocalNav, LocalNavItem } from '@avp/design-system';
import type { Client, Me } from '@avp/shared-types';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';

export type ClientSection = 'overview' | 'sources' | 'rankings' | 'technical';

export function ClientSpace({
  client,
  me,
  current,
  /**
   * The scan whose report the Report item opens. Null when the client has
   * never produced one — in which case the item is not rendered at all rather
   * than rendered dead, which is `NavItem`'s rule and holds here too.
   */
  latestReportScanId,
  meta,
  children,
}: {
  client: Pick<Client, 'id' | 'name' | 'brandName' | 'domain'>;
  me: Me | null;
  current: ClientSection;
  latestReportScanId: string | null;
  meta?: ReactNode;
  children: ReactNode;
}): JSX.Element {
  const base = `/clients/${client.id}`;
  return (
    <WorkspaceShell current="clients" agencyName={me?.agency.name} seats={me?.seats} wide>
      <div className="flex flex-col gap-8">
        <LocalNav
          title={client.brandName ?? client.name}
          subtitle={client.domain}
          back={{ href: '/clients', label: 'All clients' }}
          meta={meta}
        >
          <LocalNavItem href={base} label="Overview" current={current === 'overview'} />
          {latestReportScanId != null && (
            <LocalNavItem
              href={`/scans/${latestReportScanId}/report`}
              label="Report"
              external
            />
          )}
          <LocalNavItem
            href={`${base}/sources`}
            label="Sources"
            current={current === 'sources'}
          />
          <LocalNavItem
            href={`${base}/rankings`}
            label="Rankings"
            current={current === 'rankings'}
          />
          <LocalNavItem
            href={`${base}/technical`}
            label="Technical"
            current={current === 'technical'}
          />
        </LocalNav>
        {children}
      </div>
    </WorkspaceShell>
  );
}
