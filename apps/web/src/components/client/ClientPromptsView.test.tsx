/**
 * The Prompts screen — Epic 9.24.
 *
 * What these assert is the set of things this screen can get quietly wrong: the
 * difference between "an engine did not name you" and "an engine did not
 * answer", the throttle being stated before it is hit rather than after, and
 * the run never presenting itself as a measurement.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { PromptsPanel } from './ClientPromptsView';
import {
  EXHAUSTED,
  LIMITS,
  absentRun,
  mixedRun,
} from '@/lib/client/__fixtures__/promptRuns';

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

  it('keeps it distinct from an engine that genuinely failed', () => {
    const html = panel();
    expect(html).toContain('This engine did not answer');
    expect(html).toContain('ENGINE_TIMEOUT');
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
