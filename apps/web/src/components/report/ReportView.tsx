'use client';

/**
 * The narrative report — Epic 7, the first customer-facing surface.
 *
 * The narrative-report rule requires primary screens to read as a narrative report:
 * score -> biggest gap -> proof -> fix -> pitch. Not a metrics-tile dashboard.
 * The structure is not decorative — it is an argument, and each beat only
 * exists because the one before it earned the right to make it.
 *
 * The design-system-only rule: every element here comes from `@avp/design-system`. The
 * Tailwind classes used for layout resolve exclusively to design tokens — the
 * preset REPLACES Tailwind's scales rather than extending them, so an off-system
 * value like `bg-slate-500` does not compile. There is no ad hoc styling on this
 * screen and no way to add any without a build error.
 *
 * The facts-only rule, with new weight: this is the first screen that RENDERS the
 * facts other epics collected. A citation appears as a domain and a link out. A
 * competitor appears as a name and a domain. No engine's prose appears anywhere,
 * and there is none to appear — `engine_results` has no text-bearing column at
 * all, which `test_engine_result_has_no_text_column_for_a_report_to_render`
 * asserts against the actual column types.
 */

import {
  AnswerShelf,
  Badge,
  Beat,
  BEAT_SEQUENCE,
  Button,
  Card,
  CardBody,
  DataTable,
  Evidence,
  FixList,
  LuminanceLedger,
  Prose,
  ReportHeader,
  ReportPage,
  ScoreDisplay,
  VisibilityBadge,
} from '@avp/design-system';
import type { CSSProperties } from 'react';
import type { ReactNode } from 'react';
import type { Report, ReportCompetitorSet } from '@avp/shared-types';
import { ArrowRight } from 'lucide-react';
import { deriveNarrative, gapHeading, scoreHeading } from '@/lib/report/derive';
import {
  DEGRADATION_FLAG,
  DETECTION_STATUS,
  EXCLUSION_REASON,
  VISIBILITY_FLAG,
  checkLabel,
  dimensionLabel,
  engineLabel,
} from '@/lib/report/strings';

export interface ReportViewProps {
  report: Report;
  /** Disable motion in tests and in the print/PDF path. */
  animate?: boolean;
  /**
   * Operator-only chrome rendered under the competitor table — Epic 3's
   * manual override UI.
   *
   * A slot rather than a prop the view acts on, because this component is
   * rendered with `renderToStaticMarkup` and is meant to stay
   * server-renderable for the PDF path; the editor needs hooks. It also keeps
   * the report a document: rendered for a client, no slot is passed and no
   * editing affordance exists.
   */
  competitorEditor?: ReactNode;
  /**
   * Render as a PUBLIC document — Epic 9.8, `/share/{token}`.
   *
   * Omitting `competitorEditor` already removes the only *editing* affordance,
   * which is why this component was almost public-ready by construction. What
   * that reasoning missed is the pitch beat's "Build the proposal" CTA: it is
   * operator chrome (a prospect is not building the proposal), and it has no
   * handler, so to a stranger it reads as a broken button rather than a
   * disabled one.
   *
   * A boolean rather than another slot because there is nothing to put in its
   * place — the public view wants the CTA *gone*, not replaced.
   */
  publicView?: boolean;
}

export function ReportView({
  report,
  animate = true,
  competitorEditor,
  publicView = false,
}: ReportViewProps) {
  const narrative = deriveNarrative(report);
  const subjectName = report.subject.brandName || report.subject.name;
  const scanned = report.scannedAt ?? report.generatedAt;

  return (
    <ReportPage brand={<Brand agency={report.agency} />}>
      <ReportHeader
        subject={subjectName}
        subtitle={`How ${subjectName} appears when buyers ask AI assistants for a recommendation.`}
        meta={
          <>
            <span>{report.subject.domain}</span>
            {report.subject.industry && <span>{report.subject.industry}</span>}
            <span>{`${report.proof.promptsRun} prompts`}</span>
            <span>{`${report.proof.engineCoverage.length} engines`}</span>
            <span>{`Scanned ${formatDate(scanned)}`}</span>
          </>
        }
      />

      <ScoreBeat report={report} narrative={narrative} subjectName={subjectName} animate={animate} />
      <GapBeat report={report} narrative={narrative} subjectName={subjectName} animate={animate} />
      <ProofBeat
        report={report}
        narrative={narrative}
        subjectName={subjectName}
        competitorEditor={competitorEditor}
      />
      <FixBeat report={report} narrative={narrative} />
      <PitchBeat
        report={report}
        narrative={narrative}
        subjectName={subjectName}
        publicView={publicView}
      />
    </ReportPage>
  );
}

type Narrative = ReturnType<typeof deriveNarrative>;

/**
 * The white-label surface — Epic 9.22.
 *
 * Agency identity, never ours: this document is put in front of the agency's
 * prospect under the agency's name.
 *
 * WHAT AN AGENCY MAY CHANGE, AND WHY IT IS THIS LITTLE
 * ----------------------------------------------------
 * A logo and ONE colour, and the colour reaches chrome only — the rule below
 * the masthead, and nothing else. The report's palette is notation rather than
 * decoration: `--avp-vis-*` encodes the score on a monotonic lightness ramp
 * that survives greyscale and colour-vision deficiency, `--avp-competitor-*`
 * is neutral so no rival reads as endorsed or attacked, `--avp-beacon-*` marks
 * the subject being scanned (the prospect, not the agency), and the semantic
 * four say a scan failed. An agency free to recolour any of those changes what
 * the document MEANS.
 *
 * THE ISOLATION IS STRUCTURAL, NOT A CONVENTION. `--avp-agency-accent` is set
 * inline on this element and nowhere above it, so the variable is not in scope
 * for a single beat of the report — an encoded token could not read it even if
 * some future stylesheet asked. If a footer ever wants the accent, scope it to
 * that element too; do NOT hoist this to the page root, which would trade a
 * guarantee for a convenience. `AgencyBranding.test.tsx` asserts it.
 *
 * The logo is an `<img src>` and is never fetched by us. It is agency-supplied
 * and this page is unauthenticated, so the API accepts `https://` only — see
 * `schemas/agency.py`. The PDF deliberately does not carry it (Epic 9.22): the
 * writer has no image support by design, and adding one would reopen a
 * dependency survey that was closed at zero.
 */
