'use client';

/**
 * A client's Answer gaps screen — Epic B.
 *
 * WHAT IT SHOWS
 * -------------
 * Which questions somebody else owns. For one scan, every prompt it asked
 * against every brand any engine named, with the client's own column pinned
 * first — so "we are absent from the comparison questions and present on the
 * branded ones" is readable in one pass instead of inferred from a score.
 *
 * **Nothing is re-analysed and no answer text is read.** There is none to read:
 * ip-safety.md #7 means an engine's answer exists only inside the request that
 * produced it. Every figure here is a count of `engine_result_brand_mentions`
 * and `engine_result_citations` rows a scan already wrote. The only free text
 * on the screen is the prompt, which Epic 4 generated — ours, not an engine's.
 *
 * THE STATE THAT IS NOT A GAP, AGAIN
 * -----------------------------------
 * A prompt where NO brand was named by anyone is not a question this client
 * lost. It is a question with no commercial answer, and it is kept apart
 * everywhere on this screen: its own tile, its own quiet chip in the grid, and
 * sorted below every real verdict. Folding it into the absent count roughly
 * doubles the reported gap on real data — on the worst client measured, 10
 * no-brand prompts sit beside 9 genuine absences. Epic A drew the same line
 * around `unclassified` sentiment.
 *
 * WHAT THIS SCREEN CANNOT SAY, AND SAYS SO
 * -----------------------------------------
 * Two limits are stated in the copy rather than hidden:
 *
 * 1. The grid can only name rivals that competitor detection found, because
 *    `extract_facts` searches for the brands it is handed and discovers none.
 * 2. "Named but not cited" is only claimed when the scan proves this client's
 *    domain is citable at all. With no owned citation anywhere, the claim is
 *    unsupported and is not made — `subjectCitable` carries that.
 *
 * Pure and prop-driven, so every state is reachable by a static render — the
 * split `DashboardView` established in Epic 9.3.
 */

import { useState, type JSX } from 'react';
import {
  Button,
  EmptyState,
  ErrorState,
  GapGrid,
  LoadingState,
  SelectField,
  StatRow,
  StatTile,
} from '@avp/design-system';
import type { AnswerGaps, Me } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import { accentFor } from '@/components/client/clientNav';
import {
  latestScanId,
  type ClientDetailState,
} from '@/components/client/ClientDetailView';

/**
 * The screen's own accent, read from the nav table rather than typed here.
 *
 * It was a literal `6` (crimson) for exactly as long as accents were global.
 * Epic B.1 made them cluster-relative, so this screen is now the FIRST item of
 * the Investigation cluster and wears `cobalt` — the same hue Overview wears in
 * the Measurement cluster, which is the trade that grouping buys.
 *
 * Reading it from `clientNav` rather than restating it is the point: a screen
 * whose figure accent disagreed with its own nav item would be worse than
 * either choice, and a second hand-typed copy is how that happens.
 */
const ACCENT = accentFor('gaps') ?? 0;

/**
 * The accented tiles, derived FROM the screen's accent rather than typed.
 *
 * They were literals — `6`, `0`, `2` — and that survived only while `ACCENT`
 * happened to be 6. Epic B.1 moved this screen to the first seat of its
 * cluster, `ACCENT` became 0, and "Rivals took" and "Partly held" silently
 * became the same blue. It reached a browser screenshot.
 *
 * `benchAccent` cycles, so offsetting from `ACCENT` keeps these distinct from
 * it and from each other no matter where the nav table moves this screen —
 * which is the property that was missing, not the particular hues.
 */
const TILE = [ACCENT, ACCENT + 1, ACCENT + 2] as const;

export type AnswerGapsState =
  | { kind: 'loading' }
  /**
   * `pending` is a scan being SWAPPED, not a first load.
   *
   * The two are different states and the first draft conflated them: picking
   * an earlier scan reset the whole body to `loading`, which unmounted the
   * picker that had just been used. `data` is the last good grid and stays on
   * screen, dimmed, while the next one is fetched.
   */
  | { kind: 'ready'; data: AnswerGaps | null; pending?: boolean }
  | { kind: 'error'; title: string; detail: string };

