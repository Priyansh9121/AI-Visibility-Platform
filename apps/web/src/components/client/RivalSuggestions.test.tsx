// @vitest-environment jsdom

/**
 * "Probable rivals" on a client's Competitors page, mounted — F2 (2026-10-09).
 *
 * The founder's scan: RMIT University, whose answers cited deakin.edu.au 6 times and named Deakin
 * University in 4 answers. The page offers it with the founder's sentence and adds it through the
 * existing PUT (`api.replaceCompetitors`), the client's current list plus this one. Held here: the
 * sentence and the button, and room for a long domain to wrap; the exact body of the save; the
 * polite status that follows, where focus goes, and that the status and any alert sit before the
 * rows, so the next Tab from the status is the next row's button and not the end of the card; the
 * API's own sentence for a refusal, with nothing changed, and a plain line when the problem carries
 * none; a full list, a name taken and a domain already there refused before any save; presses
 * ignored while a save runs; and nothing drawn while it reads, when the read fails, or when there
 * is nothing to offer.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type {
  Competitor,
  CompetitorInput,
  CompetitorSet,
  RivalSuggestion,
  RivalSuggestions as RivalSuggestionsBody,
} from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';
import { COMPETITOR_EDITOR_COPY } from '@/lib/report/strings';
import { RIVAL_SUGGESTIONS_COPY as COPY } from '@/lib/client/rivalSuggestions';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const rivalSuggestions = vi.fn<(id: string) => Promise<RivalSuggestionsBody>>();
const competitorSet = vi.fn<(id: string) => Promise<CompetitorSet | null>>();
const replaceCompetitors = vi.fn<(id: string, body: CompetitorInput[]) => Promise<CompetitorSet>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      rivalSuggestions: (id: string) => rivalSuggestions(id),
      competitorSet: (id: string) => competitorSet(id),
      replaceCompetitors: (id: string, body: CompetitorInput[]) => replaceCompetitors(id, body),
    },
  };
});

import { RivalSuggestions } from './RivalSuggestions';

// `__dirname`, not `import.meta.url`: this file runs under jsdom, where the latter is not a file URL.
const fixture = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../packages/shared-types/fixtures/probable-rival.json'),
    'utf8',
  ),
) as {
  suggestion: RivalSuggestion;
  sentence: string;
};
const DEAKIN: RivalSuggestion = fixture.suggestion;
const UNSW: RivalSuggestion = { domain: 'unsw.edu.au', name: 'UNSW', citations: 1, answersNaming: 1 };
const LATROBE: RivalSuggestion = {
  domain: 'latrobe.edu.au',
  name: 'Latrobe',
  citations: 3,
  answersNaming: 2,
};

const rival = (name: string, domain: string | null, rank = 1): Competitor => ({
  id: `comp_${name}`,
  name,
  domain,
  rank,
  detectionSource: 'both',
  serpMentions: 2,
  coCitationMentions: 2,
  corroborated: true,
  signalCount: 2,
  score: null,
  isManualOverride: false,
});
const listOf = (...competitors: Competitor[]): CompetitorSet =>
  ({ id: 'set_1', scanId: null, status: 'ok', competitors }) as unknown as CompetitorSet;

const MONASH = rival('Monash University', 'monash.edu');
const STATUS = '[data-testid="rival-suggestions-status"]';
const problem = (status: number, detail: string, errors?: { field: string; message: string; type: string }[]) =>
  new ApiProblem({
    type: '/problems/x',
    title: 'Problem',
    status,
    detail,
    instance: '/x',
    ...(errors ? { errors } : {}),
  } as never);

let host: HTMLDivElement;
let root: Root;
const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
const buttons = () => [...host.querySelectorAll('button')] as HTMLButtonElement[];
const byName = (n: string) => buttons().find((b) => b.getAttribute('aria-label') === n);

/** Every child of the card body, in page order, as `tag` or `tag[role]`. */
const bodyOrder = () =>
  [...host.querySelector('p')!.parentElement!.children].map((el) => {
    const role = el.getAttribute('role');
    return `${el.tagName.toLowerCase()}${role ? `[${role}]` : ''}`;
  });

