'use client';

/**
 * A client's Prompts screen — Epic 9.24.
 *
 * WHAT IT IS
 * ----------
 * Type a question, run it against every engine, see who they name. It is the
 * first screen in this product that CREATES data on demand rather than reading
 * what a scan already produced, and the only one whose primary control spends
 * money when it is clicked.
 *
 * WHY IT IS NOT A SCAN
 * --------------------
 * A scan generates ~24 prompts from the client's industry, runs each against
 * three engines, scores the result and writes a report — six minutes and 72
 * engine calls. An operator who wants to know whether ONE question names their
 * client has, until now, had to buy all of that. A run is three engine calls
 * and no score, and `test_prompt_runs.py` asserts it writes no `Scan` row: a
 * question an operator asked must never appear in a trend or a dashboard figure
 * as though it were a measurement.
 *
 * BUILT IN THE WORKING-SCREEN LANGUAGE FROM THE START
 * ---------------------------------------------------
 * This is the first screen designed after Epic 9.24's accent layer existed
 * rather than retrofitted onto it: the per-engine cards carry bench hues, the
 * summary is a `StatRow`, and the composer sits above a dense history. Nothing
 * here reads from the report's restrained palette, and nothing here is reused
 * by the report — see `reportIsolation.test.ts`.
 *
 * HONEST ABSENCE, THE SAME AS EVERYWHERE ELSE
 * -------------------------------------------
 * An engine that answered without naming the client shows "Not named" — a
 * finding, and the single most useful thing this screen can tell an operator.
 * It is not an error, it is not a zero, and it is not styled as a failure. An
 * engine that genuinely failed is a separate state that says so.
 */

import { useCallback, useEffect, useState, type FormEvent, type JSX } from 'react';
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  LoadingState,
  StatRow,
  StatTile,
  TextField,
} from '@avp/design-system';
import type { BadgeTone } from '@avp/design-system';
import type {
  Client,
  Me,
  PromptRun,
  PromptRunHistory,
  PromptRunResult,
} from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { ClientSpace } from '@/components/client/ClientSpace';
import { latestScanId } from '@/components/client/ClientDetailView';
import { formatStamp } from '@/lib/dates';
import { ENGINE_ACCENT, engineLabel } from '@/lib/client/engines';
import type { ClientDetailState } from '@/components/client/ClientDetailView';

const RUN_TONE: Record<string, BadgeTone> = {
  ok: 'success',
  partial: 'warn',
  failed: 'danger',
};

const RUN_LABEL: Record<string, string> = {
  ok: 'All engines answered',
  partial: 'Some engines answered',
  failed: 'No engine answered',
};

export function ClientPromptsView({
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
        current="prompts"
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
      current="prompts"
      latestReportScanId={latestScanId(history)}
    >
      <PromptWorkbench client={client} />
    </ClientSpace>
  );
}

/**
 * The stateful half: fetching, submitting, and the throttle figure.
 *
 * Kept as thin as it can be, because everything below `PromptsPanel` is pure
 * and prop-driven — the split `DashboardView` established in Epic 9.3 and every
 * screen since has followed. It is what makes each state of this screen
 * reachable by a static render instead of only by a live browser.
 */
function PromptWorkbench({ client }: { client: Client }): JSX.Element {
  const [runs, setRuns] = useState<PromptRun[] | null>(null);
  const [limits, setLimits] = useState<Pick<
    PromptRunHistory,
    'runsRemaining' | 'runsPerHour' | 'maxPromptChars'
  > | null>(null);
  const [prompt, setPrompt] = useState('');
  const [running, setRunning] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const body = await api.promptRuns(client.id);
    setRuns(body.data);
    setLimits({
      runsRemaining: body.runsRemaining,
      runsPerHour: body.runsPerHour,
      maxPromptChars: body.maxPromptChars,
    });
  }, [client.id]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
      } catch (err) {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiProblem ? err.problem.detail : 'The request did not complete.',
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (running || prompt.trim() === '') return;
    setRunning(true);
    setProblem(null);
    try {
      await api.runPrompt(client.id, prompt.trim());
      setPrompt('');
      // Re-read rather than pushing the new run onto the list: the throttle
      // figure has to come back from the server anyway, and one source for
      // both keeps them from disagreeing on the screen.
      await load();
    } catch (err) {
      setProblem(
        err instanceof ApiProblem
          ? err.problem.detail
          : 'The run did not complete. Nothing was recorded.',
      );
    } finally {
      setRunning(false);
    }
  };

  if (loadError !== null) {
    return <ErrorState title="Prompt history could not be loaded" detail={loadError} />;
  }
  if (runs === null || limits === null) {
    return <LoadingState message="Loading prompt history…" />;
  }

  return (
    <PromptsPanel
      runs={runs}
      limits={limits}
      prompt={prompt}
      running={running}
      problem={problem}
      onPromptChange={setPrompt}
      onSubmit={submit}
    />
  );
}

