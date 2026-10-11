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
import { LIST_CHANGED_COPY } from '@/lib/competitorListChange';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const rivalSuggestions = vi.fn<(id: string) => Promise<RivalSuggestionsBody>>();
const competitorSet = vi.fn<(id: string) => Promise<CompetitorSet | null>>();
const replaceCompetitors =
  vi.fn<(id: string, body: CompetitorInput[], version: string | null) => Promise<CompetitorSet>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      rivalSuggestions: (id: string) => rivalSuggestions(id),
      competitorSet: (id: string) => competitorSet(id),
      replaceCompetitors: (id: string, body: CompetitorInput[], version: string | null) =>
        replaceCompetitors(id, body, version),
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
// The version a list is read at (launch checklist 3.113): a save names it.
const READ = 'a'.repeat(32);
const listAt = (version: string, ...competitors: Competitor[]): CompetitorSet =>
  ({ id: 'set_1', scanId: null, status: 'ok', version, competitors }) as unknown as CompetitorSet;
const listOf = (...competitors: Competitor[]): CompetitorSet => listAt(READ, ...competitors);

const MONASH = rival('Monash University', 'monash.edu');
const LATROBE_RIVAL = rival('Latrobe', 'latrobe.edu.au', 2);
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

/** The API's refusal of a save made from a list that has changed, with the list as it now stands. */
const listChangedBy = (current: CompetitorSet | null) =>
  new ApiProblem({
    type: '/problems/competitor-list-changed',
    title: 'The competitor list changed',
    status: 409,
    detail: 'The list changed since you read it.',
    instance: '/x',
    current,
  } as never);

