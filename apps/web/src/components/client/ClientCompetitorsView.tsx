'use client';

/**
 * A client's Competitors screen — Epic 13.
 *
 * WHAT IT SHOWS
 * -------------
 * The field from the latest scan, one column per brand: the client first,
 * then every rival in the scan's competitor set, in detection rank. Each
 * column is a compact Luminance Ledger — the product's own chart, at a third
 * of its width — so the comparison is made in the shape the score is drawn
 * in, segment against segment, rather than in a table of six-decimal figures.
 *
 * THE CLAIM IT REFUSES TO MAKE
 * ----------------------------
 * A rival has no composite, and this screen does not invent one. Sentiment
 * is classified toward the client only and the technical audit is of the
 * client's own site, so a rival is measured on three of the five dimensions.
 * The report says this in a sentence under its ledger and compares
 * per-dimension in its proof beat; the Rankings tab plots share of voice for
 * the same reason. Here the rule is drawn: a rival's column keeps the
 * client's five-segment shape, the two segments nobody measured for rivals
 * are hatched, and the column speaks no number for itself. The client's
 * column is the one column on this screen that carries a score, and it is
 * the score the report shows.
 *
 * `ClientCompetitorsView.test.tsx` asserts this rather than trusting it:
 * exactly one "out of 100" on the page, however many rivals there are.
 *
 * WHY THE REPORT, AND NOT THE HISTORY
 * -----------------------------------
 * `ClientHistory` carries the per-rival figures scan by scan, and the trends
 * read them. But it carries the CLIENT'S side as a composite and a share of
 * voice only — not mention rate, not citation strength — so a column for the
 * client could not be built from it. The latest scan's report has both sides
 * at once, already shaped, and it is the document the Report item opens:
 * a figure here and a figure there are the same row read twice.
 *
 * Pure and prop-driven, so every state is reachable by a static render.
 */

import type { JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  ErrorState,
  LoadingState,
  LuminanceLedger,
  StatRow,
  StatTile,
} from '@avp/design-system';
import type { ClientHistory, Me, Report } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import { accentFor } from '@/components/client/clientNav';
import {
  ClientMetaFigures,
  latestComposite,
  latestScanId,
  type ClientDetailState,
} from '@/components/client/ClientDetailView';
import {
  fieldColumns,
  fieldLeads,
  presence,
  type FieldColumn,
} from '@/lib/client/competitors';
import { intermittentRivals } from '@/lib/client/trends';

/** The screen's accent, read from the nav table — never a second literal. */
const ACCENT = accentFor('competitors') ?? 0;

/**
 * Stride 3 between the two accented tiles, for the reason
 * `ClientCrawlerView` records: adjacent offsets on the 97-degree arc are not
 * safe, least of all at its crowded end.
 */
const TILE = [ACCENT, ACCENT + 3] as const;

/**
 * The latest scan's report — the screen's second read.
 *
 * `report: null` is a scan the history lists but whose report answered 404,
 * which the route treats as an ordinary state rather than a failure.
 */
export type FieldState =
  | { kind: 'loading' }
  | { kind: 'ready'; report: Report | null }
  | { kind: 'error'; title: string; detail: string };

export function ClientCompetitorsView({
  state,
  field,
  me,
}: {
  state: ClientDetailState;
  field: FieldState;
  me: Me | null;
}): JSX.Element {
  if (state.kind === 'loading' || state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="competitors"
        latestReportScanId={null}
      >
        {state.kind === 'loading' ? (
          <LoadingState message="Loading this client…" />
        ) : (
          <ErrorState title={state.title} detail={state.detail} />
        )}
      </ClientSpace>
    );
  }

  const { client, history } = state;
  return (
    <ClientSpace
      client={client}
      me={me}
      current="competitors"
      latestReportScanId={latestScanId(history)}
      latestScore={latestComposite(history)}
      figures={<ClientMetaFigures history={history} />}
    >
      <Body history={history} field={field} />
    </ClientSpace>
  );
}

