'use client';

// A client boundary although nothing here is interactive, like every other
// page in this app: the header's buttons are client code, and the plan
// constants live in a client module (`PricingCard`), whose values a server
// component could not read. Next still pre-renders the whole page to HTML.

/**
 * The privacy policy — 2026-09-29.
 *
 * DRAFT FOR THE FOUNDER'S REVIEW, written by an agent that is not a lawyer.
 * Have it reviewed before real customers pay. The draft status is recorded
 * here and in the build log, and deliberately not on the page.
 *
 * EVERY SENTENCE STATES SOMETHING THE CODE DOES. Nothing here is template
 * wording; each claim was traced to the code on 2026-09-29, and the ones most
 * likely to drift are held to the code by `legalPages.test.tsx`:
 *
 *   - the providers named under "Who else handles it" against a grep of every
 *     outbound host and dependency in the repository
 *     (`__fixtures__/outboundProviders.ts`), so adding a provider without
 *     naming it here fails the suite;
 *   - every duration and limit against the API setting it copies
 *     (`lib/legal/retention.ts`);
 *   - the operator's ABN against the ATO checksum.
 *
 * Two named on the founder's word, because the repository cannot show them:
 * Supabase as the database host, and Render Key Value as the Redis host
 * (so Render "holds sign-in sessions"). Both are configuration, not code.
 *
 * The governing context is the Australian Privacy Act 1988 and its Australian
 * Privacy Principles. No other jurisdiction's boilerplate, by instruction.
 */

import type { JSX } from 'react';
import { PageSection } from '@avp/design-system';
import { CRAWLER_PATH } from '@/components/marketing/MarketingFooter';
import { OPERATOR } from '@/lib/legal/operator';
import { RETENTION, counted } from '@/lib/legal/retention';
import { LegalPage, Para, Point, Points, TextLink } from './LegalPage';

const mailto = `mailto:${OPERATOR.email}`;