let host: HTMLDivElement;
let root: Root;
const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
const buttons = () => [...host.querySelectorAll('button')] as HTMLButtonElement[];
const byName = (n: string) => buttons().find((b) => b.getAttribute('aria-label') === n);
/** What each alert on the page says, in page order. */
const alertTexts = () => [...host.querySelectorAll('[role="alert"]')].map((el) => el.textContent);

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
    expect(replaceCompetitors).toHaveBeenCalledWith(
      'cli_1',
      [
        { name: 'Monash University', domain: 'monash.edu' },
        { name: 'Deakin', domain: 'deakin.edu.au' },
      ],
      READ,
    );
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

    // It read no list, so it names none.
    expect(replaceCompetitors).toHaveBeenCalledWith(
      'cli_1',
      [{ name: 'Deakin', domain: 'deakin.edu.au' }],
      null,
    );
  });

  it('adds a second suggestion to the list as it now stands, not as it stood', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValueOnce(listOf(MONASH));
    replaceCompetitors.mockResolvedValueOnce(listOf(MONASH));
    await mount();
    await click(deakinButton());

    const second = 'b'.repeat(32);
    competitorSet.mockResolvedValueOnce(listAt(second, MONASH, rival('Deakin', 'deakin.edu.au', 2)));
    replaceCompetitors.mockResolvedValueOnce(listOf());
    await click(byName('Add as a rival: unsw.edu.au')!);

    // The version it names is that of the list it read for THIS press, not the first one's.
    expect(replaceCompetitors).toHaveBeenLastCalledWith(
      'cli_1',
      [
        { name: 'Monash University', domain: 'monash.edu' },
        { name: 'Deakin', domain: 'deakin.edu.au' },
        { name: 'UNSW', domain: 'unsw.edu.au' },
      ],
      second,
    );
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

// Launch checklist 3.113. The card reads the list as it saves and names the version it read; if the
// list changed in between, the API writes nothing and says so. The card says what changed, from the
// list it read and the list the API sends back, and offers to read the suggestions again.
describe('a save refused because the list changed', () => {
  const refreshButton = () => buttons().find((b) => b.textContent === COPY.refresh);

  it('says what was added, saves nothing, keeps the line and offers to refresh the suggestions', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The client’s list changed just now: Latrobe (latrobe.edu.au) was added. Your change was not saved.',
    );
    expect(host.querySelectorAll('li')).toHaveLength(2);
    expect(deakinButton()).toBeDefined();
    // Nothing was saved, so nothing is said as if it had been.
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(refreshButton()!.textContent).toBe('Refresh the suggestions');
    expect(deakinButton().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('says what was removed', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH, LATROBE_RIVAL));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH)));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The client’s list changed just now: Latrobe (latrobe.edu.au) was removed. Your change was not saved.',
    );
  });

  it('names no one when the lists hold the same rivals, and uses the card’s own words for when', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH)));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The client’s list changed just now. Your change was not saved.',
    );
    expect(LIST_CHANGED_COPY.justNow).toBe('just now');
  });

  it('names no one when the refusal carries no list it can read', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(null));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The client’s list changed just now. Your change was not saved.',
    );
    expect(refreshButton()).toBeDefined();
  });

  it('puts the control between the alert and the rows, as the alert sits before them', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();

    await click(deakinButton());

    expect(bodyOrder()).toEqual(['p', 'p', 'div[status]', 'p[alert]', 'div', 'ul']);
  });

  it('offers the control only for this refusal, not for the others', async () => {
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(problem(409, 'A scan of this client is running.'));
    await mount();

    await click(deakinButton());

    expect(host.querySelector('[role="alert"]')!.textContent).toBe('A scan of this client is running.');
    expect(refreshButton()).toBeUndefined();
  });

  it('reads the suggestions again when the control is pressed, and says so politely', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    expect(rivalSuggestions).toHaveBeenCalledTimes(1);

    // Somebody added Deakin: it is no longer a suggestion.
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [UNSW] });
    await click(refreshButton()!);

    expect(rivalSuggestions).toHaveBeenCalledTimes(2);
    expect(byName('Add as a rival: deakin.edu.au')).toBeUndefined();
    expect(byName('Add as a rival: unsw.edu.au')).toBeDefined();
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(refreshButton()).toBeUndefined();
    const status = host.querySelector(STATUS)!;
    expect(status.textContent).toBe('Showing the latest suggestions.');
    expect(document.activeElement).toBe(status);
  });

  it('keeps the sentence and the control, and says it could not refresh, when the read fails', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());

    const refusal =
      'The client’s list changed just now: Latrobe (latrobe.edu.au) was added. Your change was not saved.';
    expect(alertTexts()).toEqual([refusal]);

    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);

    // The sentence that the add was not saved is STILL shown: a failed refresh does not replace it.
    // The failure is a line of its own, after it.
    expect(alertTexts()).toEqual([refusal, 'The suggestions could not be refreshed. Try again.']);
    expect(refreshButton()).toBeDefined();
    expect(byName('Add as a rival: deakin.edu.au')).toBeDefined();
    // Nothing is said as if it had worked.
    expect(host.querySelector(STATUS)!.textContent).toBe('');
  });

  it('puts the line that it could not refresh after the sentence about the list and before the control', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);

    expect(bodyOrder()).toEqual(['p', 'p', 'div[status]', 'p[alert]', 'p[alert]', 'div', 'ul']);
  });

  // A state set to the value it already holds changes nothing on the page, so a second identical
  // failure would not be announced and the press would look dead. The line is taken away when the
  // press starts and put back when it fails, as a new alert, which a screen reader announces.
  it('announces a second failed refresh again: the line goes while it reads and comes back as a new alert', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    const refusal = alertTexts()[0];
    const failure = () =>
      [...host.querySelectorAll('[role="alert"]')].find((p) => p.textContent === COPY.refreshFailed);

    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);
    const first = failure();
    expect(first).toBeDefined();

    let fail!: (reason: unknown) => void;
    rivalSuggestions.mockReturnValueOnce(
      new Promise<RivalSuggestionsBody>((_, reject) => (fail = reject)),
    );
    await click(refreshButton()!);
    // While the second press reads, the first failure's line is not left standing...
    expect(failure()).toBeUndefined();
    // ...and the refusal's sentence is: the add was still not saved.
    expect(alertTexts()).toEqual([refusal]);
    expect(refreshButton()!.getAttribute('aria-disabled')).toBe('true');

    await act(async () => fail(problem(500, 'Internal')));
    const second = failure();
    expect(second).toBeDefined();
    // A new element on the page, not the first one left as it was.
    expect(second).not.toBe(first);
    expect(alertTexts()).toEqual([refusal, COPY.refreshFailed]);
    expect(refreshButton()!.getAttribute('aria-disabled')).toBe('false');
    expect(rivalSuggestions).toHaveBeenCalledTimes(3);
  });

  it('takes the line that it could not refresh away when a refresh works, with the sentence about the list', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);
    expect(alertTexts()).toHaveLength(2);

    rivalSuggestions.mockResolvedValueOnce({ suggestions: [UNSW] });
    await click(refreshButton()!);

    expect(alertTexts()).toEqual([]);
    expect(refreshButton()).toBeUndefined();
    expect(host.querySelector(STATUS)!.textContent).toBe('Showing the latest suggestions.');
  });

  it('takes the line that it could not refresh away when the next add is pressed', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);
    expect(alertTexts()).toContain(COPY.refreshFailed);

    await click(byName('Add as a rival: unsw.edu.au')!);

    // The press has its own answer; the failure of an earlier refresh is not carried along with it.
    expect(alertTexts()).toEqual([
      'The client’s list changed just now: Latrobe (latrobe.edu.au) was added. Your change was not saved.',
    ]);
  });

  it('shows nothing of the first client’s failed refresh under another client', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount('cli_1');
    await click(deakinButton());
    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);
    expect(alertTexts()).toContain(COPY.refreshFailed);

    rivalSuggestions.mockResolvedValueOnce({ suggestions: [UNSW] });
    await mount('cli_2');

    expect(rivalSuggestions).toHaveBeenLastCalledWith('cli_2');
    expect(byName('Add as a rival: unsw.edu.au')).toBeDefined();
    expect(alertTexts()).toEqual([]);
    expect(refreshButton()).toBeUndefined();
  });

  // The Refresh control stands beside the sentence about a refused add, so an add can be pressed
  // while it reads. That press would clear the sentence and its control, read the list and save, and
  // the read landing after it would clear whatever the press had said. So the press is ignored, as
  // one is while a save runs. The buttons stay enabled (aria-disabled, not disabled), so a focused
  // one keeps its focus.
  it('ignores an add pressed while the suggestions are being read again: nothing is read, saved or changed', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN, UNSW] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());
    const refusal = alertTexts();
    expect(refusal).toHaveLength(1);
    expect(competitorSet).toHaveBeenCalledTimes(1);
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);

    // Refresh, and keep its read pending.
    let land!: (found: RivalSuggestionsBody) => void;
    rivalSuggestions.mockReturnValueOnce(new Promise<RivalSuggestionsBody>((r) => (land = r)));
    await click(refreshButton()!);
    expect(refreshButton()!.getAttribute('aria-disabled')).toBe('true');

    const unsw = byName('Add as a rival: unsw.edu.au')!;
    unsw.focus();
    await click(unsw);

    // Nothing was read and nothing was saved.
    expect(competitorSet).toHaveBeenCalledTimes(1);
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    // Nothing changed: the sentence and its control are where they were, and nothing was said.
    expect(alertTexts()).toEqual(refusal);
    expect(refreshButton()).toBeDefined();
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(host.querySelectorAll('li')).toHaveLength(2);
    // The button is enabled, so it kept its focus; and it says it is unavailable, so the press is
    // not a dead one to a screen reader.
    expect(unsw.disabled).toBe(false);
    expect(unsw.getAttribute('aria-disabled')).toBe('true');
    expect(deakinButton().disabled).toBe(false);
    expect(deakinButton().getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(unsw);

    // The read lands: the card shows it, as it would have without the press.
    await act(async () => land({ suggestions: [UNSW] }));
    expect(host.querySelector(STATUS)!.textContent).toBe('Showing the latest suggestions.');
    expect(alertTexts()).toEqual([]);
    expect(byName('Add as a rival: deakin.edu.au')).toBeUndefined();
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);

    // And an add pressed after that is taken as it always was.
    expect(byName('Add as a rival: unsw.edu.au')!.getAttribute('aria-disabled')).toBe('false');
    replaceCompetitors.mockResolvedValueOnce(listOf(MONASH));
    await click(byName('Add as a rival: unsw.edu.au')!);
    expect(competitorSet).toHaveBeenCalledTimes(2);
    expect(replaceCompetitors).toHaveBeenCalledTimes(2);
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'unsw.edu.au is now a rival. The next scan measures it.',
    );
  });

  // The card stays up after a refresh, because it has just said something. With no rows under it, a
  // line that says it is showing the latest suggestions is false.
  it('says there are no suggestions now, not that it is showing them, when the refresh finds none', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());

    // Somebody added Deakin, and it was the only suggestion.
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [] });
    await click(refreshButton()!);

    expect(COPY.refreshedNone).toBe('There are no suggestions now.');
    const status = host.querySelector(STATUS)!;
    expect(status.textContent).toBe('There are no suggestions now.');
    expect(text()).not.toContain('Showing the latest suggestions.');
    expect(document.activeElement).toBe(status);
    // The card is still there, with its title and its status and no rows, and the refusal is gone.
    expect(host.querySelector('[role="heading"]')!.textContent).toBe('Probable rivals');
    expect(host.querySelectorAll('li')).toHaveLength(0);
    expect(bodyOrder()).toEqual(['p', 'div[status]']);
    expect(alertTexts()).toEqual([]);
    expect(refreshButton()).toBeUndefined();
  });

  it('still says it is showing the latest suggestions when the refresh finds some', async () => {
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(listChangedBy(listAt('b'.repeat(32), MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());

    rivalSuggestions.mockResolvedValueOnce({ suggestions: [UNSW, LATROBE] });
    await click(refreshButton()!);

    expect(host.querySelector(STATUS)!.textContent).toBe(COPY.refreshed);
    expect(host.querySelectorAll('li')).toHaveLength(2);
  });

  // The sentences carry user-entered domains (up to 253 characters, no break). At 320px one such word
  // would push the card wider than the screen unless the alert is allowed to break inside it.
  it('lets a sentence that names a long domain wrap: the alerts break words', async () => {
    const long = `${'a'.repeat(240)}.example.com`;
    rivalSuggestions.mockResolvedValueOnce({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValue(listOf(MONASH));
    replaceCompetitors.mockRejectedValue(
      listChangedBy(listAt('b'.repeat(32), MONASH, rival('Latrobe', long, 2))),
    );
    await mount();
    await click(deakinButton());

    const sentence = host.querySelector('[role="alert"]')!;
    expect(sentence.textContent).toContain(`Latrobe (${long}) was added`);
    expect(sentence.classList.contains('break-words')).toBe(true);

    rivalSuggestions.mockRejectedValueOnce(problem(500, 'Internal'));
    await click(refreshButton()!);
    const alertsOnPage = [...host.querySelectorAll('[role="alert"]')];
    expect(alertsOnPage).toHaveLength(2);
    for (const alert of alertsOnPage) expect(alert.classList.contains('break-words')).toBe(true);
  });

  it('names the new version on the next press, which reads the list again', async () => {
    const moved = 'b'.repeat(32);
    rivalSuggestions.mockResolvedValue({ suggestions: [DEAKIN] });
    competitorSet.mockResolvedValueOnce(listOf(MONASH));
    replaceCompetitors.mockRejectedValueOnce(listChangedBy(listAt(moved, MONASH, LATROBE_RIVAL)));
    await mount();
    await click(deakinButton());

    competitorSet.mockResolvedValueOnce(listAt(moved, MONASH, LATROBE_RIVAL));
    replaceCompetitors.mockResolvedValueOnce(listAt(moved, MONASH, LATROBE_RIVAL));
    await click(deakinButton());

    expect(replaceCompetitors).toHaveBeenLastCalledWith(
      'cli_1',
      [
        { name: 'Monash University', domain: 'monash.edu' },
        { name: 'Latrobe', domain: 'latrobe.edu.au' },
        { name: 'Deakin', domain: 'deakin.edu.au' },
      ],
      moved,
    );
    // The refusal is gone, and so is its control.
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(refreshButton()).toBeUndefined();
    expect(host.querySelector(STATUS)!.textContent).toBe(
      'deakin.edu.au is now a rival. The next scan measures it.',
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
