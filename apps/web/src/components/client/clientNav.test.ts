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
     * The projection tracks REALITY, not the original prediction. Alerts
     * landed in Epic E and AI crawlers in Epic F, so both have been taken out
     * of `planned` — a number left standing after its section shipped would
     * quietly assert room for a seat that is already occupied.
     *
     * Measurement is now FULL: six items and no free seat. Any seventh
     * section there needs a palette decision, not a nav edit, and this test is
     * where that will surface. Epic 13's Competitors section went to
     * Investigation, where it belongs on its own merits — see `clientNav.ts`
     * — and this test is the record that Measurement could not have taken it.
     *
     * Prompt discovery (G) is the one section still to come, in Investigation.
     */
    const planned: Record<string, number> = { measurement: 0, investigation: 1 };
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
      'crawler',
      'competitors',
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

  it('files AI crawlers as Measurement, and takes the seat B.1 reserved', () => {
    // Epic F. It reads a first-party SOURCE — the site's own robots.txt — the
    // same reason Sentiment is filed here rather than under Investigation.
    // Accent 5 is the seat B.1 held open and asserted would fit, and taking it
    // must not have repainted anything above it.
    const measurement = CLIENT_NAV.find((c) => c.key === 'measurement');
    const crawler = measurement!.items.find((i) => i.section === 'crawler');
    expect(crawler, 'AI crawlers is not in Measurement').toBeDefined();
    expect(crawler!.accent).toBe(5);
    expect(accentFor('technical')).toBe(4);
    expect(accentFor('sentiment')).toBe(3);
  });

  it('names the section for what it measures, not for what F was called', () => {
    /*
     * The roadmap called this "Crawler activity" and meant server-log evidence
     * of bots hitting the site. Nothing in this product ingests server logs,
     * so the section reads the site's stated POLICY instead — and the label
     * has to say so, because a nav item promising activity is a claim the
     * screen behind it cannot honour.
     */
    const crawler = CLIENT_NAV.flatMap((c) => c.items).find(
      (i) => i.section === 'crawler',
    );
    expect(crawler!.label).toBe('AI crawlers');
    expect(crawler!.label.toLowerCase()).not.toContain('activity');
  });

  it('files Competitors as Investigation, beside Answer gaps, repainting nothing — Epic 13', () => {
    /*
     * A derivation over rows a scan already wrote, one step past the report's
     * own per-dimension comparison, so it is Investigation for the reason
     * Answer gaps is. It is inserted ABOVE Prompts and Alerts, and the point
     * of accents being identities is that doing so changed neither of their
     * hues — asserted, because a positional accent would have failed exactly
     * here and looked fine in the diff.
     */
    const investigation = CLIENT_NAV.find((c) => c.key === 'investigation')!;
    const keys = investigation.items.map((i) => i.section);
    expect(keys.indexOf('competitors')).toBe(keys.indexOf('gaps') + 1);
    expect(accentFor('competitors')).toBe(3);
    expect(accentFor('gaps')).toBe(0);
    expect(accentFor('prompts')).toBe(1);
    expect(accentFor('alerts')).toBe(2);
    const item = investigation.items.find((i) => i.section === 'competitors')!;
    expect(item.path).toBe('/competitors');
    expect(item.external).toBeUndefined();
  });

  it('files Sentiment as Measurement, not as an analysis', () => {
    // It is 15% of the composite and its labels have been stored since Epic 4.
    // The decision brief's first draft put it under Analysis; it reads rows a
    // scan recorded, which is what Measurement means.
    const measurement = CLIENT_NAV.find((c) => c.key === 'measurement');
    expect(measurement!.items.some((i) => i.section === 'sentiment')).toBe(true);
  });
});
