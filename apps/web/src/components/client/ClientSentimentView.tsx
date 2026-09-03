'use client';

/**
 * A client's Sentiment screen — Epic A.
 *
 * WHAT IT SHOWS, AND WHAT IT DELIBERATELY DOES NOT
 * -------------------------------------------------
 * How each engine described the client, scan by scan. Sentiment has been
 * classified and stored since Epic 4 — it is one of the five scored dimensions,
 * at 15% of the composite — and until now it appeared nowhere except folded
 * into that one number. This is the same stored labels read on their own.
 *
 * **Nothing is re-analysed and no answer text is read.** There is none to read:
 * ip-safety.md #7 means an engine's answer exists only inside the request that
 * produced it, and `classify_sentiment` runs there, against the transient text,
 * persisting a LABEL. Every figure on this screen is a count of those labels.
 *
 * THE THIRD STATE IS THE POINT
 * -----------------------------
 * An answer that never named the client has no tone — the classifier is not
 * even called for it. That is not a neutral, and this screen keeps the two
 * apart everywhere: the tide does not draw it, the tiles count it separately,
 * and the copy names it. Folding them would report a brand nobody mentioned as
 * having been described neutrally.
 *
 * Pure and prop-driven, so every state is reachable by a static render — the
 * split `DashboardView` established in Epic 9.3.
 */

import type { JSX } from 'react';
import {
  Button,
  EmptyState,
  ErrorState,
  LoadingState,
  SentimentTide,
  StatRow,
  StatTile,
  netByEngine,
} from '@avp/design-system';
import type { ClientHistory, Me } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import {
  ClientMetaFigures,
  latestScanId,
  type ClientDetailState,
} from '@/components/client/ClientDetailView';
import { ENGINE_ACCENT, engineShort } from '@/lib/client/engines';
import { hasSentiment, tidePoints, unclassifiedTotal } from '@/lib/client/trends';

export function ClientSentimentView({
  state,
  me,
}: {
  state: ClientDetailState;
  me: Me | null;
}): JSX.Element {
  if (state.kind === 'loading' || state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="sentiment"
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
      current="sentiment"
      latestReportScanId={latestScanId(history)}
      figures={<ClientMetaFigures history={history} />}
    >
      <SentimentBody history={history} />
    </ClientSpace>
  );
}

function SentimentBody({ history }: { history: ClientHistory }): JSX.Element {
  if (!hasSentiment(history)) {
    return <NoToneYet history={history} />;
  }

  const points = tidePoints(history);
  const nets = netByEngine(points);
  const unnamed = unclassifiedTotal(history);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-editorial text-ed-xs leading-display tracking-display text-text-primary">
          How the engines talk about this client
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          Every answer that named this client was classified for tone toward it.
          Positive sits above the line, negative below, neutral straddles it —
          so a column mostly under the line is bad news before you read a
          number. Sentiment is 15% of the composite score.
        </p>
        {/*
          THE FIRST-READING NOTE — copy, not a redesign.

          At one scan the tide is three wide bars against a single x-tick, and
          the design review's read was that it looks like the chart is
          malfunctioning rather than correctly reporting that this is all the
          data there is. `hasSentiment`'s own argument stands and is not
          reverted: one scan IS a readable tide, and the honest fix is telling
          the reader what they are looking at.

          So nothing about the chart's rendering changes — no forced narrower
          bars, no fake second column. Disguising an n of 1 would be the
          dishonest version of this fix.

          Distinct from `NoToneYet`, which handles zero scans or zero named
          answers. Those are an absence of data; this is data with no trend
          yet, and the two need different words.
        */}
        {points.length === 1 && (
          <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
            One scan so far, so this is a first reading rather than a trend.
            Tone will show as a direction once a second scan runs.
          </p>
        )}
      </div>

      <div className="grid items-start gap-8 lg:grid-cols-[auto_minmax(16rem,1fr)]">
        <SentimentTide
          points={points}
          height={340}
          animate
          engineLabel={Object.fromEntries(
            points
              .flatMap((p) => Object.keys(p.byEngine))
              .map((e) => [e, engineShort(e)]),
          )}
          engineAccent={ENGINE_ACCENT}
          title="Tone by engine"
          caption="Counted across every answered prompt in each scan. Answers that never named this client carry no tone and are not drawn — they are counted separately."
          ariaLabel={`Tone toward ${history.name} by engine across ${points.length} ${
            points.length === 1 ? 'scan' : 'scans'
          }.`}
        />
        <ToneLedger nets={nets} />
      </div>

      <StatRow min="11rem">
        <StatTile
          label="Answers with a tone"
          value={String(nets.reduce((s, n) => s + n.classified, 0))}
          accent={0}
          note="Every answer that named this client."
        />
        <StatTile
          label="Named no one"
          value={String(unnamed)}
          accent={1}
          emphasis={unnamed > 0}
          // The distinction the whole screen turns on, said in words rather
          // than left to be inferred from a missing bar.
          note="Answered without naming this client, so tone was never asked. Not a neutral."
        />
        <StatTile
          label="Engines reporting"
          value={String(nets.length)}
          accent={2}
        />
      </StatRow>
    </section>
  );
}

