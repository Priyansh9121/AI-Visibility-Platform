/**
 * The Prompts screen — Epic 9.24.
 *
 * What these assert is the set of things this screen can get quietly wrong: the
 * difference between "an engine did not name you" and "an engine did not
 * answer", the throttle being stated before it is hit rather than after, and
 * the run never presenting itself as a measurement.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Client, PromptRun } from '@avp/shared-types';
import { ClientPromptsView, PromptsPanel, runSegments } from './ClientPromptsView';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { threeScanHistory } from '@/lib/client/__fixtures__/history';
import {
  EXHAUSTED,
  LIMITS,
  absentRun,
  mixedRun,
} from '@/lib/client/__fixtures__/promptRuns';
import { ENGINE_FAILURE_SENTENCE, engineFailureSentence } from '@/lib/client/engines';

const noop = () => {};

const panel = (over: Partial<Parameters<typeof PromptsPanel>[0]> = {}) =>
  renderToStaticMarkup(
    <PromptsPanel
      runs={[mixedRun]}
      limits={LIMITS}
      prompt=""
      running={false}
      problem={null}
      onPromptChange={noop}
      onSubmit={noop}
      {...over}
    />,
  );

describe('a fresh run settles in; the rest never move — Epic 16.3', () => {
  it('marks only the run the submit produced', () => {
    const html = panel({ runs: [mixedRun, absentRun], freshId: absentRun.id });
    const cards = html.split('<article ').slice(1);
    expect(cards.length).toBe(2);
    expect(cards[0]!).toMatch(/^class="avp-run /);
    expect(cards[0]!).not.toContain('is-new');
    expect(cards[1]!).toMatch(/^class="avp-run [^"]*is-new"/);
  });

  it('marks nothing when nothing was just submitted, so a reload performs nothing', () => {
    expect(panel({ runs: [mixedRun, absentRun] })).not.toContain('is-new');
  });
});

describe('a run’s header carries its stamp as a fact — Epic 16.2', () => {
  it('sets the time it ran as a chip under the question', () => {
    const html = panel();
    expect(html).toMatch(/avp-chip[^>]*>.*?\d{2} \w{3} \d{4}, \d{2}:\d{2} UTC/);
  });
});

describe('the composer says what a run costs before one is bought', () => {
  it('states how many runs are left this hour', () => {
    // Served by the API rather than guessed — a browser that guessed would
    // either block a legal run or offer one that 429s.
    expect(panel()).toContain('28 of 30 runs left this hour');
  });

  it('disables the run and explains why when the hour is used up', () => {
    const html = panel({ limits: EXHAUSTED });
    expect(html).toContain('used all 30 runs for this hour');
    // Explained, not merely greyed out. A disabled control with no reason is
    // the "button that does nothing" this project rules out everywhere else.
    expect(html).toContain('costing more than a scan');
    expect(html).toContain('disabled');
  });

  it('will not submit an empty prompt', () => {
    expect(panel({ prompt: '   ' })).toContain('disabled');
  });

  it('refuses an over-long prompt in the field rather than at the server', () => {
    const html = panel({ prompt: 'x'.repeat(LIMITS.maxPromptChars + 1) });
    expect(html).toContain('limited to 500 characters');
    expect(html).toContain('501');
  });

  it('says the engines are being asked while a run is in flight', () => {
    // ~23s median, and considerably longer at the tail. A button that simply
    // sat there would read as broken.
    const html = panel({ running: true });
    expect(html).toContain('Asking the engines');
    expect(html).toContain('Every engine is being asked');
  });

  it('shows no progress bar, because progress cannot be measured', () => {
    expect(panel({ running: true })).not.toContain('role="progressbar"');
  });
});

describe('an engine that did not name the client is a finding, not a failure', () => {
  it('says so in words rather than showing a zero or a dash', () => {
    const html = panel();
    expect(html).toContain('Did not name');
    expect(html).not.toContain('position 0');
  });

  it('names who it did say instead', () => {
    // The most useful thing this screen can tell an operator.
    const html = panel();
    expect(html).toContain('Named instead');
    expect(html).toContain('Front');
    expect(html).toContain('Zendesk');
  });

  it('keeps it distinct from an engine that genuinely failed, and says why in a sentence, not the code', () => {
    const html = panel();
    expect(html).toContain('This engine did not answer');
    // The card used to print the code in mono: `(ENGINE_TIMEOUT)`, which is not even one
    // the API records. It says what happened instead (launch checklist 3.39).
    expect(html).toContain('It did not respond in the time we allow.');
    expect(html).not.toContain('ENGINE_TIMEOUT');
    expect(html).not.toContain('(TIMEOUT)');
  });

  it('reports a run where nobody named the client without reading as broken', () => {
    const html = panel({ runs: [absentRun] });
    expect(html).toContain('Did not name');
    // It ANSWERED — the run succeeded and the answer is the bad news.
    expect(html).toContain('All engines answered');
  });
});

describe('the per-engine cards', () => {
  it('distinguishes the two Claude modes, because the difference is the finding', () => {
    // A brand can be absent from grounded answers and present in parametric
    // ones. Two rows both labelled "Claude" would hide exactly that.
    const html = panel();
    expect(html).toContain('Claude — from memory');
    expect(html).toContain('Claude — with web search');
  });

  it('reports the client’s position as an ordinal, never as a score', () => {
    expect(panel()).toContain('at position 1');
  });

  it('lists cited domains, and says so when an engine cited nothing', () => {
    const html = panel();
    expect(html).toContain('helpscout.com');
    // A parametric engine cites nothing BY DESIGN; leaving the section out
    // would look like a rendering fault.
    expect(html).toContain('Cited no sources');
  });

  it('carries the Working-screen accent layer', () => {
    // The visual half of this epic, asserted rather than eyeballed: each
    // engine card takes a bench hue.
    expect(panel()).toContain('--avp-bench-');
  });
});

describe('a run is not a measurement', () => {
  it('says plainly that it produces no score and joins no history', () => {
    // If an operator believed a run moved the score, they would read every
    // trend on the neighbouring screens wrong.
    const html = panel();
    expect(html).toContain('produces no score');
    expect(html).toContain('does not appear in this client');
  });

  it('shows the prompt verbatim, since the operator wrote it', () => {
    expect(panel()).toContain('best help desk software for small teams');
  });

  it('summarises how many engines named the client, with a denominator', () => {
    // "1" alone is not checkable. "1 / 3" is.
    expect(panel()).toContain('1 / 3');
  });
});

describe('empty and error states', () => {
  it('explains what the screen is for when nothing has been asked', () => {
    const html = panel({ runs: [] });
    expect(html).toContain('Nothing asked yet');
    expect(html).toContain('test the ones you think they ask');
  });

  it('surfaces a failed run rather than swallowing it', () => {
    const html = panel({ problem: 'The engines could not be reached.' });
    expect(html).toContain('That prompt did not run');
    expect(html).toContain('The engines could not be reached.');
  });
});


/**
 * The per-run strip — 2026-09-22. Named you / did not name you / did not
 * answer, summed inline; the three-way distinction the cards already draw,
 * readable before the cards are.
 */
