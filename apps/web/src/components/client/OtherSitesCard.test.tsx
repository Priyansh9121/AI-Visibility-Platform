// @vitest-environment jsdom

/**
 * "Sites with the same name" on a client's Sources tab, mounted — M1.
 *
 * The RMIT shape: rmit.edu.vn is proposed for the client RMIT University, and monash.edu.my for
 * the rival Monash University, which already holds a confirmed site. What a static render cannot
 * show: the exact body each button sends (a save never clears another rival's sites), what is
 * said after it, where focus goes, and that a refusal changes nothing on screen.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { OtherSites, ReplaceOtherSites } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const otherSites = vi.fn<(id: string) => Promise<OtherSites>>();
const replaceOtherSites = vi.fn<(id: string, body: ReplaceOtherSites) => Promise<OtherSites>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      otherSites: (id: string) => otherSites(id),
      replaceOtherSites: (id: string, body: ReplaceOtherSites) => replaceOtherSites(id, body),
    },
  };
});

import { OtherSitesCard } from './OtherSitesCard';

const RMIT = {
  domain: 'rmit.edu.vn',
  citations: 4,
  brandKind: 'client',
  brandName: 'RMIT University',
  brandDomain: 'rmit.edu.au',
} as const;
const RMIT_HELD = {
  domain: 'rmit.edu.vn',
  brandKind: 'client',
  brandName: 'RMIT University',
  brandDomain: 'rmit.edu.au',
} as const;
const MONASH = {
  domain: 'monash.edu.my',
  citations: 1,
  brandKind: 'rival',
  brandName: 'Monash University',
  brandDomain: 'monash.edu',
} as const;
const MONASH_HELD = {
  domain: 'monash.edu.sg',
  brandKind: 'rival',
  brandName: 'Monash University',
  brandDomain: 'monash.edu',
} as const;

const start = (): OtherSites => ({
  confirmed: [{ ...MONASH_HELD }],
  proposals: [{ ...RMIT }, { ...MONASH }],
  dismissed: [],
});

const STATUS = '[data-testid="other-sites-status"]';
let host: HTMLDivElement;
let root: Root;
const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
const name = (b: Element) => b.getAttribute('aria-label') ?? b.textContent;
const byName = (n: string) =>
  [...host.querySelectorAll('button')].find((b) => name(b) === n) as HTMLButtonElement;

const toggle = () => byName('Dismissed (2)') ?? byName('Dismissed (1)');
const listEl = () =>
  host.querySelector('#' + CSS.escape(toggle().getAttribute('aria-controls')!)) as HTMLElement;

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
}

async function mount() {
  await act(async () => {
    root.render(<OtherSitesCard clientId="cli_1" />);
  });
}

beforeEach(() => {
  otherSites.mockReset();
  replaceOtherSites.mockReset();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe('what the card shows', () => {
  it('lists each proposal with whose name it carries and its citations, and the confirmed list', async () => {
    otherSites.mockResolvedValue(start());
    await mount();
    expect(otherSites).toHaveBeenCalledWith('cli_1');
    expect(host.querySelector('h3')?.textContent).toBe('Sites with the same name');
    const t = text();
    expect(t).toContain('rmit.edu.vn');
    expect(t).toContain('Same name as RMIT University (rmit.edu.au)');
    expect(t).toContain('4 citations');
    expect(t).toContain('monash.edu.my');
    expect(t).toContain('Same name as Monash University (monash.edu), a rival');
    expect(t).toContain('1 citation');
    expect(t).not.toContain('1 citations');
    expect(t).toContain('Confirmed');
    expect(t).toContain('monash.edu.sg');
  });

  it('reads each row with its parts separated, in order: domain, whose name, citations', async () => {
    otherSites.mockResolvedValue(start());
    await mount();
    const rows = [...host.querySelectorAll('li')].map((li) =>
      (li.firstElementChild!.textContent ?? '').trim(),
    );
    expect(rows).toEqual([
      'rmit.edu.vn Same name as RMIT University (rmit.edu.au) 4 citations',
      'monash.edu.my Same name as Monash University (monash.edu), a rival 1 citation',
      'monash.edu.sg Monash University (a rival)',
    ]);
  });

  it('names the client plainly on a confirmed row', async () => {
    otherSites.mockResolvedValue({ confirmed: [{ ...RMIT_HELD }], proposals: [], dismissed: [] });
    await mount();
    expect(host.querySelector('li')!.firstElementChild!.textContent).toBe('rmit.edu.vn RMIT University');
  });

  it('gives every button on every row its own accessible name', async () => {
    otherSites.mockResolvedValue(start());
    await mount();
    const buttons = [...host.querySelectorAll('button')];
    const names = buttons.map(name);
    expect(names.length).toBe(5);
    expect(new Set(names).size).toBe(5);
    expect(names).toContain("It's theirs: rmit.edu.vn");
    expect(names).toContain('Not theirs: monash.edu.my');
    expect(names).toContain('Remove monash.edu.sg');
    // The visible words open every accessible name.
    for (const b of buttons) expect(name(b)!.startsWith(b.textContent!)).toBe(true);
  });

  it('says so when nothing was cited and nothing is confirmed or dismissed', async () => {
    otherSites.mockResolvedValue({ confirmed: [], proposals: [], dismissed: [] });
    await mount();
    expect(text()).toContain('No site with the same name was cited in the latest scan.');
    expect(host.querySelectorAll('button').length).toBe(0);
    expect(text()).not.toContain('Confirmed');
    expect(text()).not.toContain('Dismissed');
  });

  const OLD = 'No site with the same name was cited in the latest scan.';
  const WAITING = 'No site with the same name is waiting for a decision.';

  it('with only dismissed sites says none is waiting, not that none was cited', async () => {
    otherSites.mockResolvedValue({ confirmed: [], proposals: [], dismissed: ['x.example'] });
    await mount();
    expect(text()).toContain(WAITING);
    expect(text()).not.toContain(OLD);
  });

  it('with dismissed and confirmed sites and no proposal says none is waiting', async () => {
    otherSites.mockResolvedValue({ confirmed: [{ ...MONASH_HELD }], proposals: [], dismissed: ['x.example'] });
    await mount();
    expect(text()).toContain(WAITING);
    expect(text()).not.toContain(OLD);
  });

  it('with only confirmed sites shows neither line', async () => {
    otherSites.mockResolvedValue({ confirmed: [{ ...MONASH_HELD }], proposals: [], dismissed: [] });
    await mount();
    expect(text()).not.toContain(WAITING);
    expect(text()).not.toContain(OLD);
  });

  it('with a proposal shows neither line, dismissed or not', async () => {
    otherSites.mockResolvedValue({ ...start(), dismissed: ['x.example'] });
    await mount();
    expect(text()).not.toContain(WAITING);
    expect(text()).not.toContain(OLD);
  });

  it('still offers the dismissed list, collapsed, when nothing else is listed', async () => {
    otherSites.mockResolvedValue({ confirmed: [], proposals: [], dismissed: ['x.example'] });
    await mount();
    expect(text()).toContain('No site with the same name is waiting for a decision.');
    expect(byName('Dismissed (1)').getAttribute('aria-expanded')).toBe('false');
    expect(listEl().hidden).toBe(true);
  });

  it('shows the loading line, then an error line when the read fails', async () => {
    otherSites.mockReturnValue(new Promise(() => undefined));
    await mount();
    expect(host.querySelector('[role="status"]')).not.toBeNull();
    act(() => root.unmount());
    root = createRoot(host);
    otherSites.mockRejectedValue(new Error('down'));
    await mount();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
  });

  it('has the status line on the page from the first render, empty', async () => {
    otherSites.mockResolvedValue(start());
    await mount();
    const status = host.querySelector(STATUS)!;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toBe('');
  });
});

describe('the buttons', () => {
  it('It’s theirs sends the body that keeps Monash’s site, draws the answer, says so and moves focus', async () => {
    otherSites.mockResolvedValue(start());
    replaceOtherSites.mockResolvedValue({
      confirmed: [{ ...MONASH_HELD }, { ...RMIT_HELD }],
      proposals: [{ ...MONASH }],
      dismissed: [],
    });
    await mount();
    await click(byName("It's theirs: rmit.edu.vn"));

    expect(replaceOtherSites).toHaveBeenCalledTimes(1);
    expect(replaceOtherSites).toHaveBeenCalledWith('cli_1', {
      client: ['rmit.edu.vn'],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] }],
      dismissed: [],
    });
    const status = host.querySelector(STATUS)!;
    expect(status.textContent).toBe(
      "rmit.edu.vn now counts as RMIT University's own site from the next scan.",
    );
    // Drawn from the PUT's answer, with no second read.
    expect(otherSites).toHaveBeenCalledTimes(1);
    expect(byName("It's theirs: rmit.edu.vn")).toBeUndefined();
    expect(byName('Remove rmit.edu.vn')).toBeDefined();
    // The row the focus was on has gone: focus moves to the status line.
    expect(document.activeElement).toBe(status);
  });

  it('Not theirs adds the domain to dismissed and keeps Monash’s site', async () => {
    otherSites.mockResolvedValue(start());
    replaceOtherSites.mockResolvedValue({
      confirmed: [{ ...MONASH_HELD }],
      proposals: [{ ...MONASH }],
      dismissed: ['rmit.edu.vn'],
    });
    await mount();
    await click(byName('Not theirs: rmit.edu.vn'));

    expect(replaceOtherSites).toHaveBeenCalledWith('cli_1', {
      client: [],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] }],
      dismissed: ['rmit.edu.vn'],
    });
    expect(host.querySelector(STATUS)!.textContent).toBe('rmit.edu.vn will not be suggested again.');
    expect(byName('Not theirs: rmit.edu.vn')).toBeUndefined();
    expect(text()).not.toContain('Same name as RMIT University');
  });

  it('Remove sends the body without that site', async () => {
    otherSites.mockResolvedValue(start());
    replaceOtherSites.mockResolvedValue({ confirmed: [], proposals: start().proposals, dismissed: [] });
    await mount();
    await click(byName('Remove monash.edu.sg'));

    expect(replaceOtherSites).toHaveBeenCalledWith('cli_1', { client: [], rivals: [], dismissed: [] });
    const status = host.querySelector(STATUS)!;
    expect(status.textContent).toBe('monash.edu.sg was removed.');
    expect(byName('Remove monash.edu.sg')).toBeUndefined();
    expect(document.activeElement).toBe(status);
  });

  it('a 409 shows the API’s sentence and changes nothing', async () => {
    otherSites.mockResolvedValue(start());
    const sentence = 'A scan of this client is running. Change its other sites after it finishes.';
    replaceOtherSites.mockRejectedValue(
      new ApiProblem({ type: '/problems/conflict', title: 'Conflict', status: 409, detail: sentence, instance: '/x' }),
    );
    await mount();
    const rows = host.querySelectorAll('li').length;
    await click(byName("It's theirs: rmit.edu.vn"));

    expect(host.querySelector('[role="alert"]')!.textContent).toBe(sentence);
    expect(host.querySelectorAll('li').length).toBe(rows);
    expect(byName("It's theirs: rmit.edu.vn")).toBeDefined();
    expect(byName('Remove monash.edu.sg')).toBeDefined();
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(byName("It's theirs: rmit.edu.vn").getAttribute('aria-disabled')).not.toBe('true');
  });

  it('a 422 shows its sentence, and the next success clears it', async () => {
    otherSites.mockResolvedValue(start());
    const sentence = 'rmit.edu.vn is not a domain this brand can hold.';
    replaceOtherSites.mockRejectedValueOnce(
      new ApiProblem({ type: '/problems/validation', title: 'Invalid', status: 422, detail: sentence, instance: '/x' }),
    );
    await mount();
    await click(byName('Not theirs: rmit.edu.vn'));
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(sentence);
    expect(byName('Not theirs: rmit.edu.vn')).toBeDefined();

    replaceOtherSites.mockResolvedValueOnce({
      confirmed: [{ ...MONASH_HELD }],
      proposals: [{ ...MONASH }],
      dismissed: ['rmit.edu.vn'],
    });
    await click(byName('Not theirs: rmit.edu.vn'));
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('is aria-disabled, not disabled, while a save runs, keeps focus, and ignores a second press', async () => {
    otherSites.mockResolvedValue(start());
    let finish!: (v: OtherSites) => void;
    replaceOtherSites.mockReturnValue(new Promise((r) => (finish = r)));
    await mount();
    const first = byName('Not theirs: rmit.edu.vn');
    first.focus();
    await click(first);

    for (const b of host.querySelectorAll('button')) {
      expect(b.getAttribute('aria-disabled')).toBe('true');
      expect(b.disabled).toBe(false);
    }
    expect(document.activeElement).toBe(first);
    await click(byName('Remove monash.edu.sg'));
    expect(replaceOtherSites).toHaveBeenCalledTimes(1);

    await act(async () =>
      finish({ confirmed: [{ ...MONASH_HELD }], proposals: [{ ...MONASH }], dismissed: ['rmit.edu.vn'] }),
    );
    for (const b of host.querySelectorAll('button')) {
      expect(b.getAttribute('aria-disabled')).not.toBe('true');
    }
  });
});

describe('stale lines and switching clients', () => {
  it('drops the earlier success sentence when the next save is refused', async () => {
    otherSites.mockResolvedValue(start());
    replaceOtherSites.mockResolvedValueOnce({
      confirmed: [{ ...MONASH_HELD }],
      proposals: [{ ...MONASH }],
      dismissed: ['rmit.edu.vn'],
    });
    await mount();
    await click(byName('Not theirs: rmit.edu.vn'));
    expect(host.querySelector(STATUS)!.textContent).toBe('rmit.edu.vn will not be suggested again.');

    const sentence = 'A scan of this client is running. Change its other sites after it finishes.';
    replaceOtherSites.mockRejectedValueOnce(
      new ApiProblem({ type: '/problems/conflict', title: 'Conflict', status: 409, detail: sentence, instance: '/x' }),
    );
    await click(byName('Not theirs: monash.edu.my'));
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(sentence);
    expect(host.querySelector(STATUS)!.textContent).toBe('');
  });

  it('shows nothing of one client under another when clientId changes', async () => {
    otherSites.mockResolvedValueOnce(start());
    await mount();
    replaceOtherSites.mockRejectedValueOnce(
      new ApiProblem({ type: '/p', title: 'Invalid', status: 422, detail: 'Nope.', instance: '/x' }),
    );
    await click(byName('Not theirs: rmit.edu.vn'));
    expect(host.querySelector('[role="alert"]')).not.toBeNull();

    otherSites.mockReturnValueOnce(new Promise(() => undefined));
    await act(async () => {
      root.render(<OtherSitesCard clientId="cli_2" />);
    });
    expect(otherSites).toHaveBeenLastCalledWith('cli_2');
    expect(text()).not.toContain('rmit.edu.vn');
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(host.querySelector('[role="status"] , [role="status"]')).not.toBeNull();
  });
});

describe('the dismissed list and Undo', () => {
  const withDismissed = (dismissed: string[]): OtherSites => ({ ...start(), dismissed });

  it('shows no toggle when nothing is dismissed', async () => {
    otherSites.mockResolvedValue(start());
    await mount();
    expect(text()).not.toContain('Dismissed');
    expect(host.querySelector('[aria-expanded]')).toBeNull();
  });

  it('is collapsed by default, with the list hidden, and expands to one row per domain in order', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example', 'rmit.edu.my']));
    await mount();
    const t = toggle();
    expect(t.textContent).toBe('Dismissed (2)');
    expect(t.getAttribute('aria-expanded')).toBe('false');
    expect(t.getAttribute('aria-controls')).toBeTruthy();
    expect(listEl().hidden).toBe(true);

    await click(t);
    expect(t.getAttribute('aria-expanded')).toBe('true');
    expect(listEl().hidden).toBe(false);
    const rows = [...listEl().querySelectorAll('li')].map((li) =>
      [...li.children].map((c) => (c.textContent ?? '').trim()).join(' | '),
    );
    expect(rows).toEqual(['old.example | Undo', 'rmit.edu.my | Undo']);

    await click(t);
    expect(t.getAttribute('aria-expanded')).toBe('false');
    expect(listEl().hidden).toBe(true);
  });

  it('hides the list with an element that sets no display of its own (jsdom has no CSS)', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example']));
    await mount();
    const el = listEl();
    expect(el.hasAttribute('hidden')).toBe(true);
    // A display utility on the same element beats the stylesheet's [hidden] rule in a browser.
    const display = /(^|\s)(flex|inline-flex|grid|inline-grid|block|inline|inline-block|table\S*|contents|flow-root)(\s|$)/;
    expect(el.className).not.toMatch(display);
    expect(el.querySelectorAll('li').length).toBe(1);
  });

  it('gives each Undo its own accessible name starting with the visible word', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example', 'rmit.edu.my']));
    await mount();
    await click(toggle());
    const names = [...listEl().querySelectorAll('button')].map(name);
    expect(names).toEqual(['Undo: old.example', 'Undo: rmit.edu.my']);
    for (const b of listEl().querySelectorAll('button')) expect(name(b)!.startsWith(b.textContent!)).toBe(true);
  });

  it('Undo sends the body without that domain and with every rival, says so, and proposes it again when the answer does', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example', 'rmit.edu.vn']));
    replaceOtherSites.mockResolvedValue({
      confirmed: [{ ...MONASH_HELD }],
      proposals: [{ ...RMIT }, { ...MONASH }],
      dismissed: ['old.example'],
    });
    await mount();
    await click(toggle());
    byName('Undo: rmit.edu.vn').focus();
    await click(byName('Undo: rmit.edu.vn'));

    expect(replaceOtherSites).toHaveBeenCalledTimes(1);
    expect(replaceOtherSites).toHaveBeenCalledWith('cli_1', {
      client: [],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] }],
      dismissed: ['old.example'],
    });
    const status = host.querySelector(STATUS)!;
    expect(status.textContent).toBe('rmit.edu.vn can be suggested again.');
    expect(document.activeElement).toBe(status);
    expect(otherSites).toHaveBeenCalledTimes(1);
    expect(byName("It's theirs: rmit.edu.vn")).toBeDefined();
    // The toggle follows the list and stays expanded while sites remain dismissed.
    expect(byName('Dismissed (1)').getAttribute('aria-expanded')).toBe('true');
    expect(byName('Undo: rmit.edu.vn')).toBeUndefined();
    expect(byName('Undo: old.example')).toBeDefined();
  });

  it('does not list the domain when the answer does not propose it', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example']));
    replaceOtherSites.mockResolvedValue(start());
    await mount();
    await click(toggle());
    await click(byName('Undo: old.example'));
    expect(host.querySelector(STATUS)!.textContent).toBe('old.example can be suggested again.');
    expect(text()).not.toContain('old.example can be suggested again. old.example');
    expect(byName("It's theirs: old.example")).toBeUndefined();
  });

  it('the toggle disappears when the last one is undone, and focus goes to the status line', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example']));
    replaceOtherSites.mockResolvedValue(start());
    await mount();
    await click(toggle());
    byName('Undo: old.example').focus();
    await click(byName('Undo: old.example'));
    expect(replaceOtherSites).toHaveBeenCalledWith('cli_1', {
      client: [],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] }],
      dismissed: [],
    });
    expect(text()).not.toContain('Dismissed');
    expect(host.querySelector('[aria-expanded]')).toBeNull();
    expect(document.activeElement).toBe(host.querySelector(STATUS));
  });

  it('a 409 shows the sentence and changes nothing', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example', 'rmit.edu.vn']));
    const sentence = 'A scan of this client is running. Change its other sites after it finishes.';
    replaceOtherSites.mockRejectedValue(
      new ApiProblem({ type: '/problems/conflict', title: 'Conflict', status: 409, detail: sentence, instance: '/x' }),
    );
    await mount();
    await click(toggle());
    await click(byName('Undo: rmit.edu.vn'));
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(sentence);
    expect(byName('Dismissed (2)').getAttribute('aria-expanded')).toBe('true');
    expect(byName('Undo: rmit.edu.vn')).toBeDefined();
    expect(byName('Undo: old.example')).toBeDefined();
    expect(host.querySelector(STATUS)!.textContent).toBe('');
    expect(byName('Undo: rmit.edu.vn').getAttribute('aria-disabled')).not.toBe('true');
  });

  it('is aria-disabled while a save runs and ignores a second press', async () => {
    otherSites.mockResolvedValue(withDismissed(['old.example', 'rmit.edu.vn']));
    let finish!: (v: OtherSites) => void;
    replaceOtherSites.mockReturnValue(new Promise((r) => (finish = r)));
    await mount();
    await click(toggle());
    const first = byName('Undo: old.example');
    first.focus();
    await click(first);
    // Every save button, not the disclosure toggle (it saves nothing).
    for (const b of host.querySelectorAll<HTMLButtonElement>('button:not([aria-expanded])')) {
      expect(b.getAttribute('aria-disabled')).toBe('true');
      expect(b.disabled).toBe(false);
    }
    expect(document.activeElement).toBe(first);
    await click(byName('Undo: rmit.edu.vn'));
    expect(replaceOtherSites).toHaveBeenCalledTimes(1);
    await act(async () => finish(withDismissed(['rmit.edu.vn'])));
    for (const b of host.querySelectorAll('button')) {
      expect(b.getAttribute('aria-disabled')).not.toBe('true');
    }
  });

  it('collapses again for another client', async () => {
    otherSites.mockResolvedValueOnce(withDismissed(['old.example']));
    await mount();
    await click(toggle());
    otherSites.mockResolvedValueOnce(withDismissed(['other.example']));
    await act(async () => {
      root.render(<OtherSitesCard clientId="cli_2" />);
    });
    expect(byName('Dismissed (1)').getAttribute('aria-expanded')).toBe('false');
  });
});
