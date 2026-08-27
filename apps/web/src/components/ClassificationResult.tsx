'use client';

import { useState } from 'react';
import { Badge, Button, Card, CardBody, ErrorState, LoadingState } from '@avp/design-system';
import type { ClientDetail } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';

/**
 * The result of an intake classification.
 *
 * Three distinct outcomes get three distinct treatments, because the whole
 * point of the AMBIGUOUS status is that it must not look like a result:
 *
 *  - classified     -> the conclusion, stated as a sentence
 *  - ambiguous      -> "we could not tell", with the reason and a retry
 *  - unclassifiable -> what went wrong, with a retry
 *
 * A null industry is never rendered as an empty string or a dash-in-a-field
 * that reads like data. Same discipline as a null score in the report.
 *
 * RESTRUCTURED IN EPIC 9.11, and it was not a cosmetic change
 * -----------------------------------------------------------
 * The two failure branches already read as prose. The SUCCESS branch did not:
 * it was a `<dl>` of Industry / Niche / Confidence, which is a field list — the
 * generic-metrics shape ip-safety.md #3 rules against — sitting inside a screen
 * whose other two branches argue in sentences. The screen disagreed with
 * itself about what kind of thing it was.
 *
 * It now leads with the conclusion as a claim, the way the report's beats do,
 * and keeps the supporting facts underneath it.
 *
 * THE CONFIDENCE NUMBER IS DELIBERATELY NO LONGER SHOWN AS A PERCENTAGE
 * ---------------------------------------------------------------------
 * `industryConfidenceScore` is the model's own self-report, and build-log Epic
 * 2.6 Finding 2 measured what it actually does: **0.97, 0.97, 0.97, 0.97, 0.96
 * across five different sites.** Effectively one value, despite the prompt
 * asking for calibration. A number that does not move with its input carries no
 * information, and rendering it as "97%" borrows the authority of a measurement
 * it has not earned — the same error as rendering a null score as a zero.
 *
 * The qualitative label is kept, because the threshold behind it is real and
 * does gate the AMBIGUOUS path. What is dropped is the false precision. This is
 * a presentation fix; the underlying calibration problem is still open and
 * still Epic 2.6's to own.
 */
