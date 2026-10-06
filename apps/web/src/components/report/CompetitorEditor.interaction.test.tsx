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

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const competitorSet = vi.fn<(id: string) => Promise<CompetitorSet | null>>();
const replaceCompetitors = vi.fn<(id: string, list: unknown[]) => Promise<CompetitorSet>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      competitorSet: (id: string) => competitorSet(id),
      replaceCompetitors: (id: string, list: unknown[]) => replaceCompetitors(id, list),
    },
  };
});

import { CompetitorEditor } from './CompetitorEditor';

const rival = (name: string, domain: string | null = null) =>
  ({ id: name, name, domain, rank: 1, isManualOverride: false }) as unknown as Competitor;
const setOf = (...names: string[]) =>
  ({ competitors: names.map((n) => rival(n)) }) as unknown as CompetitorSet;

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

    expect(replaceCompetitors).toHaveBeenNthCalledWith(1, 'cli_1', [
      { name: 'Zendesk', domain: null },
      { name: 'G2', domain: 'g2.com' },
      { name: 'Other', domain: null },
    ]);
    const alert = host.querySelector('p[role="alert"]')!;
    expect(alert.textContent).toBe(`G2: ${PLATFORM_SENTENCE}`);
    expect(text()).not.toContain('One or more fields were invalid.');
    expect(onSaved).not.toHaveBeenCalled();

    await click(host.querySelector('button[aria-label="Remove G2"]')!);
    expect(host.querySelector('p[role="alert"]')).toBeNull();
    await click(button('Save the list'));

    expect(replaceCompetitors).toHaveBeenNthCalledWith(2, 'cli_1', [
      { name: 'Zendesk', domain: null },
      { name: 'Other', domain: null },
    ]);
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
    expect(host.querySelector('[role="status"]')).not.toBeNull();

    await click(button('Correct this list'));
    expect(host.querySelector('[role="status"]')).toBeNull();
    await click(button('Cancel'));
    expect(host.querySelector('[role="status"]')).toBeNull();
    expect(document.activeElement).toBe(button('Correct this list'));
  });
});
