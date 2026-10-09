import { describe, it, expect } from 'vitest';
import { SCAN_DURATION_SENTENCE, scanOutcomeSentence } from './scanOutcome';

const COUNTS = 'It counts as one of your scans.';
const FREE = 'It does not use up one of your scans.';

describe('scanOutcomeSentence', () => {
  it('partial, no code', () => {
    expect(scanOutcomeSentence('partial', null)).toBe(
      `Some engines did not answer. These results use the answers that came back. ${COUNTS}`,
    );
    expect(scanOutcomeSentence('partial', undefined)).toBe(scanOutcomeSentence('partial', null));
  });

  it('partial + SCAN_DEADLINE_EXCEEDED', () => {
    expect(scanOutcomeSentence('partial', 'SCAN_DEADLINE_EXCEEDED')).toBe(
      `This scan ran out of time. These results use the prompts that finished. ${COUNTS}`,
    );
  });

  it('failed + SCAN_DEADLINE_EXCEEDED', () => {
    expect(scanOutcomeSentence('failed', 'SCAN_DEADLINE_EXCEEDED')).toBe(
      `This scan ran out of time before enough prompts were answered. ${FREE}`,
    );
  });

  it('failed + ALL_ENGINE_CALLS_FAILED', () => {
    expect(scanOutcomeSentence('failed', 'ALL_ENGINE_CALLS_FAILED')).toBe(
      `None of the AI engines answered, so there are no results. ${FREE}`,
    );
  });

  it('failed + EXECUTION_FAILED', () => {
    expect(scanOutcomeSentence('failed', 'EXECUTION_FAILED')).toBe(
      `This scan stopped because of an error on our side. ${FREE}`,
    );
  });

  it('failed + PROMPTS_UNAVAILABLE', () => {
    expect(scanOutcomeSentence('failed', 'PROMPTS_UNAVAILABLE')).toBe(
      `The questions for this scan could not be written, so nothing was asked. Run the scan again. ${FREE}`,
    );
  });

  it('failed + EXECUTOR_LOST', () => {
    expect(scanOutcomeSentence('failed', 'EXECUTOR_LOST')).toBe(
      `This scan stopped when our server restarted. ${FREE}`,
    );
  });

  it('failed + EXECUTOR_NEVER_STARTED', () => {
    expect(scanOutcomeSentence('failed', 'EXECUTOR_NEVER_STARTED')).toBe(
      `This scan did not start because our server restarted. ${FREE}`,
    );
  });

  it('failed + an unknown or missing code', () => {
    const sentence = `This scan did not finish. ${FREE}`;
    expect(scanOutcomeSentence('failed', 'SOMETHING_NEW')).toBe(sentence);
    expect(scanOutcomeSentence('failed', null)).toBe(sentence);
    expect(scanOutcomeSentence('failed')).toBe(sentence);
  });

  it('succeeded, queued, running and cancelled say nothing', () => {
    for (const status of ['succeeded', 'queued', 'running', 'cancelled'] as const) {
      expect(scanOutcomeSentence(status, 'EXECUTION_FAILED')).toBeNull();
    }
  });

  it('never prints a raw code', () => {
    for (const code of ['SCAN_DEADLINE_EXCEEDED', 'ALL_ENGINE_CALLS_FAILED', 'EXECUTION_FAILED', 'PROMPTS_UNAVAILABLE', 'WHATEVER']) {
      for (const status of ['partial', 'failed'] as const) {
        expect(scanOutcomeSentence(status, code)).not.toContain(code);
      }
    }
  });
});

describe('SCAN_DURATION_SENTENCE — launch checklist 3.39', () => {
  it('says a scan usually takes a few minutes, and names no figure a slow scan could break', () => {
    // The 300-second promise the screens made was untrue: 9 of the 25 finished scans in the
    // dev database took longer (read on 2026-10-03), and a scan's audit can wait up to 300
    // seconds for the crawler's one browser. A number here is a promise the next slow scan
    // breaks; "usually" is what keeps "a few minutes" true of the four scans outside the
    // two-to-six-minute range the other 21 sit in.
    expect(SCAN_DURATION_SENTENCE).toBe('A scan usually takes a few minutes.');
    expect(SCAN_DURATION_SENTENCE).not.toMatch(/\d/);
    expect(SCAN_DURATION_SENTENCE.toLowerCase()).not.toMatch(/five|ten|under|within|less than/);
  });
});