describe('each run carries a strip of what the engines did', () => {
  it('keeps "did not name you" apart from "did not answer" on the strip', () => {
    // mixedRun: one engine named the client, one answered without naming it,
    // one timed out.
    const segments = runSegments(mixedRun);
    expect(segments.map((s) => `${s.key}=${s.value}`)).toEqual([
      'named=1',
      'unnamed=1',
      'failed=1',
    ]);
    const html = panel();
    expect(html).toContain('avp-verdict__seg--tone-beacon');
    expect(html).toContain('avp-verdict__seg--tone-neutral');
    expect(html).toContain('avp-verdict__seg--tone-warn');
    expect(html).toContain('did not answer');
  });

  it('a run nobody named is a strip with no beacon segment, not an error', () => {
    const html = panel({ runs: [absentRun] });
    expect(html).not.toContain('avp-verdict__seg--tone-beacon');
    expect(html).toContain('Did not name you');
  });
});

describe('an engine that did not answer says why in a sentence, and never the code — launch checklist 3.39', () => {
  // One row per code an engine result can carry (`services/engines.py`). The sentence
  // follows "This engine did not answer." in the same paragraph.
  // Three of the provider's refusals (a key it refused, an account with no credit or quota,
  // a request it rejected) are each a problem on our side, and share one sentence.
  const OUR_SIDE = 'We could not get an answer from its provider because of a problem on our side.';
  const CASES: [code: string, sentence: string][] = [
    ['TIMEOUT', 'It did not respond in the time we allow.'],
    ['PROVIDER_RATE_LIMITED', 'Its provider asked us to slow down.'],
    ['PROVIDER_AUTH_FAILED', OUR_SIDE],
    ['PROVIDER_QUOTA_EXHAUSTED', OUR_SIDE],
    ['PROVIDER_BAD_REQUEST', OUR_SIDE],
    ['PROVIDER_UNREACHABLE', 'We could not reach its provider.'],
    ['PROVIDER_ERROR', 'The request to its provider failed.'],
    ['PROVIDER_REFUSED', 'It declined to answer this question.'],
    ['ANSWER_TRUNCATED', 'Its answer was cut off before it finished.'],
    ['ANSWER_PAUSED', 'It had not finished its answer when the response came back.'],
    ['STOP_REASON_UNKNOWN', 'It stopped for a reason we do not recognise.'],
  ];
  const MACHINE_CODE = /\b[A-Z]{3,}_[A-Z0-9_]+\b/;

  /** `mixedRun` with its failed engine's code replaced: the other two engines are as they were. */
  const withCode = (code: string | null): PromptRun =>
    ({
      ...mixedRun,
      results: mixedRun.results.map((r) => (r.status === 'timeout' ? { ...r, errorCode: code } : r)),
    }) as PromptRun;

  it('has a row for every sentence the screen has, and a sentence of its own for each but three', () => {
    expect(CASES.map(([code]) => code).sort()).toEqual(Object.keys(ENGINE_FAILURE_SENTENCE).sort());
    const sharers = CASES.filter(([, sentence]) => sentence === OUR_SIDE).map(([code]) => code);
    expect(sharers.sort()).toEqual(['PROVIDER_AUTH_FAILED', 'PROVIDER_BAD_REQUEST', 'PROVIDER_QUOTA_EXHAUSTED']);
    expect(new Set(CASES.map(([, sentence]) => sentence)).size).toBe(CASES.length - 2);
  });

  it.each(CASES)('%s: the sentence, once, after the lead, and not the code', (code, sentence) => {
    const html = panel({ runs: [withCode(code)] });
    expect(html.split(`This engine did not answer. ${sentence}`).length - 1).toBe(1);
    expect(html).not.toContain(`(${code})`);
    expect(html).not.toContain(code);
    expect(html.match(MACHINE_CODE)).toBeNull();
    expect(html).not.toContain('font-mono text-ui-2xs">(');
  });

  it.each([['SOMETHING_NEW'], [null]])(
    'a code nothing words (%s) leaves the lead on its own, and shows no code',
    (code) => {
      const html = panel({ runs: [withCode(code)] });
      expect(html).toContain('This engine did not answer.</p>');
      expect(html).not.toContain('This engine did not answer. ');
      if (code !== null) expect(html).not.toContain(code);
      expect(html.match(MACHINE_CODE)).toBeNull();
    },
  );

  it('blames nobody: not the person who asked, not the brand, not the engine', () => {
    for (const [code, sentence] of CASES) {
      expect(sentence, code).not.toMatch(/\byou\b|\byour\b|\bbrand\b|\bclient\b|\bprompt\b/i);
    }
  });

  it('a named or unnamed answer is not given a failure sentence', () => {
    const html = panel();
    // Three engines in `mixedRun`: one named, one did not, one timed out. One sentence.
    expect(html.split('This engine did not answer.').length - 1).toBe(1);
    for (const sentence of Object.values(ENGINE_FAILURE_SENTENCE)) {
      expect(html.split(sentence).length - 1).toBeLessThanOrEqual(1);
    }
  });

  it('engineFailureSentence is null for no code, an empty one, one nothing words, and an inherited name', () => {
    for (const code of [null, undefined, '', 'SOMETHING_NEW', 'constructor', 'toString']) {
      expect(engineFailureSentence(code), String(code)).toBeNull();
    }
  });

  describe('every code an engine can record is worded — read back from engines.py', () => {
    const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
    const source = readFileSync(join(ROOT, 'apps/api/src/avp_api/services/engines.py'), 'utf8');
    // The error mapping's strings, the one place `TIMEOUT` is spelled, and the three stop-reason
    // constants (`ANSWER_TRUNCATED`, `ANSWER_PAUSED`, `STOP_REASON_UNKNOWN`).
    const recorded = new Set<string>([
      ...[...source.matchAll(/"(PROVIDER_[A-Z_]+)"/g)].map((m) => m[1]!),
      ...[...source.matchAll(/^(ANSWER_[A-Z_]+|STOP_REASON_UNKNOWN) = "\1"$/gm)].map((m) => m[1]!),
      ...(source.includes('EngineResultStatus.TIMEOUT, "TIMEOUT"') ? ['TIMEOUT'] : []),
    ]);

    it('finds the codes it is meant to find, so an empty read cannot pass', () => {
      for (const code of ['TIMEOUT', 'PROVIDER_RATE_LIMITED', 'PROVIDER_REFUSED', 'ANSWER_TRUNCATED', 'STOP_REASON_UNKNOWN']) {
        expect(recorded.has(code), code).toBe(true);
      }
      expect(recorded.size).toBeGreaterThanOrEqual(11);
    });

    it('has a sentence for each', () => {
      expect([...recorded].filter((code) => engineFailureSentence(code) === null)).toEqual([]);
    });

    it('has no sentence for a code the engines no longer record', () => {
      expect(Object.keys(ENGINE_FAILURE_SENTENCE).filter((code) => !recorded.has(code))).toEqual([]);
    });
  });
});

describe('the tab opens with the questions this client’s scans ask — F6 section 3', () => {
  const view = (state: Parameters<typeof ClientPromptsView>[0]['state']) =>
    renderToStaticMarkup(<ClientPromptsView state={state} me={clientsMe} />);

  it('puts that panel above the prompt workbench', () => {
    const html = view({ kind: 'ready', client: identifiedClient as Client, history: threeScanHistory });
    const questions = html.indexOf('The questions this client’s scans ask');
    const workbench = html.indexOf('Loading prompt history…');
    expect(questions).toBeGreaterThan(-1);
    expect(workbench).toBeGreaterThan(questions);
  });

  it('has no such panel while the client itself is loading: there is no client to ask about', () => {
    expect(view({ kind: 'loading' })).not.toContain('scans ask');
  });

  it('says what a scan asks without a number nobody counts, and that a rescan asks the same ones', () => {
    // U2: "about two dozen questions it wrote itself" was untrue of a rescan, which copies the client’s set.
    const html = panel();
    expect(html).toContain(
      'A scan asks this client’s questions; a rescan asks the same ones again until new ones are written.',
    );
    expect(html).not.toContain('two dozen');
    expect(html).not.toContain('wrote itself');
  });
});