export function ClassificationResult({
  client,
  onReset,
}: {
  client: ClientDetail;
  onReset: () => void;
}) {
  const status = client.classificationStatus;
  const name = client.brandName ?? client.name;
  const [scan, setScan] = useState<ScanState>({ kind: 'idle' });

  /**
   * Start the scan — Epic 9.12, closing the second dead end Epic 9.11 named.
   *
   * Calls the SAME `api.runScan` the dashboard's re-run calls. There is
   * deliberately no second scan-triggering path: `get_or_create_scan` owns the
   * one-open-scan-per-client invariant (Epic 9.6) and a second client-side
   * route into it would be a second place for that to be got wrong.
   *
   * The disable semantics mirror `RerunButton` in DashboardView: the button
   * goes disabled the instant it is pressed, because a double click is not
   * caught by the server — `get_or_create_scan` reuses an unfinished scan, but
   * the first request has not committed one yet, so a second would buy a second
   * scan's worth of model calls.
   */
  async function start() {
    setScan({ kind: 'starting' });
    try {
      const started = await api.runScan(client.id);
      setScan({ kind: 'started', scanId: started.id });
    } catch (err) {
      setScan({
        kind: 'failed',
        detail:
          err instanceof ApiProblem
            ? err.problem.detail
            : 'The API did not respond. Check that it is running on port 8000.',
      });
    }
  }

  return (
    <Card elevation="raised">
      <CardBody>
        <div className="flex flex-col gap-6">
          <div className="flex items-start justify-between gap-4">
            <p className="font-mono text-ui-xs text-text-tertiary">{client.domain}</p>
            <StatusBadge status={status} />
          </div>

          {status === 'classified' && (
            <div className="flex flex-col gap-4">
              <h2 className="max-w-headline font-editorial text-ed-sm leading-display tracking-display text-text-primary">
                {name} is {article(client.industry)}
                {client.industry}.
              </h2>

              <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
                {client.industryNiche
                  ? `More precisely: ${client.industryNiche}. `
                  : ''}
                This is what every competitor and prompt in the scan will be generated
                from, so it is worth correcting now if it is wrong.
              </p>

              {client.industryConfidence && (
                <p className="text-ui-sm leading-prose text-text-tertiary">
                  Model-reported confidence: {client.industryConfidence}. Shown as a
                  label rather than a score — the underlying number is the model&apos;s
                  own estimate and is not calibrated, so a precise-looking percentage
                  would overstate it.
                </p>
              )}
            </div>
          )}

          {status === 'ambiguous' && (
            <Explain
              heading="We could not classify this business confidently."
              body="The site did not give a clear enough signal. Rather than guess — a wrong
                    industry would skew every competitor and prompt we generate from it — we
                    have left it unset."
              code={client.classificationReasonCode}
            />
          )}

          {status === 'unclassifiable' && (
            <Explain
              heading="We could not read this site."
              body="Nothing was classified. This usually means the site did not load, or the
                    page carried no description of a business."
              code={client.classificationReasonCode}
            />
          )}

          {client.crawl && (
            <div className="border-t border-line-hairline pt-4">
              <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
                What we read
              </p>
              <p className="mt-2 text-ui-sm text-text-secondary">
                {client.crawl.pagesFetched} page
                {client.crawl.pagesFetched === 1 ? '' : 's'} · {client.crawl.wordCount} words
                {client.crawl.schemaTypes.length > 0 &&
                  ` · structured data: ${client.crawl.schemaTypes.slice(0, 4).join(', ')}`}
              </p>
            </div>
          )}

          {/*
            The screen used to end here offering only "Scan another site", so a
            classified business was a dead end and the core loop's next step
            (product-spec.md §3: URL -> detect -> RUN SCAN) was reachable only
            by finding the client again on the dashboard. Epic 9.12 closes that.

            SCANNING IS OFFERED ONLY ON A CLASSIFIED CLIENT. An ambiguous or
            unreadable one has no industry, and the industry is what every
            competitor and prompt is generated from — spending a scan's worth
            of model calls on a guess is the one thing Epic 2.3 built the
            AMBIGUOUS path to prevent.
          */}
          {scan.kind === 'failed' && (
            <ErrorState title="The scan could not be started" detail={scan.detail} />
          )}

          {scan.kind === 'started' ? (
            /*
              Confirmation in place rather than an immediate redirect. A scan
              runs for about six minutes, so navigating away instantly would put
              the action and its consequence on two different screens, with the
              result one row among many. Saying what happened where it happened
              is the same reasoning Epic 9.7 used to make the dashboard announce
              that it is updating rather than silently doing it.
            */
            <div className="flex flex-col gap-4">
              <LoadingState
                message="Scan started."
                steps={[
                  'Finding who this business competes with',
                  'Generating the questions its buyers ask',
                  'Putting each question to the AI answer engines',
                ]}
                hint="This takes about six minutes. You can leave this page — it keeps running."
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  onClick={() => window.location.assign('/dashboard')}
                >
                  Watch it on your dashboard
                </Button>
                <Button variant="secondary" onClick={onReset}>
                  Scan another site
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {status === 'classified' && (
                <Button
                  variant="primary"
                  disabled={scan.kind === 'starting'}
                  onClick={start}
                >
                  {scan.kind === 'starting' ? 'Starting…' : 'Run a scan'}
                </Button>
              )}
              <Button
                variant={status === 'classified' ? 'secondary' : 'primary'}
                onClick={() => window.location.assign('/dashboard')}
              >
                Go to your scans
              </Button>
              <Button variant="secondary" onClick={onReset}>
                Scan another site
              </Button>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

type ScanState =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'started'; scanId: string }
  | { kind: 'failed'; detail: string };

/** "a" or "an", so the sentence reads. Purely grammatical. */
function article(industry: string | null | undefined): string {
  if (!industry) return '';
  return /^[aeiou]/i.test(industry) ? 'an ' : 'a ';
}

function StatusBadge({ status }: { status: ClientDetail['classificationStatus'] }) {
  if (status === 'classified') return <Badge tone="success">Identified</Badge>;
  if (status === 'ambiguous') return <Badge tone="warn">Needs review</Badge>;
  if (status === 'unclassifiable') return <Badge tone="danger">Could not read</Badge>;
  return <Badge tone="neutral">Pending</Badge>;
}

function Explain({
  heading,
  body,
  code,
}: {
  heading: string;
  body: string;
  code: string | null | undefined;
}) {
  return (
    <div>
      <p className="text-ui-md font-medium text-text-primary">{heading}</p>
      <p className="mt-2 max-w-measure text-ui-base leading-prose text-text-secondary">{body}</p>
      {code && <p className="mt-3 font-mono text-ui-2xs text-text-tertiary">Reason: {code}</p>}
    </div>
  );
}