function Body({ history, field }: { history: ClientHistory; field: FieldState }): JSX.Element {
  if (history.scans.length === 0) {
    return (
      <EmptyState
        eyebrow="Competitors"
        title="This client has not been scanned"
        body="A scan is what detects a competitor set and measures the field against it. Until one runs there is nobody to compare."
        action={
          <Button variant="primary" onClick={() => window.location.assign('/')}>
            Run a scan
          </Button>
        }
      />
    );
  }

  if (field.kind === 'loading') {
    return <LoadingState message="Reading the latest scan…" />;
  }
  if (field.kind === 'error') {
    return <ErrorState title={field.title} detail={field.detail} />;
  }

  const scanId = latestScanId(history);
  const { report } = field;

  if (report === null) {
    return (
      <EmptyState
        eyebrow="Competitors"
        title="The latest scan has no report to read"
        body="The field is read off the scan's report, and this scan's could not be found. A scan that is still running or that ended without answers has none yet."
      />
    );
  }

  const columns = fieldColumns(report);
  if (columns.length === 0) {
    return (
      <EmptyState
        eyebrow="Competitors"
        title="The latest scan was not scored"
        body="A rival's column is built to the shape of the client's score, and this scan produced none. An unrunnable scan is never compared as a low one."
        {...(scanId ? { action: <ReportLink scanId={scanId} /> } : {})}
      />
    );
  }

  const rivals = columns.filter((c) => !c.isSubject);
  if (rivals.length === 0) {
    const status = report.competitorSet?.status;
    return (
      <EmptyState
        eyebrow="Competitors"
        title="No competitors in this scan"
        body={
          report.competitorSet === null
            ? 'Competitor detection did not run for this scan, so share of voice was left out of its score and there is no field to draw. The report is where a competitor set is detected or named by hand.'
            : 'Detection ran and found nobody it was confident enough to name. The report is where a competitor set can be named by hand.'
        }
        {...(status && status !== 'ok' ? { note: `Detection status: ${status}.` } : {})}
        {...(scanId ? { action: <ReportLink scanId={scanId} /> } : {})}
      />
    );
  }

  return <Field history={history} report={report} columns={columns} />;
}

