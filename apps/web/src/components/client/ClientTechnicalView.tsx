'use client';

/**
 * A client's Technical screen — Epic 9.22.
 *
 * `TechnicalAudit` and its per-check verdicts have existed since Epic 6 and
 * have only ever appeared folded into the report's fix beat, where they are
 * reduced to the findings that warn or fail. This is the same stored audit read
 * on its own, with nothing new crawled and no new check.
 *
 * THE LEDGER HERE IS EXACT, NOT DECORATIVE
 * ----------------------------------------
 * Technical Foundation is a weighted sum of four components, and the Luminance
 * Ledger's composite is a weighted sum of its dimensions — the same arithmetic.
 * So the column's total lit height IS the sub-score shown beside it, the same
 * correctness condition the report's Ledger has. Verified against six stored
 * audits, every one re-summing to its stored value, including one with an
 * excluded component whose weights redistribute to 40.00 / 26.67 / 33.33.
 *
 * That is why the weights come from `componentWeights` (effective, after
 * redistribution) rather than the nominal 30/20/25/25: nominal weights would
 * draw a column whose height disagreed with the number next to it in exactly
 * the case worth drawing honestly.
 */

import type { JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  LuminanceLedger,
  StatRow,
  StatTile,
  VerdictBar,
} from '@avp/design-system';
import type { BadgeTone } from '@avp/design-system';
import type { AuditCheck, Client, ClientHistory, Me, TechnicalAudit } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import { latestComposite, latestScanId } from '@/components/client/ClientDetailView';
import {
  auditDimensions,
  checkLabel,
  excludedComponents,
  groupChecks,
  num,
  tally,
} from '@/lib/client/technical';

export type TechnicalState =
  | { kind: 'loading' }
  | { kind: 'ready'; client: Client; history: ClientHistory; audit: TechnicalAudit | null }
  | { kind: 'error'; title: string; detail: string };

const STATUS_TONE: Record<string, BadgeTone> = {
  pass: 'success',
  warn: 'warn',
  fail: 'danger',
  not_applicable: 'neutral',
  error: 'danger',
};

const STATUS_LABEL: Record<string, string> = {
  pass: 'Pass',
  warn: 'Warn',
  fail: 'Fail',
  not_applicable: 'N/A',
  error: 'Error',
};

export function ClientTechnicalView({
  state,
  me,
}: {
  state: TechnicalState;
  me: Me | null;
}): JSX.Element {
  if (state.kind !== 'ready') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="technical"
        latestReportScanId={null}
      >
        {state.kind === 'loading' ? (
          <p role="status" className="text-ui-md font-medium text-text-primary">
            Loading this client&rsquo;s technical audit…
          </p>
        ) : (
          <EmptyState
            eyebrow="Technical"
            title={state.title}
            body={state.detail}
            action={
              <Button variant="secondary" onClick={() => window.location.assign('/clients')}>
                Back to clients
              </Button>
            }
          />
        )}
      </ClientSpace>
    );
  }

  const { client, history, audit } = state;
  // `checks` is optional on the wire, so the same `?? []` the body uses. One
  // call, not three: `tally` is pure, but three of them in JSX is three reads
  // of the same list for one row of figures.
  const headline = tally(audit?.checks ?? []);

  return (
    <ClientSpace
      client={client}
      me={me}
      current="technical"
      latestReportScanId={latestScanId(history)}
      latestScore={latestComposite(history)}
      figures={
        audit && audit.status === 'ok' ? (
          /*
            Two figures became four — Epic 9.24. The extra pair is the verdict
            tally the "Every check" section below already computes and renders
            as bars; saying it as numbers at the top means an operator does not
            have to scroll to learn whether this audit needs them. `tally` is
            the same function on the same checks — no second count.

            `figures` rather than `meta`, and the accent rule follows
            ClientDetailView's: Technical foundation is a SCORE and takes no
            categorical hue, because the ramp is already the colour language for
            a score. The three counts beside it do.
          */
          <StatRow min="10rem">
            <StatTile
              label="Technical foundation"
              value={
                num(audit.technicalFoundation) === null
                  ? 'Not scored'
                  : `${num(audit.technicalFoundation)!.toFixed(0)}`
              }
            />
            <StatTile label="Pages crawled" value={String(audit.pagesCrawled)} accent={0} />
            <StatTile label="Passing" value={String(headline.pass)} accent={1} />
            <StatTile
              label="Failing"
              value={String(headline.fail)}
              accent={2}
              emphasis={headline.fail > 0}
            />
          </StatRow>
        ) : undefined
      }
    >
      {audit === null ? <NotAudited /> : <Audited audit={audit} />}
    </ClientSpace>
  );
}

/* ============================== body =============================== */

