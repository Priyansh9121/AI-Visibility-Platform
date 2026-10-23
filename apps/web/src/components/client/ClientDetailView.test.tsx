/**
 * A client's own space — Epic 9.20.
 *
 * The three cases the brief names are all here: one scan, several, and a
 * competitor set that changed between them. What each asserts is that the
 * screen says something TRUE about thin or shifting data rather than drawing a
 * shape the data does not support.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ClientOverviewView,
  ClientRankingsView,
  ClientSourcesView,
  QuestionBreakNote,
  latestScanId,
  type ClientDetailState,
} from './ClientDetailView';
import type { Client, ClientHistory } from '@avp/shared-types';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import {
  noScanHistory,
  oneScanHistory,
  oneScanPlusDeadHistory,
  shiftingSetHistory,
  threeScanHistory,
  msmAvHistory,
  newQuestionsHistory,
} from '@/lib/client/__fixtures__/history';

const CLIENT = identifiedClient as Client;

/**
 * A way to say "the trend is not drawn" for a history that does have a break. On real data the
 * two cannot part: a break needs a second scan, and a second scan is what draws the trend. So the
 * condition that keeps the note with its chart is reached by switching `hasTrend` off, and only
 * ever inside the one test that does so. Everything else in this file sees the real `hasTrend`.
 */
const chartGate = vi.hoisted(() => ({ noTrend: false }));
vi.mock('@/lib/client/trends', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/client/trends')>();
  return {
    ...real,
    hasTrend: (history: ClientHistory) => (chartGate.noTrend ? false : real.hasTrend(history)),
  };
});

const ready = (history: ClientHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const overview = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientOverviewView state={ready(h)} me={clientsMe} />);
const sources = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientSourcesView state={ready(h)} me={clientsMe} />);
const rankings = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientRankingsView state={ready(h)} me={clientsMe} />);

describe('the hero’s facts take the fact’s shape — Epic 16.2', () => {
  it('sets the scan date, share of voice and rival count as chips beside the delta', () => {
    const html = overview(threeScanHistory);
    const meta = html.slice(html.indexOf('avp-hero__meta'), html.indexOf('avp-hero__aside'));
    expect(meta.match(/class="avp-chip"/g)?.length).toBe(3);
    expect(meta).toMatch(/avp-chip[^>]*>.*?Scanned /);
    expect(meta).toMatch(/share of voice/);
    expect(meta).toMatch(/rivals? in the set/);
    // The delta is a reading, not a credential: still text, still not a chip.
    expect(meta).toMatch(/avp-hero__delta/);
    expect(meta).not.toMatch(/avp-chip[^>]*>[^<]*since last scan/);
  });

  it('says the absence in a sentence, not a chip', () => {
    // Scans that were never scored: the hero renders, and its meta line is
    // an explanation rather than credentials.
    const unscored: ClientHistory = {
      ...threeScanHistory,
      scans: threeScanHistory.scans.map((s) => ({ ...s, composite: null })),
    };
    const html = overview(unscored);
    expect(html).toContain('No scan of this client has produced a reading.');
    const meta = html.slice(html.indexOf('avp-hero__meta'), html.indexOf('</section>'));
    expect(meta).not.toContain('avp-chip');
  });
});

describe('the space says whose it is, and how to leave', () => {
  it('names the client and its domain', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain(CLIENT.brandName ?? CLIENT.name);
    expect(html).toContain(CLIENT.domain);
  });

  it('carries a way back to the list it came from', () => {
    expect(overview(threeScanHistory)).toContain('All clients');
  });

  it('sits inside the shell, which is in client mode — Epic 13', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain('avp-shell');
    expect(html).toContain('avp-nav__head');
    expect(html).not.toContain('avp-localnav');
  });

  it('is a Working screen and takes the app width', () => {
    expect(overview(threeScanHistory)).toContain('avp-shell__content--wide');
  });

  it('marks exactly one section as current, per screen', () => {
    for (const html of [overview(threeScanHistory), sources(threeScanHistory), rankings(threeScanHistory)]) {
      expect((html.match(/aria-current="page"/g) ?? []).length).toBe(1);
    }
  });
});

