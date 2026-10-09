/**
 * Probable rivals on the Competitors page: the line, the body a save sends and the refusals made
 * before it (F2, 2026-10-09). The line is the founder's, and it is read from `probable-rival.json`,
 * the same scan the API tests build, so the page and the API cannot tell different stories.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  alreadyARival,
  MAX_RIVALS,
  refusalBeforeSaving,
  RIVAL_SUGGESTIONS_COPY as COPY,
  rivalsBody,
  rowProblem,
  suggestionSentence,
} from './rivalSuggestions';

const FIXTURES = fileURLToPath(
  new URL('../../../../../packages/shared-types/fixtures/', import.meta.url),
);
const fixture = JSON.parse(readFileSync(`${FIXTURES}probable-rival.json`, 'utf8')) as {
  suggestion: { domain: string; name: string; citations: number; answersNaming: number };
  sentence: string;
};
const DEAKIN = fixture.suggestion;

describe('the line', () => {
  it('is the founder’s sentence for the founder’s scan', () => {
    expect(suggestionSentence(DEAKIN)).toBe(
      'deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?',
    );
    expect(suggestionSentence(DEAKIN)).toBe(fixture.sentence);
  });

  it.each([
    [1, 1, 'x.edu.au: cited 1 time, named in 1 answer. Add it as a rival?'],
    [1, 2, 'x.edu.au: cited 1 time, named in 2 answers. Add it as a rival?'],
    [2, 1, 'x.edu.au: cited 2 times, named in 1 answer. Add it as a rival?'],
    [12, 30, 'x.edu.au: cited 12 times, named in 30 answers. Add it as a rival?'],
  ])('with %i citation(s) and %i answer(s) it agrees in number', (citations, answersNaming, line) => {
    expect(suggestionSentence({ domain: 'x.edu.au', name: 'X', citations, answersNaming })).toBe(line);
  });

  it('says what happened once the rival is added', () => {
    expect(COPY.added('deakin.edu.au')).toBe('deakin.edu.au is now a rival. The next scan measures it.');
    expect(COPY.alreadyThere('deakin.edu.au')).toBe('deakin.edu.au is already a rival.');
  });
});

describe('the body a save sends', () => {
  it('is the current list, in order, and then this suggestion with its own name and domain', () => {
    expect(
      rivalsBody(
        [
          { name: 'Monash University', domain: 'monash.edu' },
          { name: 'UNSW', domain: 'unsw.edu.au' },
        ],
        DEAKIN,
      ),
    ).toEqual([
      { name: 'Monash University', domain: 'monash.edu' },
      { name: 'UNSW', domain: 'unsw.edu.au' },
      { name: 'Deakin', domain: 'deakin.edu.au' },
    ]);
  });

  it('is just the suggestion for a client with no list', () => {
    expect(rivalsBody([], DEAKIN)).toEqual([{ name: 'Deakin', domain: 'deakin.edu.au' }]);
  });

  it('keeps a rival that has no domain as one, without a domain, as the editor does', () => {
    expect(rivalsBody([{ name: 'Acme', domain: null }, { name: 'Beta' }], DEAKIN)).toEqual([
      { name: 'Acme', domain: null },
      { name: 'Beta', domain: null },
      { name: 'Deakin', domain: 'deakin.edu.au' },
    ]);
  });

  it('trims a stored domain exactly as the editor trims one', () => {
    expect(rivalsBody([{ name: 'A', domain: ' a.example ' }], DEAKIN)[0]).toEqual({
      name: 'A',
      domain: 'a.example',
    });
  });

  it('does not change the list it was given', () => {
    const current = [{ name: 'A', domain: 'a.example' }];
    rivalsBody(current, DEAKIN);
    expect(current).toEqual([{ name: 'A', domain: 'a.example' }]);
  });
});

describe('what is refused before a save, as the editor refuses it', () => {
  const names = (n: number) => Array.from({ length: n }, (_, i) => ({ name: `Rival ${i}` }));

  it('allows a list with room and no such name', () => {
    expect(refusalBeforeSaving(names(0), DEAKIN)).toBeNull();
    expect(refusalBeforeSaving(names(MAX_RIVALS - 1), DEAKIN)).toBeNull();
  });

  it('refuses a full list in the editor’s sentence', () => {
    expect(refusalBeforeSaving(names(MAX_RIVALS), DEAKIN)).toBe(
      'A set carries at most 10 competitors.',
    );
  });

  it('refuses a name already on the list, whatever its case', () => {
    expect(refusalBeforeSaving([{ name: 'deakin' }], DEAKIN)).toBe('Deakin is already on the list.');
    expect(refusalBeforeSaving([{ name: 'DEAKIN' }], DEAKIN)).toBe('Deakin is already on the list.');
  });

  it('names the cap before the name, as the editor checks them', () => {
    const room = [...names(MAX_RIVALS - 2), { name: 'Deakin' }];
    expect(room).toHaveLength(MAX_RIVALS - 1);
    expect(refusalBeforeSaving(room, DEAKIN)).toBe('Deakin is already on the list.');
    // One more and the list is full: the cap is what is said, though the name is on it too.
    expect(refusalBeforeSaving([...room, { name: 'Another' }], DEAKIN)).toBe(
      'A set carries at most 10 competitors.',
    );
  });

  it('the cap is the editor’s and the API’s', () => {
    const editor = readFileSync(
      fileURLToPath(new URL('../../components/report/CompetitorEditor.tsx', import.meta.url)),
      'utf8',
    );
    expect(editor).toContain(`const MAX_COMPETITORS = ${MAX_RIVALS};`);
  });
});

describe('a domain already on the list', () => {
  it('is found by its host, in either case', () => {
    expect(alreadyARival([{ domain: 'Deakin.EDU.au' }], DEAKIN)).toBe(true);
    expect(alreadyARival([{ domain: ' deakin.edu.au ' }], DEAKIN)).toBe(true);
  });

  it('is found in either spelling of an internationalised host', () => {
    expect(alreadyARival([{ domain: 'bücher.edu.au' }], { domain: 'xn--bcher-kva.edu.au' })).toBe(true);
    expect(alreadyARival([{ domain: 'xn--bcher-kva.edu.au' }], { domain: 'bücher.edu.au' })).toBe(true);
  });

  it('is not found for another domain with the same label, or for a rival with no domain', () => {
    expect(alreadyARival([{ domain: 'deakin.com' }, { domain: 'deakin.org' }], DEAKIN)).toBe(false);
    expect(alreadyARival([{ domain: null }, {}], DEAKIN)).toBe(false);
    expect(alreadyARival([], DEAKIN)).toBe(false);
  });
});

describe('the API’s own refusal of a row', () => {
  const rows = [{ name: 'Monash University' }, { name: 'Deakin' }];

  it('names the row whose domain it refused, in the editor’s shape', () => {
    expect(
      rowProblem(
        { 'competitors.1.domain': 'That is a platform or publisher, so it cannot be added as a rival.' },
        rows,
      ),
    ).toBe('Deakin: That is a platform or publisher, so it cannot be added as a rival.');
  });

  it('is empty when no row’s domain is named, so the caller says the problem’s own detail', () => {
    expect(rowProblem({}, rows)).toBe('');
    expect(rowProblem({ 'competitors.9.domain': 'x' }, rows)).toBe('');
    expect(rowProblem({ 'competitors.0.name': 'x' }, rows)).toBe('');
  });
});