function Field({
  history,
  report,
  columns,
}: {
  history: ClientHistory;
  report: Report;
  columns: readonly FieldColumn[];
}): JSX.Element {
  const subject = columns[0]!;
  const rivals = columns.slice(1);
  const leads = fieldLeads(columns);
  const leaders = new Set(leads.map((l) => l.name)).size;
  const intermittent = intermittentRivals(history);
  const confidence = numberOrNull(report.competitorSet?.detectionConfidence);

  // The stack, heaviest first — the order the layout draws from the base up.
  const stack = subject.dimensions;
  const rivalBlind = stack.filter(
    (d) => !rivals.some((r) => r.dimensions.find((x) => x.key === d.key)?.measured !== false),
  );

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-display text-ed-xs leading-display tracking-display text-text-primary">
          The field, dimension by dimension
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          Every brand in the latest scan&rsquo;s competitor set, drawn the way the score is drawn:
          each segment is one dimension, its height is the dimension&rsquo;s weight and its lit
          part is that brand&rsquo;s sub-score. Only {subject.name} has a combined score. Rivals
          are measured on {rivals[0]!.measuredCount} of the {stack.length} dimensions, so a
          rival&rsquo;s column shows its readings and claims no total.
        </p>
        <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
          {`Columns stack ${stack
            .map((d) => `${d.label} (${formatWeight(d.weight)})`)
            .join(', ')}, heaviest at the base.`}
          {rivalBlind.length > 0 &&
            ` ${listNames(rivalBlind.map((d) => d.label))} ${
              rivalBlind.length === 1 ? 'is' : 'are'
            } measured for ${subject.name} only and ${
              rivalBlind.length === 1 ? 'is' : 'are'
            } hatched on every rival.`}
        </p>
      </div>

      <StatRow min="11rem">
        <StatTile label="Rivals in this scan" value={String(rivals.length)} accent={TILE[0]} />
        <StatTile
          label="Ahead on a dimension"
          value={String(leaders)}
          {...(leaders > 0 ? { accent: TILE[1], emphasis: true } : {})}
          note={
            leaders === 0
              ? `No rival leads ${subject.name} on any measured dimension.`
              : 'Rivals with at least one sub-score above this client\u2019s.'
          }
        />
        {/* A confidence is a measurement, not a count, so it carries no accent. */}
        <StatTile
          label="Detection confidence"
          value={confidence === null ? 'Not stated' : `${Math.round(confidence * 100)}%`}
        />
      </StatRow>

      <ul className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(11rem,1fr))]">
        {columns.map((column) => (
          <li key={column.key} className="min-w-0">
            <FieldCard column={column} subjectName={subject.name} history={history} />
          </li>
        ))}
      </ul>

      <div className="grid items-start gap-8 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-ui-md font-medium text-text-primary">Who leads on what</h3>
          {leads.length === 0 ? (
            <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
              {`No rival leads ${subject.name} on any dimension both sides are measured on.`}
            </p>
          ) : (
            <ul className="flex flex-col">
              {leads.map((lead) => (
                <li
                  key={`${lead.name}:${lead.key}`}
                  className="flex items-baseline justify-between gap-4 border-b border-line-hairline py-2 text-ui-sm last:border-0"
                >
                  <span className="text-text-secondary">
                    <span className="font-medium text-text-primary">{lead.name}</span>
                    {` leads on ${lead.label}`}
                  </span>
                  <span className="tabular-nums text-text-primary">{`+${lead.delta.toFixed(1)}`}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {intermittent.length > 0 && (
          // The set is re-detected per scan, and a rival missing from an
          // earlier one is a real event. Said here the way Rankings says it
          // under its chart, so the two screens agree about the same rivals.
          <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
            {intermittent.length === 1
              ? `${intermittent[0]} was not in every scan's competitor set. Each card says how many scans carried its rival.`
              : `${listNames(intermittent)} were not in every scan's competitor set. Each card says how many scans carried its rival.`}
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * One brand's column, with its figures.
 *
 * The ledger draws the comparison; the readings under it are the same
 * figures as numbers, because reading an exact value off a segment is
 * guesswork and an operator asking "how far behind on share of voice" wants
 * the number. A rival's delta is its lead over the client, signed; the
 * client's own row carries no delta because there is nothing to lead.
 */
function FieldCard({
  column,
  subjectName,
  history,
}: {
  column: FieldColumn;
  subjectName: string;
  history: ClientHistory;
}): JSX.Element {
  const seen = column.isSubject ? null : presence(history, column.name);
  return (
    <Card elevation={column.isSubject ? 'seated' : 'flat'} className="h-full">
      <CardBody>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <p className="flex flex-wrap items-center gap-2">
              <span
                className={
                  column.isSubject
                    ? 'truncate text-ui-base font-medium text-text-primary'
                    : 'truncate text-ui-base text-text-primary'
                }
              >
                {column.name}
              </span>
              {column.isSubject && <Badge tone="beacon">This client</Badge>}
              {column.isManual && <Badge tone="neutral">Named by hand</Badge>}
            </p>
            {column.domain && (
              <p className="truncate text-ui-xs text-text-tertiary">{column.domain}</p>
            )}
          </div>

          <LuminanceLedger
            subjectName={column.name}
            dimensions={column.dimensions}
            compact
            palette="working"
            animate={false}
          />

          <table className="w-full">
            <thead className="sr-only">
              <tr>
                <th scope="col">Dimension</th>
                <th scope="col">Sub-score</th>
                {!column.isSubject && <th scope="col">{`Lead over ${subjectName}`}</th>}
              </tr>
            </thead>
            <tbody>
              {column.readings.map((r) => (
                <tr key={r.key} className="border-b border-line-hairline last:border-0">
                  <td className="py-1 pr-2 text-ui-xs text-text-secondary">{r.label}</td>
                  <td className="py-1 text-right text-ui-xs tabular-nums text-text-primary">
                    {r.value === null ? (
                      <span className="text-text-tertiary">Not measured</span>
                    ) : (
                      Math.round(r.value)
                    )}
                  </td>
                  {!column.isSubject && (
                    <td className="py-1 pl-2 text-right text-ui-xs tabular-nums text-text-secondary">
                      {r.delta === null ? (
                        <span className="text-text-tertiary">—</span>
                      ) : (
                        `${r.delta > 0 ? '+' : r.delta < 0 ? '−' : '±'}${Math.abs(r.delta).toFixed(1)}`
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>

          {seen && (
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
              {`In ${seen.seen} of ${seen.of} ${seen.of === 1 ? 'scan' : 'scans'}`}
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function ReportLink({ scanId }: { scanId: string }): JSX.Element {
  return (
    <Button variant="secondary" onClick={() => window.location.assign(`/scans/${scanId}/report`)}>
      Open the report
    </Button>
  );
}

function formatWeight(weight: number): string {
  return Number.isInteger(weight) ? `${weight}%` : `${weight.toFixed(2)}%`;
}

function listNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

function numberOrNull(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}
