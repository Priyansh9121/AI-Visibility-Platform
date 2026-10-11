// @vitest-environment jsdom

/**
 * The competitor editor, mounted (launch checklist 3.78 follow-up).
 *
 * What a static render cannot show: the editor seeds from the client's CURRENT list, because that
 * is what `PUT` replaces, and on an older scan that is not the list on screen; where focus goes
 * when it opens, closes, saves or fails; and what it says after a save (launch checklist 3.79: the
 * list is saved for later scans, and the scan's own card is not made to claim the new rivals).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import type { Competitor, CompetitorSet } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';
import { COMPETITOR_EDITOR_COPY as COPY } from '@/lib/report/strings';
import { LIST_CHANGED_COPY } from '@/lib/competitorListChange';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const competitorSet = vi.fn<(id: string) => Promise<CompetitorSet | null>>();
const replaceCompetitors =
  vi.fn<(id: string, list: unknown[], version: string | null) => Promise<CompetitorSet>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      competitorSet: (id: string) => competitorSet(id),
      replaceCompetitors: (id: string, list: unknown[], version: string | null) =>
        replaceCompetitors(id, list, version),
    },
  };
});

import { CompetitorEditor } from './CompetitorEditor';

const rival = (name: string, domain: string | null = null) =>
  ({ id: name, name, domain, rank: 1, isManualOverride: false }) as unknown as Competitor;
// The version a list is read at (launch checklist 3.113): a save names it.
const READ = 'a'.repeat(32);
const setAt = (version: string, ...names: string[]) =>
  ({ version, competitors: names.map((n) => rival(n)) }) as unknown as CompetitorSet;
const setOf = (...names: string[]) => setAt(READ, ...names);
/** The API's refusal of a save made from a list that has changed, with the list as it now stands. */
const listChangedBy = (current: CompetitorSet | null) =>
  new ApiProblem({
    type: '/problems/competitor-list-changed',
    title: 'The competitor list changed',
    status: 409,
    detail: 'The list changed since you read it.',
    instance: '/api/v1/clients/cli_1/competitors',
    current,
  } as never);

const DOMAIN_SENTENCE = 'Enter just the domain, like rival.com: no https://, page path or subdomain.';
// The API's refusal of a platform or publisher typed as a rival (launch checklist 3.90).
const PLATFORM_SENTENCE = 'That is a platform or publisher, so it cannot be added as a rival.';
const fieldFor = (label: string) =>
  [...host.querySelectorAll('label')].find((l) => l.textContent === label)!.getAttribute('for')!;

let host: HTMLDivElement;
let root: Root;
const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
const button = (label: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement;
/**
 * What every status region on the page says. The open editor has one of its own from the moment it
 * opens, empty until the current list has been loaded, so "no status line" reads `['']` while the
 * editor is open and `[]` when it is closed and has nothing to say.
 */
const statuses = () => [...host.querySelectorAll('[role="status"]')].map((p) => p.textContent);

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
}