function Brand({ agency }: { agency: Report['agency'] }) {
  const accent = agency.accentColor ?? undefined;

  return (
    <div
      className="flex flex-col gap-3"
      style={accent ? ({ '--avp-agency-accent': accent } as CSSProperties) : undefined}
    >
      <div className="flex items-baseline justify-between gap-4">
        {agency.logoUrl ? (
          <img
            src={agency.logoUrl}
            alt={agency.name}
            className="max-h-8 w-auto max-w-[200px] object-contain"
          />
        ) : (
          <span className="text-ui-md font-medium text-text-primary">{agency.name}</span>
        )}
        <span className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
          {`${agency.slug} · AI visibility report`}
        </span>
      </div>
      {/*
        The letterhead rule. The ONLY thing the agency's colour touches, and it
        carries no data — it is a horizontal line under a masthead. Falls back
        to the hairline every unbranded report already draws, so an agency that
        set no colour sees no change at all.
      */}
      <div
        aria-hidden="true"
        className="h-px w-full bg-line-hairline"
        style={accent ? { backgroundColor: 'var(--avp-agency-accent)' } : undefined}
      />
    </div>
  );
}

/**
 * Where the engines disagree — Epic 9.23, Layer 3.
 *
 * Every scan already ran each prompt against three engines across two vendors
 * and stored a mention and a sentiment for each. Nothing read them
 * comparatively until this: the report gave one mention rate, one sentiment,
 * and per-engine counts with nothing said about the difference between them.
 *
 * TWO THINGS THIS MUST NOT SAY
 * ----------------------------
 * It must not report an OUTAGE as a disagreement. An engine that timed out has
 * no opinion, and the projection has already excluded it — every engine named
 * on either side of a split answered the question it is being compared on.
 *
 * AND IT MUST NOT CALL VISIBILITY AGREEMENT A SHARED VERDICT. The splits
 * measure ONE thing: whether each engine named the subject. The first live run
 * (Epic 9.24, front.com) came back with zero splits and three sentiments of
 * 75.00, 58.33 and 41.67 — total agreement on visibility, disagreement on tone
 * in eight of twelve prompts. The copy said "gave the same verdict", which the
 * standings rendered directly beneath it contradicted. It now says what was
 * actually compared and points at the tone rather than speaking for it.
 *
 * And it must not report "there was nothing to compare" as agreement. A scan
 * where one engine was down all run agrees with itself trivially, and rendering
 * that as consensus would make the strongest available claim from the weakest
 * available evidence. `agreementRate` is null in that case and this renders the
 * absence in words instead of a percentage.
 */