function Audited({ audit }: { audit: TechnicalAudit }): JSX.Element {
  if (audit.status !== 'ok') {
    return (
      <EmptyState
        eyebrow="Technical"
        title="The site could not be read"
        body="An unreachable site is not a site with a bad technical foundation, so nothing here is scored. The audit records what went wrong rather than guessing."
        note={audit.errorCode ? `Reason: ${audit.errorCode}` : undefined}
      />
    );
  }

  const dimensions = auditDimensions(audit);
  const excluded = excludedComponents(audit);
  const counts = tally(audit.checks ?? []);
  const groups = groupChecks(audit.checks ?? []);
  const score = num(audit.technicalFoundation);

  return (
    <div className="flex flex-col gap-10">
      <section className="grid gap-10 lg:grid-cols-[minmax(0,26rem)_auto]">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <h2 className="max-w-headline font-display text-ed-xs leading-display tracking-display text-text-primary">
              What the site itself contributes
            </h2>
            <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
              Technical Foundation is one of the five scored dimensions, and it is the only
              one measured on this client&rsquo;s own site rather than on what engines said.
              It is a weighted sum of the four parts on the right — so the lit height of that
              column is the score.
            </p>
          </div>

          <VerdictBar
            counts={{
              pass: counts.pass,
              warn: counts.warn,
              fail: counts.fail,
              notApplicable: counts.not_applicable,
            }}
            ariaLabel={`${counts.pass} checks passed, ${counts.warn} warned and ${counts.fail} failed, of ${counts.measured} that returned a verdict.`}
          />

          {excluded.length > 0 && (
            <Card elevation="seated">
              <CardBody>
                {excluded.map((e) => (
                  <div key={e.key} className="flex flex-col gap-2">
                    <p className="flex flex-wrap items-center gap-2 text-ui-base font-medium text-text-primary">
                      {e.label}
                      <Badge tone="neutral">Not measured</Badge>
                    </p>
                    <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
                      {e.copy}
                    </p>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
        </div>

        {dimensions.length > 0 && (
          <LuminanceLedger
            subjectName="Technical foundation"
            dimensions={dimensions}
            height={300}
            annotateGap={false}
            animate={false}
            bounded
          />
        )}
      </section>

      <section className="flex flex-col gap-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line-hairline pb-4">
          <h2 className="text-ui-md font-medium text-text-primary">Every check</h2>
          <p className="text-ui-sm text-text-tertiary">
            {score === null
              ? 'Grouped the way the audit reasons about them.'
              : `${counts.measured} checks returned a verdict. Grouped the way the audit reasons about them.`}
          </p>
        </div>

        <div className="flex flex-col gap-8">
          {groups.map((g) => {
            const gc = tally(g.checks);
            return (
              <div key={g.title} className="flex flex-col gap-3">
                <div className="flex flex-wrap items-baseline justify-between gap-4">
                  <div className="flex flex-col gap-1">
                    <h3 className="text-ui-base font-medium text-text-primary">{g.title}</h3>
                    <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
                      {g.note}
                    </p>
                  </div>
                  {/*
                    This was a width utility at step 40 until Epic 9.24, and
                    that step does not exist here: the Tailwind preset REPLACES
                    the spacing scale rather than extending it, so the class
                    compiled to nothing and this bar has been unconstrained
                    since Epic 9.22 — through a review and a screenshot pass.
                    Step 32 is 8rem, the nearest the scale actually defines to
                    the 10rem intended. The off-scale guard in
                    reportIsolation.test.ts now catches this class of mistake,
                    which is why it exists.
                  */}
                  <div className="w-32">
                    <VerdictBar
                      counts={{ pass: gc.pass, warn: gc.warn, fail: gc.fail }}
                      showLegend={false}
                      ariaLabel={`${g.title}: ${gc.pass} passed, ${gc.warn} warned, ${gc.fail} failed.`}
                    />
                  </div>
                </div>
                <ul className="flex flex-col">
                  {g.checks.map((c) => (
                    <CheckRow key={c.id} check={c} />
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function CheckRow({ check }: { check: AuditCheck }): JSX.Element {
  const value = num(check.value);
  return (
    <li className="flex flex-wrap items-center justify-between gap-4 border-t border-line-hairline py-3 first:border-t-0">
      <span className="flex flex-wrap items-center gap-3">
        <Badge tone={STATUS_TONE[check.status] ?? 'neutral'}>
          {STATUS_LABEL[check.status] ?? check.status}
        </Badge>
        <span className="text-ui-base text-text-primary">{checkLabel(check.checkKey)}</span>
      </span>
      <span className="flex flex-wrap items-center gap-4">
        {value !== null && (
          <span className="font-mono text-ui-sm tabular-nums text-text-secondary">{value}</span>
        )}
        {check.detailCode && (
          // The machine code, small and last — the same treatment ErrorState
          // gives a reference code. Never prose, because the backend never
          // sends prose here.
          <span className="font-mono text-ui-2xs text-text-tertiary">{check.detailCode}</span>
        )}
      </span>
    </li>
  );
}

function NotAudited(): JSX.Element {
  return (
    <EmptyState
      eyebrow="Technical"
      title="This client&rsquo;s site has not been audited"
      body="The audit reads the client's own site — indexability, structured data, page markup and freshness — and produces the Technical Foundation dimension of the score. It runs as part of a scan."
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          Run a scan
        </Button>
      }
    />
  );
}

/* The private `Stat` helper is gone — it is `StatTile` in the design system
   now. See the note in ClientsView.tsx for the condition that was met. */
