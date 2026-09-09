/**
 * The Sentiment screen — Epic A.
 *
 * The assertions that matter are the ones that are silent failures if wrong:
 * an answer that never named the client read as a neutral, an engine outage
 * read as indifference, and a net of zero read as "not measured".
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientSentimentView } from './ClientSentimentView';
import { accentFor } from './clientNav';
import type { ClientDetailState } from './ClientDetailView';
import type { Client, ClientHistory } from '@avp/shared-types';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import {
  neverNamedHistory,
  noScanHistory,
  oneScanTonedHistory,
  threeScanHistory,
} from '@/lib/client/__fixtures__/history';

const CLIENT = identifiedClient as Client;

const ready = (history: ClientHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const render = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientSentimentView state={ready(h)} me={clientsMe} />);

describe('the tab exists and says whose space it is', () => {
  it('joins the client’s LocalNav as its own destination', () => {
    const html = render(threeScanHistory);
    expect(html).toContain('href="/clients/clnt_01AAA/sentiment"');
    expect(html).toContain('aria-current="page"');
  });

  it('carries a distinct bench accent, so the strip stays legible as it grows', () => {
    // Read from the nav table, not typed here. This asserted `bench-6` while
    // accents were global; Epic B.1 scoped them to clusters and Sentiment moved
    // into Measurement, which changed the hue without changing the property
    // being tested — the strip does not flatten into sameness.
    const accent = accentFor('sentiment');
    expect(accent).not.toBeNull();
    expect(render(threeScanHistory)).toContain(`--avp-bench-${accent! + 1}-600`);
  });

  it('sits in the Measurement cluster — tone is a scored dimension, not an analysis', () => {
    // 15% of the composite, stored since Epic 4. It reads what a scan recorded,
    // which is what the cluster means.
    const html = render(threeScanHistory);
    expect(html).toMatch(/<ul[^>]*aria-label="Measurement"/);
  });
});

describe('one scan is a first reading, and says so — design review 2026-09-02', () => {
  it('names the n of 1 rather than leaving the chart looking broken', () => {
    // At one scan the tide is three wide bars against a single x-tick. The
    // review's read was that it looks like the chart is malfunctioning rather
    // than correctly reporting that this is all the data there is.
    const html = render(oneScanTonedHistory);
    expect(html).toContain('One scan so far');
    expect(html).toContain('once a second scan runs');
  });

  it('does not say it once there IS a trend', () => {
    expect(render(threeScanHistory)).not.toContain('One scan so far');
  });

  it('still draws the tide — the note explains it, it does not replace it', () => {
    // `hasSentiment` argues correctly that one scan IS a readable tide. That
    // reasoning is not reverted here; the fix is copy, not a redesign.
    const html = render(oneScanTonedHistory);
    expect(html).toContain('avp-tide__svg');
    expect(html).not.toContain('Nothing measured yet');
  });

  it('is distinct from the empty state, which answers a different question', () => {
    // Zero scans is an absence of data; one scan is data with no trend yet.
    const empty = render(noScanHistory);
    expect(empty).not.toContain('One scan so far');
    expect(empty).toContain('Nothing measured yet');
  });
});

describe('“named no one” is never a neutral', () => {
  it('counts it as its own figure and says what it means', () => {
    const html = render(threeScanHistory);
    expect(html).toContain('Named no one');
    expect(html).toContain('tone was never asked');
    expect(html).toContain('Not a neutral');
  });

  it('keeps it out of the classified total', () => {
    // 26 + 18 + 21 across the three scans. The 20 unnamed answers must not
    // be in it.
    expect(render(threeScanHistory)).toContain('>65</dd>');
  });

  it('reports the unnamed answers separately', () => {
    // 8 + 6 + 6 = 20
    expect(render(threeScanHistory)).toContain('>20</dd>');
  });

  it('does not draw it as a segment on the chart', () => {
    // Eight engine-scans have tone (claude_search is absent from scan 2).
    // Three tone rects each = 24 modifier classes, and not one more for the
    // 20 unnamed answers.
    const html = render(threeScanHistory);
    expect((html.match(/avp-tide__seg--/g) ?? []).length).toBe(24);
  });
});

describe('an engine that did not answer is an outage, not indifference', () => {
  it('says so in the hidden data table', () => {
    // `claude_search` is absent from the partial scan.
    expect(render(threeScanHistory)).toContain('did not answer');
  });

  it('draws no column for it', () => {
    const html = render(threeScanHistory);
    // 3 + 2 + 3 = 8 bars, not the 9 a naive "every engine every scan" would
    // draw.
    expect((html.match(/class="avp-tide__bar"/g) ?? []).length).toBe(8);
  });
});

describe('the ledger states the numbers the chart can only show', () => {
  it('gives every engine a net with a sign', () => {
    const html = render(threeScanHistory);
    expect(html).toContain('Net tone, whole history');
    // chatgpt: (6-1) + (2-7) + (1-1) = 0 — a genuine zero, rendered with ±.
    expect(html).toContain('±0');
  });

  it('distinguishes a genuine zero from a missing measurement', () => {
    // A zero net means as much praise as criticism. It must not read the same
    // as an engine nobody classified.
    const html = render(threeScanHistory);
    expect(html).toContain('±0');
    expect(html).not.toContain('Not measured');
  });

  it('keeps the engine hue the same one the Prompts screen uses', () => {
    // An operator who learns "violet is ChatGPT" must not re-learn it one tab
    // across. ENGINE_ACCENT is shared, not copied.
    const html = render(threeScanHistory);
    expect(html).toContain('--avp-bench-5-600'); // chatgpt -> index 4
    expect(html).toContain('--avp-bench-1-600'); // claude  -> index 0
    expect(html).toContain('--avp-bench-3-600'); // claude_search -> index 2
  });
});

describe('one scan is enough for a tide', () => {
  it('draws the chart from the first scan, unlike the trend tabs', () => {
    // A single scan's split IS a finding; a single point on a line is not a
    // direction. `hasSentiment` is deliberately not `hasTrend`.
    const html = render(oneScanTonedHistory);
    expect(html).toContain('avp-tide__svg');
    expect(html).not.toContain('Nothing measured yet');
  });
});

describe('absence states say which absence it is', () => {
  it('a client scanned but never named reads as the finding, not a blank', () => {
    const html = render(neverNamedHistory);
    expect(html).toContain('No engine has described this client yet');
    expect(html).toContain('it is the finding');
    // And it says how many answers there were, so the claim is checkable.
    expect(html).toContain('16 answers so far');
  });

  it('a client never scanned reads differently, because the fix is different', () => {
    const html = render(noScanHistory);
    expect(html).toContain('Nothing measured yet');
    expect(html).not.toContain('it is the finding');
  });
});

describe('the accessibility contract survives the new chart', () => {
  it('keeps the aria-label and the hidden data table', () => {
    const html = render(threeScanHistory);
    expect(html).toContain('avp-visually-hidden');
    expect(html).toContain('<table>');
    expect(html).toMatch(/aria-label="Tone toward .* across 3 scans\./);
  });

  it('puts all four buckets in the table, so nothing is chart-only', () => {
    const html = render(threeScanHistory);
    for (const header of ['Positive', 'Neutral', 'Negative', 'Not named']) {
      expect(html).toContain(header);
    }
  });

  it('names the two Claude modes apart, because the difference is the finding', () => {
    const html = render(threeScanHistory);
    expect(html).toContain('Claude + search');
  });
});

describe('what this screen does not claim', () => {
  it('says sentiment is 15% of the composite rather than implying it is the score', () => {
    expect(render(threeScanHistory)).toContain('15% of the composite');
  });

  it('does not re-analyse anything — no answer text appears anywhere', () => {
    // There is none to appear: the facts-only rule means the text never left the
    // request that produced it. This asserts the screen reads labels only.
    const html = render(threeScanHistory);
    expect(html).not.toMatch(/answerText|responseText|"text":/);
  });
});
