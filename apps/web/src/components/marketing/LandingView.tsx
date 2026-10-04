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
 *   - 20 prompts, intent-tagged            — Epic 4.1; 24 until 2026-09-12, when the
 *                                            founder cut to the spec's floor (build log)
 *   - the five weighted dimensions         — scoring-spec.md, Epic 5.2
 *   - per-answer ordinality (Answer Shelf) — Epic 7.1
 *   - facts-only storage, test-enforced    — test_facts_only.py
 *   - named fixes with priority + effort   — Epic 8.0. The step 07 sentence (and the pricing
 *                                            card's), "ordered by the points each would
 *                                            recover, with the effort of each shown beside
 *                                            it", is the SHORT FORM of the rule in
 *                                            `deriveFixes` (launch checklist 3.49, 3.63): a
 *                                            fix for a noindex tag, a `Disallow: /` rule or a
 *                                            missing robots.txt leads and carries no figure,
 *                                            and fixes with no figure follow those that have
 *                                            one. The report's own lead states both
 *   - four vendors, one of them searching  — Epic 21 (five engines) narrowed to four
 *                                            defaults on 2026-09-12 (build log)
 *   - PDF export and a share link          — Epic 9.8 and 9.14, both shipped
 *   - $150/month, 3 seats                  — Epic 9.15 at $29, repriced to $160 on
 *                                            2026-09-22 and to $150 on 2026-10-04
 *                                            (founder decision). A $160 test Price
 *                                            has existed since 2026-09-23; a $150
 *                                            one is the founder's to create (see
 *                                            `PLAN_PRICE_USD`)
 *   - a free trial                         — 2026-10-04, ONLY when the API says the plan
 *                                            has one (`GET /billing/plan`), and only in
 *                                            the pricing card, which reads it. This page
 *                                            types no length, so it cannot say a trial
 *                                            that checkout would not give
 *
 * SENTENCES CORRECTED AGAINST THE CODE — launch checklist 3.63, 2026-10-04
 * ----------------------------------------------------------------------
 * Every sentence here and on the pricing card was read against the code. These were not true,
 * and the tests pin the new wording and assert the old is gone:
 *
 *   - fixes "ordered by what they would move and what they would cost": they are ordered by the
 *     points each would recover, and the effort of each is shown beside it (`deriveFixes` in
 *     `lib/report/derive.ts`, `FixList`); the cost of a fix never ranks anything. The new
 *     sentence is the short form of that rule, as the list of claims above says
 *   - a hand-corrected rival list "survives every later re-detection": no screen starts a
 *     re-detection, and a later scan carries the list forward (`carry_forward_set`)
 *   - rivals found from "the AI engines": one model is asked, not the four engines
 *     (`services/cocitation.py`)
 *   - rivals found from who "ranks for the niche in search": three of the six search queries
 *     name the prospect and three name its field (`services/serp.py`, `build_queries`)
 *   - what the business sells "and to whom": the classifier returns an industry, a niche and a
 *     name, and no audience (`services/classify.py`)
 *   - "the questions buyers actually ask": a model writes the questions (`services/prompts.py`)
 *   - engines that "disagree more often than you would expect": nothing measures how often
 *   - the site "checked for what the engines need", by signals that "decide whether a page can
 *     be quoted at all" and would otherwise be "a separate audit nobody reads": the audit loads
 *     one page, the homepage, reads robots.txt and looks for a sitemap (`services/audit_runner.py`,
 *     `technical_audit.py`), and nothing measures that its signals decide anything about
 *     quoting. Structured data, crawlability and freshness are the inputs to Technical
 *     Foundation, one of the five weighted dimensions: the report's own sentence about it
 *   - tests that "walk every table and every response shape", and then cover "every field the
 *     report can show": `test_facts_only.py` covers a named list of the tables that hold anything
 *     taken from an engine or a site, and sweeps the schemas the report module defines. The
 *     report also shows two free-text fields by design, a fix's title and detail, which are the
 *     product's own wording
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
  compositeScore,
  PageSection,
  Reveal,
  RevealGroup,
  ScoreMeter,
} from '@avp/design-system';
import type { LedgerDimension } from '@avp/design-system';
import { MarketingFooter } from './MarketingFooter';
import { MarketingHeader } from './MarketingHeader';
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
export const EXAMPLE_DIMENSIONS: LedgerDimension[] = [
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
  /**
   * The free trial's length, when the page already asked the API: the home route asks early,
   * beside its session check, and passes the answer down, so the pricing card is drawn with its
   * sentence and does not ask (0 is "no trial"). Left out, the card asks for itself when it
   * mounts. A static render never runs a request, so this is also how a test renders the page
   * as it is once the API has said the trial is this many days. See `PricingCard`.
   */
  initialTrialDays?: number | undefined;
  /**
   * The home route's own request for the plan, handed to the pricing card for the case where the
   * route gave up waiting (it waits half a second at the draw point, as the card sits far below
   * the fold): the card awaits it rather than asking again. Ignored when `initialTrialDays` is
   * given. Round three.
   */
  trialPlan?: Promise<number> | undefined;
}