/**
 * Net tone per engine, beside the chart.
 *
 * The chart answers "which way and when"; this answers "by how much, exactly".
 * Reading a net off stacked bars is guesswork, and an operator comparing two
 * engines wants the number. Same reason `SeriesLedger` sits beside the trends.
 */
function ToneLedger({
  nets,
}: {
  nets: readonly { engine: string; net: number | null; classified: number }[];
}): JSX.Element {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
        Net tone, whole history
      </p>
      <table className="w-full">
        <thead className="sr-only">
          <tr>
            <th scope="col">Engine</th>
            <th scope="col">Net tone</th>
            <th scope="col">Answers classified</th>
          </tr>
        </thead>
        <tbody>
          {nets.map((row) => (
            <tr key={row.engine} className="border-b border-line-hairline last:border-0">
              <td className="py-2 pr-3">
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 flex-none rounded-full"
                    style={{
                      background: `var(--avp-bench-${(ENGINE_ACCENT[row.engine] ?? 0) + 1}-600)`,
                    }}
                  />
                  <span className="truncate text-ui-sm text-text-secondary">
                    {engineShort(row.engine)}
                  </span>
                </span>
              </td>
              <td className="py-2 pr-3 text-right text-ui-sm tabular-nums text-text-primary">
                {row.net === null ? (
                  // Never zero. Zero is a real answer — as much praise as
                  // criticism — and must not read the same as "never measured".
                  <span className="text-text-tertiary">Not measured</span>
                ) : (
                  `${row.net > 0 ? '+' : row.net < 0 ? '−' : '±'}${Math.abs(row.net)}`
                )}
              </td>
              <td className="py-2 text-right text-ui-sm tabular-nums text-text-secondary">
                {row.classified}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * No tone anywhere.
 *
 * Two genuinely different reasons, and the copy separates them, because the
 * fix is different: a client nobody has scanned needs a scan, and a client
 * whose answers never name it has a visibility problem — which is the finding,
 * not an empty screen.
 */
function NoToneYet({ history }: { history: ClientHistory }): JSX.Element {
  const scanned = history.scans.length > 0;
  const unnamed = unclassifiedTotal(history);

  return (
    <EmptyState
      eyebrow="Sentiment"
      title={
        scanned && unnamed > 0
          ? 'No engine has described this client yet'
          : 'Nothing measured yet'
      }
      body={
        scanned && unnamed > 0
          ? `Every answer so far was given without naming this client, so there was nothing to judge the tone of. That is not a gap in the data — it is the finding, and it is the same one the visibility score is reporting.`
          : 'Tone is classified from answers that name this client. Until a scan produces one there is nothing to show.'
      }
      {...(scanned && unnamed > 0
        ? { note: `${unnamed} answers so far, none of them naming this client.` }
        : {})}
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          {scanned ? 'Run another scan' : 'Run a scan'}
        </Button>
      }
    />
  );
}