async function type(label: string, value: string) {
  const labelEl = [...host.querySelectorAll('label')].find((l) => l.textContent === label)!;
  const field = host.querySelector(`#${CSS.escape(labelEl.getAttribute('for')!)}`) as HTMLInputElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    setter.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function mount(props: {
  competitors: readonly Competitor[];
  setMissing?: boolean;
  scanFinished?: boolean;
  onSaved?: () => void | Promise<void>;
}) {
  await act(async () => {
    root.render(
      <div>
        <h2 id="beat-proof">Proof</h2>
        <CompetitorEditor
          clientId="cli_1"
          competitors={props.competitors}
          {...(props.setMissing !== undefined ? { setMissing: props.setMissing } : {})}
          {...(props.scanFinished !== undefined ? { scanFinished: props.scanFinished } : {})}
          {...(props.onSaved !== undefined ? { onSaved: props.onSaved } : {})}
        />
      </div>,
    );
  });
}

beforeEach(() => {
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

describe('an older scan whose list differs from the client’s current list', () => {
  it('seeds the drafts from the current list, says the lists differ, and saves the edited current list', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk', 'Intercom'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Intercom', 'Help Scout'));
    const onSaved = vi.fn();
    await mount({ competitors: [], setMissing: true, onSaved });

    await click(button('Name the rivals'));

    expect(competitorSet).toHaveBeenCalledWith('cli_1');
    expect(text()).toContain('Zendesk');
    expect(text()).toContain('Intercom');
    expect(text()).toContain(COPY.differsFromScan);
    expect(text()).toContain(
      'The client’s current list, which later scans use, differs from this scan’s. Saving replaces the current list.',
    );
    // Nothing edited yet: the list on screen is empty, but the current list is what is compared.
    expect(button('Save the list').disabled).toBe(true);

    await type('Brand', 'Help Scout');
    await click(button('Add'));
    expect(button('Save the list').disabled).toBe(false);
    await click(button('Save the list'));

    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    expect(replaceCompetitors.mock.calls[0]![1]).toEqual([
      { name: 'Zendesk', domain: null },
      { name: 'Intercom', domain: null },
      { name: 'Help Scout', domain: null },
    ]);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('says nothing about a difference when the lists are the same', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    expect(text()).not.toContain(COPY.differsFromScan);
  });

  it('a client with no current list (404) starts from nothing', async () => {
    competitorSet.mockResolvedValue(null);
    await mount({ competitors: [], setMissing: true });
    await click(button('Name the rivals'));
    expect(text()).toContain(COPY.noneYet);
    expect(text()).not.toContain(COPY.differsFromScan);
  });

  it('a current list that cannot be read opens nothing and says so', async () => {
    competitorSet.mockRejectedValue(new Error('down'));
    await mount({ competitors: [], setMissing: true });
    await click(button('Name the rivals'));
    expect(text()).toContain('The client’s current list could not be read. Try again.');
    expect(button('Save the list')).toBeUndefined();
  });
});

describe('focus', () => {
  it('moves into the editor on open and back to the opener on Cancel', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    expect(document.activeElement?.tagName).toBe('H4');
    expect(document.activeElement?.textContent).toBe('Correct the competitor list');

    await click(button('Cancel'));
    expect(document.activeElement).toBe(button('Correct this list'));
  });

  it('when a caller’s reaction to a save takes the editor away, no state is set on it', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    // The caller's reload swaps the card for a table: the editor goes away inside `onSaved`.
    const onSaved = vi.fn(async () => {
      flushSync(() => root.render(<h2 id="beat-proof">Proof</h2>));
    });
    await mount({ competitors: [], setMissing: true, onSaved });
    await click(button('Name the rivals'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it('a failed save names the problem and the Brand field points at it, but is not marked invalid: the Brand is fine', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(
      new ApiProblem({
        type: '/problems/conflict',
        title: 'Conflicting request',
        status: 409,
        instance: '/api/v1/clients/cli_1/competitors',
        detail: 'A scan of this client is running. Name the rivals after it finishes.',
      }),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toBe(
      'A scan of this client is running. Name the rivals after it finishes.',
    );
    const labelEl = [...host.querySelectorAll('label')].find((l) => l.textContent === 'Brand')!;
    const brand = host.querySelector(`#${CSS.escape(labelEl.getAttribute('for')!)}`)!;
    expect(brand.getAttribute('aria-invalid')).toBeNull();
    // A save error is announced by its own role=alert: Brand does not point at it (only an
    // error add() raised on Brand does).
    expect(brand.getAttribute('aria-describedby')).toBeNull();
  });

  it('a 422 on one row’s domain names that row and says what is wrong with its domain', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk', 'Intercom'));
    replaceCompetitors.mockRejectedValue(
      new ApiProblem({
        type: '/problems/validation-failed',
        title: 'Request validation failed',
        status: 422,
        instance: '/api/v1/clients/cli_1/competitors',
        detail: 'One or more fields were invalid.',
        errors: [
          { field: 'competitors.1.domain', message: DOMAIN_SENTENCE, type: 'invalid_domain' },
        ],
      } as never),
    );
    await mount({ competitors: [rival('Zendesk'), rival('Intercom')] });
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toBe(`Intercom: ${DOMAIN_SENTENCE}`);
    const domainField = (host.querySelector(`#${CSS.escape(fieldFor('Domain (optional)'))}`))!;
    expect(domainField.getAttribute('aria-invalid')).toBeNull();
    const brandField = host.querySelector(`#${CSS.escape(fieldFor('Brand'))}`)!;
    expect(brandField.getAttribute('aria-describedby')).toBeNull();
    expect(text()).not.toContain('One or more fields were invalid.');
  });

  it('a platform stored before 3.90 is named by the refusal, and removing its row lets the save through', async () => {
    // The API refuses a platform or publisher typed as a rival, but a list an older version
    // stored may still hold one. The editor reads that list as it is and sends it back with
    // whatever else changed, so the refusal names the row (any `competitors.<i>.domain` error
    // does) and the operator takes it off.
    const stored = [rival('Zendesk'), rival('G2', 'g2.com')];
    competitorSet.mockResolvedValue({ competitors: stored } as unknown as CompetitorSet);
    replaceCompetitors.mockRejectedValueOnce(
      new ApiProblem({
        type: '/problems/validation-failed',
        title: 'Request validation failed',
        status: 422,
        instance: '/api/v1/clients/cli_1/competitors',
        detail: 'One or more fields were invalid.',
        errors: [
          { field: 'competitors.1.domain', message: PLATFORM_SENTENCE, type: 'invalid_domain' },
        ],
      } as never),
    );
    replaceCompetitors.mockResolvedValueOnce(setOf('Zendesk', 'Other'));
    const onSaved = vi.fn();
    await mount({ competitors: stored, onSaved });
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    // This list came with no version (the test's stub gave none), so the save names none.
    expect(replaceCompetitors).toHaveBeenNthCalledWith(
      1,
      'cli_1',
      [
        { name: 'Zendesk', domain: null },
        { name: 'G2', domain: 'g2.com' },
        { name: 'Other', domain: null },
      ],
      null,
    );
    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toBe(`G2: ${PLATFORM_SENTENCE}`);
    expect(text()).not.toContain('One or more fields were invalid.');
    expect(onSaved).not.toHaveBeenCalled();

    await click(host.querySelector('button[aria-label="Remove G2"]')!);
    expect(host.querySelector('p[role="alert"]')).toBeNull();
    await click(button('Save the list'));

    expect(replaceCompetitors).toHaveBeenNthCalledWith(
      2,
      'cli_1',
      [
        { name: 'Zendesk', domain: null },
        { name: 'Other', domain: null },
      ],
      null,
    );
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it.each(['https://www.g2.com/products/x', 'g2.com/x', 'rival.com:80', 'a@rival.com', 'rival.com?x', 'rival.com#x', 'riv al.com'])(
    'a domain shaped like a page (%s) is refused by Add with the sentence, marks Domain invalid, and focuses it',
    async (pasted) => {
      competitorSet.mockResolvedValue(setOf('Zendesk'));
      await mount({ competitors: [rival('Zendesk')] });
      await click(button('Correct this list'));
      await type('Brand', 'Rival');
      await type('Domain (optional)', pasted);
      await click(button('Add'));

      const alert = host.querySelector('p[role="alert"]')!;
      expect(alert.textContent).toBe(DOMAIN_SENTENCE);
      const domain = host.querySelector(`#${CSS.escape(fieldFor('Domain (optional)'))}`) as HTMLInputElement;
      expect(domain.getAttribute('aria-invalid')).toBe('true');
      expect(domain.getAttribute('aria-describedby')).toBe(alert.id);
      const brand = host.querySelector(`#${CSS.escape(fieldFor('Brand'))}`)!;
      expect(brand.getAttribute('aria-invalid')).toBeNull();
      expect(document.activeElement).toBe(domain);
      expect(text()).not.toContain('Rival rival');
      expect(host.querySelectorAll('ol li').length).toBe(1); // only Zendesk: nothing was added
    },
  );

  it('a bare domain is added, and Remove clears an error left from before', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await type('Brand', 'Rival');
    await type('Domain (optional)', 'g2.com/x');
    await click(button('Add'));
    expect(host.querySelector('p[role="alert"]')).not.toBeNull();

    await click(button('Remove'));
    expect(host.querySelector('p[role="alert"]')).toBeNull();

    await type('Domain (optional)', 'rival.com');
    await click(button('Add'));
    expect(host.querySelector('p[role="alert"]')).toBeNull();
    expect(text()).toContain('rival.com');
  });

  it('a duplicate name marks the Brand field invalid, and Cancel clears the error: it is not announced again on the closed editor', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await type('Brand', 'Zendesk');
    await click(button('Add'));

    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toBe('Zendesk is already on the list.');
    const labelEl = [...host.querySelectorAll('label')].find((l) => l.textContent === 'Brand')!;
    const brand = host.querySelector(`#${CSS.escape(labelEl.getAttribute('for')!)}`)!;
    expect(brand.getAttribute('aria-invalid')).toBe('true');
    expect(brand.getAttribute('aria-describedby')).toBe(alert.id);

    await click(button('Cancel'));
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(text()).not.toContain('is already on the list');
  });

  it('after Add focus goes back to the Brand field, and each Remove names its brand', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    const labelEl = [...host.querySelectorAll('label')].find((l) => l.textContent === 'Brand')!;
    const brand = host.querySelector(`#${CSS.escape(labelEl.getAttribute('for')!)}`)!;
    expect(document.activeElement).toBe(brand);
    const removes = [...host.querySelectorAll('button')].filter((b) => b.textContent === 'Remove');
    expect(removes.map((b) => b.getAttribute('aria-label'))).toEqual(['Remove Zendesk', 'Remove Other']);
  });
});

describe('opening', () => {
  it('says it is reading in a status region, does not disable the opener, and ignores a second press', async () => {
    let release!: (value: CompetitorSet) => void;
    competitorSet.mockReturnValue(new Promise<CompetitorSet>((r) => (release = r)));
    await mount({ competitors: [rival('Zendesk')] });
    const opener = button('Correct this list');
    await click(opener);
    expect(text()).toContain(COPY.reading);
    const reading = [...host.querySelectorAll('[role="status"]')].find((p) => p.textContent === COPY.reading);
    expect(reading).toBeDefined();
    expect(opener.disabled).toBe(false);
    expect(opener.getAttribute('aria-disabled')).toBe('true');
    await click(opener);
    expect(competitorSet).toHaveBeenCalledTimes(1);
    await act(async () => release(setOf('Zendesk')));
    expect(button('Save the list')).toBeDefined();
  });

  it('a failed read puts focus back on the opener', async () => {
    competitorSet.mockRejectedValue(new Error('down'));
    await mount({ competitors: [], setMissing: true });
    button('Name the rivals').focus();
    await click(button('Name the rivals'));
    expect(host.querySelector('[role="alert"]')!.textContent).toBe(
      'The client’s current list could not be read. Try again.',
    );
    expect(document.activeElement).toBe(button('Name the rivals'));
    expect(button('Name the rivals').getAttribute('aria-disabled')).toBeNull();
  });
});

// Launch checklist 3.113. The editor reads the list when it opens and its save names the version it
// read. If the list moved on (a rival added from the suggestions card, a new scan's detection), the
// API writes nothing and says so; the editor says what changed and offers to load the list.
describe('a save names the list the editor read', () => {
  it('sends the version of the list read when it opened, with the drafts', async () => {
    competitorSet.mockResolvedValue(setAt('c'.repeat(32), 'Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    expect(replaceCompetitors).toHaveBeenCalledWith(
      'cli_1',
      [
        { name: 'Zendesk', domain: null },
        { name: 'Other', domain: null },
      ],
      'c'.repeat(32),
    );
  });

  it('names none when the client had no list to read', async () => {
    competitorSet.mockResolvedValue(null);
    replaceCompetitors.mockResolvedValue(setOf('Other'));
    await mount({ competitors: [], setMissing: true });
    await click(button('Name the rivals'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));

    expect(replaceCompetitors).toHaveBeenCalledWith('cli_1', [{ name: 'Other', domain: null }], null);
  });

  it('names the version of the list it read at the latest opening, not the first', async () => {
    competitorSet.mockResolvedValueOnce(setAt('c'.repeat(32), 'Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await click(button('Cancel'));

    competitorSet.mockResolvedValueOnce(setAt('d'.repeat(32), 'Zendesk', 'Front'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk'));
    await click(button('Correct this list'));
    await click(host.querySelector('button[aria-label="Remove Front"]')!);
    await click(button('Save the list'));

    expect(replaceCompetitors.mock.calls[0]![2]).toBe('d'.repeat(32));
  });
});

describe('a save refused because the list changed', () => {
  const loadButton = () => button(COPY.loadCurrent);
  const alerts = () => [...host.querySelectorAll('p[role="alert"]')].map((p) => p.textContent);
  /** What the alert beside the control says: the refusal's sentence, then what the control does. */
  const notice = (sentence: string) => `${sentence} ${COPY.loadReplaces}`;
  const UNREADABLE = 'The client’s current list could not be read. Try again.';
  /** The alert that says the current list could not be read, if it is on the page. */
  const unreadable = () =>
    [...host.querySelectorAll('p[role="alert"]')].find((p) => p.textContent === UNREADABLE);

  /** Opens the editor on `Zendesk`, removes it and adds `Other`, and presses Save. */
  async function editAndSave() {
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));
  }

  it('says what was added, keeps the editor and the edits, and offers to load the current list', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    const onSaved = vi.fn();
    await mount({ competitors: [rival('Zendesk')], onSaved });

    await editAndSave();

    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it: Deakin was added. Your change was not saved.'),
    ]);
    expect(loadButton().textContent).toBe('Load the current list');
    // Nothing was saved: no "saved" line (the one status region is the open editor's own, empty),
    // the editor is still open with the person's edit in it.
    expect(onSaved).not.toHaveBeenCalled();
    expect(statuses()).toEqual(['']);
    expect(button('Save the list')).toBeDefined();
    expect([...host.querySelectorAll('ol li')].map((li) => li.textContent)).toEqual([
      'ZendeskRemove',
      'OtherRemove',
    ]);
  });

  // Loading replaces the person's edits, so the alert says so before they press it. The button's
  // label stays short: `.avp-btn` does not wrap, and a long label would push the card off a phone.
  it('says, in the same alert and before the control is pressed, that loading replaces the edits', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });

    await editAndSave();

    expect(COPY.loadReplaces).toBe('Loading the current list replaces your edits.');
    const alertsOnPage = [...host.querySelectorAll('[role="alert"]')];
    // One alert, so one announcement: the warning is part of the sentence the reader hears.
    expect(alertsOnPage).toHaveLength(1);
    expect(alertsOnPage[0]!.textContent).toBe(
      'The client’s list changed after you opened it: Deakin was added. Your change was not saved. Loading the current list replaces your edits.',
    );
    expect(loadButton().textContent).toBe('Load the current list');
    expect(loadButton().textContent).not.toContain('replaces');
    // Nothing has been replaced yet.
    expect([...host.querySelectorAll('ol li')].map((li) => li.textContent)).toEqual([
      'ZendeskRemove',
      'OtherRemove',
    ]);
  });

  // The sentences carry user-entered domains (up to 253 characters, no break), and the notice is a
  // flex item beside the button: it must be allowed to shrink below such a word and to break it, or
  // at 320px the word pushes the card wider than the screen.
  it('lets a long domain wrap: the notice can shrink below it and breaks words', async () => {
    const long = `${'a'.repeat(240)}.example.com`;
    const withLong = {
      version: 'b'.repeat(32),
      competitors: [rival('Zendesk'), rival('Deakin', long)],
    } as unknown as CompetitorSet;
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(withLong));
    await mount({ competitors: [rival('Zendesk')] });

    await editAndSave();

    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toContain(`Deakin (${long}) was added`);
    expect(alert.classList.contains('min-w-0')).toBe(true);
    expect(alert.classList.contains('break-words')).toBe(true);
    // It is a flex item, which is why `min-w-0` is needed.
    expect(alert.parentElement!.classList.contains('flex')).toBe(true);
    expect(alert.parentElement!.contains(loadButton())).toBe(true);
  });

  it('says what was removed, and what was added and removed together', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk', 'Intercom'));
    replaceCompetitors.mockRejectedValueOnce(listChangedBy(setAt('b'.repeat(32), 'Zendesk')));
    await mount({ competitors: [rival('Zendesk'), rival('Intercom')] });
    await editAndSave();
    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it: Intercom was removed. Your change was not saved.'),
    ]);

    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('c'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await click(button('Save the list'));
    expect(alerts()).toEqual([
      notice(
        'The client’s list changed after you opened it: Deakin was added and Intercom was removed. Your change was not saved.',
      ),
    ]);
  });

  it('names no one when the same rivals are on both lists', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk')));
    await mount({ competitors: [rival('Zendesk')] });

    await editAndSave();

    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it. Your change was not saved.'),
    ]);
    expect(LIST_CHANGED_COPY.afterOpening).toBe('after you opened it');
    expect(loadButton()).toBeDefined();
  });

  it('names no one, and still offers the control, when the refusal carries no list it can read', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(null));
    await mount({ competitors: [rival('Zendesk')] });

    await editAndSave();

    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it. Your change was not saved.'),
    ]);
    expect(loadButton()).toBeDefined();
  });

  it('puts the current list in the editor when the control is pressed, and the next save names its version', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    competitorSet.mockResolvedValueOnce(setAt('b'.repeat(32), 'Zendesk', 'Deakin'));
    await click(loadButton());

    // The list as it stands replaces the person's edits; the sentence and its control are gone.
    expect(competitorSet).toHaveBeenCalledTimes(2);
    expect([...host.querySelectorAll('ol li')].map((li) => li.textContent)).toEqual([
      'ZendeskRemove',
      'DeakinRemove',
    ]);
    expect(host.querySelector('p[role="alert"]')).toBeNull();
    expect(button(COPY.loadCurrent)).toBeUndefined();
    // Focus goes where it goes when the editor opens: the control that held it is gone.
    expect(document.activeElement?.tagName).toBe('H4');
    // Nothing to save until the person changes something again, and then it names the new version.
    expect(button('Save the list').disabled).toBe(true);
    replaceCompetitors.mockResolvedValueOnce(setOf('Zendesk'));
    await click(host.querySelector('button[aria-label="Remove Deakin"]')!);
    await click(button('Save the list'));
    expect(replaceCompetitors).toHaveBeenLastCalledWith(
      'cli_1',
      [{ name: 'Zendesk', domain: null }],
      'b'.repeat(32),
    );
  });

  // The replaced edits are announced after the fact too. The status region is on the page from the
  // moment the editor opens, empty, so the words arriving in it are announced (a region inserted
  // with its words may not be); focus stays on the heading, where a Load puts it.
  it('says, in a status that was already on the page, that the current list is showing, and focus stays on the heading', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    const region = host.querySelector('[role="status"]')!;
    expect(region.textContent).toBe('');
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));
    // A refusal does not put words in it.
    expect(region.textContent).toBe('');

    competitorSet.mockResolvedValueOnce(setAt('b'.repeat(32), 'Zendesk', 'Deakin'));
    await click(loadButton());

    expect(COPY.loadedCurrent).toBe('Showing the current list.');
    // The same element it was before the words arrived, in the heading's wrapper.
    expect(host.querySelector('[role="status"]')).toBe(region);
    expect(statuses()).toEqual(['Showing the current list.']);
    const heading = host.querySelector('h4')!;
    expect(region.parentElement).toBe(heading.parentElement);
    // Focus is still on the heading: the status is read, not focused.
    expect(document.activeElement).toBe(heading);
    expect(document.activeElement).not.toBe(region);
  });

  /**
   * Opens the editor on `Zendesk`, gets a save refused because `Deakin` was added, and loads the
   * current list: the editor then holds Zendesk and Deakin, and its status says so.
   */
  async function loadTheCurrentList() {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();
    competitorSet.mockResolvedValueOnce(setAt('b'.repeat(32), 'Zendesk', 'Deakin'));
    await click(loadButton());
    expect(statuses()).toEqual(['Showing the current list.']);
  }
  /** The rows of the open editor's list, by text. */
  const listed = () => [...host.querySelectorAll('ol li')].map((li) => li.textContent);

  // The line says the editor is showing the list as it stands. It stops being true the moment the
  // person changes the drafts by hand, so it goes then. Typing alone changes the drafts only when
  // Add is pressed, and an add that is refused changes nothing: both leave the line.
  it('takes the status away when the person adds a rival to the list that was loaded', async () => {
    await loadTheCurrentList();

    await type('Brand', 'Front');
    expect(statuses()).toEqual(['Showing the current list.']);
    await click(button('Add'));

    expect(listed()).toEqual(['ZendeskRemove', 'DeakinRemove', 'FrontRemove']);
    expect(statuses()).toEqual(['']);
  });

  it('takes the status away when the person removes a rival from the list that was loaded', async () => {
    await loadTheCurrentList();

    await click(host.querySelector('button[aria-label="Remove Deakin"]')!);

    expect(listed()).toEqual(['ZendeskRemove']);
    expect(statuses()).toEqual(['']);
  });

  it('keeps the status through what changes nothing on the list: typing, and an add that is refused', async () => {
    await loadTheCurrentList();

    await type('Brand', 'Zendesk');
    await type('Domain (optional)', 'zendesk.com');
    expect(statuses()).toEqual(['Showing the current list.']);
    // Zendesk is on the list already: the add is refused and the drafts are as they were.
    await click(button('Add'));

    expect(alerts()).toEqual(['Zendesk is already on the list.']);
    expect(listed()).toEqual(['ZendeskRemove', 'DeakinRemove']);
    expect(statuses()).toEqual(['Showing the current list.']);
  });

  // Because an edit empties the region, the words arrive anew when the list is loaded again, which
  // is what makes a screen reader say them again: a region set to the text it already holds is not.
  it('says it again when the list is loaded again after an edit', async () => {
    await loadTheCurrentList();
    await type('Brand', 'Other');
    await click(button('Add'));
    expect(statuses()).toEqual(['']);

    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('c'.repeat(32), 'Zendesk', 'Deakin', 'Front')),
    );
    await click(button('Save the list'));
    competitorSet.mockResolvedValueOnce(setAt('c'.repeat(32), 'Zendesk', 'Deakin', 'Front'));
    await click(loadButton());

    expect(listed()).toEqual(['ZendeskRemove', 'DeakinRemove', 'FrontRemove']);
    expect(statuses()).toEqual(['Showing the current list.']);
  });

  // An edit has taken the status away by the time a save can be made (Save is disabled while the
  // drafts are the loaded list), and the save clears it as well. A refusal does not bring it back.
  it('has no status after the next save is refused, though the list has moved again', async () => {
    await loadTheCurrentList();

    // The person edits the list that was loaded and saves, and the list has moved on once more.
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('c'.repeat(32), 'Zendesk', 'Deakin', 'Front')),
    );
    await click(host.querySelector('button[aria-label="Remove Deakin"]')!);
    await click(button('Save the list'));

    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it: Front was added. Your change was not saved.'),
    ]);
    expect(statuses()).toEqual(['']);
  });

  it('takes the status away when the editor is closed and opened again', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();
    competitorSet.mockResolvedValueOnce(setAt('b'.repeat(32), 'Zendesk', 'Deakin'));
    await click(loadButton());
    expect(statuses()).toEqual(['Showing the current list.']);

    await click(button('Cancel'));
    expect(statuses()).toEqual([]);
    competitorSet.mockResolvedValueOnce(setAt('b'.repeat(32), 'Zendesk', 'Deakin'));
    await click(button('Correct this list'));

    expect(statuses()).toEqual(['']);
  });

  it('keeps the sentence while the person keeps editing: it is about the list, not the edit', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    await type('Brand', 'Front');
    await click(button('Add'));
    await click(host.querySelector('button[aria-label="Remove Other"]')!);

    expect(alerts()).toHaveLength(1);
    expect(loadButton()).toBeDefined();
  });

  it('is gone when the editor is cancelled, and not announced again on the closed editor', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    await click(button('Cancel'));

    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(text()).not.toContain('The client’s list changed');
    expect(text()).not.toContain(COPY.loadReplaces);
    expect(button(COPY.loadCurrent)).toBeUndefined();
  });

  it('says the current list could not be read, and keeps the sentence and the control, when loading fails', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    competitorSet.mockRejectedValueOnce(new Error('down'));
    await click(loadButton());

    expect(alerts()).toEqual([
      UNREADABLE,
      notice('The client’s list changed after you opened it: Deakin was added. Your change was not saved.'),
    ]);
    expect(loadButton()).toBeDefined();
    // The person's edit is still there, and nothing says the current list is showing.
    expect([...host.querySelectorAll('ol li')].map((li) => li.textContent)).toEqual([
      'ZendeskRemove',
      'OtherRemove',
    ]);
    expect(statuses()).toEqual(['']);
  });

  // A state set to the value it already holds changes nothing on the page, so a second identical
  // failure would not be announced, and the press would look dead. The line is taken away when the
  // press starts and put back when it fails, as a new alert, which a screen reader announces.
  it('announces a second failed load again: the line goes while it reads and comes back as a new alert', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    competitorSet.mockRejectedValueOnce(new Error('down'));
    await click(loadButton());
    const first = unreadable();
    expect(first).toBeDefined();

    let fail!: (reason: unknown) => void;
    competitorSet.mockReturnValueOnce(new Promise<CompetitorSet>((_, reject) => (fail = reject)));
    await click(loadButton());
    // While the second press reads, the first failure's line is not left standing...
    expect(unreadable()).toBeUndefined();
    // ...and the sentence about the list is: the failure to load is not about the save.
    expect(alerts()).toEqual([
      notice('The client’s list changed after you opened it: Deakin was added. Your change was not saved.'),
    ]);

    await act(async () => fail(new Error('down')));
    const second = unreadable();
    expect(second).toBeDefined();
    // A new element on the page, not the first one left as it was.
    expect(second).not.toBe(first);
    expect(alerts()).toEqual([
      UNREADABLE,
      notice('The client’s list changed after you opened it: Deakin was added. Your change was not saved.'),
    ]);
    expect(competitorSet).toHaveBeenCalledTimes(3);
  });

  it('is aria-disabled, not disabled, while the list loads, and ignores a second press', async () => {
    competitorSet.mockResolvedValueOnce(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    await mount({ competitors: [rival('Zendesk')] });
    await editAndSave();

    let release!: (value: CompetitorSet) => void;
    competitorSet.mockReturnValueOnce(new Promise<CompetitorSet>((r) => (release = r)));
    const control = loadButton();
    await click(control);
    expect(control.disabled).toBe(false);
    expect(control.getAttribute('aria-disabled')).toBe('true');
    await click(control);
    expect(competitorSet).toHaveBeenCalledTimes(2);
    await act(async () => release(setAt('b'.repeat(32), 'Zendesk', 'Deakin')));
    expect(button(COPY.loadCurrent)).toBeUndefined();
  });

  it('is offered for this refusal only: the other 409 shows the API’s sentence and no control', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValue(
      new ApiProblem({
        type: '/problems/conflict',
        title: 'Conflicting request',
        status: 409,
        instance: '/api/v1/clients/cli_1/competitors',
        detail: 'A scan of this client is running. Name the rivals after it finishes.',
      }),
    );
    await mount({ competitors: [rival('Zendesk')] });

    await editAndSave();

    expect(alerts()).toEqual(['A scan of this client is running. Name the rivals after it finishes.']);
    expect(button(COPY.loadCurrent)).toBeUndefined();
  });
});