/**
 * Where a Tab press from `from` lands. jsdom has no Tab key, so this is the browser's rule for the
 * case that matters here, a page with no positive tabindex: the next element after `from` in document
 * order whose tabIndex is not negative. The status region is focusable (tabindex -1) but is not a tab
 * stop; an aria-disabled button still is. Undefined means the press leaves the page's content.
 */
function tabStopAfter(from: Element): Element | undefined {
  const candidates = [
    ...document.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]'),
  ];
  if (candidates.some((el) => el.tabIndex > 0)) {
    throw new Error('A positive tabindex: the Tab order is no longer the document order.');
  }
  return candidates.find(
    (el) =>
      el.tabIndex >= 0 &&
      !(el as HTMLButtonElement).disabled &&
      !from.contains(el) &&
      (from.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
  );
}

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
}

async function mount(clientId = 'cli_1') {
  await act(async () => {
    root.render(<RivalSuggestions clientId={clientId} />);
  });
}

const deakinButton = () => byName('Add as a rival: deakin.edu.au')!;

beforeEach(() => {
  rivalSuggestions.mockReset();
  competitorSet.mockReset();
  replaceCompetitors.mockReset();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe('what the page offers', () => {
  it('prints the founder’s sentence for the founder’s scan, with the button beside it', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    await mount();

    expect(rivalSuggestions).toHaveBeenCalledWith('cli_1');
    const line = host.querySelector('li')!;
    expect(line.firstElementChild!.textContent).toBe(
      'deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?',
    );
    expect(line.firstElementChild!.textContent).toBe(fixture.sentence);
    expect(line.querySelector('button')!.textContent).toBe('Add as a rival');
  });

  it('agrees in number for a domain cited once and named in one answer', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [UNSW] });
    await mount();
    expect(host.querySelector('li')!.firstElementChild!.textContent).toBe(
      'unsw.edu.au: cited 1 time, named in 1 answer. Add it as a rival?',
    );
  });

  it('lists every suggestion, in the order the API gave', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    await mount();
    expect([...host.querySelectorAll('li')].map((li) => li.firstElementChild!.textContent)).toEqual([
      'deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?',
      'unsw.edu.au: cited 1 time, named in 1 answer. Add it as a rival?',
    ]);
  });

  it('titles the section as a second-level heading under the page’s own', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    await mount();
    const heading = host.querySelector('[role="heading"]')!;
    expect(heading.textContent).toBe('Probable rivals');
    expect(heading.getAttribute('aria-level')).toBe('2');
  });

  // At 320px a long domain is one unbreakable word. The row is a wrapping flex line, so the span that
  // holds the domain and its sentence must be allowed to shrink below that word (`min-w-0`) and to
  // break inside it (`break-words`), or the word pushes the card wider than the screen.
  it('lets a long domain wrap: the span with the domain and its sentence can shrink and breaks words', async () => {
    const LONG: RivalSuggestion = {
      domain: 'school-of-engineering-and-information-technology.faculty.edu.au',
      name: 'School',
      citations: 2,
      answersNaming: 1,
    };
    rivalSuggestions.mockResolvedValue({ suggestions: [LONG, DEAKIN] });
    await mount();

    const lines = [...host.querySelectorAll('li')];
    expect(lines).toHaveLength(2);
    for (const li of lines) {
      const sentence = li.firstElementChild!;
      expect(sentence.tagName).toBe('SPAN');
      expect(sentence.classList.contains('min-w-0')).toBe(true);
      expect(sentence.classList.contains('break-words')).toBe(true);
    }
    // It is the outer span, the one that holds the domain and the sentence, that carries them.
    expect(lines[0]!.firstElementChild!.textContent!.startsWith(LONG.domain)).toBe(true);
    expect(lines[0]!.firstElementChild!.querySelector('.font-medium')!.textContent).toBe(LONG.domain);
  });

  it('gives every button its own accessible name, which opens with its visible words', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    await mount();
    const names = buttons().map((b) => b.getAttribute('aria-label'));
    expect(names).toEqual(['Add as a rival: deakin.edu.au', 'Add as a rival: unsw.edu.au']);
    for (const b of buttons()) expect(b.getAttribute('aria-label')!.startsWith(b.textContent!)).toBe(true);
  });

  it('has the status region on the page from the first render, empty and polite', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    await mount();
    const status = host.querySelector(STATUS)!;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toBe('');
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('says only facts: a domain, two counts and the words around them', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    await mount();
    expect(
      [...host.querySelectorAll('[role="heading"], p, li > span:first-child, button')].map(
        (el) => el.textContent,
      ),
    ).toEqual([
      'Probable rivals',
      'Sites that share this client’s domain ending, were cited in the latest scan, and match a brand named in the scan’s answers.',
      COMPETITOR_EDITOR_COPY.openNote,
      'deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?',
      'Add as a rival',
    ]);
  });

  // The save is the competitor editor's: the WHOLE list is replaced, every rival on it is marked as
  // set by hand and the corroboration figure is cleared. The editor says so above its list; the card
  // says the same sentence, taken from the same constant, above its suggestions.
  it('says, once and above the suggestions, what saving does to the whole list: the editor’s sentence', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    await mount();

    const note = COMPETITOR_EDITOR_COPY.openNote;
    const notes = [...host.querySelectorAll('p')].filter((p) => p.textContent === note);
    expect(notes).toHaveLength(1);
    // Once in the card, not once a suggestion.
    expect(text().split(note).length - 1).toBe(1);
    // Above the list, in the card's existing note style: the intro's own classes, none new.
    expect(notes[0]!.compareDocumentPosition(host.querySelector('ul')!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(notes[0]!.className).toBe(host.querySelector('p')!.className);
  });

  it('takes the sentence from the editor’s copy and carries no copy of its words', () => {
    const source = readFileSync(join(__dirname, 'RivalSuggestions.tsx'), 'utf8');
    expect(source).toContain('COMPETITOR_EDITOR_COPY.openNote');
    expect(source).not.toContain(COMPETITOR_EDITOR_COPY.openNote.slice(0, 40));
  });
});

