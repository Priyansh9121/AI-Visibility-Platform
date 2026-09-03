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
 * - **Prompts** — Epic 9.24. Ad-hoc prompt testing: type a question, run it
 *   against this product's own engines on demand, and see who they name. The
 *   only destination here that CREATES data rather than reading a scan's.
 * - **Sentiment** — Epic A. How each engine described the client, scan by
 *   scan. The labels have been stored since Epic 4 and appeared nowhere except
 *   folded into the composite; this reads them on their own.
 * - **Answer gaps** — Epic B. Which questions a rival owns and this client does
 *   not, per scan. Sits beside Rankings because both read the same competitive
 *   picture: Rankings says who is ahead overall, this says on WHICH questions.
 *   Reads only rows a scan already wrote; it runs nothing.
 *
 * Nothing about this frame assumes a fixed number of items.
 *
 * ACCENTS — Epic 9.24
 * -------------------
 * Each section carries a fixed index into the Working-screen accent layer, and
 * the indices are written by name for the reason `WorkspaceShell`'s are: they
 * are identity, not position.
 *
 * What Epic B.1 changed is the SCOPE. Epic B filled the seventh and last seat
 * in the layer, and this space is heading for ten sections, so the strip is
 * now CLUSTERED and an accent is cluster-relative — each cluster restarts at
 * 0. The whole table lives in `clientNav.ts`, which is where the reasoning and
 * the tests for it are. Report is deliberately UNACCENTED: it is the one item
 * that leaves for a Presenting-context document, and a Working hue would imply
 * it belongs to the same set as the sections that stay.
 */

import type { JSX, ReactNode } from 'react';
import { LocalNav, LocalNavGroup, LocalNavItem } from '@avp/design-system';
import type { Client, Me } from '@avp/shared-types';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';
import { CLIENT_NAV, type ClientSection } from '@/components/client/clientNav';

export type { ClientSection };

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
  figures,
  children,
}: {
  client: Pick<Client, 'id' | 'name' | 'brandName' | 'domain'>;
  me: Me | null;
  current: ClientSection;
  latestReportScanId: string | null;
  meta?: ReactNode;
  /**
   * A full-width row of figures, under the nav strip — Epic 9.24.
   *
   * NOT `meta`. `meta` sits beside the title inside `LocalNav`'s head, which is
   * a flex row sized to its content: a row of tiles put there collapses to one
   * narrow column, stretches the header to its height and leaves the space
   * beside the title emptier than before. Measured in a live browser on the
   * Rankings screen, which is exactly the "big empty margins" complaint this
   * epic set out to close.
   *
   * Given its own row it fills the Working column, which is what actually
   * closes it. `meta` stays for what it was for: a short fact or two.
   */
  figures?: ReactNode;
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
          {CLIENT_NAV.map((cluster) => (
            <LocalNavGroup key={cluster.key} label={cluster.label}>
              {cluster.items.map((item) => {
                /*
                 * The Report resolves to a SCAN, not to a path under the
                 * client, and when there is no scan it is not rendered at all
                 * rather than rendered dead — `NavItem`'s rule, which has held
                 * here since Epic 9.20.
                 */
                if (item.section === null) {
                  return latestReportScanId == null ? null : (
                    <LocalNavItem
                      key="report"
                      href={`/scans/${latestReportScanId}/report`}
                      label={item.label}
                      external
                    />
                  );
                }
                return (
                  <LocalNavItem
                    key={item.section}
                    href={`${base}${item.path ?? ''}`}
                    label={item.label}
                    current={current === item.section}
                    {...(item.accent != null ? { accent: item.accent } : {})}
                  />
                );
              })}
            </LocalNavGroup>
          ))}
        </LocalNav>
        {figures}
        {children}
      </div>
    </WorkspaceShell>
  );
}
