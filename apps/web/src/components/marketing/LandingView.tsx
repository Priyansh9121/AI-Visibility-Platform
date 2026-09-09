/**
 * The public landing page — Epic 9.10, given a header and a price in Epic 9.15.
 *
 * The first screen in this product a stranger can reach on purpose. Until 9.10
 * `/` showed a sign-in panel to anyone without a session, so there was nowhere
 * to send a prospective agency to find out what this is.
 *
 * WHAT IS CLAIMED HERE, AND WHY EACH CLAIM IS SAFE TO MAKE
 * -------------------------------------------------------
 * Every number and capability below is one this codebase actually ships, and
 * was verified against the build log before it was written:
 *
 *   - the scan pipeline's duration          — see the note beside the copy;
 *                                            the old "~6 minutes" was stale
 *   - 24 prompts, intent-tagged            — Epic 4.1
 *   - the five weighted dimensions         — scoring-spec.md, Epic 5.2
 *   - per-answer ordinality (Answer Shelf) — Epic 7.1
 *   - facts-only storage, test-enforced    — test_facts_only.py
 *   - named fixes with priority + effort   — Epic 8.0
 *   - one vendor, two modes, today         — Epic 4.2's own stated limitation
 *   - PDF export and a share link          — Epic 9.8 and 9.14, both shipped
 *   - $29/month, 3 seats                   — Epic 9.15, a real Stripe Price
 *
 * NOTHING ELSE IS CLAIMED. There are no testimonials, no customer logos, no
 * user counts, no press mentions and no funding line on this page, because
 * this product has none of those things yet and inventing them would be a lie
 * that the trust section immediately below would then contradict. Where a page
 * like this would normally carry social proof, it carries a verifiable
 * statement about the data discipline instead — north-star.md §7's argument
 * that the facts-only rule is a genuine commercial asset, not just hygiene.
 *
 * THE ONE RULE THAT CHANGED IN 9.15, AND THE ONE THAT DID NOT
 * ----------------------------------------------------------
 * 9.10 published no price, and its test said so in as many words: "advertises
 * no price or plan, because none is decided". That was correct while
 * north-star.md §5.3's tiers were [HYPOTHESIS]. The founder has now decided a
 * real price, so the page publishes it and §5.3 has been updated to match
 * rather than left to contradict the live page.
 *
 * **Fabricated social proof remains exactly as prohibited as before.** A price
 * we have decided to charge is a fact about us. A customer quote we do not
 * have is not, and nothing in this change licenses inventing one.
 *
 * TWO STALE CLAIMS WERE REMOVED, NOT REWORDED
 * -------------------------------------------
 * The "what it does not do yet" list said "There is no PDF export yet" and
 * "there is no self-serve plan to sign up to today". Epic 9.14 shipped PDF
 * export and this epic ships the plan, so both sentences were false on a page
 * whose entire argument is that its claims are checkable. They are gone. The
 * list itself stays, because the remaining lines are still true.
 *
 * The no-competitor-reference and no-borrowed-copy rules: designed and written from the data model, the
 * build log, and product-spec.md §3's core loop and §5.4's pipeline. No
 * competitor site was opened, referenced or paraphrased while building this,
 * and no sentence here is a reworded version of anyone else's marketing copy.
 *
 * The licensed-assets rule: no icon pack, illustration kit or stock imagery. The only
 * graphic is the product's own Luminance Ledger, rendered from example data
 * that is labelled as example data.
 */

import type { JSX } from 'react';
import {
  Button,
  Card,
  CardBody,
  LuminanceLedger,
  PageSection,
  Reveal,
  RevealGroup,
  ScoreMeter,
} from '@avp/design-system';
import type { LedgerDimension } from '@avp/design-system';
import { PLAN_PRICE_USD, PricingCard } from './PricingCard';

/**
 * EXAMPLE DATA — not a client, not a real scan.
 *
 * A real scan of a real company exists and is more persuasive, but publishing
 * a named business's visibility score without that business's consent is not
 * something a page selling trustworthiness gets to do. So the shape is real —
 * these are the actual five dimensions and their actual §6 weights — and the
 * sub-scores are invented and labelled as such on screen.
 */
const EXAMPLE_DIMENSIONS: LedgerDimension[] = [
  { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 34 },
  { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 28 },
  { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 11 },
  { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 62 },
  { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 90 },
];

/**
 * The two in-page anchors the header navigates to.
 *
 * Scroll-to on the same page rather than routes of their own: there is one
 * plan and one product, and a comparison page with a single column in it is a
 * page that exists to look like a bigger company's page.
 *
 * Both targets carry `scroll-mt-18`, which is the header's own height plus
 * room. Without it the browser scrolls the anchor to y=0 and the sticky header
 * covers the heading the reader just asked to see — the nav link appearing to
 * land one section too far down. Caught in the live browser pass, not by a
 * test: a static render has no scroll position to be wrong about.
 */
