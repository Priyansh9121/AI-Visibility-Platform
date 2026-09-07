/**
 * The AI crawlers screen — Epic F.
 *
 * The assertion that matters most here is the VOCABULARY one. This screen
 * reads a stated policy and must never imply an observed visit, and that
 * constraint is the kind that erodes one reasonable-looking copy edit at a
 * time — "AI crawler activity" reads better than "what this site asks AI
 * crawlers to do", right up until it is claiming something the product cannot
 * measure. A test is better at holding that line than a comment.
 *
 * The second is the unreadable state: a robots.txt that could not be fetched
 * must not render as a permissive grid, which is the same house rule Epics A,
 * B and E each landed on from a different direction.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientCrawlerView, type CrawlerAccessState } from './ClientCrawlerView';
import type { ClientDetailState } from './ClientDetailView';
import type { Client, ClientHistory, CrawlerAccess } from '@avp/shared-types';
import { BENCH_ACCENTS } from '@avp/design-system';
import { accentFor } from './clientNav';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { threeScanHistory } from '@/lib/client/__fixtures__/history';
import {
  blanketBlockAccess,
  notionAccess,
  permissiveAccess,
  silentAccess,
  unreadableAccess,
} from '@/lib/client/__fixtures__/crawlerAccess';

const CLIENT = identifiedClient as Client;

const ready = (history: ClientHistory = threeScanHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const render = (access: CrawlerAccessState, onSelectScan?: (id: string) => void) =>
  renderToStaticMarkup(
    <ClientCrawlerView
      state={ready()}
      access={access}
      me={clientsMe}
      {...(onSelectScan ? { onSelectScan } : {})}
    />,
  );

const shown = (data: CrawlerAccess | null): CrawlerAccessState => ({
  kind: 'ready',
  data,
});

describe('the tab exists and takes the seat B.1 reserved', () => {
  it('joins the client’s LocalNav', () => {
    const html = render(shown(notionAccess));
    expect(html).toContain('href="/clients/clnt_01AAA/crawler"');
    expect(html).toContain('aria-current="page"');
  });

  it('wears its own cluster hue, taken from the nav table', () => {
    // Never a second literal: if the nav table moves this section, the screen
    // follows. Epic B.1 rewrote two tests that asserted a hue directly, after
    // a moved accent left them green and proving nothing.
    const html = render(shown(notionAccess));
    const accent = accentFor('crawler');
    expect(accent).not.toBeNull();
    expect(html).toContain(`--avp-bench-${accent! + 1}-600`);
  });
});

describe('it reports a stated policy and never an observed visit', () => {
  /*
   * The premise correction that defined this epic, asserted rather than
   * trusted. "Crawler activity" was the roadmap's name and would have needed
   * server logs this product does not ingest.
   */
  const FORBIDDEN = [
    'activity',
    'is crawling',
    'has crawled',
    'has visited',
    'visits',
    'traffic',
    'hits',
    'last seen',
    'crawled your',
  ];

  for (const fixture of [notionAccess, silentAccess, permissiveAccess, unreadableAccess]) {
    it(`claims no crawler behaviour for ${fixture.urlAudited}`, () => {
      const text = render(shown(fixture)).replace(/<[^>]*>/g, ' ').toLowerCase();
      for (const phrase of FORBIDDEN) {
        expect(text, `screen copy claims "${phrase}"`).not.toContain(phrase);
      }
    });
  }

  it('says in words that this is what the site asks for', () => {
    const html = render(shown(notionAccess));
    expect(html).toContain('asks AI crawlers to do');
    // And that a stated policy is not proof of compliance.
    expect(html).toContain('ignores robots.txt');
  });
});

describe('the finding leads', () => {
  it('puts the blocked search crawler in the first group', () => {
    const html = render(shown(notionAccess));
    const searchHeading = html.indexOf('Search and citation');
    const trainingHeading = html.indexOf('Model training');
    const amazonbot = html.indexOf('Amazonbot');
    expect(searchHeading).toBeGreaterThan(-1);
    // Ordered by what a block costs, so search leads training.
    expect(searchHeading).toBeLessThan(trainingHeading);
    // And the one blocked row sits under that first heading.
    expect(amazonbot).toBeGreaterThan(searchHeading);
    expect(amazonbot).toBeLessThan(trainingHeading);
  });

  it('separates the citation cost from the raw block count', () => {
    // `searchBlocked` is the figure with a cost attached. Blocking a training
    // crawler is a rights decision; blocking a search one removes the site
    // from the index engines cite.
    const html = render(shown(notionAccess));
    expect(html).toContain('Costing citations');
  });

  it('explains what each group’s block would cost, next to the rows', () => {
    // The line that earns the purpose grouping. Without it the headings are a
    // taxonomy that names categories and teaches nothing.
    const html = render(shown(notionAccess));
    expect(html).toContain('index an engine cites from');
    expect(html).toContain('rights decision');
  });
});