export function ClientAnswerGapsView({
  state,
  gaps,
  me,
  onSelectScan,
}: {
  state: ClientDetailState;
  gaps: AnswerGapsState;
  me: Me | null;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  if (state.kind === 'loading' || state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="gaps"
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
      current="gaps"
      latestReportScanId={latestScanId(history)}
    >
      <GapsBody gaps={gaps} onSelectScan={onSelectScan} />
    </ClientSpace>
  );
}

function GapsBody({
  gaps,
  onSelectScan,
}: {
  gaps: AnswerGapsState;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  if (gaps.kind === 'loading') return <LoadingState message="Reading this scan…" />;
  if (gaps.kind === 'error') {
    return <ErrorState title={gaps.title} detail={gaps.detail} />;
  }
  if (gaps.data == null) return <NoScanYet />;
  return (
    <Grid
      gaps={gaps.data}
      pending={gaps.pending ?? false}
      onSelectScan={onSelectScan}
    />
  );
}

function Grid({
  gaps,
  pending,
  onSelectScan,
}: {
  gaps: AnswerGaps;
  pending: boolean;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  const [sort, setSort] = useState<'recurrence' | 'order'>('recurrence');
  const rivalsFound = gaps.brands.filter((b) => !b.isSubject).length;

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-editorial text-ed-xs leading-display tracking-display text-text-primary">
          Which questions somebody else owns
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          Every prompt this scan asked, against every brand the engines named.
          A row is a gap when a rival was named and {gaps.name} was not — and a
          row where <em>nobody</em> was named is not a gap at all, so it is
          counted and sorted separately rather than added to the total.
        </p>
      </div>

      <StatRow min="11rem">
        <StatTile
          label="Rivals took"
          value={String(gaps.absent)}
          accent={TILE[0]}
          emphasis={gaps.absent > 0}
          note="Answered by a rival, with this client named on no engine."
        />
        <StatTile
          label="Partly held"
          value={String(gaps.partial)}
          accent={TILE[1]}
          note="Named by some engines and not others."
        />
        <StatTile
          label="Named, not cited"
          value={gaps.subjectCitable ? String(gaps.uncited) : '—'}
          accent={TILE[2]}
          // The limit, stated on the tile rather than left to be inferred from
          // a zero that would otherwise read as "no citation gaps".
          note={
            gaps.subjectCitable
              ? 'Named, but this scan cited someone else’s page for it.'
              : 'No answer in this scan cited this client’s domain, so this cannot be measured.'
          }
        />
        {/*
          DELIBERATELY UNACCENTED, and the live pass is why.

          It first carried accent 4 (magenta, hue 326) beside "Rivals took" at
          crimson 355. Twenty-nine degrees apart at the same lightness and
          chroma, in two small chips at opposite ends of a row, they read as
          the same pink — and on a client where both figures were 0 the two
          tiles were indistinguishable. That is the palette pressure
          BENCH_ACCENTS documents, showing up the first time seven accents
          were used at once.

          Plain is also the more correct answer. This is the ONE tile that is
          not a finding, and the grid already says so — its chip is dashed and
          grey, and its rows sort below every real verdict. A tile with no
          category matches how the row it counts is drawn, and Epic 9.24 set
          the precedent for unaccenting a figure that should not wear one.
        */}
        <StatTile
          label="Named no one"
          value={String(gaps.noBrands)}
          note="No brand named by any engine. Not a gap — nobody won these."
        />
      </StatRow>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap gap-4">
          {gaps.availableScanIds.length > 1 && onSelectScan != null && (
            <SelectField
              label="Scan"
              value={gaps.scanId}
              onChange={(e) => onSelectScan(e.target.value)}
              options={gaps.availableScanIds.map((id, i) => ({
                value: id,
                label: i === 0 ? 'Latest' : `${i + 1} scans ago`,
              }))}
            />
          )}
          <SelectField
            label="Order"
            value={sort}
            onChange={(e) => setSort(e.target.value as 'recurrence' | 'order')}
            options={[
              { value: 'recurrence', label: 'Worst first' },
              { value: 'order', label: 'As asked' },
            ]}
          />
        </div>
        <p className="max-w-measure text-ui-xs text-text-tertiary">
          {/*
            The limit that would otherwise look like a clean grid: a rival the
            detector never found leaves no mention row and cannot appear here,
            however often an engine named it.
          */}
          {rivalsFound === 0
            ? 'No rivals were detected for this scan, so only this client has a column. Run competitor detection to widen the grid.'
            : `${rivalsFound} rival ${rivalsFound === 1 ? 'column' : 'columns'}, from the competitors detected for this scan. A rival never detected cannot appear here.`}
        </p>
      </div>

      <GapGrid
        rows={gaps.rows}
        brands={gaps.brands}
        sort={sort}
        accent={ACCENT}
        animate
        pending={pending}
        caption={`${gaps.prompts} prompts across ${gaps.engines.length} ${
          gaps.engines.length === 1 ? 'engine' : 'engines'
        }. A cell counts the engines that named that brand for that prompt.`}
      />

      {gaps.rivals.length > 0 && <RivalLedger gaps={gaps} />}
    </section>
  );
}

/**
 * Rivals across the client's whole history, beside the per-scan grid.
 *
 * This is the only recurrence this data can honestly report over TIME. The
 * brief asked for gaps sorted by how often they recur, which assumed prompts
 * persist between scans; they do not — `generate_prompts` writes a fresh set
 * every scan, and across three real scans 72 prompts carried 71 distinct
 * texts. Competitors DO persist, so "which rival keeps taking answers from us"
 * is answerable where "which prompt keeps failing" is not.
 */
function RivalLedger({ gaps }: { gaps: AnswerGaps }): JSX.Element {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
        Rivals taking answers, across every scan
      </h3>
      <p className="max-w-measure text-ui-xs text-text-secondary">
        Counted over this client’s whole history rather than this one scan.
        Prompts are written fresh for every scan, so a rival is the only thing
        here that can recur.
      </p>
      <table className="w-full max-w-measure">
        <thead>
          <tr className="border-b border-line-hairline">
            <th scope="col" className="py-2 pr-3 text-left text-ui-2xs uppercase tracking-caps text-text-tertiary">
              Rival
            </th>
            <th scope="col" className="py-2 pr-3 text-right text-ui-2xs uppercase tracking-caps text-text-tertiary">
              Answers won
            </th>
            <th scope="col" className="py-2 text-right text-ui-2xs uppercase tracking-caps text-text-tertiary">
              Scans
            </th>
          </tr>
        </thead>
        <tbody>
          {gaps.rivals.map((rival) => (
            <tr key={rival.name} className="border-b border-line-hairline last:border-0">
              <td className="py-2 pr-3 text-ui-sm text-text-primary">
                {rival.name}
                {rival.domain != null && (
                  <span className="ml-2 text-ui-2xs text-text-tertiary">{rival.domain}</span>
                )}
              </td>
              <td className="py-2 pr-3 text-right text-ui-sm tabular-nums text-text-primary">
                {rival.answersWon}
              </td>
              <td className="py-2 text-right text-ui-sm tabular-nums text-text-secondary">
                {rival.scansPresent}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * No scan has produced a grid.
 *
 * One reason only, unlike Sentiment's empty state: a gap grid needs a scan
 * that asked prompts, and there is no second way to arrive here.
 */
function NoScanYet(): JSX.Element {
  return (
    <EmptyState
      eyebrow="Answer gaps"
      title="Nothing measured yet"
      body="Answer gaps read the brands each engine named for each prompt a scan asked. Until a scan has run there is no grid to build."
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          Run a scan
        </Button>
      }
    />
  );
}