describe('Report is a path into the existing document, not a copy of it', () => {
  it('links out to the newest scan report', () => {
    expect(latestScanId(threeScanHistory)).toBe('scan_3');
    expect(overview(threeScanHistory)).toContain('href="/scans/scan_3/report"');
  });

  it('renders no report of its own inside the client frame', () => {
    // The report is a Presenting-context document at --avp-report-width (72rem, Epic 17).
    // Rendering it inside a wide Working shell would change the artefact.
    const html = overview(threeScanHistory);
    expect(html).not.toContain('avp-report__title');
    expect(html).not.toContain('avp-beat');
  });

  it('omits the item entirely when there is no report to open', () => {
    // Not a dead nav item — `NavItem`'s rule, and it holds here too.
    const html = overview(noScanHistory);
    expect(latestScanId(noScanHistory)).toBeNull();
    expect(html).not.toContain('/report"');
  });
});

describe('one scan is stated, not drawn', () => {
  it('says so on Sources rather than plotting a single point', () => {
    const html = sources(oneScanHistory);
    expect(html).toContain('One scan is not a trend yet');
    expect(html).toContain('avp-empty');
    expect(html).not.toContain('avp-trend__svg');
  });

  it('says so on Rankings too', () => {
    const html = rankings(oneScanHistory);
    expect(html).toContain('One scan is not a trend yet');
    expect(html).not.toContain('avp-trend__svg');
  });

  it('names what would change it, in this product own voice for thin data', () => {
    // The Ledger's "Not enough data to score" and ScoreMeter's named absences
    // both state the fact and what would resolve it. This matches, rather than
    // inventing a new tone.
    expect(sources(oneScanHistory)).toContain('A second scan is what turns');
    expect(rankings(oneScanHistory)).toContain('Run another scan');
  });

  it('counts scans that produced nothing rather than hiding them', () => {
    const html = sources(oneScanPlusDeadHistory);
    expect(html).toContain('2 further scans produced no reading');
  });

  it('a client never scanned is a different sentence from one scanned once', () => {
    const html = overview(noScanHistory);
    expect(html).toContain('This client has not been scanned');
    expect(html).not.toContain('One scan is not a trend yet');
  });
});

describe('several scans draw a real trend', () => {
  it('Sources plots one line per domain with the client pinned in', () => {
    const html = sources(threeScanHistory);
    expect(html).toContain('avp-trend__svg');
    expect(html).toContain('plausible.io');
    expect(html).toContain('matomo.org');
  });

  it('Rankings plots share of voice on a 0-100 axis', () => {
    const html = rankings(threeScanHistory);
    expect(html).toContain('avp-trend__svg');
    expect(html).toContain('100%');
  });

  it('Rankings says WHY it is share of voice and not the composite', () => {
    // The reason is load-bearing: there is no per-competitor composite, so a
    // reader must not assume the lines are scores.
    expect(rankings(threeScanHistory)).toContain('no per-competitor composite');
  });

  it('both charts carry a data table for screen readers', () => {
    for (const html of [sources(threeScanHistory), rankings(threeScanHistory)]) {
      expect(html).toContain('avp-visually-hidden');
      expect(html).toContain('<table>');
    }
  });

  it('the overview lists the history oldest first, matching the charts', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain('Oldest first');
    // Scoped to the TABLE. The nav's Report link points at the newest scan and
    // sits above the table, so searching the whole document finds scan_3 first
    // and says nothing about row order.
    const body = html.slice(html.indexOf('<tbody>'));
    expect(body.indexOf('scan_1')).toBeLessThan(body.indexOf('scan_3'));
  });
});

describe('a competitor set that changed does not break or silently drop a line', () => {
  it('keeps a rival that vanished for one scan', () => {
    const html = rankings(shiftingSetHistory);
    expect(html).toContain('Fathom Analytics');
  });

  it('keeps a rival that only appeared in the last scan', () => {
    expect(rankings(shiftingSetHistory)).toContain('Seline');
  });

  it('explains the gaps in words instead of leaving them to be noticed', () => {
    const html = rankings(shiftingSetHistory);
    // React escapes the apostrophe, so the assertion matches what is served.
    expect(html).toContain('were not in every scan&#x27;s competitor set');
    expect(html).toContain('not measured');
  });

  it('says nothing about gaps when the set never changed', () => {
    expect(rankings(threeScanHistory)).not.toContain('not in every scan&#x27;s competitor set');
  });

  it('still renders a chart rather than falling over', () => {
    expect(rankings(shiftingSetHistory)).toContain('avp-trend__svg');
    expect(sources(shiftingSetHistory)).toContain('avp-trend__svg');
  });
});

