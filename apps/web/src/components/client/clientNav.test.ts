/**
 * The client nav's cluster invariants — Epic B.1.
 *
 * These are the assertions the flat `ACCENT` record could not carry. Epic B
 * shipped a crimson/magenta collision to a live browser because ten accent
 * numbers typed into JSX have no place to be checked; this file is that place.
 *
 * Every test here is a rule Epics E, F and G will be adding sections under.
 */

import { describe, it, expect } from 'vitest';
import { BENCH_ACCENTS } from '@avp/design-system';
import { CLIENT_NAV, accentFor, type ClientSection } from './clientNav';

const CEILING = BENCH_ACCENTS.length;

describe('a hue is scoped to its cluster', () => {
  it('never repeats an accent WITHIN a cluster', () => {
    // The rule the whole grouping exists to make satisfiable. Across clusters
    // a hue may repeat — that repetition is the mechanism that buys seats —
    // but two items under one label wearing one colour is the collision the
    // accent layer exists to prevent.
    for (const cluster of CLIENT_NAV) {
      const accents = cluster.items
        .map((i) => i.accent)
        .filter((a): a is number => a !== null);
      expect(new Set(accents).size, `${cluster.label} repeats an accent`).toBe(
        accents.length,
      );
    }
  });

  it('keeps every cluster inside the layer the palette can actually hold', () => {
    // The ceiling that ended the flat model: seven accents, and no eighth is
    // available — the 30deg meaning buffer and the sRGB gamut at the shared
    // chroma table leave one arc, 256.5 to 355 degrees, and seven fill it.
    for (const cluster of CLIENT_NAV) {
      expect(cluster.items.length, `${cluster.label} outgrew the layer`).toBeLessThanOrEqual(
        CEILING,
      );
      for (const item of cluster.items) {
        if (item.accent !== null) {
          expect(item.accent).toBeGreaterThanOrEqual(0);
          expect(item.accent).toBeLessThan(CEILING);
        }
      }
    }
  });

  it('starts each cluster at the first accent, so a cluster is self-contained', () => {
    for (const cluster of CLIENT_NAV) {
      const accents = cluster.items
        .map((i) => i.accent)
        .filter((a): a is number => a !== null)
        .sort((a, b) => a - b);
      if (accents.length > 0) expect(accents[0]).toBe(0);
    }
  });

  it('leaves room for the sections still to be built', () => {
    /*
     * Alerts (E) and Prompt discovery (G) land in Investigation; Crawler
     * activity (F) lands in Measurement, because it is a data SOURCE rather
     * than an analysis of one. This asserts the end state fits — the whole
     * point of doing this before Epic E rather than during it.
     */
    // Alerts landed in Epic E; Crawler activity (F) is still to come in
    // Measurement, Prompt discovery (G) in Investigation.
    const planned: Record<string, number> = { measurement: 1, investigation: 1 };
    for (const cluster of CLIENT_NAV) {
      const projected = cluster.items.length + (planned[cluster.key] ?? 0);
      expect(projected, `${cluster.label} will outgrow the layer`).toBeLessThanOrEqual(
        CEILING,
      );
    }
  });
});

describe('sections and clusters', () => {
  it('gives every section exactly one home', () => {
    const seen = new Map<string, number>();
    for (const cluster of CLIENT_NAV) {
      for (const item of cluster.items) {
        if (item.section === null) continue;
        seen.set(item.section, (seen.get(item.section) ?? 0) + 1);
      }
    }
    for (const [section, count] of seen) {
      expect(count, `${section} appears in more than one cluster`).toBe(1);
    }
  });

  it('resolves an accent for every accented section', () => {
    const sections: ClientSection[] = [
      'overview',
      'sources',
      'rankings',
      'sentiment',
      'technical',
      'gaps',
      'prompts',
      'alerts',
    ];
    for (const section of sections) {
      expect(accentFor(section), `${section} has no accent`).not.toBeNull();
    }
  });

  it('leaves the Report unaccented, because it leaves this space', () => {
    const report = CLIENT_NAV.flatMap((c) => c.items).find((i) => i.section === null);
    expect(report).toBeDefined();
    expect(report!.accent).toBeNull();
    expect(report!.external).toBe(true);
  });

  it('files Sentiment as Measurement, not as an analysis', () => {
    // It is 15% of the composite and its labels have been stored since Epic 4.
    // The decision brief's first draft put it under Analysis; it reads rows a
    // scan recorded, which is what Measurement means.
    const measurement = CLIENT_NAV.find((c) => c.key === 'measurement');
    expect(measurement!.items.some((i) => i.section === 'sentiment')).toBe(true);
  });
});