const PRODUCT_ID = 'product';
const PRICING_ID = 'pricing';

export interface LandingViewProps {
  /** Send the visitor to the SIGN-UP surface — the primary call to action. */
  onGetStarted?: (() => void) | undefined;
  /**
   * Send the visitor to the SIGN-IN surface.
   *
   * Deliberately not `onGetStarted`. Someone who already has an account and
   * someone evaluating the product want two different forms, and landing an
   * existing user on a sign-up panel asks them to read their way out of it.
   */
  onLogIn?: (() => void) | undefined;
  /** Disable chart motion in tests and static renders. */
  animate?: boolean | undefined;
}

export function LandingView({
  onGetStarted,
  onLogIn,
  animate = true,
}: LandingViewProps): JSX.Element {
  return (
    <div id="top" className="flex flex-col">
      <LandingHeader onGetStarted={onGetStarted} onLogIn={onLogIn} />

      <main className="mx-auto flex w-full max-w-report flex-col gap-24 px-6 py-18">
        {/*
          The hero staggers its OWN parts rather than arriving as one block —
          eyebrow, then headline, then lead, then the call to action. It is the
          only section that does, because it is the only one already on screen
          when the page loads: there is no scroll to give it an arrival, so the
          sequence is the arrival.
        */}
        <PageSection
          stagger
          animate={animate}
          tone="lead"
          eyebrow="For SEO and digital marketing agencies"
          heading="Find out what AI assistants say when your client's buyers ask."
          lead={
            <>
              Buyers increasingly ask an assistant before they ask a search engine. This
              measures whether your client is named in those answers, who is named instead,
              and what to change — as a report you can put in front of them.
            </>
          }
        >
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={onGetStarted}>
              Scan a website
            </Button>
            <p className="text-ui-sm text-text-tertiary">
              {/*
                "About six minutes" was true of Epic 9.2's measurement and
                stopped being true at Epic 9.17, which made the UI-triggered
                path run the whole nine-phase chain. `product-spec.md` already
                records the 300s budget as missed "and by more" since then, and
                the pilot dry run measured 286s for a HALF-LENGTH scan — so the
                default 24-prompt scan is roughly nine minutes, not six.

                NO PRECISE NUMBER IS COMMITTED TO, deliberately. Three
                12-prompt runs came in at 286s, 306s and 331s — a 16% spread on
                identical work, because the time is dominated by engine latency
                this product does not control. And there is no post-9.17
                measurement of a 24-prompt scan at all; nine minutes is an
                extrapolation from the loop, not an observation. Printing a
                figure that precise on a marketing page would repeat the
                original mistake at a different number.

                So: a round central figure, hedged, and one that a scan running
                long does not falsify. The failure this replaces is a prospect
                told six minutes who waits nine and assumes it broke.
              */}
              A scan usually takes about ten minutes — twenty-four questions, put to three
              AI engines, one at a time. ${PLAN_PRICE_USD}/month when you are ready to
              pay for it.
            </p>
          </div>
        </PageSection>

        <div id={PRODUCT_ID} className="flex scroll-mt-18 flex-col gap-24">
          <Reveal animate={animate}>
          <PageSection
            eyebrow="What a scan does"
            heading="It asks the questions your client's buyers actually ask."
            lead={
              <>
                You give it a website. It reads the site the way a buyer would, works out what
                the business does, and finds who it competes with — from search results and from
                which brands get named alongside it in AI answers.
              </>
            }
          >
            {/*
              A group, so all seven arrive together one step apart rather than
              each waiting to cross the fold itself — which on a list this tall
              would mean the stagger reading as scroll speed rather than as an
              authored sequence.
            */}
            <RevealGroup as="ol" animate={animate} className="flex flex-col gap-4">
              <Step
                index={0}
                n="01"
                animate={animate}
                title="One URL in, and nothing else to fill in"
                body="You paste the website. It fetches the homepage and a few key pages, reads the structure and the copy, and works out for itself what the business sells and to whom — so the first thing you do is not a form about your prospect's industry."
              />
              <Step
                index={1}
                n="02"
                animate={animate}
                title="Rivals found rather than guessed at"
                body="Two independent signals: who ranks for the niche in search, and which brands the AI engines name in the same breath as your prospect. Both, deduplicated and ranked — and you can overrule the result, which then survives every later re-detection."
              />
              <Step
                index={2}
                n="03"
                animate={animate}
                title="Twenty-four questions, tagged by buying stage"
                body="Generated for that specific business — awareness, comparison and bottom-of-funnel — so the result is not one lucky prompt but a spread across how people actually shop."
              />
              <Step
                index={3}
                n="04"
                animate={animate}
                title="Each question, put to AI answer engines"
                body="Today that is Claude in two modes: what it recalls unprompted, and what it says when it searches the live web and cites sources. Those disagree more often than you would expect, and the difference is itself a finding."
              />
              <Step
                index={4}
                n="05"
                animate={animate}
                title="Every answer read for facts, never stored as prose"
                body="Was the brand named? Where in the answer? Which sources were cited, and who owns them? Which rivals appeared instead?"
              />
              <Step
                index={5}
                n="06"
                animate={animate}
                title="The site itself checked for what the engines need"
                body="Structured data, page structure, indexability — the technical signals that decide whether a page can be quoted at all, scored as one of the five dimensions rather than filed as a separate audit nobody reads."
              />
              <Step
                index={6}
                n="07"
                animate={animate}
                title="A score, a gap, and a fix list"
                body="Five weighted dimensions into one number out of 100, the biggest gap named in points, and specific changes ordered by what they would move and what they would cost to do."
              />
            </RevealGroup>
          </PageSection>
          </Reveal>

          <Reveal animate={animate}>
          <PageSection
            eyebrow="The report"
            heading="A document, not a dashboard."
            lead={
              <>
                The report is built to be sent. It argues in order — where you stand, the
                biggest gap, the proof, the fix, the pitch — because a client reads an argument,
                not a wall of tiles. The score is drawn as light: each dimension's height is the
                points available, and its lit part is the points earned, so the picture is the
                number rather than an illustration of it.
              </>
            }
          >
            <Card elevation="seated">
              <CardBody>
                <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
                  Example — illustrative figures, not a real client
                </p>
                <div className="mt-4">
                  {/*
                    `staggerDimensions` is set HERE and nowhere else in the
                    product. It is the same component the client-facing report
                    renders, and the report must not acquire this — see the
                    prop's own docstring, and the regression test in
                    ReportView.test.tsx that fails if it ever does.
                  */}
                  <LuminanceLedger
                    subjectName="Example Co"
                    dimensions={EXAMPLE_DIMENSIONS}
                    animate={animate}
                    staggerDimensions
                    annotateGap
                  />
                </div>
              </CardBody>
            </Card>
          </PageSection>
          </Reveal>

          <Reveal animate={animate}>
          <PageSection
            eyebrow="What is different"
            heading="It records where your client placed in each answer, not just how often."
            lead={
              <>
                Being mentioned in half the answers tells you very little on its own. Named
                first in a three-way comparison and named last are both a mention, and they are
                not the same commercial position.
              </>
            }
          >
            <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
              Every answer the engines gave gets a row, in order, showing who was named and
              where your client landed among them. An answer where the client was absent gets an
              explicit empty mark rather than a blank — a row that renders nothing when the
              subject is missing does not look like a problem, it looks like a clean report, and
              that is exactly the finding you most need to see.
            </p>
          </PageSection>
          </Reveal>

          <Reveal animate={animate}>
          <PageSection
            eyebrow="What you can stand behind"
            heading="Every claim in the report traces back to something recorded."
            lead={
              <>
                You are putting this in front of a client with your name on it, so it matters
                what is underneath it.
              </>
            }
          >
            <div className="flex flex-col gap-4">
              <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
                This tool never stores or reproduces anyone&apos;s copyrighted content — not an
                engine&apos;s answer text, not a competitor&apos;s page copy. What it keeps is
                facts: whether a brand was named, in what position, which domains were cited,
                what structured data a page carries. Raw text exists only long enough to read
                those facts out of it, and never reaches the database, the API, or the report.
              </p>
              <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
                That is not a policy paragraph. It is enforced by tests that walk every table
                and every response shape in the system, so a field that could carry someone
                else&apos;s prose fails the build rather than reaching a client&apos;s report.
              </p>
            </div>
          </PageSection>
          </Reveal>
        </div>

        <Reveal animate={animate}>
        <PageSection
          eyebrow="What it does not do yet"
          heading="The honest version."
          lead={
            <>
              A page like this usually stops before this section. It is here because the
              argument above is about verifiability, and a page that overstates its own
              product undermines it.
            </>
          }
        >
          <ul className="flex flex-col gap-3">
            <Limit body="It measures one vendor's models today, in two modes. Cross-vendor comparison is the obvious next thing and is not built yet." />
            <Limit body="White-labelling covers your agency name. Logo and colour control are not built yet." />
            <Limit body="Scan volume is not metered or capped, in either direction. There is no usage limit to hit and no usage figure to look at." />
            <Limit body="Nothing tracks whether a fix was actually done, or re-measures what it changed. The report ends at the recommendation." />
          </ul>
        </PageSection>
        </Reveal>

        <div id={PRICING_ID} className="scroll-mt-18">
          <Reveal animate={animate}>
          <PageSection
            eyebrow="Pricing"
            heading="One plan. Twenty-nine dollars a month."
            lead={
              <>
                One price, published, with no &ldquo;contact us&rdquo; between you and it. It
                buys the product as described above — not a trial of it, and not a
                cut-down tier with the useful part held back.
              </>
            }
          >
            <PricingCard signedIn={false} onGetStarted={onGetStarted} />
          </PageSection>
          </Reveal>
        </div>

        <Reveal animate={animate}>
        <PageSection
          eyebrow="Start"
          heading="Run one scan on a prospect you already want."
          lead={
            <>
              The fastest way to judge this is to point it at a business you know well and see
              whether the report tells you something you did not already know about them.
            </>
          }
        >
          <div className="flex flex-wrap items-center gap-4">
            <Button variant="primary" onClick={onGetStarted}>
              Scan a website
            </Button>
            <div className="flex items-center gap-3">
              <ScoreMeter score={41} />
              <p className="max-w-measure text-ui-sm text-text-tertiary">
                Example of a score a real prospect might return.
              </p>
            </div>
          </div>
        </PageSection>
        </Reveal>
      </main>
    </div>
  );
}

