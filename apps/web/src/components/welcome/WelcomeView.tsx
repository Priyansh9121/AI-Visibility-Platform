'use client';

/**
 * The onboarding screen — Epic 9.13, split out and tested in Epic 9.14.
 *
 * Pure and prop-driven so every step is reachable by a static render. The route
 * owns fetching and the state machine. Same split as `DashboardView`, and the
 * reason this screen shipped in 9.13 with no tests: its steps lived inside an
 * effect and a set of handlers a static render never reaches.
 *
 * **This is orchestration, not new capability.** Every step is a component or
 * an endpoint that already existed:
 *
 *   step 1  `IntakeForm`            -> POST /clients {classify:true}   (Epic 2)
 *   step 2  `ClassificationResult`  -> which already offers "Run a scan" (9.12)
 *   step 3  the dashboard, inside the shell                             (9.13)
 *
 * Neither `IntakeForm` nor `ClassificationResult` is forked, wrapped in a
 * variant, or copied. They are rendered as they are, which is why this file is
 * mostly copy — and why a change to the intake flow later cannot silently
 * diverge between here and Compare.
 *
 * WHY THE AGENCY'S OWN SITE
 * -------------------------
 * The fastest way to judge whether this product tells you anything is to point
 * it at a business you already know completely. An agency knows its own site
 * better than any prospect's, so a finding there is immediately checkable —
 * and an agency that is itself invisible in AI answers has just learned
 * something it can sell.
 *
 * **The wizard is skippable at every step.** Someone who signed up to scan a
 * prospect, not themselves, should not be held here.
 */

import type { JSX } from 'react';
import { Button, Card, CardBody, LoadingState, PageSection } from '@avp/design-system';
import type { ClientDetail, Me } from '@avp/shared-types';
import { IntakeForm } from '@/components/IntakeForm';
import { ClassificationResult } from '@/components/ClassificationResult';

export type WelcomeState =
  | { kind: 'loading' }
  | { kind: 'ask' }
  | { kind: 'working' }
  | { kind: 'result'; client: ClientDetail };

export function WelcomeView({
  state,
  me,
  onStarted,
  onClassified,
  onReset,
}: {
  state: WelcomeState;
  me: Me | null;
  onStarted: () => void;
  onClassified: (client: ClientDetail) => void;
  onReset: () => void;
}): JSX.Element {
  if (state.kind === 'loading') {
    return (
      <main className="mx-auto max-w-report px-6 py-18">
        <LoadingState message="Setting up your workspace…" />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-report px-6 py-18">
      <div className="flex flex-col gap-10">
        <PageSection
          tone="lead"
          eyebrow={me ? `Welcome, ${me.agency.name}` : 'Welcome'}
          heading="Start with a site you already know."
          lead={
            <>
              Point it at your own agency first. You know that site better than any
              prospect&apos;s, so whatever the report says about it is something you can
              check immediately — and an agency that is itself invisible when buyers ask
              has just found something worth selling.
            </>
          }
        />

        {state.kind === 'result' ? (
          <div className="flex flex-col gap-6">
            {/*
              The EXISTING component, unmodified. It already leads onward —
              "Run a scan" landed in Epic 9.12 — so the wizard does not need
              its own scan button and deliberately does not have one.
            */}
            <ClassificationResult
              client={state.client}
              onReset={() => onReset()}
            />
            <Done />
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            <Card elevation="seated">
              <CardBody>
                <IntakeForm
                  onStarted={() => onStarted()}
                  onClassified={(client) => onClassified(client)}
                  onFailed={() => onReset()}
                />
              </CardBody>
            </Card>

            {state.kind === 'working' && (
              <Card elevation="seated">
                <CardBody>
                  <LoadingState
                    message="Reading the site"
                    steps={[
                      'Fetching the homepage and a few key pages',
                      'Pulling out structured data and page structure',
                      'Working out the industry and brand name',
                    ]}
                    hint="This usually takes a few seconds."
                  />
                </CardBody>
              </Card>
            )}

            <Skip />
          </div>
        )}
      </div>
    </main>
  );
}

/**
 * The way out of the wizard once a client exists.
 *
 * **The agency's own client is an ORDINARY dashboard row, deliberately.**
 * Nothing pins or labels it, for three reasons: there is no column that records
 * "this one is ours", so the badge would have to be inferred; inferring it by
 * matching the agency name against the domain is wrong for every agency whose
 * trading name differs from its URL; and the product's whole loop treats every
 * client identically, so a visual exception would be a claim the data cannot
 * support. If that distinction is ever wanted it is a schema change and its own
 * brief, not a guess made in the view layer.
 */
function Done() {
  return (
    <Card elevation="seated">
      <CardBody>
        <p className="text-ui-md font-medium text-text-primary">That is your workspace set up.</p>
        <p className="mt-2 max-w-measure text-ui-base leading-prose text-text-secondary">
          Everything you scan from here lands on your dashboard, your own site included —
          it is an ordinary client, with nothing special about it.
        </p>
        <div className="mt-5">
          <Button variant="secondary" onClick={() => window.location.assign('/dashboard')}>
            Go to your dashboard
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function Skip() {
  return (
    <p className="text-ui-sm leading-prose text-text-tertiary">
      Would rather start with a prospect?{' '}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => window.location.assign('/dashboard')}
      >
        Skip for now
      </Button>
    </p>
  );
}