describe('the accented tiles are far enough apart in hue to be told apart', () => {
  /*
   * The bug a browser found and no test could: the first draft's tile offsets
   * were `+0, +1, +2`, and at this section's accent that resolved to bench-6
   * (hue 343) and bench-7 (hue 355) — TWELVE degrees apart, the tightest
   * neighbouring pair in the layer. On the one real client with a finding both
   * tiles read "1" in visibly the same red-pink.
   *
   * Asserted as a PROPERTY across every seat rather than against this
   * section's current accent, because the failure was caused by a nav position
   * and would come straight back if the nav table moved this screen.
   */
  const STRIDE = 3;
  const MIN_SEPARATION = 30;

  it('keeps the accented tiles apart wherever the nav puts this section', () => {
    for (let seat = 0; seat < BENCH_ACCENTS.length; seat++) {
      const a = BENCH_ACCENTS[seat % BENCH_ACCENTS.length]!;
      const b = BENCH_ACCENTS[(seat + STRIDE) % BENCH_ACCENTS.length]!;
      const gap = Math.abs(a.hue - b.hue);
      expect(
        gap,
        `seat ${seat}: ${a.name} (${a.hue}) and ${b.name} (${b.hue}) are ${gap}deg apart`,
      ).toBeGreaterThanOrEqual(MIN_SEPARATION);
    }
  });

  it('rejects the adjacent offsets the first draft used', () => {
    // The negative control. Without it the test above could pass because the
    // stride happens to work, rather than because adjacent offsets fail.
    const adjacentGaps = BENCH_ACCENTS.map((a, i) => {
      const b = BENCH_ACCENTS[(i + 1) % BENCH_ACCENTS.length]!;
      return Math.abs(a.hue - b.hue);
    });
    expect(Math.min(...adjacentGaps)).toBeLessThan(MIN_SEPARATION);
  });

  it('accents exactly the two tiles that are findings', () => {
    // Four accents off a 97-degree arc is what produced the collision. Two of
    // these tiles are findings; the other two are the context explaining them.
    const html = render(shown(notionAccess));
    const tiles = html.slice(html.indexOf('avp-tile-row'), html.indexOf('</dl>'));
    const accented = [...tiles.matchAll(/--avp-bench-\d-600/g)].length;
    expect(accented).toBe(2);
  });
});

describe('a zero does not look like a finding', () => {
  /*
   * Scoped to the tile row, and that scoping is the point rather than
   * tidiness. The section's own LocalNav item carries the SAME
   * `--avp-bench-6-600` variable — correctly, it is the section's identity —
   * so a whole-page assertion here reports the nav and never looks at a tile.
   * The first draft of this test did exactly that and failed against a screen
   * that was already behaving correctly.
   */
  const tileRow = (html: string): string => {
    const start = html.indexOf('avp-tile-row');
    expect(start, 'no tile row rendered').toBeGreaterThan(-1);
    return html.slice(start, html.indexOf('</dl>', start));
  };

  it('drops the accent on a count of zero', () => {
    // The design review's rule. `permissiveAccess` blocks nothing, so neither
    // "Blocked" nor "Costing citations" may carry a bench rail.
    const tiles = tileRow(render(shown(permissiveAccess)));
    expect(tiles).toContain('Blocked');
    expect(tiles).not.toContain('--avp-bench');
  });

  it('keeps the accent when there is something to report', () => {
    // The positive control for the test above: without it, that assertion
    // could pass because the screen stopped accenting tiles altogether.
    const tiles = tileRow(render(shown(notionAccess)));
    const accent = accentFor('crawler')!;
    expect(tiles).toContain(`--avp-bench-${accent + 1}-600`);
  });
});