describe('what it draws when there is nothing to say', () => {
  it('does not say what saving does when there is nothing to save', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [] });
    await mount();
    expect(text()).not.toContain(COMPETITOR_EDITOR_COPY.openNote);
    expect(host.innerHTML).toBe('');
  });

  it('draws nothing while it reads', async () => {
    rivalSuggestions.mockReturnValue(new Promise(() => undefined));
    await mount();
    expect(host.innerHTML).toBe('');
  });

  it('draws nothing when there is nothing to suggest', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [] });
    await mount();
    expect(host.innerHTML).toBe('');
  });

  it('draws nothing, and says nothing, when the read fails', async () => {
    rivalSuggestions.mockRejectedValue(problem(500, 'Internal'));
    await mount();
    expect(host.innerHTML).toBe('');
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('reads again for another client and shows nothing of the first one’s', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    await mount('cli_1');
    expect(text()).toContain('deakin.edu.au');

    rivalSuggestions.mockResolvedValueOnce({ suggestions: [] });
    await mount('cli_2');
    expect(rivalSuggestions).toHaveBeenLastCalledWith('cli_2');
    expect(host.innerHTML).toBe('');
  });
});

describe('adding it as a rival', () => {
  it('reads the current list now and saves it plus this one, through the existing PUT', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockResolvedValue(listOf(MONASH, rival('Deakin', 'deakin.edu.au', 2)));
    await mount();

    await click(deakinButton());

    expect(competitorSet).toHaveBeenCalledWith('cli_1');
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    expect(replaceCompetitors).toHaveBeenCalledWith('cli_1', [
      { name: 'Monash University', domain: 'monash.edu' },
      { name: 'Deakin', domain: 'deakin.edu.au' },
    ]);
  });

  it('says, politely, that it is now a rival and that the next scan measures it, and moves focus there', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockResolvedValue(listOf(MONASH));
    await mount();

    await click(deakinButton());

    const status = host.querySelector(STATUS)!;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toBe('deakin.edu.au is now a rival. The next scan measures it.');
    expect(document.activeElement).toBe(status);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('takes the line away, keeps the other suggestions, and does not read the suggestions again', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockResolvedValue(listOf(MONASH));
    await mount();

    await click(deakinButton());

    expect(byName('Add as a rival: deakin.edu.au')).toBeUndefined();
    expect(byName('Add as a rival: unsw.edu.au')).toBeDefined();
    expect(host.querySelectorAll('li')).toHaveLength(1);
    expect(rivalSuggestions).toHaveBeenCalledTimes(1);
  });

  it('stops saying what saving does once the last suggestion is added, and says it while one is left', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockResolvedValue(listOf(MONASH));
    await mount();
    const note = COMPETITOR_EDITOR_COPY.openNote;

    await click(deakinButton());
    expect(text().split(note).length - 1).toBe(1); // one suggestion is left to save

    await click(byName('Add as a rival: unsw.edu.au')!);
    expect(host.querySelectorAll('li')).toHaveLength(0);
    expect(text()).not.toContain(note); // nothing is left to save
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'unsw.edu.au is now a rival. The next scan measures it.',
    );
  });

  it('keeps the section, with its status, after the last suggestion is added', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf());
    replaceCompetitors.mockResolvedValue(listOf());
    await mount();

    await click(deakinButton());

    expect(host.querySelectorAll('li')).toHaveLength(0);
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'deakin.edu.au is now a rival. The next scan measures it.',
    );
    expect(host.querySelector('[role="heading"]')!.textContent).toBe('Probable rivals');
  });

  it('saves just this one for a client with no list at all', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(null);
    replaceCompetitors.mockResolvedValue(listOf());
    await mount();

    await click(deakinButton());

    expect(replaceCompetitors).toHaveBeenCalledWith('cli_1', [{ name: 'Deakin', domain: 'deakin.edu.au' }]);
  });

  it('adds a second suggestion to the list as it now stands, not as it stood', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValueOnce(listOf(MONASH));
    replaceCompetitors.mockResolvedValueOnce(listOf(MONASH));
    await mount();
    await click(deakinButton());

    competitorSet.mockResolvedValueOnce(listOf(MONASH, rival('Deakin', 'deakin.edu.au', 2)));
    replaceCompetitors.mockResolvedValueOnce(listOf());
    await click(byName('Add as a rival: unsw.edu.au')!);

    expect(replaceCompetitors).toHaveBeenLastCalledWith('cli_1', [
      { name: 'Monash University', domain: 'monash.edu' },
      { name: 'Deakin', domain: 'deakin.edu.au' },
      { name: 'UNSW', domain: 'unsw.edu.au' },
    ]);
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'unsw.edu.au is now a rival. The next scan measures it.',
    );
  });
});

