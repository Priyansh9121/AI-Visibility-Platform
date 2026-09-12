/**
 * The getting-started derivation — Epic 19.
 *
 * Plain objects in, plain objects out. What is asserted is that every step is
 * read off a fact the dashboard already serves, that the count in words
 * matches the count in steps, that the page-bounded list never decides a
 * whole-history question, and that the checklist un-lights when the account
 * leaves a state — it is a reading of the account, not a diary.
 */

import { describe, it, expect } from 'vitest';
import { deriveGettingStarted } from './gettingStarted';
import {
  completeDashboard,
  dismissedDashboard,
  emptyDashboard,
  noScansYetDashboard,
  partialProgressDashboard,
  scan,
  scoredDashboard,
  scoredOffPageDashboard,
  dashboard,
} from './__fixtures__/dashboards';

const byKey = (d: Parameters<typeof deriveGettingStarted>[0]) =>
  Object.fromEntries(deriveGettingStarted(d).steps.map((s) => [s.key, s]));

describe('a brand-new agency', () => {
  const model = deriveGettingStarted(emptyDashboard);

  it('has five steps, none lit, and says so in words', () => {
    expect(model.total).toBe(5);
    expect(model.lit).toBe(0);
    expect(model.lead).toBe('None of five lit.');
    expect(model.complete).toBe(false);
    expect(model.visible).toBe(true);
  });

  it('offers exactly the two things that can be done from nothing', () => {
    const steps = byKey(emptyDashboard);
    expect(steps.client!.action).toEqual({ label: 'Add your first client', href: '/' });
    expect(steps.scan!.action).toBeUndefined();
    expect(steps.scan!.detail).toBe('After a client is added.');
    expect(steps.score!.action).toBeUndefined();
    expect(steps.teammate!.action).toEqual({ label: 'Invite a teammate', href: '/settings' });
    expect(steps.share!.action).toBeUndefined();
    expect(steps.share!.detail).toBe('After the first score.');
  });
});

describe('the steps are read off the account, one fact each', () => {
  it('a client lights the first step and hands the button to the scan', () => {
    const steps = byKey(noScansYetDashboard);
    expect(steps.client!.done).toBe(true);
    expect(steps.client!.detail).toBe('2 clients');
    expect(steps.client!.action).toBeUndefined();
    expect(steps.scan!.done).toBe(false);
    expect(steps.scan!.action).toEqual({ label: 'Run the first scan', href: '/' });
    expect(steps.scan!.detail).toBe('Under five minutes from start to score.');
  });

  it('a running scan lights the scan step and tells the score step to wait', () => {
    const model = deriveGettingStarted(partialProgressDashboard);
    const steps = byKey(partialProgressDashboard);
    expect(model.lit).toBe(2);
    expect(model.lead).toBe('Two of five lit.');
    expect(steps.scan!.done).toBe(true);
    expect(steps.scan!.detail).toBe('One is running now.');
    expect(steps.score!.done).toBe(false);
    expect(steps.score!.detail).toBe('A scan is running. Its score lands here when it finishes.');
    expect(steps.score!.action).toBeUndefined();
  });

  it('a scored scan lights the score step and points the share step at that report', () => {
    const steps = byKey(scoredDashboard);
    expect(steps.score!.done).toBe(true);
    expect(steps.score!.detail).toBe('1 scored');
    expect(steps.share!.done).toBe(false);
    expect(steps.share!.action).toEqual({
      label: 'Open the latest report',
      href: '/scans/scan_01M0SCANFIXTURE000000001/report',
    });
  });

  it('a scan that ran and produced nothing does not light the score step', () => {
    const d = dashboard([scan({ compositeScore: null, status: 'succeeded' })]);
    const steps = byKey(d);
    expect(d.scoredScanCount).toBe(0);
    expect(steps.score!.done).toBe(false);
    expect(steps.score!.detail).toBe(
      'The scans so far produced no score. Re-run one from the list below.',
    );
  });

  it('a second seat lights the teammate step; a full agency with one seat has no button', () => {
    expect(byKey(dashboard([scan()], { seats: { used: 2, limit: 5 } })).teammate).toMatchObject({
      done: true,
      detail: '2 of 5 seats in use',
    });
    const full = byKey(dashboard([scan()], { seats: { used: 1, limit: 1 } })).teammate!;
    expect(full.done).toBe(false);
    expect(full.detail).toBe('Every seat is in use.');
    expect(full.action).toBeUndefined();
  });

  it('a live share link lights the last step', () => {
    const steps = byKey(completeDashboard);
    expect(steps.share!.done).toBe(true);
    expect(steps.share!.detail).toBe('One live link.');
    expect(steps.share!.action).toBeUndefined();
  });
});

describe('the page is not the history', () => {
  it('a scored scan that scrolled off the page still counts, and the share step has words but no button', () => {
    const model = deriveGettingStarted(scoredOffPageDashboard);
    const steps = byKey(scoredOffPageDashboard);
    expect(steps.score!.done).toBe(true);
    expect(steps.share!.done).toBe(false);
    expect(steps.share!.detail).toBe('Open any scored report and create its link.');
    expect(steps.share!.action).toBeUndefined();
    expect(model.lit).toBe(3);
  });
});

describe('completion and dismissal', () => {
  it('all five lit is complete, with the completed sentence', () => {
    const model = deriveGettingStarted(completeDashboard);
    expect(model.complete).toBe(true);
    expect(model.lit).toBe(5);
    expect(model.lead).toBe('All five are lit. Close this and the dashboard is yours.');
    expect(model.visible).toBe(true);
  });

  it('dismissed hides it and changes nothing else', () => {
    const model = deriveGettingStarted(dismissedDashboard);
    expect(model.visible).toBe(false);
    // Client, scan and score are lit on this fixture; dismissal hides the
    // card and leaves the facts exactly where they were.
    expect(model.lit).toBe(3);
  });

  it('is a reading, not a diary: revoking the only link un-lights the step', () => {
    const before = deriveGettingStarted(completeDashboard);
    const after = deriveGettingStarted({ ...completeDashboard, sharedScanCount: 0 });
    expect(before.complete).toBe(true);
    expect(after.complete).toBe(false);
    expect(after.lit).toBe(4);
  });
});

describe('the words', () => {
  it('never label a step by number', () => {
    for (const step of deriveGettingStarted(emptyDashboard).steps) {
      expect(step.label).not.toMatch(/step \d|^\d/i);
    }
  });

  it('carry no em dash in anything shown on screen', () => {
    const model = deriveGettingStarted(partialProgressDashboard);
    const strings = [model.lead, ...model.steps.flatMap((s) => [s.label, s.detail, s.action?.label ?? ''])];
    for (const text of strings) expect(text).not.toContain('—');
  });
});