export function LandingView({
  onGetStarted,
  onLogIn,
  animate = true,
  initialTrialDays,
  trialPlan,
}: LandingViewProps): JSX.Element {
  return (
    <div id="top" className="flex flex-col">
      <MarketingHeader
        sections={{ productId: PRODUCT_ID, pricingId: PRICING_ID }}
        onGetStarted={onGetStarted}
        onLogIn={onLogIn}
      />

      <main className="mx-auto flex w-full max-w-page flex-col gap-24 px-6 py-18">
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
                "A scan usually takes a few minutes" — launch checklist 3.39.
                This said "under five minutes", on the premise that five
                minutes was a scan budget the product enforces. It is not:
                nothing cuts a scan at 300 s (the deadline is
                `MAX_SCAN_DURATION`, forty minutes), and a scan's audit can
                wait up to `SCAN_AUDIT_WAIT_S` (120 s since launch checklist
                3.35; it was 300 s) for the crawler's one browser before it
                runs. Read from the dev database on
                2026-10-03, 9 of 25 finished scans took longer than five
                minutes (median 4.5, longest 7.6); 21 took between two and six.
                So no figure, and "usually", which is what that range supports.
                It is the sentence the dashboard and the intake card use
                (`SCAN_DURATION_SENTENCE`), run on into this one.

                The rest is what the code says today: twenty questions
                (`TARGET_PROMPTS`), four default engines (`DEFAULT_ENGINES`),
                one free scan for a Google sign-in (`FREE_SCAN_ALLOWANCE`,
                `free_scan_requires_google`), the plan's price. ", one at a
                time" is gone: it was never true of the engines, which a scan
                asks together (`engines.ask_all` gathers them, and
                `PROMPT_CONCURRENCY` runs twelve questions at once).
              */}
              A scan usually takes a few minutes — twenty questions, put to four AI
              answer engines. Your first scan is free with a Google sign-in.
              ${PLAN_PRICE_USD}/month after that.
            </p>
          </div>
        </PageSection>

        <div id={PRODUCT_ID} className="flex scroll-mt-18 flex-col gap-24">
          <Reveal animate={animate}>
          <PageSection
            eyebrow="What a scan does"
            heading="It asks the questions your client's buyers would ask."
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
                body="You paste the website. It fetches the homepage and a few key pages, reads the structure and the copy, and works out for itself what the business sells — so the first thing you do is not a form about your prospect's industry."
              />
              <Step
                index={1}
                n="02"
                animate={animate}
                title="Rivals found rather than guessed at"
                body="Two independent signals: who ranks in search, and which brands an AI model names in the same breath as your prospect. Both, deduplicated and ranked — and you can overrule the result, which then stays in place for every later scan."
              />
              <Step
                index={2}
                n="03"
                animate={animate}
                title="Twenty questions, tagged by buying stage"
                body="Generated for that specific business — awareness, comparison and bottom-of-funnel — so the result is not one lucky prompt but a spread across the buying stages."
              />
              <Step
                index={3}
                n="04"
                animate={animate}
                title="Each question, put to AI answer engines"
                body="Today that is Claude, ChatGPT and Gemini answering from what they recall, and Perplexity searching the live web and citing sources. They do not always agree, and where they differ is itself a finding."
              />
              <Step
                index={4}
                n="05"
                animate={animate}
                title="Every answer read for facts, never stored as prose"
                body="Was the brand named? Where in the answer? Which sources were cited, and who owns them? Which rivals appeared instead?"
              />
              {/*
                Launch checklist 3.63, round two. This said "The site itself checked for what the
                engines need" and that these signals "decide whether a page can be quoted at all,
                scored as one of the five dimensions rather than filed as a separate audit nobody
                reads". The audit loads ONE page, the homepage (`audit_runner`, `https://{domain}`),
                and reads robots.txt and looks for a sitemap (`technical_audit._fetch_side_files`).
                Nothing measures that its signals decide whether anything can be quoted, and
                nothing shows the audit would be unread. What the code supports is the report's
                own sentence about the dimension (`FIX_FOR_DIMENSION.technical_foundation`):
                structured data, crawlability and freshness are its inputs. It is one of the five
                weighted dimensions (10% of the score, `scoring-spec.md`).
              */}
              <Step
                index={5}
                n="06"
                animate={animate}
                title="The homepage checked for structured data, crawlability and freshness"
                body="It loads the homepage, reads the site's robots.txt and checks for a sitemap. The result is Technical Foundation, one of the five weighted dimensions of the score."
              />
              {/*
                "Ordered by the points each would recover, with the effort of each shown beside
                it" — launch checklist 3.63. This said "ordered by what they would move and what
                they would cost to do", which is not the order: the report ranks its fixes by the
                points each recovers (a fix for a noindex tag, a `Disallow: /` rule or a missing
                robots.txt first) and shows an effort of S, M or L beside each, which ranks
                nothing. This sentence is the short form of that rule, as the header says.
                `deriveFixes` and `FixList` are the authority, and
                `claimsAgainstCode.test.tsx` holds this sentence and the pricing card's to them.
              */}
              <Step
                index={6}
                n="07"
                animate={animate}
                title="A score, a gap, and a fix list"
                body="Five weighted dimensions into one number out of 100, the biggest gap named in points, and specific changes ordered by the points each would recover, with the effort of each shown beside it."
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
                That is not a policy paragraph. It is enforced by tests over the tables that
                hold anything taken from an engine&apos;s answer or a website, and over the
                report&apos;s own response schemas, so a field that could carry someone
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
            <Limit body="It measures four vendors' models today — Claude, ChatGPT, Gemini and Perplexity — and only Perplexity searches the web as it answers. Google's AI Overviews and Copilot are not measured." />
            <Limit body="White-labelling covers your agency name. Logo and colour control are not built yet." />
            <Limit body="Nothing tracks whether a fix was actually done, or re-measures what it changed. The report ends at the recommendation." />
          </ul>
        </PageSection>
        </Reveal>

        <div id={PRICING_ID} className="scroll-mt-18">
          <Reveal animate={animate}>
          <PageSection
            eyebrow="Pricing"
            // Derived from the constant, never typed a second time: this string
            // said "Twenty-nine dollars" for six days after the card under it
            // said $160 (read live 2026-09-28), which is exactly the drift the
            // constant exists to prevent and the price-consistency test now catches.
            heading={`One plan. $${PLAN_PRICE_USD} a month.`}
            lead={
              <>
                One price, published, with no &ldquo;contact us&rdquo; between you and it. It
                buys the whole product as described above, and not a cut-down tier with
                the useful part held back.
              </>
            }
          >
            <PricingCard
              signedIn={false}
              onGetStarted={onGetStarted}
              initialTrialDays={initialTrialDays}
              trialPlan={trialPlan}
            />
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
              {/*
                The SAME example the worked ledger above draws, so the two
                figures on this page agree: the composite is computed from
                EXAMPLE_DIMENSIONS by the design system's own arithmetic (38),
                and ScoreMeter derives the band label from `visibilityBand`,
                not from a string typed here. Until 2026-09-28 this read 41
                Emerging beside a ledger that summed to 38.
              */}
              <ScoreMeter score={compositeScore(EXAMPLE_DIMENSIONS)} />
              <p className="max-w-measure text-ui-sm text-text-tertiary">
                Example of a score a real prospect might return.
              </p>
            </div>
          </div>
        </PageSection>
        </Reveal>
      </main>

      <MarketingFooter />
    </div>
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
