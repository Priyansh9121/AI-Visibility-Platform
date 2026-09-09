/**
 * URL intake — Epic 2, with the loading/error collision fixed in Epic 9.11.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { IntakeForm } from './IntakeForm';

const render = () =>
  renderToStaticMarkup(
    <IntakeForm onClassified={() => {}} onStarted={() => {}} onFailed={() => {}} />,
  );

describe('the form is built from the design system', () => {
  it('renders a real labelled field and a primary action', () => {
    const out = render();
    expect(out).toContain('avp-field');
    expect(out).toContain('Website address');
    expect(out).toContain('Identify this business');
  });

  it('tells the user what will actually be read', () => {
    expect(render()).toContain('we read the homepage and a few key pages');
  });

  it('emits no arbitrary-value or raw-palette utility', () => {
    const out = render();
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('shows no error before anything is submitted', () => {
    expect(render()).not.toContain('avp-field__error');
  });
});

describe('a failed submission cannot leave the page mid-flight', () => {
  it('makes onFailed a REQUIRED prop, so the wiring cannot be dropped', () => {
    // The defect this guards, found in a browser: `onStarted` moved the page
    // into "working" and nothing moved it back, so a rejected submit rendered
    // the field error with "Reading the site" still sitting underneath it.
    // Both components were individually correct in isolation, which is why no
    // unit test caught it.
    //
    // The guarantee is enforced by the TYPE, not by this assertion: omitting
    // `onFailed` is a compile error, and `tsc --noEmit` runs in the same gate
    // as these tests. This repo has no DOM-driving test library (adding one
    // would need a licence review under the dependency-licensing rule), so a required prop
    // is both the stronger guard and the cheaper one.
    //
    // @ts-expect-error - onFailed is required; removing it must not compile.
    const missing = <IntakeForm onClassified={() => {}} onStarted={() => {}} />;
    expect(missing).toBeTruthy();
  });
});