describe('loading and failure', () => {
  it('renders the shell while loading, so navigation works before data', () => {
    const html = renderToStaticMarkup(
      <ClientOverviewView state={{ kind: 'loading' }} me={clientsMe} />,
    );
    expect(html).toContain('avp-shell');
    expect(html).toContain('role="status"');
  });

  it('offers a way back when the client cannot be loaded', () => {
    const html = renderToStaticMarkup(
      <ClientOverviewView
        state={{ kind: 'error', title: 'No such client', detail: 'It does not exist.' }}
        me={clientsMe}
      />,
    );
    expect(html).toContain('No such client');
    // Every page that mounts the shell has a standing, empty `role="alert"` in its sidebar footer,
    // so the error's own is the one around its title.
    expect(html).toContain('role="alert"><p class="avp-errorstate__title">');
    expect(html).toContain('Back to clients');
  });
});


/**
 * The split at one scan — 2026-09-22, built against the MSM AV scan whose
 * field has the subject at zero, a rival at zero, and four real shares.
 */
describe('Rankings shows the field divided at the latest scan', () => {
  it('draws the four real shares and lists the two zeros as sentences, never segments', () => {
    const html = rankings(msmAvHistory);
    expect(html).toContain('Share of voice in the latest scan');
    const segments = html.match(/<rect class="avp-share__seg[^"]*"/g) ?? [];
    expect(segments).toHaveLength(4);
    expect(html).toContain('Sweetwater');
    expect(html).toContain('Avalliance');
    expect(html).toContain('0%, named in none of the answers');
    expect(html).toContain('MSM AV was named in none of the answers, so it holds none of the field.');
  });

  it('a single scan gets the split AND the note that it is not yet a trend', () => {
    const html = rankings(msmAvHistory);
    expect(html).toContain('avp-share__svg');
    expect(html).toContain('One scan is not a trend yet');
    expect(html).not.toContain('avp-trend__svg');
  });

  it('three scans get the split above the trend', () => {
    const html = rankings(threeScanHistory);
    expect(html).toContain('avp-share__svg');
    expect(html).toContain('avp-trend__svg');
    expect(html.indexOf('avp-share__svg')).toBeLessThan(html.indexOf('avp-trend__svg'));
  });
});

describe('the Sources tab carries "Sites with the same name" — M1', () => {
  it('with a trend', () => {
    const html = sources(threeScanHistory);
    expect(html).toContain('Sites with the same name');
    expect(html).toContain('Who gets cited when engines answer');
  });

  it('and with no trend yet, because one finished scan already has proposals', () => {
    for (const h of [oneScanHistory, noScanHistory]) {
      const html = sources(h);
      expect(html).toContain('Sites with the same name');
      expect(html).not.toContain('Who gets cited when engines answer');
    }
  });

  it('draws nothing of it while the client is loading', () => {
    const html = renderToStaticMarkup(
      <ClientSourcesView state={{ kind: 'loading' }} me={clientsMe} />,
    );
    expect(html).not.toContain('Sites with the same name');
  });
});


/**
 * The note under a broken line is a sentence (F6): what the line does, why, and what is not
 * stated across it. "New questions from here" is the marker's own label, drawn by the chart; it
 * is not the note's lead-in. The apostrophe is the typographic one (U+2019), as the note writes it.
 */
const NOTE_ONE =
  'The line breaks where this client’s questions changed, because scans that asked different questions are not comparable. No change is stated across the break.';
const noteMany = (n: number) =>
  `The line breaks at each of the ${n} points where this client’s questions changed, because scans that asked different questions are not comparable. No change is stated across a break.`;
/** The note as a whole paragraph: nothing in front of the sentence, nothing after it. */
const asNote = (sentence: string) => `>${sentence}</p>`;
/** The hero chart's accessible label: what a screen reader reads of it. */
const heroChartLabel = (html: string) =>
  /aria-label="(AI Visibility Score across[^"]*)"/.exec(html)?.[1];

