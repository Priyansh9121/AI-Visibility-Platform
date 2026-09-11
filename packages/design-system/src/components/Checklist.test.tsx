/**
 * Checklist — Epic 19.
 *
 * Rendered to static markup, the way every primitive here is tested. What is
 * asserted is the contract the dashboard relies on: the count is honest, a
 * step's state is said in words as well as drawn, the current step is marked,
 * completion changes the words and the container's class, and — the part
 * that matters most for a Working screen — nothing performs on first paint.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Checklist, type ChecklistStep } from './Checklist.js';
import { Button } from './Button.js';

const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

const steps = (done: readonly boolean[]): ChecklistStep[] =>
  done.map((d, i) => ({
    key: `s${i}`,
    label: `Step ${i}`,
    done: d,
    detail: d ? 'Yes' : 'Not yet',
    action: d ? undefined : <Button size="sm">Do it</Button>,
  }));

describe('the count', () => {
  it('is the number of lit steps over the total', () => {
    const markup = html(<Checklist title="Getting started" steps={steps([true, true, false, false, false])} />);
    expect(markup).toContain('avp-checklist__lit">2<');
    expect(markup).toContain('avp-checklist__of">/5<');
  });

  it('is hidden from assistive tech because the lead says it in words', () => {
    const markup = html(
      <Checklist title="Getting started" lead="Two of five lit." steps={steps([true, true, false])} />,
    );
    expect(markup).toMatch(/avp-checklist__count" aria-hidden="true"/);
    expect(markup).toContain('Two of five lit.');
  });
});

describe('each step says its state in words and marks the current one', () => {
  const markup = html(<Checklist title="T" steps={steps([true, false, false])} />);

  it('names done and to-do for a screen reader', () => {
    expect(markup).toContain('Done: </span>Step 0');
    expect(markup).toContain('To do: </span>Step 1');
  });

  it('the first unlit step is the current one, and only it', () => {
    expect(markup.match(/aria-current="step"/g)).toHaveLength(1);
    const current = markup.slice(markup.indexOf('is-current'), markup.indexOf('is-pending'));
    expect(current).toContain('Step 1');
  });

  it('a done step drops its action; an unlit one keeps it', () => {
    const done = markup.slice(markup.indexOf('is-done'), markup.indexOf('is-current'));
    expect(done).not.toContain('Do it');
    const current = markup.slice(markup.indexOf('is-current'), markup.indexOf('is-pending'));
    expect(current).toContain('Do it');
  });

  it('the lamps are decoration; the words carry the state', () => {
    expect(markup.match(/avp-checklist__lamp" aria-hidden="true"/g)).toHaveLength(3);
  });

  it('indexes each step for the stagger in CSS, not in a millisecond value', () => {
    expect(markup).toContain('--avp-checklist-index:0');
    expect(markup).toContain('--avp-checklist-index:2');
    expect(markup).toContain('--avp-checklist-count:3');
    expect(markup).not.toMatch(/\d+ms/);
  });
});

describe('completion', () => {
  it('changes the words and lights the container', () => {
    const markup = html(
      <Checklist
        title="Getting started"
        completeTitle="Up and running"
        completeLead="Every step is lit."
        steps={steps([true, true])}
        onDismiss={() => {}}
        completeDismissLabel="Done"
      />,
    );
    expect(markup).toContain('is-complete');
    expect(markup).toContain('Up and running');
    expect(markup).not.toContain('Getting started');
    expect(markup).toContain('Every step is lit.');
    expect(markup).toContain('avp-btn--primary');
    expect(markup).toContain('>Done<');
  });

  it('never performs on first paint — the completing class needs a change during the mount', () => {
    // A page that loads already complete is a page checked many times a day,
    // not a milestone happening now. Static render is first paint; the class
    // that runs the breath must not be there.
    const markup = html(<Checklist title="T" steps={steps([true, true])} />);
    expect(markup).toContain('is-complete');
    expect(markup).not.toContain('is-completing');
  });

  it('an empty step list is not complete', () => {
    expect(html(<Checklist title="T" steps={[]} />)).not.toContain('is-complete');
  });
});

describe('the close control', () => {
  it('is absent when nothing is passed to it', () => {
    expect(html(<Checklist title="T" steps={steps([false])} />)).not.toContain('avp-checklist__dismiss');
  });

  it('is a quiet ghost while steps remain', () => {
    const markup = html(<Checklist title="T" steps={steps([false])} onDismiss={() => {}} dismissLabel="Hide this" />);
    const control = markup.slice(markup.indexOf('avp-checklist__dismiss'), markup.indexOf('avp-checklist__steps'));
    expect(control).toContain('avp-btn--ghost');
    expect(control).toContain('Hide this');
  });
});