export interface PromptsPanelProps {
  runs: readonly PromptRun[];
  limits: Pick<PromptRunHistory, 'runsRemaining' | 'runsPerHour' | 'maxPromptChars'>;
  prompt: string;
  running: boolean;
  problem: string | null;
  onPromptChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
}

/**
 * The composer and the history. Pure, so every state is statically renderable.
 *
 * Exported for the tests, which is the point of the split: "no runs yet",
 * "throttle exhausted", "an engine did not name you" and "an engine failed" are
 * all states an operator will hit, and none of them should need a live browser
 * and three paid engine calls to verify.
 */
export function PromptsPanel({
  runs,
  limits,
  prompt,
  running,
  problem,
  onPromptChange,
  onSubmit,
}: PromptsPanelProps): JSX.Element {
  const exhausted = limits.runsRemaining <= 0;
  const tooLong = prompt.trim().length > limits.maxPromptChars;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-editorial text-ed-xs leading-display tracking-display text-text-primary">
          Ask the engines something now
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          A scan asks about two dozen questions it wrote itself. This asks one
          you wrote, against the same engines, and records who they named. It
          produces no score and does not appear in this client&rsquo;s history.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <TextField
          label="Your question"
          placeholder="best help desk software for small teams"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          disabled={running || exhausted}
          hint={
            exhausted
              ? `This client has used all ${limits.runsPerHour} runs for this hour. Each run asks every engine, so the limit keeps ad-hoc testing from costing more than a scan.`
              : `Phrase it the way a buyer would. ${limits.runsRemaining} of ${limits.runsPerHour} runs left this hour.`
          }
          {...(tooLong
            ? {
                error: `A question is limited to ${limits.maxPromptChars} characters; this one is ${prompt.trim().length}.`,
              }
            : {})}
        />
        <div className="flex flex-wrap items-center gap-4">
          <Button
            type="submit"
            variant="primary"
            disabled={running || exhausted || tooLong || prompt.trim() === ''}
          >
            {running ? 'Asking the engines…' : 'Run this prompt'}
          </Button>
          {running && (
            // Said, not hidden. Three engine calls take ~23s and can take
            // considerably longer; a button that simply sat there would read
            // as broken. There is no progress BAR for the reason the intake
            // screen gives: we cannot measure real progress, and a fake one is
            // a lie the operator will notice.
            <p className="flex items-center gap-3 text-ui-sm text-text-tertiary">
              <Badge tone="beacon" live>
                Running
              </Badge>
              Every engine is being asked. This usually takes under a minute.
            </p>
          )}
        </div>
      </form>

      {problem !== null && (
        <ErrorState title="That prompt did not run" detail={problem} />
      )}

      {runs.length === 0 ? (
        <EmptyState
          eyebrow="Prompts"
          title="Nothing asked yet"
          body="A scan tests the questions it thinks this client's buyers ask. This is where you test the ones you think they ask — a phrasing a client mentioned, a competitor's angle, a service you are not sure the site explains well enough."
          note="Runs are kept here so you can compare a phrasing against the same phrasing next month."
        />
      ) : (
        <section className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line-hairline pb-4">
            <h2 className="text-ui-md font-medium text-text-primary">
              {runs.length === 1 ? 'One run' : `${runs.length} runs`}
            </h2>
            <p className="text-ui-sm text-text-tertiary">Newest first.</p>
          </div>
          <div className="flex flex-col gap-6">
            {runs.map((run) => (
              <RunCard key={run.id} run={run} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function RunCard({ run }: { run: PromptRun }): JSX.Element {
  const named = run.results.filter((r) => r.mentioned).length;
  const best = run.results
    .map((r) => r.position)
    .filter((p): p is number => p != null);

  return (
    <article className="flex flex-col gap-4 rounded-lg border border-line-hairline p-5">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-measure font-mono text-ui-sm leading-mono text-text-primary">
            &ldquo;{run.promptText}&rdquo;
          </p>
          <Badge tone={RUN_TONE[run.status] ?? 'neutral'}>
            {RUN_LABEL[run.status] ?? run.status}
          </Badge>
        </div>
        <p className="text-ui-xs text-text-tertiary">{formatStamp(run.createdAt)}</p>
      </header>

      <StatRow min="9rem">
        <StatTile
          label="Engines naming you"
          value={`${named} / ${run.results.length}`}
          accent={0}
          emphasis={named === 0}
        />
        <StatTile
          label="Best position"
          // No reading is not a zero, and zero would sort as "first".
          value={best.length === 0 ? 'Not named' : `#${Math.min(...best)}`}
          accent={2}
        />
        <StatTile
          label="Brands named"
          value={String(Math.max(...run.results.map((r) => r.brandsMentioned), 0))}
          accent={4}
        />
      </StatRow>

      <div className="grid gap-3 md:grid-cols-3">
        {run.results.map((result) => (
          <EngineCard key={result.id} result={result} subject={run.subjectName} />
        ))}
      </div>
    </article>
  );
}

function EngineCard({
  result,
  subject,
}: {
  result: PromptRunResult;
  subject: string;
}): JSX.Element {
  const accent = ENGINE_ACCENT[result.engine] ?? 0;
  const answered =
    result.status === 'ok' || result.status === 'answered_no_mention';
  const rivals = result.brands.filter((b) => !b.isSubject);

  return (
    <div
      className="flex flex-col gap-3 rounded-md border border-line-hairline bg-surface-sunken p-4"
      style={
        {
          // Same custom-property route StatTile uses, so the card follows a
          // theme switch rather than freezing a light-mode literal.
          borderTopColor: `var(--avp-bench-${accent + 1}-600)`,
          borderTopWidth: '3px',
        } as Record<string, string>
      }
    >
      <h3 className="text-ui-sm font-medium text-text-primary">
        {engineLabel(result.engine)}
      </h3>

      {!answered ? (
        // A genuine failure, distinct from an answer that did not name you.
        // Collapsing the two would turn an outage into a finding.
        <p className="text-ui-sm text-text-tertiary">
          This engine did not answer.
          {result.errorCode != null && (
            <span className="ml-1 font-mono text-ui-2xs">({result.errorCode})</span>
          )}
        </p>
      ) : result.mentioned ? (
        <p className="text-ui-sm text-text-body">
          Named <strong className="font-medium">{subject}</strong>
          {result.position != null && ` at position ${result.position}`} of{' '}
          {result.brandsMentioned}.
        </p>
      ) : (
        // THE FINDING. Stated plainly, not styled as an error.
        <p className="text-ui-sm text-text-body">
          Did not name <strong className="font-medium">{subject}</strong>
          {result.brandsMentioned > 0 &&
            ` — it named ${result.brandsMentioned} other ${
              result.brandsMentioned === 1 ? 'brand' : 'brands'
            }.`}
        </p>
      )}

      {rivals.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
            Named instead
          </p>
          <ol className="flex flex-col gap-0.5">
            {rivals.map((brand) => (
              <li key={`${brand.name}-${brand.position}`} className="text-ui-xs text-text-secondary">
                {brand.position}. {brand.name}
              </li>
            ))}
          </ol>
        </div>
      )}

      {result.citations.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
            Cited
          </p>
          <ul className="flex flex-col gap-0.5">
            {result.citations.map((cite) => (
              <li
                key={`${cite.url}-${cite.position}`}
                className="truncate font-mono text-ui-2xs text-text-secondary"
                title={cite.domain}
              >
                {cite.domain}
              </li>
            ))}
          </ul>
        </div>
      )}

      {answered && result.citations.length === 0 && (
        // Not an omission — a parametric engine cites nothing by design, and a
        // card that simply left this out would look like a rendering fault.
        <p className="text-ui-2xs text-text-tertiary">Cited no sources.</p>
      )}
    </div>
  );
}