/**
 * The persistent header — Epic 9.15.
 *
 * 9.10 shipped this page with no header at all: the only way to reach the
 * product was the hero's own button, and the only way to reach a SIGN-IN form
 * was to click a sign-up button and then read your way out of it. A returning
 * customer had no door.
 *
 * Sticky rather than merely present, because the page is long by design — the
 * honest-limitations section and the pricing card are both below the fold, and
 * a header that scrolls away takes "Log in" with it exactly when a reader has
 * decided.
 *
 * The nav is two in-page anchors, plain `<a href="#…">`. No router, no scroll
 * library, no motion: the browser already does this correctly, and the
 * animation question is open from a previous epic and is not being answered
 * here by accident.
 */
function LandingHeader({
  onGetStarted,
  onLogIn,
}: {
  onGetStarted?: (() => void) | undefined;
  onLogIn?: (() => void) | undefined;
}): JSX.Element {
  return (
    <header className="sticky top-0 z-10 border-b border-line-hairline bg-surface-ground">
      <div className="mx-auto flex w-full max-w-report flex-wrap items-center gap-4 px-6 py-4">
        {/* The wordmark, not a logo: the licensed-assets rule permits custom-drawn marks
            and licensed fonts only, and no mark has been drawn. Type is the
            honest option, and it is the name `layout.tsx` already ships as the
            document title rather than a second name invented here. */}
        <a
          href="#top"
          className="font-display text-ui-lg tracking-display text-text-primary"
        >
          AI Visibility Platform
        </a>

        <nav aria-label="Sections of this page" className="flex items-center gap-4">
          <a
            href={`#${PRODUCT_ID}`}
            className="text-ui-sm text-text-secondary hover:underline"
          >
            Product
          </a>
          <a
            href={`#${PRICING_ID}`}
            className="text-ui-sm text-text-secondary hover:underline"
          >
            Pricing
          </a>
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {/*
            "Log in" is a button element rather than an anchor because sign-in
            is a view state on this route, not a URL — see `app/page.tsx`. It is
            styled ghost so that the page has exactly one primary action, which
            is the one for people who do not have an account yet.
          */}
          <Button variant="ghost" size="sm" onClick={onLogIn}>
            Log in
          </Button>
          <Button variant="primary" size="sm" onClick={onGetStarted}>
            Get started free
          </Button>
        </div>
      </div>
    </header>
  );
}

function Step({
  n,
  title,
  body,
  index,
  animate,
}: {
  n: string;
  title: string;
  body: string;
  index: number;
  animate: boolean;
}): JSX.Element {
  return (
    // `as="li"` rather than a wrapping div: this sits directly inside an <ol>,
    // and a div between them is invalid markup that breaks list semantics for
    // a screen reader.
    <Reveal as="li" index={index} animate={animate} className="flex gap-4">
      <span className="font-mono text-ui-xs text-text-tertiary">{n}</span>
      <div className="flex flex-col gap-1">
        <p className="text-ui-md font-medium text-text-primary">{title}</p>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">{body}</p>
      </div>
    </Reveal>
  );
}

function Limit({ body }: { body: string }): JSX.Element {
  return (
    <li className="max-w-measure text-ui-base leading-prose text-text-secondary">{body}</li>
  );
}
