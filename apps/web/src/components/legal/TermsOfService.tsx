'use client';

// A client boundary although nothing here is interactive, like every other
// page in this app: the header's buttons are client code, and the plan
// constants live in a client module (`PricingCard`), whose values a server
// component could not read. Next still pre-renders the whole page to HTML.

/**
 * The terms of service — 2026-09-29.
 *
 * DRAFT FOR THE FOUNDER'S REVIEW, written by an agent that is not a lawyer.
 * Have it reviewed before real customers pay. The draft status is recorded
 * here and in the build log, and deliberately not on the page.
 *
 * Short and plain, as the brief asked, and limited to what it listed: the one
 * plan, the free scan, cancelling, what a score is and is not, suspension for
 * abuse, and the law that applies. Every number comes from a constant, never
 * from typing it again: the price and seats from `PricingCard`, the scan
 * counts from the constants a test holds to the API's own
 * (`PAID_SCANS_PER_MONTH`, `FREE_SCAN_ALLOWANCE`).
 *
 * What is NOT here, and why: no limitation of liability and no warranty
 * disclaimer. Under the Australian Consumer Law some guarantees cannot be
 * excluded, and wording that appears to exclude them can itself mislead; that
 * is a lawyer's drafting, not an agent's. The page says instead that nothing
 * in it limits those rights.
 */

import type { JSX } from 'react';
import { PageSection } from '@avp/design-system';
import {
  PLAN_FREE_SCANS,
  PLAN_PRICE_USD,
  PLAN_SCANS_PER_MONTH,
  PLAN_SEATS,
} from '@/components/marketing/PricingCard';
import { OPERATOR } from '@/lib/legal/operator';
import { LegalPage, Para, Point, Points, TextLink } from './LegalPage';

export function TermsOfService(): JSX.Element {
  const freeScans =
    PLAN_FREE_SCANS === 1
      ? 'your agency’s first scan is free'
      : `your agency’s first ${PLAN_FREE_SCANS} scans are free`;

  return (
    <LegalPage eyebrow="Terms" title="The terms of using this service." lead="Short, and in plain words.">
      <PageSection heading="Who you are dealing with">
        <Para>
          AI Visibility Platform is run by {OPERATOR.name}, {OPERATOR.trading} in {OPERATOR.city},{' '}
          {OPERATOR.country} (ABN {OPERATOR.abn}). Questions about these terms go to{' '}
          <TextLink href={`mailto:${OPERATOR.email}`}>{OPERATOR.email}</TextLink>.
        </Para>
      </PageSection>

      <PageSection heading="What the service does">
        <Para>
          It puts the questions a business’s buyers ask to AI answer engines and reports how that
          business appears in the answers — whether it is named, in what position, which sources
          are cited and in what tone — alongside a technical audit of its website and
          recommendations for what to change.
        </Para>
        <Para>
          AI answers vary from one asking to the next and change over time, so a score or finding
          describes what the engines said when the scan ran. It is an indication, not a guarantee
          of how a business will be shown to anyone else, or later.
        </Para>
      </PageSection>

      <PageSection heading="The plan">
        <Points>
          <Point>
            One plan: ${PLAN_PRICE_USD} a month, billed monthly in US dollars through Stripe. It
            includes {PLAN_SEATS} seats and {PLAN_SCANS_PER_MONTH} scans a month for each agency,
            counted by calendar month (UTC). A scan that fails does not count.
          </Point>
          <Point>
            Before you subscribe, {freeScans} with a Google sign-in.
          </Point>
          <Point>Viewing, sharing and exporting reports is never limited.</Point>
          <Point>
            You can cancel at any time in Stripe’s billing portal: Settings, then Billing, then
            Manage billing. Once Stripe reports the subscription cancelled, starting a scan goes
            back to the free allowance, and the reports you already have stay open.
          </Point>
        </Points>
      </PageSection>

      <PageSection heading="Fair use">
        <Para>
          Do not use the service to break the law, to attack or overload it, or to get around its
          limits — for example, by making extra accounts to collect more free scans. The operator
          can suspend an account that does, or that puts the service or its other customers at
          risk.
        </Para>
      </PageSection>

      <PageSection heading="The law">
        <Para>
          Australian law applies to these terms — the law of {OPERATOR.state}, where the operator
          is based. Nothing in them limits the rights you have under the Australian Consumer Law.
        </Para>
      </PageSection>

      <PageSection heading="Changes">
        <Para>When these terms change, the date at the top changes with them.</Para>
      </PageSection>
    </LegalPage>
  );
}