describe('silence is a finding, and unreadable is not silence', () => {
  it('names the common case rather than reporting nothing', () => {
    // Most real client domains name no AI crawler at all. That is a decision
    // nobody made, not an absence of data, and it is the thing an agency is
    // there to point out.
    const html = render(shown(silentAccess));
    expect(html).toContain('No rule on this site names a single AI crawler');
  });

  it('does not claim to have read a file it could not read', () => {
    /*
     * Found by driving the one client whose domain does not resolve: the lead
     * opened "Read from …/robots.txt on Aug 22, 2026" directly above a card
     * saying the file could not be read. Two contradictory sentences, and the
     * confident one came first.
     */
    const html = render(shown(unreadableAccess));
    expect(html).toContain('This scan tried to read');
    expect(html).not.toContain('Read from');
    // ...and the readable case still says it plainly.
    expect(render(shown(notionAccess))).toContain('Read from');
  });

  it('refuses to draw an unreadable robots.txt as permissive', () => {
    const html = render(shown(unreadableAccess));
    expect(html).toContain('could not');
    // The inference is named AND declined, rather than quietly acted on.
    expect(html).toContain('inference, not a reading');
    // No permissive tally is drawn at all in this state.
    expect(html).not.toContain('Not mentioned');
  });

  it('draws no badge for a crawler no rule names', () => {
    /*
     * A badge means a rule applies; its absence means none does. That is the
     * literal difference between `allowed` and `unspecified`, and drawing it
     * structurally is what keeps them apart without ranking one above the
     * other.
     */
    const html = render(shown(silentAccess));
    expect(html).not.toContain('avp-badge--danger');
    expect(html).toContain('Not mentioned');
  });

  it('never tones an allowed crawler as a success', () => {
    // Green would read as "you are doing this right", and for a TRAINING
    // crawler that is a rights decision this product takes no position on.
    const html = render(shown(permissiveAccess));
    expect(html).not.toContain('avp-badge--success');
  });
});

describe('the rule that decided a verdict is on the row', () => {
  it('distinguishes a named block from an inherited one', () => {
    // An agency saying "you are blocking Amazonbot" needs to know whether the
    // client typed the name or inherited a blanket rule. Different fixes.
    expect(render(shown(notionAccess))).toContain('named as amazonbot');
    expect(render(shown(blanketBlockAccess))).toContain('via User-agent: *');
  });

  it('does not repeat the wildcard on every ordinary allowed row', () => {
    /*
     * Found by driving the real screen: thirteen of fourteen rows carried
     * "via User-agent: *", restating the default until it was wallpaper and
     * drowning the one row that named a crawler. The line survives only where
     * it says something — an explicitly named agent, or a block.
     */
    const html = render(shown(notionAccess));
    expect(html).not.toContain('via User-agent: *');
    expect(render(shown(permissiveAccess))).not.toContain('via User-agent: *');
  });

  it('keeps the vendor legible rather than making it the quietest thing', () => {
    // Epic E's finding, applied before it could repeat: with three OpenAI
    // rows carrying different verdicts, the vendor is the discriminator.
    const html = render(shown(notionAccess));
    expect(html).toContain('text-ui-sm text-text-secondary">OpenAI');
  });
});

describe('states', () => {
  it('offers a scan picker only when there is more than one scan', () => {
    const many = render(shown(notionAccess), () => {});
    expect(many).toContain('scan_01NOTION_OLD');
    const one = render(shown(silentAccess), () => {});
    expect(one).not.toContain('<select');
  });

  it('dims rather than unmounting while a scan is being swapped', () => {
    // Epic B found the alternative live: resetting to a spinner unmounted the
    // picker that had just been used.
    const html = renderToStaticMarkup(
      <ClientCrawlerView
        state={ready()}
        access={{ kind: 'ready', data: notionAccess, pending: true }}
        me={clientsMe}
        onSelectScan={() => {}}
      />,
    );
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('scan_01NOTION');
  });

  it('says nothing has been recorded rather than nothing being wrong', () => {
    const html = render(shown(null));
    expect(html).toContain('No crawler policy recorded yet');
    expect(html).toContain('not a finding about the site');
  });

  it('renders the error state', () => {
    const html = render({
      kind: 'error',
      title: 'This crawler policy could not be loaded',
      detail: 'The request did not complete.',
    });
    expect(html).toContain('This crawler policy could not be loaded');
  });
});