// The editor stays open when a save is refused, so a Save that disabled itself while the request ran
// could strand the focus it held (a focused button that becomes `disabled` can lose its focus). It
// is `aria-disabled` instead, as the opener and the Load control are, and ignores a second press.
describe('while a save runs', () => {
  /** Opens the editor on `Zendesk` and adds `Other`, so there is something to save. */
  async function openAndEdit() {
    await click(button('Correct this list'));
    await type('Brand', 'Other');
    await click(button('Add'));
  }

  it('keeps Save enabled and focused, says it is saving, and sends nothing on a second press', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    let finish!: (value: CompetitorSet) => void;
    replaceCompetitors.mockReturnValue(new Promise<CompetitorSet>((r) => (finish = r)));
    await mount({ competitors: [rival('Zendesk')] });
    await openAndEdit();
    const save = button('Save the list');
    expect(save.disabled).toBe(false);
    expect(save.getAttribute('aria-disabled')).toBeNull();
    save.focus();

    await click(save);

    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    // Not `disabled`: the focused button would lose its focus. It says so to assistive technology.
    expect(save.disabled).toBe(false);
    expect(save.getAttribute('aria-disabled')).toBe('true');
    expect(save.textContent).toBe('Saving…');
    expect(document.activeElement).toBe(save);
    // Cancel is left as it was: it is disabled while the save runs.
    expect(button('Cancel').disabled).toBe(true);

    // A second and a third press are ignored, whatever a click on an enabled button does.
    await click(save);
    await click(save);
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);

    await act(async () => finish(setOf('Zendesk', 'Other')));
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="status"]')!.textContent).toBe(COPY.saved);
  });

  it('is pressable again, with nothing aria-disabled left and its focus kept, when the save is refused', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockRejectedValueOnce(
      listChangedBy(setAt('b'.repeat(32), 'Zendesk', 'Deakin')),
    );
    await mount({ competitors: [rival('Zendesk')] });
    await openAndEdit();
    button('Save the list').focus();

    await click(button('Save the list'));

    const save = button('Save the list');
    expect(save.disabled).toBe(false);
    expect(save.getAttribute('aria-disabled')).toBeNull();
    expect(document.activeElement).toBe(save);
    expect(button('Cancel').disabled).toBe(false);
    // Pressed again, it sends again: a refusal does not lock it.
    replaceCompetitors.mockResolvedValueOnce(setOf('Zendesk', 'Other'));
    await click(save);
    expect(replaceCompetitors).toHaveBeenCalledTimes(2);
  });

  it('is still disabled, for real, when there is nothing to save', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));

    const save = button('Save the list');
    expect(save.disabled).toBe(true);
    expect(save.getAttribute('aria-disabled')).toBeNull();
    await click(save);
    expect(replaceCompetitors).not.toHaveBeenCalled();
  });
});

