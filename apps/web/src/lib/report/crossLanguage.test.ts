/**
 * The TypeScript narrative derivation is the source of truth — Epic 9.14.
 *
 * `services/report_narrative.py` is a second implementation of everything in
 * `derive.ts`, and it exists only because the PDF endpoint is a Python process
 * that cannot call a TypeScript function. `derive.ts`'s own docstring warns
 * about exactly this: two sources for one number, whose failure mode is "the
 * chart annotating one dimension while the headline names another".
 *
 * So the duplication is policed rather than trusted. This file and
 * `apps/api/tests/test_report_narrative.py` read the SAME two checked-in files:
 *
 *   packages/shared-types/fixtures/report-cases.json       six report payloads
 *   packages/shared-types/fixtures/expected-narrative.json  what they must produce
 *
 * This side asserts that the expected output still describes what `derive.ts`
 * actually does; the Python side asserts that its port produces the same thing.
 * Change either derivation and one of the two goes red on the field that moved.
 *
 * If you meant to change the derivation, regenerate `expected-narrative.json`
 * from THIS implementation and let the Python suite tell you what it costs.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { deriveNarrative } from './derive';
import type { Report } from '@avp/shared-types';

const FIXTURES = fileURLToPath(
  new URL('../../../../../packages/shared-types/fixtures/', import.meta.url),
);

const cases = JSON.parse(
  readFileSync(`${FIXTURES}report-cases.json`, 'utf8'),
) as Record<string, Report>;

const expected = JSON.parse(
  readFileSync(`${FIXTURES}expected-narrative.json`, 'utf8'),
) as Record<string, unknown>;

/** The projection both languages compare. */
function project(report: Report) {
  const n = deriveNarrative(report);
  return {
    composite: n.composite,
    status: n.status,
    biggestGapKey: n.biggestGap?.key ?? null,
    recoverablePoints: n.recoverablePoints,
    potentialComposite: n.potentialComposite,
    fixes: n.fixes.map((f) => ({
      id: f.id,
      title: f.title,
      detail: f.detail,
      priority: f.priority,
      effort: f.effort,
      pointsUpside: f.pointsUpside ?? null,
      source: f.source,
      generated: f.generated ?? false,
    })),
  };
}

describe('the shared fixtures still describe this implementation', () => {
  it('covers every case, so none can pass by never being compared', () => {
    expect(Object.keys(cases).sort()).toEqual(Object.keys(expected).sort());
    expect(Object.keys(cases).length).toBeGreaterThanOrEqual(6);
  });

  for (const name of Object.keys(cases)) {
    it(`${name} derives exactly what the Python side is held to`, () => {
      expect(project(cases[name]!)).toEqual(expected[name]);
    });
  }
});

describe('the cases are the degraded states, not six variations of one', () => {
  it('includes a scan that ran and was never scored', () => {
    const n = deriveNarrative(cases['unscored']!);
    expect(n.status).toBe('not_scored');
    expect(n.composite).toBeNull();
    // Never zero. An unrunnable scan must not read as a bad score.
    expect(n.composite).not.toBe(0);
  });

  it('includes a scan where detection never ran', () => {
    expect(cases['no-competitor-set']!.competitorSet).toBeNull();
  });

  it('includes a dimension we have not measured, which must produce no fix', () => {
    const n = deriveNarrative(cases['not-yet-measured']!);
    expect(n.fixes.map((f) => f.id)).not.toContain('gap:citation_strength');
  });

  it('includes a report with and without Epic 8 generated wording', () => {
    const plain = deriveNarrative(cases['scored-no-generated-fixes']!);
    const rich = deriveNarrative(cases['scored-with-generated-fixes']!);
    expect(plain.fixes.some((f) => f.generated)).toBe(false);
    expect(rich.fixes.some((f) => f.generated)).toBe(true);
    // Same fixes, same numbers, different words.
    expect(rich.fixes.map((f) => f.id)).toEqual(plain.fixes.map((f) => f.id));
    expect(rich.recoverablePoints).toBe(plain.recoverablePoints);
  });

  it('includes one with nothing but dimension gaps to rank', () => {
    const n = deriveNarrative(cases['bare']!);
    expect(n.fixes.every((f) => f.source === 'gap')).toBe(true);
  });
});