// The status region takes focus when the row a press was on has gone. It sits before the rows, so
// the next Tab from it is the next row's button; after the rows, that Tab would leave the card over
// every row still waiting.
describe('where the status and the alert sit', () => {
  it('puts the status before the rows, after the intro and the note', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    await mount();

    expect(bodyOrder()).toEqual(['p', 'p', 'div[status]', 'ul']);
    expect(host.querySelector(STATUS)!.compareDocumentPosition(host.querySelector('ul')!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('puts the alert between the status and the rows', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(problem(409, 'A scan of this client is running.'));
    await mount();

    await click(deakinButton());

    expect(bodyOrder()).toEqual(['p', 'p', 'div[status]', 'p[alert]', 'ul']);
  });

  it('leaves the intro and the status alone, with no note and no rows, once the last suggestion is added', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf());
    replaceCompetitors.mockResolvedValue(listOf());
    await mount();

    await click(deakinButton());

    expect(bodyOrder()).toEqual(['p', 'div[status]']);
  });

  it('makes the next Tab from the focused status the next suggestion’s button, and so on down the rows', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW, LATROBE] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockResolvedValue(listOf(MONASH));
    await mount();

    await click(deakinButton());

    const status = host.querySelector(STATUS)!;
    expect(document.activeElement).toBe(status);
    expect(tabStopAfter(status)).toBe(byName('Add as a rival: unsw.edu.au'));
    expect(tabStopAfter(byName('Add as a rival: unsw.edu.au')!)).toBe(
      byName('Add as a rival: latrobe.edu.au'),
    );
    // Only past the last row does a Tab leave the card.
    expect(tabStopAfter(byName('Add as a rival: latrobe.edu.au')!)).toBeUndefined();
  });
});