function CrossEngineReading({
  report,
  subjectName,
}: {
  report: Report;
  subjectName: string;
}) {
  const cross = report.proof.crossEngine;
  if (!cross || cross.standings.length === 0) return null;

  const splits = cross.splits.length;
  // Whether the headline Mention Rate exists at all changes what the note
  // below has to say — scoring v2 excludes it when a prompt set has no
  // unprompted questions, and then there is no number above to differ from.
  const headline = report.dimensions.find((d) => d.key === 'mention_rate');
  const headlineMeasured = headline?.included !== false;

  return (
    <div>
      <h3 className="text-ui-sm font-medium text-text-primary">Where the engines disagree</h3>

      <p className="mt-2 max-w-measure text-ui-sm leading-prose text-text-secondary">
        {cross.comparablePrompts === 0 ? (
          <>
            Only one engine answered, so there was nothing to compare. That is not the same as
            the engines agreeing — this scan cannot say either way.
          </>
        ) : splits === 0 ? (
          <>
            Every engine that answered named {subjectName} on all{' '}
            {cross.comparablePrompts} comparable questions. They agree on whether{' '}
            {subjectName} appears; the tone each takes is below, and it can differ.
          </>
        ) : (
          <>
            On {splits} of {cross.comparablePrompts} comparable questions, one engine named{' '}
            {subjectName} and another — answering the same question — did not. A buyer who asks
            one assistant sees a different shortlist from a buyer who asks the other.
          </>
        )}
      </p>

      <div className="mt-3 flex flex-col gap-2">
        {cross.standings.map((standing) => (
          <div
            key={standing.engine}
            className="flex items-baseline justify-between gap-4 border-b border-line-hairline pb-2 text-ui-sm"
          >
            <span className="text-text-primary">{engineLabel(standing.engine)}</span>
            <span className="text-text-secondary">
              {standing.answered === 0 ? (
                'Did not answer'
              ) : (
                <>
                  Named {subjectName} in {standing.mentioned} of {standing.answered}
                  {standing.sentiment != null && <> · sentiment {standing.sentiment}</>}
                </>
              )}
            </span>
          </div>
        ))}
      </div>

      {cross.agreementRate != null && (
        <p className="mt-3 text-ui-2xs uppercase tracking-caps text-text-tertiary">
          {`${cross.agreementRate}% agreement across ${cross.comparablePrompts} comparable questions`}
        </p>
      )}

      {/*
        TWO POPULATIONS, ONE PAGE — scoring v2.

        The Mention Rate in the score above counts UNPROMPTED questions only,
        because the product's job is proving a stranger is invisible and a
        question that names the brand cannot show that. These per-engine
        figures deliberately count the WHOLE prompt set, including the
        comparison and bottom-funnel questions that named the subject
        themselves — that is what makes them a reading about the engines
        rather than about discovery, and it is information the score does not
        carry.

        So the two numbers differ, both are right, and a reader comparing them
        has no way to know why. Disclosed rather than reconciled: making these
        awareness-only would delete the thing this section is for.
      */}
      <p className="mt-3 max-w-measure text-ui-xs leading-prose text-text-tertiary">
        {headlineMeasured ? (
          <>
            These per-engine figures are not the Mention Rate in the score above, and will
            usually differ from it. The score counts only questions that did not name{' '}
            {subjectName} — the ones that can show whether a buyer discovers the brand. The
            figures here count every question in the set, including those that named{' '}
            {subjectName} and which an engine will nearly always echo back.
          </>
        ) : (
          <>
            Mention Rate is left out of the score above: this prompt set had no unprompted
            questions, which are the only ones that can show whether a buyer discovers{' '}
            {subjectName}. The figures here count every question, including those that named{' '}
            {subjectName} themselves, so they are not a substitute for it.
          </>
        )}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 01 — SCORE. Where you stand.
// ---------------------------------------------------------------------------

function ScoreBeat({
  report,
  narrative,
  subjectName,
  animate,
}: {
  report: Report;
  narrative: Narrative;
  subjectName: string;
  animate: boolean;
}) {
  const flags = report.score?.degradationFlags ?? [];

  return (
    <Beat id="score" heading={scoreHeading(narrative, subjectName)}>
      {/*
        A two-column grid rather than a flex row: the score block's natural
        width is set by its band copy ("Present, but losing the answer to
        competitors"), which under flex squeezed the explanation beside it to
        about thirty characters a line. An even split keeps both readable.
      */}
      <div className="grid items-start gap-10 sm:grid-cols-2">
        <div className="flex flex-col items-start gap-3">
          <ScoreDisplay score={narrative.composite} animate={animate} />
          {narrative.composite !== null && <VisibilityBadge score={narrative.composite} />}
        </div>
        <Prose>
          {narrative.status === 'not_scored' && (
            <p>
              This scan has run but has not been scored. The engine results below are real; the
              composite is simply not computed yet. Nothing here should be read as a low score.
            </p>
          )}
          {narrative.status === 'insufficient_data' && (
            <p>
              There was not enough data to compute a score for this scan — no engine returned an
              answer to measure. This is a scan that did not complete, not a brand that scored
              badly, and it is deliberately shown as no score rather than as a zero.
            </p>
          )}
          {narrative.status === 'scored' && (
            <p>
              The score is the weighted composite of{' '}
              {narrative.dimensions.length} measured dimensions, drawn below as light: each
              dimension&rsquo;s height is the points it is worth, and its lit portion is the points
              earned. The lit height of the whole column is the score.
            </p>
          )}
          {/*
            A SCORE THAT MOVED BECAUSE THE FORMULA MOVED — scoring v2.

            Rule 5 keeps a row per formula version, so re-scoring a scan under
            v2 leaves the v1.1 row intact and a client who saw last week's
            number sees a different one today. The only reading available to
            them without this note is "the score dropped", which is a claim
            about their business. The true one is "the definition changed",
            which is a claim about ours. Shown near the number rather than in a
            footnote, and only when a re-score actually happened.
          */}
          {(report.score?.previousFormulaVersions?.length ?? 0) > 0 && (
            <p className="mt-4 text-ui-sm leading-prose text-text-tertiary">
              This scan has also been scored under an earlier definition
              {report.score?.previousFormulaVersions
                ?.map((v) => ` (${v})`)
                .join('')}
              . The figure above is the current one. Where they differ, the measurement
              changed rather than the site — earlier versions counted questions that named
              the brand toward Mention Rate, and this one counts only questions that did not.
              Both scores are kept.
            </p>
          )}
          {/*
            What the composite says in one number, said in words — and the one
            case where a single number genuinely cannot carry it. Above the
            degradation flags because it is a finding rather than a caveat.
          */}
          {report.visibilityFlags?.map((flag) => (
            <p key={flag} className="mt-4 text-ui-sm leading-prose text-text-secondary">
              {VISIBILITY_FLAG[flag] ?? flag}
            </p>
          ))}
          {narrative.exclusions.length > 0 && <ExclusionNote narrative={narrative} />}
          {flags.length > 0 && (
            <ul className="mt-4 flex flex-col gap-2 text-ui-sm text-text-tertiary">
              {flags.map((flag) => (
                <li key={flag}>{DEGRADATION_FLAG[flag] ?? flag}</li>
              ))}
            </ul>
          )}
        </Prose>
      </div>
    </Beat>
  );
}

/**
 * Excluded dimensions, with the reasons kept apart.
 *
 * Epic 5's build log is explicit that `NOT_YET_MEASURED`, `NO_POPULATION` and
 * `NO_COMPETITOR_SET` are distinct codes because they are distinct facts, and a
 * viewer has to be able to tell them apart: one is a capability we have not
 * built, one is an absence of data about the brand, one is a detection that
 * came back empty. Rendering all three as a generic "not scored" chip would
 * throw away the entire point of storing the reason.
 */
function ExclusionNote({ narrative }: { narrative: Narrative }) {
  const reasons = new Set(narrative.exclusions.map((e) => e.reason));

  // When the whole score is excluded for one reason — a scan where no engine
  // answered, say — repeating the identical paragraph five times reads as a
  // fault in the page rather than a fact about the scan. State it once and
  // list what it applied to.
  if (reasons.size === 1 && narrative.exclusions.length > 1) {
    const reason = [...reasons][0]!;
    const copy = EXCLUSION_REASON[reason];
    return (
      <Card elevation="flat" className="mt-5">
        <CardBody>
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
            Left out of this score
          </p>
          <p className="mt-3 flex flex-wrap items-center gap-2 text-ui-base font-medium text-text-primary">
            Every dimension
            <Badge tone="neutral">{copy?.label ?? reason}</Badge>
          </p>
          <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
            {copy?.detail ??
              'These dimensions were excluded from the score and their weight redistributed.'}
          </p>
          <p className="mt-3 text-ui-sm text-text-tertiary">
            {narrative.exclusions.map((e) => `${e.label} (${e.weight}%)`).join(' · ')}
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card elevation="flat" className="mt-5">
      <CardBody>
        <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
          Left out of this score
        </p>
        <dl className="mt-3 flex flex-col gap-4">
          {narrative.exclusions.map((exclusion) => {
            const copy = EXCLUSION_REASON[exclusion.reason];
            return (
              <div key={exclusion.key} className="flex flex-col gap-1">
                <dt className="flex flex-wrap items-center gap-2 text-ui-base font-medium text-text-primary">
                  {exclusion.label}
                  <Badge tone="neutral">{copy?.label ?? exclusion.reason}</Badge>
                  <span className="text-ui-xs font-normal text-text-tertiary">
                    {`normally ${exclusion.weight}% of the score`}
                  </span>
                </dt>
                <dd className="text-ui-sm leading-prose text-text-secondary">
                  {copy?.detail ??
                    'This dimension was excluded from the score and its weight redistributed.'}
                </dd>
              </div>
            );
          })}
        </dl>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 02 — GAP. The biggest gap. Computed, never authored.
// ---------------------------------------------------------------------------

function GapBeat({
  report,
  narrative,
  subjectName,
  animate,
}: {
  report: Report;
  narrative: Narrative;
  subjectName: string;
  animate: boolean;
}) {
  if (narrative.dimensions.length === 0) {
    return (
      <Beat id="gap" heading="No gap can be measured until there is a score.">
        <Prose>
          <p>
            The biggest gap is the dimension with the most recoverable points, which needs
            sub-scores to compute. Once this scan produces engine results that can be scored, this
            section names the single dimension worth attacking first.
          </p>
        </Prose>
      </Beat>
    );
  }

  const gap = narrative.biggestGap;
  const runnersUp = narrative.segments
    .filter((s) => !s.isBiggestGap && s.gap > 0.5)
    .sort((a, b) => b.gap - a.gap)
    .slice(0, 2);

  return (
    <Beat id="gap" heading={gapHeading(narrative)}>
      <Prose>
        <p>
          Each dimension is drawn at the height of its weight, so the unlit area is exactly the
          points still available. {gap ? (
            <>
              The largest unlit block is <strong>{gap.label}</strong>, scoring{' '}
              {Math.round(gap.subscore)} out of 100 on a dimension worth {gap.weight} points —{' '}
              {gap.gap.toFixed(1)} points left on the table. That is more than any other dimension
              offers, which is what makes it the place to start rather than the lowest number on
              the page.
            </>
          ) : (
            <>Every dimension is close to fully lit; there is no single gap worth naming.</>
          )}
        </p>
        {runnersUp.length > 0 && (
          <p>
            Behind it:{' '}
            {runnersUp
              .map((s) => `${s.label} (${s.gap.toFixed(1)} points)`)
              .join(', ')}
            .
          </p>
        )}
      </Prose>

      <div className="mt-6">
        <LuminanceLedger
          subjectName={subjectName}
          dimensions={narrative.dimensions}
          height={360}
          animate={animate}
        />
      </div>

      {/*
        Competitors are NOT drawn as ghost columns here, though the component
        supports them. A ghost column is a competitor COMPOSITE, and competitor
        sub-scores cover only three of the five dimensions — sentiment is
        classified toward the subject only and the technical audit is of the
        subject's own site. A composite over 75% of the weight would render every
        rival shorter than they are. The comparison is made per-dimension in the
        proof beat instead, where both sides have a real figure.
      */}
      {(report.competitorSet?.competitors.length ?? 0) > 0 && (
        <p className="mt-4 text-ui-sm leading-prose text-text-tertiary">
          Competitors are compared dimension by dimension in the next section rather than as a
          single rival score. Sentiment and technical checks are measured for {subjectName} only,
          so a combined competitor score would be built on a different basis and would not be a
          fair comparison.
        </p>
      )}

      {narrative.warnings.length > 0 && (
        <ul className="mt-4 flex flex-col gap-2 text-ui-sm text-text-tertiary">
          {narrative.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </Beat>
  );
}

// ---------------------------------------------------------------------------
// 03 — PROOF. The evidence. Facts only.
// ---------------------------------------------------------------------------

function ProofBeat({
  report,
  narrative,
  subjectName,
  competitorEditor,
}: {
  report: Report;
  narrative: Narrative;
  subjectName: string;
  competitorEditor?: ReactNode;
}) {
  const { proof } = report;
  const detection = report.competitorSet
    ? DETECTION_STATUS[report.competitorSet.status as keyof typeof DETECTION_STATUS]
    : undefined;
  const ahead = narrative.aheadOnDimensions.slice(0, 4);

  return (
    <Beat id="proof" heading={proofHeading(report, subjectName)}>
      <Prose>
        <p>
          Every figure below was extracted from an answer at the time it was given. The answers
          themselves are not kept — this system records what was asked, who was named, in what
          order, and which sources were cited, and discards the prose. Nothing on this page
          reproduces an engine&rsquo;s words or a competitor&rsquo;s.
        </p>
      </Prose>

      <div className="mt-6 flex flex-col gap-6">
        {/* --- the answer shelf (Epic 7.1, Direction A) --------------------- */}
        {/*
          Additive. The tables below are unchanged and still carry the
          aggregates — how often overall, best placement, who is cited. This
          answers what they structurally cannot: in THIS question, who stood
          where, and was the subject there at all.
        */}
        {proof.promptShelf && proof.promptShelf.length > 0 && (
          <AnswerShelf
            subjectName={subjectName}
            rows={proof.promptShelf.map((row) => ({
              promptId: row.promptId,
              promptText: row.promptText,
              promptPosition: row.promptPosition,
              engine: engineLabel(row.engine),
              answered: row.answered,
              subjectPresent: row.subjectPresent,
              subjectPosition: row.subjectPosition ?? null,
              subjectCited: row.subjectCited,
              slots: row.slots.map((slot) => ({
                position: slot.position,
                entityName: slot.entityName,
                entityDomain: slot.entityDomain ?? null,
                isSubject: slot.isSubject,
                competitorName: slot.competitorName ?? null,
                cited: slot.cited,
              })),
            }))}
            title={shelfTitle(report, subjectName)}
            caption={shelfCaption(report, subjectName)}
          />
        )}

        {/* --- who is ahead, per dimension --------------------------------- */}
        {report.competitorSet && report.competitorSet.competitors.length > 0 ? (
          <div>
            <h3 className="text-ui-sm font-medium text-text-primary">Rivals in the same answers</h3>
            {detection && report.competitorSet.status !== 'ok' && (
              <p className="mt-2 text-ui-sm leading-prose text-text-tertiary">
                <Badge tone="warn">{detection.label}</Badge>{' '}
                <span className="ml-2">{detection.detail}</span>
              </p>
            )}
            <CorroborationNote competitorSet={report.competitorSet} />
            <DataTable
              className="mt-3"
              caption={`How ${subjectName} compares on the dimensions measured for both sides`}
              rows={competitorRows(report, subjectName)}
              rowKey={(row) => row.id}
              isSubject={(row) => row.isSubject}
              columns={[
                {
                  key: 'name',
                  header: 'Brand',
                  render: (row) =>
                    row.isManual ? (
                      <>
                        {row.name} <Badge tone="neutral">set by hand</Badge>
                      </>
                    ) : (
                      row.name
                    ),
                },
                { key: 'domain', header: 'Domain', render: (row) => row.domain ?? '—' },
                {
                  key: 'mention',
                  header: 'Mention rate',
                  align: 'end',
                  render: (row) => fmt(row.mentionRate),
                },
                {
                  key: 'sov',
                  header: 'Share of voice',
                  align: 'end',
                  render: (row) => fmt(row.shareOfVoice),
                },
                {
                  key: 'citations',
                  header: 'Citation strength',
                  align: 'end',
                  render: (row) => fmt(row.citationStrength),
                },
              ]}
            />
            {ahead.length > 0 && (
              <p className="mt-3 text-ui-sm leading-prose text-text-secondary">
                {ahead
                  .map(
                    (a) =>
                      `${a.competitorName} leads on ${dimensionLabel(a.dimensionKey)} by ${a.delta.toFixed(1)}`,
                  )
                  .join('; ')}
                .
              </p>
            )}
            {(report.competitorSet?.competitors ?? []).some((c) => c.isManualOverride) && (
              <p className="mt-3 text-ui-sm leading-prose text-text-tertiary">
                Rivals marked <em>set by hand</em> were named by the agency rather than found by
                the two automated signals. They are measured exactly like any other.
              </p>
            )}
            {competitorEditor}
          </div>
        ) : (
          <Card elevation="flat">
            <CardBody>
              <p className="text-ui-base font-medium text-text-primary">
                {DETECTION_STATUS.no_signal.label}
              </p>
              <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
                {DETECTION_STATUS.no_signal.detail}
              </p>
            </CardBody>
          </Card>
        )}

        {/* --- where the engines disagree (Epic 9.23, Layer 3) -------------- */}
        {/*
          Placed ABOVE the per-engine counts below, which it reads against.
          "Answered 24 of 24" is coverage; this is the finding — the same buyer
          question answered with the subject by one assistant and without it by
          another, which is the thing no single-engine product can see.
        */}
        <CrossEngineReading report={report} subjectName={subjectName} />

        {/* --- engine coverage ---------------------------------------------- */}
        {proof.engineCoverage.length > 0 && (
        <div>
          <h3 className="text-ui-sm font-medium text-text-primary">What each engine returned</h3>
          <div className="mt-3 flex flex-col gap-3">
            {proof.engineCoverage.map((coverage) => (
              <Evidence
                key={coverage.engine}
                engine={engineLabel(coverage.engine)}
                prompt={`${coverage.promptsRun} prompts from this scan's generated set`}
                findings={[
                  { label: 'Answered', value: `${coverage.answered} of ${coverage.promptsRun}` },
                  {
                    label: `Named ${subjectName}`,
                    value: `${coverage.mentioned} of ${coverage.answered}`,
                  },
                ]}
              />
            ))}
          </div>
        </div>
        )}

        {/* --- citations ----------------------------------------------------- */}
        <div>
          <h3 className="text-ui-sm font-medium text-text-primary">Sources these answers cited</h3>
          <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
            {proof.subjectCitations > 0 ? (
              <>
                {proof.subjectCitations} of {proof.totalCitations} citations pointed at{' '}
                {subjectName}&rsquo;s own pages.
              </>
            ) : (
              <>
                None of the {proof.totalCitations} citations in this scan pointed at{' '}
                {subjectName}&rsquo;s own pages.
              </>
            )}{' '}
            Each source is shown as the domain that was cited, linked to the page itself — the
            cited page&rsquo;s content is never read or reproduced here.
          </p>
          <CitationTable
            title={`Cited ${subjectName}`}
            rows={proof.subjectCitedDomains}
            emptyMessage={`No answer cited ${subjectName}'s own pages.`}
          />
          <CitationTable
            title="Cited instead"
            rows={proof.competitorCitedDomains}
            emptyMessage="No other domains were cited."
          />
        </div>

        {/* --- mention share ------------------------------------------------- */}
        {proof.mentionShares.length > 0 && (
          <div>
            <h3 className="text-ui-sm font-medium text-text-primary">Who got named</h3>
            <DataTable
              className="mt-3"
              caption="Brands named across the answered prompts"
              rows={proof.mentionShares}
              rowKey={(row) => row.entityName}
              isSubject={(row) => row.isSubject}
              columns={[
                { key: 'name', header: 'Brand', render: (row) => row.entityName },
                { key: 'domain', header: 'Domain', render: (row) => row.entityDomain ?? '—' },
                {
                  key: 'appearances',
                  header: 'Answers naming it',
                  align: 'end',
                  render: (row) => String(row.appearances),
                },
                {
                  key: 'position',
                  header: 'Best position',
                  align: 'end',
                  render: (row) => (row.bestPosition === null ? '—' : `#${row.bestPosition}`),
                },
              ]}
            />
          </div>
        )}

        {/* --- audit --------------------------------------------------------- */}
        <AuditEvidence report={report} />
      </div>
    </Beat>
  );
}

/**
 * The shelf's title. A finding, not a chart label — ChartFrame's `title` is
 * documented as "short, declarative, reads as a finding".
 *
 * **COUNTED OVER THE SCAN, NOT OVER THE TABLE** — 2026-09-08. The shelf is
 * capped at `MAX_SHELF_PROMPTS` (20 prompts, so 40 rows on a two-engine scan),
 * and this sentence used to count the rows it could see while saying "this
 * scan measured". The pilot dry run caught it saying *"missing from 31 of the
 * 40 answers this scan measured"* about a scan that measured 48.
 *
 * The scan-wide figures are the true finding and are the ones a reader should
 * carry away, so the title states those and the caption says the table below
 * is a sample. The other way round — an honest "of the 40 shown" — is
 * self-consistent but understates the result, and the headline of a beat
 * should not be the smaller number.
 */
function shelfTitle(report: Report, subjectName: string): string {
  const { answeredResults, resultsMentioningSubject } = report.proof;
  const absent = answeredResults - resultsMentioningSubject;
  if (answeredResults === 0) return 'No engine returned an answer to place anyone in.';
  if (absent === 0) {
    return `${subjectName} appears in every answer this scan measured.`;
  }
  if (absent === answeredResults) {
    return `${subjectName} appears in none of the ${answeredResults} answers this scan measured.`;
  }
  return `${subjectName} is missing from ${absent} of the ${answeredResults} answers this scan measured.`;
}

/**
 * The shelf's caption, which has to say when the table is a sample.
 *
 * The title above it counts the whole scan; the rows are capped. Saying so is
 * what keeps a reader who counts the rows from finding a discrepancy nobody
 * explained — the same reason the cross-engine section states its own
 * population.
 */
function shelfCaption(report: Report, subjectName: string): string {
  const shown = (report.proof.promptShelf ?? []).length;
  const total = report.proof.engineResults;
  const scope =
    shown < total
      ? ` The first ${shown} of ${total} answers are shown; the count above is the whole scan.`
      : '';
  return (
    `One row per answer, with the brands it named in the order it named them.${scope}` +
    ` A dashed ring is an answer that named other brands and not ${subjectName};` +
    ' a tick beneath a marker means that same answer cited it.'
  );
}

function proofHeading(report: Report, subjectName: string): string {
  const { proof } = report;
  if (proof.answeredResults === 0) return 'No engine returned an answer to measure.';
  if (proof.subjectCitations === 0 && proof.totalCitations > 0) {
    return `Answers cite ${proof.totalCitations} sources. None of them are ${subjectName}.`;
  }
  const ahead = report.proof.mentionShares.filter((m) => m.outranksSubject).length;
  if (ahead > 0) {
    return `${ahead} ${ahead === 1 ? 'rival is' : 'rivals are'} named more often than ${subjectName} in the same answers.`;
  }
  return 'Here is what the engines actually returned.';
}

function CitationTable({
  title,
  rows,
  emptyMessage,
}: {
  title: string;
  rows: Report['proof']['subjectCitedDomains'];
  emptyMessage: string;
}) {
  return (
    <DataTable
      className="mt-4"
      caption={title}
      rows={rows}
      rowKey={(row) => `${row.domain}:${row.competitorName ?? ''}`}
      isSubject={(row) => row.citesSubject}
      emptyMessage={emptyMessage}
      columns={[
        {
          key: 'domain',
          header: 'Domain',
          // A link out is the sanctioned way to evidence a source without
          // reproducing it (the facts-only rule). The link text is the domain —
          // itself a fact — never the page's title.
          render: (row) =>
            row.sampleUrl ? (
              <a
                href={row.sampleUrl}
                rel="noreferrer nofollow"
                target="_blank"
                className="text-beacon-700 underline underline-offset-2"
              >
                {row.domain}
              </a>
            ) : (
              row.domain
            ),
        },
        {
          key: 'who',
          header: 'Attributed to',
          render: (row) => row.competitorName ?? (row.citesSubject ? 'Subject' : 'Third party'),
        },
        {
          key: 'count',
          header: 'Citations',
          align: 'end',
          render: (row) => String(row.citations),
        },
      ]}
    />
  );
}

function AuditEvidence({ report }: { report: Report }) {
  const { audit } = report;

  if (!audit) {
    return (
      <Card elevation="flat">
        <CardBody>
          <p className="text-ui-base font-medium text-text-primary">The site has not been audited</p>
          <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
            No technical audit has been run for this scan, so the structural half of the score has
            not been measured. That is a check that has not happened, not a check that failed.
          </p>
        </CardBody>
      </Card>
    );
  }

  if (audit.status === 'failed') {
    return (
      <Card elevation="flat">
        <CardBody>
          <p className="flex items-center gap-3 text-ui-base font-medium text-text-primary">
            The site could not be read
            <Badge tone="warn">Audit failed</Badge>
          </p>
          <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
            The crawl of {audit.urlAudited ?? 'the site'} did not complete
            {audit.errorCode ? ` (${audit.errorCode})` : ''}, so no structural signals were
            collected. A site we could not reach is not a site with a bad technical foundation —
            the dimension is left unscored rather than scored zero.
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <div>
      <h3 className="flex items-center gap-3 text-ui-sm font-medium text-text-primary">
        What the site itself says
        {audit.status === 'partial' && <Badge tone="warn">Partial audit</Badge>}
      </h3>
      <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
        {audit.passed} checks passed, {audit.warned} raised a warning and {audit.failed} failed
        {audit.notApplicable > 0 ? `; ${audit.notApplicable} could not apply` : ''}.
        {audit.status === 'partial' &&
          ' Some signals were unavailable, so the sub-score was computed from what could be measured.'}
      </p>
      {audit.findings.length > 0 && (
        <DataTable
          className="mt-3"
          caption="Checks that did not pass"
          rows={audit.findings}
          rowKey={(row) => row.checkKey}
          columns={[
            { key: 'check', header: 'Check', render: (row) => checkLabel(row.checkKey) },
            {
              key: 'status',
              header: 'Result',
              render: (row) => (
                <Badge tone={row.status === 'fail' || row.status === 'error' ? 'danger' : 'warn'}>
                  {row.status}
                </Badge>
              ),
            },
          ]}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 04 — FIX. What to change.
// ---------------------------------------------------------------------------

function FixBeat({ report, narrative }: { report: Report; narrative: Narrative }) {
  if (narrative.fixes.length === 0) {
    return (
      <Beat id="fix" heading="There is nothing specific to fix from this scan yet.">
        <Prose>
          <p>
            {report.audit
              ? 'Every measured dimension is close to fully lit and the technical audit found nothing to correct.'
              : 'The fix list is built from measured gaps and audit findings. Neither is available for this scan yet, so no recommendation would be more than a guess.'}
          </p>
        </Prose>
      </Beat>
    );
  }

  return (
    <Beat id="fix" heading={fixHeading(narrative)}>
      <Prose>
        <p>
          Ordered by the points each one recovers, not by how quickly it can be done. The point
          figures come from the same arithmetic that drew the chart above: a dimension&rsquo;s gap
          is its weight multiplied by how far short of 100 it scored. Fixes drawn from the
          technical audit name the exact check that did not pass.
        </p>
      </Prose>
      <div className="mt-5">
        <FixList
          items={narrative.fixes.map((fix) => ({
            id: fix.id,
            title: fix.title,
            detail: fix.detail,
            priority: fix.priority,
            effort: fix.effort,
            // Spread conditionally: an audit fix has no measurable point value,
            // and FixList renders the chip only when the key is absent — not
            // when it is present and undefined.
            ...(fix.pointsUpside === undefined ? {} : { pointsUpside: fix.pointsUpside }),
          }))}
        />
      </div>
      {narrative.fixes.some((f) => f.source === 'audit') && (
        <p className="mt-4 text-ui-sm leading-prose text-text-tertiary">
          Audit fixes carry no point figure. They feed the Technical Foundation dimension, but this
          system does not measure how much any single check moves it, and an invented number would
          be worse than none.
        </p>
      )}
      {narrative.fixes.some((f) => f.generated) && (
        <p className="mt-3 text-ui-sm leading-prose text-text-tertiary">
          {narrative.fixes.every((f) => f.generated)
            ? 'The wording, priority and effort above were written for this scan from its own figures.'
            : 'Where a change was drawn from a measured gap or an audit finding, its wording, priority and effort were written for this scan from its own figures.'}{' '}
          Which changes appear, their order, and the points beside them are unchanged arithmetic
          &mdash; the same gap calculation that drew the chart.
        </p>
      )}
    </Beat>
  );
}

function fixHeading(narrative: Narrative): string {
  const withPoints = narrative.fixes.filter((f) => f.pointsUpside);
  if (withPoints.length > 0 && narrative.recoverablePoints > 0) {
    return `${narrative.fixes.length} changes, worth ${narrative.recoverablePoints.toFixed(1)} points.`;
  }
  return `${narrative.fixes.length} specific changes the audit named.`;
}

// ---------------------------------------------------------------------------
// 05 — PITCH. The opportunity. Arithmetic, not adjectives.
// ---------------------------------------------------------------------------

/**
 * The pitch beat.
 *
 * Everything asserted here is arithmetic over figures already on the page: the
 * points the listed fixes recover, the composite that would result, and the
 * per-dimension distance to named rivals. There is no revenue estimate, no
 * traffic projection and no urgency language, because nothing in this system
 * measures any of those — and a number a client can dispute costs more than it
 * wins. Product-owner call, recorded in build-log.md Epic 7.
 */
function PitchBeat({
  report,
  narrative,
  subjectName,
  publicView = false,
}: {
  report: Report;
  narrative: Narrative;
  subjectName: string;
  publicView?: boolean;
}) {
  const canProject =
    narrative.status === 'scored' &&
    narrative.composite !== null &&
    narrative.potentialComposite !== null &&
    narrative.recoverablePoints > 0;

  return (
    <Beat id="pitch" heading={pitchHeading(narrative, subjectName)}>
      <Prose>
        {canProject ? (
          <p>
            {subjectName} scores {Math.round(narrative.composite!)} today. Closing the gaps listed
            above recovers {narrative.recoverablePoints.toFixed(1)} points, which is a composite of{' '}
            {Math.round(narrative.potentialComposite!)}. That figure is the same weighted sum as
            the score itself — it is what the score becomes if those dimensions reach 100, not a
            forecast.
          </p>
        ) : (
          <p>
            There is no projection to make from this scan. A recoverable-points figure is only
            meaningful once there is a score to add it to, and this scan does not have one yet.
          </p>
        )}

        {narrative.aheadOnDimensions.length > 0 && (
          <p>
            The comparison that matters to a buyer is per-dimension:{' '}
            {narrative.aheadOnDimensions[0]!.competitorName} currently leads on{' '}
            {dimensionLabel(narrative.aheadOnDimensions[0]!.dimensionKey)} by{' '}
            {narrative.aheadOnDimensions[0]!.delta.toFixed(1)} points. Every dimension in this
            report is measured the same way for both sides, so that distance is checkable rather
            than asserted.
          </p>
        )}

        <p>
          This report is a point-in-time measurement, reproducible from the same inputs. Re-running
          it after the changes above shows the movement directly
          {report.score?.inputsDigest
            ? ` — the inputs behind this score are fingerprinted as ${report.score.inputsDigest.slice(0, 12)}…, so a later score can be attributed to changed inputs rather than a changed method`
            : ''}
          .
        </p>
      </Prose>

      {!publicView && (
        <div className="mt-6 flex flex-wrap gap-3">
          <Button variant="primary" iconEnd={<ArrowRight />}>
            Build the proposal
          </Button>
        </div>
      )}

      <p className="mt-6 text-ui-2xs uppercase tracking-caps text-text-tertiary">
        {`Beats ${BEAT_SEQUENCE.join(' · ')}`}
      </p>
    </Beat>
  );
}

function pitchHeading(narrative: Narrative, subjectName: string): string {
  if (
    narrative.status === 'scored' &&
    narrative.composite !== null &&
    narrative.potentialComposite !== null &&
    narrative.recoverablePoints > 0
  ) {
    return `${Math.round(narrative.composite)} today. ${Math.round(narrative.potentialComposite)} with the fixes above.`;
  }
  return `What a complete scan would tell you about ${subjectName}.`;
}

// ---------------------------------------------------------------------------

function competitorRows(report: Report, subjectName: string) {
  const subject = report.score;
  const rows = [
    {
      id: 'subject',
      name: subjectName,
      domain: report.subject.domain,
      isSubject: true,
      isManual: false,
      mentionRate: subject?.mentionRate ?? null,
      shareOfVoice: subject?.shareOfVoice ?? null,
      citationStrength: subject?.citationStrength ?? null,
    },
    ...(report.competitorSet?.competitors ?? []).map((competitor) => ({
      id: competitor.id,
      name: competitor.name,
      domain: competitor.domain,
      isSubject: false,
      // Provenance, same discipline as detectionConfidence and corroborated:
      // a rival an operator named is a different kind of claim from one two
      // automated signals agreed on, and the table should say which it is.
      isManual: competitor.isManualOverride,
      mentionRate: competitor.mentionRate ?? null,
      shareOfVoice: competitor.shareOfVoice ?? null,
      citationStrength: competitor.citationStrength ?? null,
    })),
  ];
  return rows;
}

/**
 * The corroboration figure, and — the part that matters — how much of the list
 * it actually describes.
 *
 * `detectionConfidence` is the share of the rivals DETECTION ranked that both
 * signals surfaced independently. Rows an operator set by hand were surfaced by
 * neither, so on a corrected set the figure covers only part of the table. It
 * was previously not rendered at all, while the note under the table referred
 * the reader to a "corroboration figure above" that was never on the page —
 * Finding 3 in api-contracts.md.
 *
 * Rendering it without the count would be worse than not rendering it: "80%"
 * over a six-row table two of whose rows nothing corroborated reads as a claim
 * about all six. `confidenceCovers` comes from the API rather than being
 * counted here, so the number the report prints is the number the projection
 * scoped it to.
 */
function CorroborationNote({ competitorSet }: { competitorSet: ReportCompetitorSet }) {
  const { detectionConfidence, confidenceCovers } = competitorSet;
  // `== null` deliberately: the generated type is `string | null | undefined`,
  // since the field is optional in the schema. A `=== null` check narrows only
  // half of that and renders "NaN%" on the other half.
  if (detectionConfidence == null || confidenceCovers === 0) return null;

  const share = Number.parseFloat(detectionConfidence);
  if (!Number.isFinite(share)) return null;

  const shown = competitorSet.competitors.length;
  const rival = confidenceCovers === 1 ? 'rival' : 'rivals';

  return (
    <p className="mt-2 text-ui-sm leading-prose text-text-tertiary">
      Search results and AI answers independently surfaced{' '}
      <strong className="font-medium text-text-secondary">{Math.round(share * 100)}%</strong> of the{' '}
      {confidenceCovers} {rival} detection found
      {confidenceCovers < shown
        ? `. The remaining ${shown - confidenceCovers} of the ${shown} below were set by hand and are not in that figure.`
        : '.'}
    </p>
  );
}

/** A null sub-score renders as an em dash, never as a zero. */
function fmt(value: string | null): string {
  if (value === null) return '—';
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed.toFixed(1) : '—';
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}
