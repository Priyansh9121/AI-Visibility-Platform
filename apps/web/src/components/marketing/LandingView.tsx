/**
 * The public landing page — Epic 9.10.
 *
 * The first screen in this product a stranger can reach on purpose. Until now
 * `/` showed a sign-in panel to anyone without a session, so there was nowhere
 * to send a prospective agency to find out what this is.
 *
 * WHAT IS CLAIMED HERE, AND WHY EACH CLAIM IS SAFE TO MAKE
 * -------------------------------------------------------
 * Every number and capability below is one this codebase actually ships, and
 * was verified against the build log before it was written:
 *
 *   - the scan pipeline and its ~6 minutes  — build-log Epic 9.2/9.8, measured
 *   - 24 prompts, intent-tagged            — Epic 4.1
 *   - the five weighted dimensions         — scoring-spec.md, Epic 5.2
 *   - per-answer ordinality (Answer Shelf) — Epic 7.1
 *   - facts-only storage, test-enforced    — ip-safety.md #7, test_ip_safety.py
 *   - named fixes with priority + effort   — Epic 8.0
 *   - one vendor, two modes, today         — Epic 4.2's own stated limitation
 *
 * NOTHING ELSE IS CLAIMED. There are no testimonials, no customer logos, no
 * user counts, no press mentions and no funding line on this page, because
 * this product has none of those things yet and inventing them would be a lie
 * that the trust section immediately below would then contradict. Where a page
 * like this would normally carry social proof, it carries a verifiable
 * statement about the data discipline instead — north-star.md §7's argument
 * that the facts-only rule is a genuine commercial asset, not just hygiene.
 *
 * ip-safety.md #1, #5 and #8: designed and written from the data model, the
 * build log and the product spec only. No competitor site was opened,
 * referenced or paraphrased while building this, and no sentence here is a
 * reworded version of anyone else's marketing copy.
 *
 * ip-safety.md #4: no icon pack, illustration kit or stock imagery. The only
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
  ScoreMeter,
} from '@avp/design-system';
import type { LedgerDimension } from '@avp/design-system';

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

export interface LandingViewProps {
  /** Send the visitor to the sign-in / sign-up surface. */
  onGetStarted?: () => void;
  /** Disable chart motion in tests and static renders. */
  animate?: boolean;
}

export function LandingView({ onGetStarted, animate = true }: LandingViewProps): JSX.Element {
  return (
    <div className="flex flex-col gap-24">
      <PageSection
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
            A scan takes about six minutes.
          </p>
        </div>
      </PageSection>

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
        <ol className="flex flex-col gap-4">
          <Step
            n="01"
            title="Twenty-four questions, tagged by buying stage"
            body="Generated for that specific business — awareness, comparison and bottom-of-funnel — so the result is not one lucky prompt but a spread across how people actually shop."
          />
          <Step
            n="02"
            title="Each question, put to AI answer engines"
            body="Today that is Claude in two modes: what it recalls unprompted, and what it says when it searches the live web and cites sources. Those disagree more often than you would expect, and the difference is itself a finding."
          />
          <Step
            n="03"
            title="Every answer read for facts, never stored as prose"
            body="Was the brand named? Where in the answer? Which sources were cited, and who owns them? Which rivals appeared instead?"
          />
          <Step
            n="04"
            title="A score, a gap, and a fix list"
            body="Five weighted dimensions into one number out of 100, the biggest gap named in points, and specific changes ordered by what they would move."
          />
        </ol>
      </PageSection>

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
              <LuminanceLedger
                subjectName="Example Co"
                dimensions={EXAMPLE_DIMENSIONS}
                animate={animate}
                annotateGap
              />
            </div>
          </CardBody>
        </Card>
      </PageSection>

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
          <Limit body="Reports are shared as a link. There is no PDF export yet." />
          <Limit body="White-labelling covers your agency name. Logo and colour control are not built yet." />
          <Limit body="Access is by conversation while this is in pilot — there is no self-serve plan to sign up to today." />
        </ul>
      </PageSection>

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
            <ScoreMeter score={29} />
            <p className="max-w-measure text-ui-sm text-text-tertiary">
              Example of a score a real prospect might return.
            </p>
          </div>
        </div>
      </PageSection>
    </div>
  );
}

function Step({ n, title, body }: { n: string; title: string; body: string }): JSX.Element {
  return (
    <li className="flex gap-4">
      <span className="font-mono text-ui-xs text-text-tertiary">{n}</span>
      <div className="flex flex-col gap-1">
        <p className="text-ui-md font-medium text-text-primary">{title}</p>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">{body}</p>
      </div>
    </li>
  );
}

function Limit({ body }: { body: string }): JSX.Element {
  return (
    <li className="max-w-measure text-ui-base leading-prose text-text-secondary">{body}</li>
  );
}