describe('a refusal', () => {
  it('shows the API’s own sentence, changes nothing and keeps the line', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    const sentence = 'A scan of this client is running. Name the rivals after it finishes.';
    replaceCompetitors.mockRejectedValue(problem(409, sentence));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(sentence);
    expect(host.querySelectorAll('li')).toHaveLength(2);
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(deakinButton().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('names the row a 422 is about, in the editor’s shape', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    const sentence = 'That is a platform or publisher, so it cannot be added as a rival.';
    replaceCompetitors.mockRejectedValue(
      problem(422, 'One or more fields were invalid.', [
        { field: 'competitors.1.domain', message: sentence, type: 'invalid_domain' },
      ]),
    );
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(`Deakin: ${sentence}`);
    expect(byName('Add as a rival: deakin.edu.au')).toBeDefined();
  });

  it('says the problem’s detail for a 422 that names no row, and a plain line for a failure with none', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf());
    replaceCompetitors.mockRejectedValueOnce(problem(422, 'One or more fields were invalid.'));
    await mount();
    await click(deakinButton());
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('One or more fields were invalid.');

    replaceCompetitors.mockRejectedValueOnce(new Error('offline'));
    await click(deakinButton());
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The change may not have been saved. Try again.',
    );
  });

  it('never leaves the alert blank: a problem with no field errors, no detail and no title says the plain line', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(
      new ApiProblem({ type: '/problems/x', title: '', status: 502, detail: '', instance: '/x' }),
    );
    await mount();

    await click(deakinButton());

    const alert = host.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toBe(COPY.saveFailed);
    expect(host.querySelectorAll('li')).toHaveLength(2);
    expect(deakinButton().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('clears a refusal when the next press succeeds', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf());
    replaceCompetitors.mockRejectedValueOnce(problem(409, 'A scan of this client is running.'));
    await mount();
    await click(deakinButton());
    expect(host.querySelector('[role="alert"]')).not.toBeNull();

    replaceCompetitors.mockResolvedValueOnce(listOf());
    await click(deakinButton());
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'deakin.edu.au is now a rival. The next scan measures it.',
    );
  });

  it('refuses a full list before any save, in the editor’s sentence', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(
      listOf(...Array.from({ length: 10 }, (_, i) => rival(`Rival ${i}`, `r${i}.example`, i + 1))),
    );
    await mount();

    await click(deakinButton());

    expect(replaceCompetitors).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('A set carries at most 10 competitors.');
    expect(byName('Add as a rival: deakin.edu.au')).toBeDefined();
  });

  it('refuses a name already on the list before any save', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(rival('deakin', 'deakin.com')));
    await mount();

    await click(deakinButton());

    expect(replaceCompetitors).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('Deakin is already on the list.');
  });

  it('does not save a domain somebody has added since, and says it is already a rival', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(rival('Deakin University', 'deakin.edu.au')));
    await mount();

    await click(deakinButton());

    expect(replaceCompetitors).not.toHaveBeenCalled();
    expect(host.querySelector(STATUS)!.textContent).toBe('deakin.edu.au is already a rival.');
    expect(byName('Add as a rival: deakin.edu.au')).toBeUndefined();
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('says so, and saves nothing, when the current list cannot be read', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockRejectedValue(problem(500, 'Internal'));
    await mount();

    await click(deakinButton());

    expect(replaceCompetitors).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The current list of rivals could not be read, so nothing was added. Try again.',
    );
  });
});

describe('while a save runs', () => {
  it('is aria-disabled, not disabled, keeps its focus and ignores a second press', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    let finish!: (v: CompetitorSet) => void;
    replaceCompetitors.mockReturnValue(new Promise((r) => (finish = r)));
    await mount();
    const button = deakinButton();
    button.focus();

    await click(button);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(button);
    await click(button);
    await click(byName('Add as a rival: unsw.edu.au')!);
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);

    await act(async () => finish(listOf(MONASH)));
    expect(byName('Add as a rival: unsw.edu.au')!.getAttribute('aria-disabled')).toBe('false');
  });

  it('sets nothing once it has been unmounted', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf());
    let finish!: (v: CompetitorSet) => void;
    replaceCompetitors.mockReturnValue(new Promise((r) => (finish = r)));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await mount();
    await click(deakinButton());

    act(() => root.unmount());
    await act(async () => finish(listOf()));

    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
    root = createRoot(host);
  });
});