export function PrivacyPolicy(): JSX.Element {
  return (
    <LegalPage
      eyebrow="Privacy"
      title="How this service handles personal information."
      lead="What it stores, who else handles it, and how to have it removed — written from what the code does, not from a template."
    >
      <PageSection heading="Who runs it">
        <Para>
          AI Visibility Platform is run by {OPERATOR.name}, {OPERATOR.trading} in {OPERATOR.city},{' '}
          {OPERATOR.state}, {OPERATOR.country} (ABN {OPERATOR.abn}). For anything on this page — a
          question, a copy of your information, a correction, or deleting it — email{' '}
          <TextLink href={mailto}>{OPERATOR.email}</TextLink>.
        </Para>
        <Para>
          Personal information is handled in line with the Australian Privacy Principles in the
          Privacy Act 1988 (Cth).
        </Para>
      </PageSection>

      <PageSection heading="What is stored">
        <Points>
          <Point term="Your account.">
            Your name and email address, your role in your agency, and when you last signed in. If
            you use a password, an Argon2id hash of it — never the password itself. If you sign in
            with Google, Google’s identifier for your account, and never your Google password.
          </Point>
          <Point term="Your agency.">
            Its name; the people it has invited, with their email address, role and whether they
            accepted; and, if it has set one, the web address of its logo.
          </Point>
          <Point term="Your clients.">
            The website address of each business you add, the name you give it, and what the
            service works out about it: its brand name, its industry and niche, and its
            competitors’ names and domains.
          </Point>
          <Point term="Scan results — facts, not text.">
            For each question a scan asks: which brands each AI answer named and in what position,
            which sources it cited (the address and domain), and a sentiment label. For a
            client’s website: a technical audit — load-time measurements, the kinds of structured
            data it declares, whether AI crawlers may read it, and counts such as how many
            headings and words it has.
          </Point>
          <Point term="Questions and recommendations.">
            The questions each scan asks, which the service writes; any question you type in
            yourself to run again; and the recommendations the service writes for you.
          </Point>
          <Point term="Billing.">
            Your agency’s Stripe customer and subscription identifiers, the subscription’s status,
            and when its current period ends. Card details never reach this service: you enter
            them on Stripe’s own pages, and Stripe holds them.
          </Point>
          <Point term="Sessions and links.">
            Signing in creates a random session token, kept only as a one-way digest; password-reset
            and invitation links are kept the same way. A shared report’s link is kept as it is, so
            it can be copied again.
          </Point>
        </Points>
      </PageSection>

      <PageSection heading="What is not stored">
        <Para>
          The text of AI answers, and the copy of your clients’ web pages. Both pass through the
          service while facts are taken from them, and are not kept. This is enforced, not
          promised: the code’s facts-only rule lets that text exist only in memory while it is
          read, and automated tests check every change against the rule — the tables that hold
          facts from answers and pages cannot hold their text, and no response sends it back.
        </Para>
        <Para>
          IP addresses and card details are not stored with your account either. IP addresses do
          appear in the server logs, described below.
        </Para>
      </PageSection>

      <PageSection heading="Who else handles it">
        <Para>
          These providers process information for this service. Each receives only what its part
          of the work needs.
        </Para>
        <Points>
          <Point term="Render">
            runs the web app and the API, holds sign-in sessions, and keeps the server logs.
          </Point>
          <Point term="Supabase">hosts the database where everything under “What is stored” is kept.</Point>
          <Point term="Stripe">
            takes payment. This service sends it your agency’s name and an internal identifier; you
            give your card and billing details to Stripe directly.
          </Point>
          <Point term="Google sign-in">
            confirms who you are when you sign in with Google. This service receives your Google
            account’s identifier, email address and name, and nothing else from it.
          </Point>
          <Point term="Anthropic, OpenAI, Google Gemini and Perplexity">
            are the AI answer engines a scan measures. They receive questions about your clients’
            businesses, never your personal information: no name, email address or account
            identifier is sent with a question. A question you type in yourself is sent exactly
            as you typed it.
          </Point>
          <Point term="Anthropic">
            also does the analysis. It receives the text of up to{' '}
            {counted(RETENTION.crawlPages, 'public page')} of a client’s website to work out what the
            business does, each engine answer that names the client to label its sentiment, and a
            finished scan’s facts to write recommendations. The page text and answers it receives
            are not stored.
          </Point>
          <Point term="SerpApi">
            runs web searches to find a client’s competitors. It receives search terms built from
            the client’s brand or domain and its industry.
          </Point>
          <Point term="Resend">
            sends password-reset and invitation emails when this service’s email is set up. It then
            receives the recipient’s email address and the message, which carries the agency’s name
            and, for an invitation, the name of the person who sent it.
          </Point>
          <Point term="Google Fonts">
            serves the typefaces these pages use, so your browser fetches them from Google, which
            sees your IP address.
          </Point>
        </Points>
        <Para>
          To audit a client’s website, the service visits its public pages the way a browser
          does. No personal information about you is involved. The{' '}
          <TextLink href={CRAWLER_PATH}>crawler page</TextLink> says what it fetches and how it
          identifies itself.
        </Para>
      </PageSection>

      <PageSection heading="Outside Australia">
        <Para>
          All of these providers are companies based outside Australia, most of them in the United
          States. Using this service discloses personal information to them as overseas
          recipients.
        </Para>
      </PageSection>

      <PageSection heading="Cookies and your browser">
        <Points>
          <Point term="One cookie.">
            avp_session keeps you signed in. It is set when you sign in, cannot be read by scripts
            on the page, and lasts {counted(RETENTION.sessionDays, 'day')} or until you sign out.
          </Point>
          <Point term="Two preferences.">
            Your colour theme (avp.theme) and whether the sidebar’s client list is open
            (avp.sidebar.clients) are kept in your browser’s own storage and never sent anywhere.
          </Point>
          <Point term="No tracking.">
            There are no analytics, advertising or tracking scripts on these pages.
          </Point>
          <Point term="Logos.">
            A report can show an agency’s logo, which your browser loads from the web address that
            agency supplied.
          </Point>
        </Points>
      </PageSection>

      <PageSection heading="Logs">
        <Para>
          The API’s server log records each request: the IP address it came from and the address
          it asked for. The secret parts of those addresses are left out — the token in a shared
          report’s link, and the one-time codes a Google sign-in passes back — so the log cannot
          be used to open a report or finish someone’s sign-in. Events such as a request to reset
          a password name the account by an internal identifier, not its email address, and no
          reset or invitation link is written to the log. Render keeps these logs; this service
          does not send them anywhere else.
        </Para>
        <Para>
          Render also keeps its own record of the requests that reach the web app and the API,
          with the addresses they asked for in full. This service cannot change what that record
          holds, so the address of a shared report, or of a reset or invitation link someone has
          opened, can appear in it. The web app keeps no request log of its own.
        </Para>
      </PageSection>

      <PageSection heading="How long it is kept">
        <Points>
          <Point>
            Accounts, agencies, clients and scan results are kept while the agency uses the
            service. There is no way to delete them yourself yet.
          </Point>
          <Point>
            Removing someone from an agency stops them signing in and ends their sessions at once,
            but their account details stay on record, attached to the reports they ran.
          </Point>
          <Point>
            Sessions end after {counted(RETENTION.sessionDays, 'day')}. Password-reset links expire
            after {counted(RETENTION.resetLinkHours, 'hour')}, invitations after{' '}
            {counted(RETENTION.invitationDays, 'day')}, and a Google sign-up that is not finished
            after {counted(RETENTION.googleSignUpMinutes, 'minute')}. A shared report’s link expires
            after {counted(RETENTION.shareLinkDays, 'day')}, and can be revoked sooner.
          </Point>
          <Point>
            To have your account, or your agency and everything in it, deleted, email{' '}
            <TextLink href={mailto}>{OPERATOR.email}</TextLink>. It is done by hand.
          </Point>
        </Points>
      </PageSection>

      <PageSection heading="Your rights, and complaints">
        <Para>
          You can ask for a copy of the personal information held about you, or for it to be
          corrected, by emailing <TextLink href={mailto}>{OPERATOR.email}</TextLink>. If you are not
          satisfied with the answer to a request or a complaint, you can contact the Office of the
          Australian Information Commissioner at{' '}
          <TextLink href="https://www.oaic.gov.au">oaic.gov.au</TextLink>.
        </Para>
      </PageSection>

      <PageSection heading="Changes">
        <Para>When this page changes, the date at the top changes with it.</Para>
      </PageSection>
    </LegalPage>
  );
}