describe('after a save', () => {
  const addOther = async () => {
    await type('Brand', 'Other');
    await click(button('Add'));
    await click(button('Save the list'));
  };

  it('says the list is saved for later scans, takes focus, and does not make the scan’s own card claim the new rival', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    const onSaved = vi.fn();
    await mount({ competitors: [], setMissing: true, onSaved });
    await click(button('Name the rivals'));
    await addOther();

    const status = host.querySelector('[role="status"]') as HTMLElement;
    expect(status.textContent).toBe('Saved for later scans. This scan’s list and score do not change.');
    expect(status.textContent).toBe(COPY.saved);
    // Said once: the paragraph below the status line does not repeat it.
    expect(text().split('This scan’s list and score do not change.').length - 1).toBe(1);
    expect(text()).toContain('A list cannot be saved while a scan of this client is running.');
    expect(document.activeElement).toBe(status);
    // Closed again, and still describing the scan's own list: the new rival is not on it.
    expect(button('Save the list')).toBeUndefined();
    expect(text()).toContain(COPY.missingLead);
    expect(text()).not.toContain('Other');
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('works for a caller that passes no onSaved: the report page passes none', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await addOther();
    expect(replaceCompetitors).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="status"]')!.textContent).toBe(COPY.saved);
  });

  it('says what is true of a scan that has not started yet', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    await mount({ competitors: [], setMissing: true, scanFinished: false });
    await click(button('Name the rivals'));
    await addOther();
    expect(host.querySelector('[role="status"]')!.textContent).toBe(
      'Saved as the client’s list. A scan that has not started yet uses it when it runs.',
    );
  });

  it('the open editor says the same before the save, for a finished and for a queued scan', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    expect(text()).toContain(COPY.openNote);
    expect(text()).toContain('The list is saved for later scans. This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.');
    await click(button('Cancel'));
    act(() => root.unmount());
    root = createRoot(host);
    await mount({ competitors: [rival('Zendesk')], scanFinished: false });
    await click(button('Correct this list'));
    expect(text()).toContain(COPY.openNoteRunning);
    expect(COPY.openNoteRunning.startsWith('A list cannot be saved while a scan of this client is running.')).toBe(true);
    expect(text()).not.toContain('This scan’s list and score do not change');
  });

  it('a re-open after a save still says the client’s list differs from the scan’s', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await addOther();
    competitorSet.mockResolvedValue(setOf('Zendesk', 'Other'));
    await click(button('Correct this list'));
    expect(text()).toContain(COPY.differsFromScan);
  });

  it('opening the editor again clears the status line, and Cancel then returns focus to the opener', async () => {
    competitorSet.mockResolvedValue(setOf('Zendesk'));
    replaceCompetitors.mockResolvedValue(setOf('Zendesk', 'Other'));
    await mount({ competitors: [rival('Zendesk')] });
    await click(button('Correct this list'));
    await addOther();
    expect(statuses()).toEqual([COPY.saved]);

    await click(button('Correct this list'));
    // The saved line is gone; the one status region left is the open editor's own, and it is empty.
    expect(statuses()).toEqual(['']);
    await click(button('Cancel'));
    expect(statuses()).toEqual([]);
    expect(document.activeElement).toBe(button('Correct this list'));
  });
});
