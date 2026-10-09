// @vitest-environment jsdom

/**
 * "The questions this client's scans ask" on the Prompts tab — F6 section 3.
 *
 * Where the set came from, whether the rivals have moved on since, and the button that asks for
 * new questions at the next scan (with its Undo). A press saves at once and the screen shows what
 * the API answered.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ClientQuestions } from '@avp/shared-types';

const clientQuestions = vi.fn<(id: string) => Promise<ClientQuestions>>();
const setNewQuestions = vi.fn<(id: string, on: boolean) => Promise<ClientQuestions>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      clientQuestions: (id: string) => clientQuestions(id),
      setNewQuestions: (id: string, on: boolean) => setNewQuestions(id, on),
    },
  };
});

const { QuestionsPanel } = await import('./QuestionsPanel');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
beforeEach(() => {
  clientQuestions.mockReset();
  setNewQuestions.mockReset();
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

const answer = (over: Partial<ClientQuestions> = {}, set: Partial<NonNullable<ClientQuestions['currentSet']>> = {}): ClientQuestions => ({
  newQuestionsNextScan: false,
  currentSet: {
    sourceScanId: 'scan_1',
    writtenAt: '2026-10-09T03:12:00Z',
    questionCount: 20,
    reusedSince: null,
    writtenWithRivals: ['Monash'],
    rivalsChanged: false,
    ...set,
  },
  ...over,
});

async function mounted(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<QuestionsPanel clientId="cli_1" />));
  await settle();
  return el;
}
const button = (el: HTMLElement, name: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === name);

const WRITE = 'Write new questions for the next scan';
const ASKED = 'The next scan will ask new questions.';
// Said, in the status region, after a request for new questions is taken back (U1).
const TAKEN_BACK = 'The next scan will ask the same questions again.';
const NO_SET = 'The next scan will write this client’s questions.';
const LOADING = 'Loading this client’s questions…';
const CHANGED =
  'These questions were written with your rivals as they were then; write new questions to ask about the current ones.';

describe('where the questions came from', () => {
  it('says when they were written', async () => {
    clientQuestions.mockResolvedValue(answer());
    const el = await mounted();
    expect(el.textContent).toContain('These questions were written on 9 Oct 2026.');
    expect(el.textContent).not.toContain('reused since');
  });

  it('says since when they have been reused', async () => {
    clientQuestions.mockResolvedValue(answer({}, { reusedSince: '2026-10-12T00:00:00Z' }));
    const el = await mounted();
    expect(el.textContent).toContain(
      'These questions were written on 9 Oct 2026 and reused since the scan of 12 Oct 2026.',
    );
  });

  it('says there are none yet, and offers no button, before the first scan wrote any', async () => {
    clientQuestions.mockResolvedValue({ newQuestionsNextScan: false, currentSet: null });
    const el = await mounted();
    expect(el.textContent).toContain(NO_SET);
    expect(button(el, WRITE)).toBeUndefined();
  });

  it('says nothing about a date it does not have', async () => {
    clientQuestions.mockResolvedValue(answer({}, { writtenAt: null }));
    const el = await mounted();
    expect(el.textContent).toContain('These questions were written for an earlier scan.');
  });
});

describe('the rivals', () => {
  it('says the questions were written with the rivals as they were, when they have changed', async () => {
    clientQuestions.mockResolvedValue(answer({}, { rivalsChanged: true }));
    const el = await mounted();
    expect(el.textContent).toContain(CHANGED);
  });

  it('says nothing about rivals when they have not changed or are not known', async () => {
    clientQuestions.mockResolvedValue(answer());
    expect((await mounted()).textContent).not.toContain('as they were then');
  });

  it('says nothing about them while new questions are already asked for, and says it again when that is taken back', async () => {
    // U6: the sentence tells the reader to write new questions; once they have, it is only noise.
    clientQuestions.mockResolvedValue(answer({ newQuestionsNextScan: true }, { rivalsChanged: true }));
    const el = await mounted();
    expect(el.textContent).toContain(ASKED);
    expect(el.textContent).not.toContain(CHANGED);

    setNewQuestions.mockResolvedValueOnce(answer({}, { rivalsChanged: true }));
    act(() => button(el, 'Undo')!.click());
    await settle();
    expect(el.textContent).toContain(CHANGED);

    setNewQuestions.mockResolvedValueOnce(answer({ newQuestionsNextScan: true }, { rivalsChanged: true }));
    act(() => button(el, WRITE)!.click());
    await settle();
    expect(el.textContent).not.toContain(CHANGED);
  });
});

describe('while the read is on its way', () => {
  it('says the questions are loading, outside the status region, and stops when they have come', async () => {
    // U8: an empty card for the length of a request reads as nothing to say.
    let finish: (a: ClientQuestions) => void = () => undefined;
    clientQuestions.mockReturnValue(new Promise<ClientQuestions>((resolve) => (finish = resolve)));
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<QuestionsPanel clientId="cli_1" />));
    expect(el.textContent).toContain(LOADING);
    const loading = [...el.querySelectorAll('p')].find((p) => p.textContent === LOADING)!;
    expect(loading.className).toBe('text-ui-sm text-text-tertiary');
    expect(el.querySelector('[role="status"]')!.contains(loading)).toBe(false);
    expect(el.querySelector('[role="status"]')!.textContent).toBe('');

    await act(async () => finish(answer()));
    expect(el.textContent).not.toContain(LOADING);
    expect(el.textContent).toContain('These questions were written on 9 Oct 2026.');
  });

  it('stops saying it when the read fails, and gives the failure instead', async () => {
    clientQuestions.mockRejectedValue(new Error('network'));
    const el = await mounted();
    expect(el.textContent).not.toContain(LOADING);
    expect(el.textContent).toContain('could not be loaded');
  });
});

describe('the button', () => {
  it('sets the flag at once, shows what the API answered, and undoes it', async () => {
    clientQuestions.mockResolvedValue(answer());
    setNewQuestions.mockResolvedValueOnce(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    expect(el.textContent).not.toContain(ASKED);

    act(() => button(el, WRITE)!.click());
    await settle();
    expect(setNewQuestions).toHaveBeenCalledWith('cli_1', true);
    expect(el.textContent).toContain(ASKED);
    expect(button(el, WRITE)).toBeUndefined();

    setNewQuestions.mockResolvedValueOnce(answer());
    act(() => button(el, 'Undo')!.click());
    await settle();
    expect(setNewQuestions).toHaveBeenLastCalledWith('cli_1', false);
    expect(el.textContent).not.toContain(ASKED);
    expect(button(el, WRITE)).toBeDefined();
  });

  it('shows the note and the Undo straight away when the flag is already set', async () => {
    clientQuestions.mockResolvedValue(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    expect(el.textContent).toContain(ASKED);
    expect(button(el, 'Undo')).toBeDefined();
    expect(button(el, WRITE)).toBeUndefined();
  });

  it('still offers the Undo for a request already made when the client has no set yet', async () => {
    clientQuestions.mockResolvedValue({ newQuestionsNextScan: true, currentSet: null });
    const el = await mounted();
    expect(el.textContent).toContain(ASKED);
    expect(button(el, 'Undo')).toBeDefined();
    expect(button(el, WRITE)).toBeUndefined();
  });

  it('says why when a scan is running, and leaves the screen as it was', async () => {
    clientQuestions.mockResolvedValue(answer());
    setNewQuestions.mockRejectedValue(
      new ApiProblem({
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        detail: 'A scan of this client is running. Change its questions after it finishes.',
        instance: '/api/v1/clients/cli_1/questions',
      }),
    );
    const el = await mounted();
    act(() => button(el, WRITE)!.click());
    await settle();
    expect(el.textContent).toContain('A scan of this client is running.');
    expect(el.textContent).not.toContain(ASKED);
    expect(button(el, WRITE)).toBeDefined();
  });

  it('ignores a second press while saving', async () => {
    clientQuestions.mockResolvedValue(answer());
    let finish: (a: ClientQuestions) => void = () => undefined;
    setNewQuestions.mockReturnValue(new Promise<ClientQuestions>((resolve) => (finish = resolve)));
    const el = await mounted();
    const write = button(el, WRITE)!;
    act(() => write.click());
    act(() => write.click());
    expect(setNewQuestions).toHaveBeenCalledTimes(1);
    await act(async () => finish(answer({ newQuestionsNextScan: true })));
  });

  it('lets its words wrap, so they stay inside the card on a narrow screen', async () => {
    // U5: `.avp-btn` never wraps, and these words are wider than the card at 320px.
    clientQuestions.mockResolvedValue(answer());
    const el = await mounted();
    expect(button(el, WRITE)!.classList.contains('whitespace-normal')).toBe(true);
  });
});

describe('keyboard and screen reader', () => {
  const status = (el: HTMLElement) => el.querySelector('[role="status"]');

  it('has its status region on the page from the first render, before the read has come back', () => {
    clientQuestions.mockReturnValue(new Promise<ClientQuestions>(() => undefined));
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<QuestionsPanel clientId="cli_1" />));
    expect(status(el)).not.toBeNull();
    expect(status(el)!.textContent).toBe('');
  });

  it('adds the sentence to that region, so it is announced, and says in it what taking the request back did', async () => {
    clientQuestions.mockResolvedValue(answer());
    setNewQuestions.mockResolvedValueOnce(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    const region = status(el)!;
    expect(region.textContent).toBe('');

    act(() => button(el, WRITE)!.click());
    await settle();
    // The same node the page was given, now holding the sentence: added to a region, not made with one.
    expect(status(el)).toBe(region);
    expect(region.textContent).toBe(ASKED);

    // U1: an Undo that only emptied the region was silent to a screen reader.
    setNewQuestions.mockResolvedValueOnce(answer());
    act(() => button(el, 'Undo')!.click());
    await settle();
    expect(status(el)).toBe(region);
    expect(region.textContent).toBe(TAKEN_BACK);

    // Asking again replaces it with the sentence for that.
    setNewQuestions.mockResolvedValueOnce(answer({ newQuestionsNextScan: true }));
    act(() => button(el, WRITE)!.click());
    await settle();
    expect(region.textContent).toBe(ASKED);
  });

  it('says the next scan writes the questions, in that region, when a request is taken back from a client with no set', async () => {
    clientQuestions.mockResolvedValue({ newQuestionsNextScan: true, currentSet: null });
    setNewQuestions.mockResolvedValueOnce({ newQuestionsNextScan: false, currentSet: null });
    const el = await mounted();
    const region = status(el)!;
    expect(region.textContent).toBe(ASKED);

    act(() => button(el, 'Undo')!.click());
    await settle();
    expect(region.textContent).toBe(NO_SET);
  });

  it('says nothing new in the region when a take-back is refused', async () => {
    clientQuestions.mockResolvedValue(answer({ newQuestionsNextScan: true }));
    setNewQuestions.mockRejectedValue(
      new ApiProblem({
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        detail: 'A scan of this client is running.',
        instance: '/api/v1/clients/cli_1/questions',
      }),
    );
    const el = await mounted();
    act(() => button(el, 'Undo')!.click());
    await settle();
    expect(status(el)!.textContent).toBe(ASKED);
    expect(el.textContent).not.toContain(TAKEN_BACK);
  });

  it('keeps the button, and the focus on it, when taking a request back leaves the client with no set', async () => {
    // U4: the button was only on the page while there was a set or a request; this Undo ended both.
    clientQuestions.mockResolvedValue({ newQuestionsNextScan: true, currentSet: null });
    setNewQuestions.mockResolvedValueOnce({ newQuestionsNextScan: false, currentSet: null });
    const el = await mounted();
    const pressed = button(el, 'Undo')!;
    pressed.focus();

    act(() => pressed.click());
    await settle();

    expect(button(el, WRITE)).toBe(pressed);
    expect(document.activeElement).toBe(pressed);
  });

  it('still offers nothing to a client with no set that nobody has pressed anything for', async () => {
    clientQuestions.mockResolvedValue({ newQuestionsNextScan: false, currentSet: null });
    const el = await mounted();
    expect(el.querySelector('button')).toBeNull();
  });

  it('shows the sentence in the region when the flag was already set', async () => {
    clientQuestions.mockResolvedValue(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    expect(status(el)!.textContent).toBe(ASKED);
  });

  it('keeps focus on the button that was pressed, through both swaps of its words', async () => {
    clientQuestions.mockResolvedValue(answer());
    setNewQuestions.mockResolvedValueOnce(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    const pressed = button(el, WRITE)!;
    pressed.focus();
    expect(document.activeElement).toBe(pressed);

    act(() => pressed.click());
    await settle();
    // Still the element that has focus; only its words changed.
    expect(button(el, 'Undo')).toBe(pressed);
    expect(document.activeElement).toBe(pressed);

    setNewQuestions.mockResolvedValueOnce(answer());
    act(() => pressed.click());
    await settle();
    expect(button(el, WRITE)).toBe(pressed);
    expect(document.activeElement).toBe(pressed);
  });

  it('keeps focus on the button when a scan that is running refuses the press', async () => {
    clientQuestions.mockResolvedValue(answer());
    setNewQuestions.mockRejectedValue(
      new ApiProblem({
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        detail: 'A scan of this client is running.',
        instance: '/api/v1/clients/cli_1/questions',
      }),
    );
    const el = await mounted();
    const pressed = button(el, WRITE)!;
    pressed.focus();
    act(() => pressed.click());
    await settle();
    expect(document.activeElement).toBe(pressed);
    expect(el.querySelector('[role="alert"]')!.textContent).toContain('A scan of this client is running.');
  });

  it('describes the Undo by the sentence it undoes, so it is never an unexplained "Undo"', async () => {
    clientQuestions.mockResolvedValue(answer({ newQuestionsNextScan: true }));
    const el = await mounted();
    const region = status(el)!;
    expect(region.id).not.toBe('');
    expect(button(el, 'Undo')!.getAttribute('aria-describedby')).toBe(region.id);
  });

  it('gives the button that writes new questions no description: its own words say it all', async () => {
    clientQuestions.mockResolvedValue(answer());
    const el = await mounted();
    expect(button(el, WRITE)!.hasAttribute('aria-describedby')).toBe(false);
  });

  it('does not take the button away while it saves: it stays enabled and says it is busy', async () => {
    clientQuestions.mockResolvedValue(answer());
    let finish: (a: ClientQuestions) => void = () => undefined;
    setNewQuestions.mockReturnValue(new Promise<ClientQuestions>((resolve) => (finish = resolve)));
    const el = await mounted();
    const pressed = button(el, WRITE)!;
    act(() => pressed.click());
    expect(pressed.disabled).toBe(false);
    expect(pressed.getAttribute('aria-disabled')).toBe('true');
    await act(async () => finish(answer({ newQuestionsNextScan: true })));
    expect(pressed.getAttribute('aria-disabled')).toBe('false');
  });
});

describe('when the read fails', () => {
  it('says so, in a sentence', async () => {
    clientQuestions.mockRejectedValue(new Error('network'));
    const el = await mounted();
    expect(el.textContent).toContain('This client’s questions could not be loaded.');
  });

  it('offers nothing to press, for it does not know what a press would change', async () => {
    clientQuestions.mockRejectedValue(new Error('network'));
    const el = await mounted();
    expect(el.querySelector('button')).toBeNull();
    expect(el.textContent).not.toContain('The next scan will');
  });
});