describe('new questions break the trends and the changes shown — F6', () => {
  const justAfterTheChange: ClientHistory = {
    ...newQuestionsHistory,
    scans: newQuestionsHistory.scans.slice(0, 4),
  };
  /** `newQuestionsHistory` (new questions at its fourth scan) with a new set at these scans too. */
  const alsoNewAt = (...indices: number[]): ClientHistory => ({
    ...newQuestionsHistory,
    scans: newQuestionsHistory.scans.map((s, i) =>
      indices.includes(i) ? { ...s, questionSet: { ...s.questionSet, isNew: true } } : s,
    ),
  });
  const twoBreaks = alsoNewAt(2);
  const fourBreaks = alsoNewAt(1, 2, 4);

  it('draws the break marker and says why, on Sources and Rankings', () => {
    for (const html of [sources(newQuestionsHistory), rankings(newQuestionsHistory)]) {
      expect(html).toContain('avp-trend__break');
      // The marker keeps its own label...
      expect(html).toContain('New questions from here');
      // ...and the note under the chart is a sentence of its own.
      expect(html).toContain(asNote(NOTE_ONE));
      expect(html.indexOf(NOTE_ONE)).toBeGreaterThan(html.indexOf('avp-trend__svg'));
    }
  });

  it('the note is a sentence: the marker’s label is no longer a lead-in in front of it', () => {
    for (const html of [
      sources(newQuestionsHistory),
      rankings(newQuestionsHistory),
      overview(newQuestionsHistory),
    ]) {
      expect(html).not.toContain('New questions from here:');
    }
  });

  it('counts the breaks when the questions changed more than once', () => {
    for (const [history, n] of [
      [twoBreaks, 2],
      [fourBreaks, 4],
    ] as const) {
      for (const html of [sources(history), rankings(history), overview(history)]) {
        expect(html).toContain(asNote(noteMany(n)));
        expect(html).not.toContain(NOTE_ONE);
      }
    }
  });

  it('QuestionBreakNote renders one paragraph, and nothing at all without a break', () => {
    const note = (h: ClientHistory) => renderToStaticMarkup(<QuestionBreakNote history={h} />);
    expect(note(threeScanHistory)).toBe('');
    expect(note(noScanHistory)).toBe('');
    expect(note(newQuestionsHistory)).toMatch(/^<p [^>]*>[^<]*<\/p>$/);
    expect(note(newQuestionsHistory)).toContain(asNote(NOTE_ONE));
    expect(note(twoBreaks)).toContain(asNote(noteMany(2)));
  });

  it('draws no marker and no note while every scan asked one set', () => {
    for (const html of [sources(threeScanHistory), rankings(threeScanHistory), overview(threeScanHistory)]) {
      expect(html).not.toContain('avp-trend__break');
      expect(html).not.toContain('New questions from here');
      expect(html).not.toContain('The line breaks');
    }
  });

  it('the hero states no change when the newest scan is the first of a new set', () => {
    expect(overview(justAfterTheChange)).not.toContain('avp-hero__delta');
  });

  it('the hero states the change once the new set has two readings', () => {
    expect(overview(newQuestionsHistory)).toContain('avp-hero__delta');
  });

  it('the hero chart is broken at the change', () => {
    expect(overview(newQuestionsHistory)).toContain('avp-trend__break');
  });

  describe('the Overview says it in words too', () => {
    const asideOf = (html: string) => {
      const from = html.indexOf('avp-hero__aside');
      return html.slice(from, html.indexOf('</section>', from));
    };

    it('puts the note under the hero chart, in a column with it rather than beside it', () => {
      const aside = asideOf(overview(newQuestionsHistory));
      expect(aside).toContain(asNote(NOTE_ONE));
      expect(aside.indexOf(NOTE_ONE)).toBeGreaterThan(aside.indexOf('</figure>'));
      // The aside is a flex row: a note placed beside the chart would sit at its side.
      expect(aside).toMatch(/avp-hero__aside"><div class="[^"]*\bflex-col\b[^"]*"><figure/);
    });

    it('leaves the aside as the chart alone while every scan asked one set', () => {
      expect(asideOf(overview(threeScanHistory))).toMatch(/^avp-hero__aside"><figure/);
    });

    it('names the break in the hero chart’s label, so it is heard as well as seen', () => {
      expect(heroChartLabel(overview(newQuestionsHistory))).toBe(
        'AI Visibility Score across 5 scans of Plausible Analytics: 40, 44, 46, 60, 63; new questions from 1 Sep 2026.',
      );
    });

    it('names every break in the label, as a list', () => {
      expect(heroChartLabel(overview(twoBreaks))).toBe(
        'AI Visibility Score across 5 scans of Plausible Analytics: 40, 44, 46, 60, 63; new questions from 20 Aug 2026 and 1 Sep 2026.',
      );
      expect(heroChartLabel(overview(fourBreaks))).toBe(
        'AI Visibility Score across 5 scans of Plausible Analytics: 40, 44, 46, 60, 63; new questions from 10 Aug 2026, 20 Aug 2026, 1 Sep 2026 and 10 Sep 2026.',
      );
    });

    it('names a day once when the questions changed twice within it', () => {
      // Re-running a client twice in an afternoon is normal: two breaks can share a UTC day.
      const base = alsoNewAt(4);
      const sameDay: ClientHistory = {
        ...base,
        scans: base.scans.map((s, i) => (i === 4 ? { ...s, scannedAt: '2026-09-01T09:00:00Z' } : s)),
      };
      const html = overview(sameDay);
      expect(heroChartLabel(html)).toBe(
        'AI Visibility Score across 5 scans of Plausible Analytics: 40, 44, 46, 60, 63; new questions from 1 Sep 2026.',
      );
      // The note still counts both: it is about points on the line, the label is about days.
      expect(html).toContain(asNote(noteMany(2)));
    });

    it('adds nothing to the label while every scan asked one set', () => {
      expect(heroChartLabel(overview(threeScanHistory))).toMatch(
        /^AI Visibility Score across 3 scans of Plausible Analytics: [\d, ]+\.$/,
      );
    });

    it('draws no note where there is no hero chart for it to describe', () => {
      // The questions did change before the newest scan, but with one reading there is no line.
      const oneReading: ClientHistory = {
        ...newQuestionsHistory,
        scans: newQuestionsHistory.scans.map((s, i) => (i < 4 ? { ...s, composite: null } : s)),
      };
      const html = overview(oneReading);
      expect(html).not.toContain('avp-trend__svg');
      expect(html).not.toContain('The line breaks');
      expect(html).not.toContain('New questions from here');
    });
  });

  it('a first scan marked new is no break, so no screen draws a note for it', () => {
    // Every first scan is "new" (nothing precedes it). A note is only reached by a later scan
    // that asked something else, and a later scan is also what makes a chart on Sources and
    // Rankings: where those screens say "one scan is not a trend yet", they say nothing of breaks.
    const markedNew = (h: ClientHistory): ClientHistory => ({
      ...h,
      scans: h.scans.map((s) => ({ ...s, questionSet: { ...s.questionSet, isNew: true } })),
    });
    for (const h of [oneScanHistory, msmAvHistory]) {
      for (const html of [overview(markedNew(h)), sources(markedNew(h)), rankings(markedNew(h))]) {
        expect(html).not.toContain('The line breaks');
        expect(html).not.toContain('avp-trend__break');
      }
    }
    // The branch the note once sat outside of: the share split drawn, the trend not.
    const html = rankings(markedNew(msmAvHistory));
    expect(html).toContain('avp-share__svg');
    expect(html).toContain('One scan is not a trend yet');
  });

  describe('the note is drawn only where the chart it describes is', () => {
    afterEach(() => {
      chartGate.noTrend = false;
    });

    it('Rankings and Sources draw no note when their trend is not drawn, whatever the history holds', () => {
      // The history does have a break: left alone, this very note is what it would say.
      expect(renderToStaticMarkup(<QuestionBreakNote history={newQuestionsHistory} />)).toContain(
        asNote(NOTE_ONE),
      );
      chartGate.noTrend = true;
      const drawn = [rankings(newQuestionsHistory), sources(newQuestionsHistory)];
      // Both screens did render...
      expect(drawn[0]).toContain('How the field is sharing the answers');
      expect(drawn[1]).toContain('Sites with the same name');
      for (const html of drawn) {
        // ...neither drew its trend...
        expect(html).not.toContain('avp-trend__svg');
        // ...so there is no line for a note to say where it breaks.
        expect(html).not.toContain('The line breaks');
      }
    });

    it('and Rankings draws it as soon as the trend is', () => {
      expect(rankings(newQuestionsHistory)).toContain(asNote(NOTE_ONE));
    });
  });
});
