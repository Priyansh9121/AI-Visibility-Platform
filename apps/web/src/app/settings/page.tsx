'use client';

/**
 * /settings — Epic 9.13.
 *
 * **This screen exists so the sidebar item is not a lie.** The brief called
 * Settings a placeholder, and the honest form of a placeholder is a destination
 * that says what is not built yet — not a nav item whose click does nothing,
 * and not a form of switches that are wired to nothing.
 *
 * Everything listed below is genuinely absent, and each line names the reason
 * rather than promising a date. When one of them ships, its line comes off.
 */

import { useEffect, useState } from 'react';
import { Card, CardBody, LoadingState, PageSection } from '@avp/design-system';
import type { Me } from '@avp/shared-types';
import { api } from '@/lib/api';
import { WorkspaceShell } from '@/components/shell/WorkspaceShell';

export default function SettingsRoute() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const identity = await api.me();
        if (!cancelled) setMe(identity);
      } catch {
        // The shell renders regardless; an unauthenticated visitor is bounced
        // by the nav rather than by an error card on a page with nothing on it.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <WorkspaceShell current="settings" agencyName={me?.agency.name} seats={me?.seats}>
      {loading ? (
        <LoadingState message="Loading your settings…" />
      ) : (
        <div className="flex flex-col gap-10">
          <PageSection
            eyebrow="Settings"
            heading="Your agency"
            lead="What the product knows about your account today."
          >
            <Card elevation="seated">
              <CardBody>
                <dl className="flex flex-col gap-4">
                  <Row label="Agency" value={me?.agency.name ?? '—'} />
                  <Row label="Signed in as" value={me?.user.email ?? '—'} />
                  <Row label="Role" value={me?.user.role ?? '—'} />
                  <Row
                    label="Seats"
                    value={me ? `${me.seats.used} of ${me.seats.limit}` : '—'}
                  />
                </dl>
              </CardBody>
            </Card>
          </PageSection>

          <PageSection
            eyebrow="Not built yet"
            heading="Nothing here is editable."
            lead="Listed rather than hidden, so it is clear what is missing instead of looking for a control that is not there."
          >
            <ul className="flex flex-col gap-3">
              <Missing text="Changing your agency name, and the logo and colours a report carries — white-labelling is still name-and-slug only, and needs a written policy on which design tokens an agency may override before it is safe to open up." />
              <Missing text="Inviting or removing seats. The invitations table, the seat-limit service and session revocation all exist; the HTTP endpoints do not." />
              <Missing text="Changing your password while signed in. The reset-by-email flow works; an authenticated change is a different endpoint and is not built." />
              <Missing text="Billing, plans and usage limits. north-star.md §5.4 records these as undecided — showing a plan picker would imply a decision nobody has made." />
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
