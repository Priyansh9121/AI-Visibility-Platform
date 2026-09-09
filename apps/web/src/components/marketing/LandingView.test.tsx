/**
 * The public landing page — Epic 9.10, extended in Epic 9.15.
 *
 * Rendered to static markup and asserted over, the approach ReportView.test.tsx
 * and DashboardView.test.tsx use.
 *
 * The bulk of these are NEGATIVE assertions, and deliberately so. The risk on a
 * marketing page is not that a heading fails to render — it is that a claim
 * appears which nothing in the product supports. A test suite that only checked
 * the copy was present would pass just as happily on invented testimonials.
 *
 * ONE ASSERTION WAS INVERTED IN 9.15, AND IT IS WORTH SAYING WHY IN THE FILE
 * -------------------------------------------------------------------------
 * `advertises no price or plan, because none is decided` was correct while
 * north-star.md §5.3's tiers were [HYPOTHESIS]. The founder has since decided a
 * real price, §5.3 has been updated to record that, and the page publishes it.
 * The test now asserts the opposite — that the price IS there, and is the right
 * number in both places it appears. Deleting the test outright would have left
 * the page's most commercially consequential sentence unguarded.
 *
 * WHAT THESE TESTS CANNOT REACH
 * -----------------------------
 * This suite has no DOM: `renderToStaticMarkup` produces a string, and a click
 * handler is not in it. So "Log in goes to sign-in rather than sign-up" is
 * asserted here only as far as it can be — two distinct controls exist, with
 * distinct labels — and the destination itself is covered by the live browser
 * pass recorded in build-log.md, not by this file. Stating that plainly is
 * better than a test named after a behaviour it does not exercise.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LandingView } from './LandingView';

const html = () => renderToStaticMarkup(<LandingView animate={false} />);

describe('the page explains the product', () => {
  it('names the buyer it is written for', () => {
    expect(html()).toContain('For SEO and digital marketing agencies');
  });

  it('describes the scan in terms of what is actually built', () => {
    const out = html();
    expect(out).toContain('Twenty-four questions, tagged by buying stage');
    // "six minutes" was Epic 9.2's measurement and went stale at 9.17. The
    // copy now says "about ten minutes", hedged, because three 12-prompt runs
    // spread 286-331s and no 24-prompt scan has been timed since 9.17.
    expect(out).toContain('about ten minutes');
    expect(out).not.toContain('six minutes');
  });

  it('walks the whole pipeline, not only the parts that photograph well', () => {
    const out = html();
    // product-spec.md §5.4's seven stages. The 9.10 page described four and
    // skipped intake, competitor detection and the technical audit — the three
    // that answer "what do I have to fill in?", which is a prospect's first
    // question.
    expect(out).toContain('One URL in, and nothing else to fill in');
    expect(out).toContain('Rivals found rather than guessed at');
    expect(out).toContain('The site itself checked for what the engines need');
  });

  it('leads with a call to action that reaches the sign-up surface', () => {
    expect(html()).toContain('Scan a website');
  });

  it('renders every section it promises, in order', () => {
    const out = html();
    const order = [
      'For SEO and digital marketing agencies',
      'What a scan does',
      'The report',
      'What is different',
      'What you can stand behind',
      'What it does not do yet',
      // Matched on the heading rather than the eyebrow: "Pricing" also appears
      // in the header nav, which is earlier in the markup by design.
      'One plan. Twenty-nine dollars a month.',
      'Start',
    ];
    let cursor = -1;
    for (const label of order) {
      const at = out.indexOf(label);
      expect(at, `${label} is missing`).toBeGreaterThan(-1);
      expect(at, `${label} is out of order`).toBeGreaterThan(cursor);
      cursor = at;
    }
  });
});

describe('the header is on page one, before any scrolling or clicking', () => {
  it('carries the wordmark, both nav links, log in and the primary action', () => {
    const out = html();
    expect(out).toContain('AI Visibility Platform');
    expect(out).toContain('>Product<');
    expect(out).toContain('>Pricing<');
    expect(out).toContain('Log in');
    expect(out).toContain('Get started free');
  });

  it('puts all five above the first section, not in a footer', () => {
    const out = html();
    // Everything in the header must precede the hero's eyebrow, which is the
    // first thing under it.
    const hero = out.indexOf('For SEO and digital marketing agencies');
    for (const label of ['AI Visibility Platform', '>Product<', '>Pricing<', 'Log in', 'Get started free']) {
      expect(out.indexOf(label), `${label} is not above the fold`).toBeLessThan(hero);
    }
  });

  it('navigates within the page rather than to routes that do not exist', () => {
    const out = html();
    // One plan does not earn a comparison page, so both nav links are in-page
    // anchors and the sections they name really carry those ids.
    expect(out).toContain('href="#product"');
    expect(out).toContain('href="#pricing"');
    expect(out).toContain('id="product"');
    expect(out).toContain('id="pricing"');
  });

  it('offers log in and get started as two separate controls', () => {
    const out = html();
    // The destinations differ — sign-in versus sign-up — and this suite has no
    // DOM to click, so what is asserted is that they are not the same control
    // wearing two labels. The destination itself is verified in the browser.
    expect(out).toContain('Log in');
    expect(out).toContain('Get started free');
    expect(out.indexOf('Log in')).not.toBe(out.indexOf('Get started free'));
  });

  it('stays put while the page scrolls', () => {
    // The pricing card and the limitations section are both below the fold by
    // design; a header that scrolled away would take "Log in" with it exactly
    // when a reader has decided.
    expect(html()).toMatch(/class="[^"]*\bsticky\b/);
  });
});

describe('the price is published, deliberately', () => {
  it('states $29 a month on the pricing card', () => {
    const out = html();
    expect(out).toContain('$29');
    expect(out).toContain('per month');
  });

  it('says what the plan includes, seats named rather than implied', () => {
    const out = html();
    expect(out).toContain('3 seats');
    expect(out).toContain('the same seat limit every agency already gets');
  });

  it('agrees with itself everywhere a number appears', () => {
    const out = html();
    // A page that says $29 in one place and something else in another is worse
    // than a page with no price. Both mentions come from one constant; this
    // asserts no third, hand-typed one crept in.
    const prices = out.match(/\$\d+/g) ?? [];
    expect(prices.length).toBeGreaterThan(0);
    expect(new Set(prices)).toEqual(new Set(['$29']));
  });

  it('offers sign-up, not checkout, to a visitor with no account', () => {
    const out = html();
    // You cannot subscribe an agency that does not exist yet.
    expect(out).toContain('Get started');
    expect(out).not.toContain('>Subscribe<');
  });

  it('says plainly that nothing is gated behind the plan', () => {
    const out = html();
    expect(out).toContain('Signing up is free and stays free');
  });
});

describe('nothing on this page is fabricated', () => {
  it('carries no testimonial, logo wall, user count or press mention', () => {
    const out = html();
    // The specific shapes a page like this is usually padded with. This product
    // has none of them yet, and north-star.md §7's trust argument is worth
    // nothing on a page that opens with an invented one. Publishing a real
    // price changed nothing about this rule.
    for (const tell of [
      'testimonial',
      'trusted by',
      'as featured in',
      'as seen in',
      'customers say',
      'join thousands',
      'brands trust',
      'reviews',
      '★',
    ]) {
      expect(out.toLowerCase(), `found "${tell}"`).not.toContain(tell);
    }
    // Word-bounded: a bare `toContain('rated')` matches inside "Generated",
    // which is how this assertion first failed on honest copy.
    expect(out.toLowerCase()).not.toMatch(/\brated\b/);
  });

  it('claims no scale it cannot evidence', () => {
    const out = html();
    // Any "N+ brands / agencies / customers / users" construction. Scoped to
    // exclude the seat count, which is a fact about the plan rather than a
    // claim about how many people bought it.
    expect(out).not.toMatch(/\d[\d,]*\s*\+?\s*(brands|agencies|customers|users|companies|teams)/i);
  });

  it('does not name a real client or publish anyone real score', () => {
    const out = html();
    // Epic 9.8 scanned a real, named business. Its score is not this page's to
    // publish — consent was never asked for, and a page arguing that its data
    // discipline is trustworthy cannot open by breaching a client confidence.
    expect(out).not.toContain('psmdigitalagency');
    expect(out).not.toContain('PSM');
    expect(out).not.toContain('28.89');
  });

  it('labels its example chart AS an example, right next to it', () => {
    const out = html();
    expect(out).toContain('Example — illustrative figures, not a real client');
    // The label must sit before the chart in the markup, not in a footnote
    // below the fold where it stops doing its job.
    expect(out.indexOf('illustrative figures')).toBeLessThan(out.indexOf('avp-ledger'));
  });

  it('states what the product cannot do, not only what it can', () => {
    const out = html();
    expect(out).toContain('What it does not do yet');
    // React escapes the apostrophe in static markup, so match around it.
    expect(out).toContain('measures one vendor');
    expect(out).toContain('models today');
    expect(out).toContain('Nothing tracks whether a fix was actually done');
  });

  it('no longer claims two things that have since shipped', () => {
    const out = html();
    // Both sentences were true when 9.10 wrote them and false by 9.15. On a
    // page whose whole argument is that its claims are checkable, a stale
    // limitation is the same defect as an inflated feature — it is just the
    // flattering direction that usually gets caught.
    expect(out).not.toContain('no PDF export yet');
    expect(out).not.toContain('Access is by conversation while this is in pilot');
    expect(out).not.toContain('no self-serve plan');
  });
});

describe('the page arrives rather than being already there — Epic 9.16', () => {
  // Rendered WITH animation on, which is what a browser gets. Every other test
  // in this file uses animate={false} and would therefore see the finished
  // markup whatever the reveal wiring did.
  const live = () => renderToStaticMarkup(<LandingView animate />);

  it('staggers the hero parts, because it has no scroll to arrive from', () => {
    const out = live();
    // Eyebrow, headline, lead, call to action — four parts, three offsets.
    expect(out).toContain('--avp-reveal-index:1');
    expect(out).toContain('--avp-reveal-index:2');
    expect(out).toContain('--avp-reveal-index:3');
  });

  it('gives every section below the hero a reveal of its own', () => {
    // Seven: the six content sections plus pricing. The hero is not among them
    // — it staggers its parts instead of arriving as a block.
    const out = live();
    expect((out.match(/avp-reveal-group/g) ?? []).length).toBeGreaterThanOrEqual(2);
    expect((out.match(/class="avp-reveal[ "]/g) ?? []).length).toBeGreaterThanOrEqual(7);
  });

  it('staggers all seven pipeline steps, in order', () => {
    const out = live();
    for (let i = 1; i <= 6; i++) {
      expect(out, `step ${i} has no offset`).toContain(`--avp-reveal-index:${i}`);
    }
  });

  it('keeps the step list a real list', () => {
    // `Reveal as="li"` rather than a wrapping div. A div between <ol> and its
    // items is invalid markup and drops list semantics for a screen reader.
    const out = live();
    expect(out).toMatch(/<ol[^>]*avp-reveal-group/);
    expect(out).toMatch(/<li[^>]*avp-reveal/);
    expect(out).not.toMatch(/<ol[^>]*>\s*<div/);
  });

  it('builds the example ledger bar by bar', () => {
    const out = live();
    expect(out).toContain('avp-ledger--staggered');
    // One index per dimension, and the ledger's own property — not the
    // page's, so the two sequences cannot be confused.
    for (let i = 0; i < 5; i++) {
      expect(out).toContain(`--avp-ledger-index:${i}`);
    }
  });

  it('is the only place in the product that sets the ledger stagger', () => {
    // Stated as a test rather than a comment. If a second call site appears,
    // this is the assertion that should be reconsidered on purpose.
    expect(live()).toContain('avp-ledger--staggered');
  });

  it('computes no timing value in JavaScript', () => {
    // Every duration and delay comes from a token; the component emits only
    // an index. A millisecond literal here would mean motion had escaped the
    // token layer.
    expect(live()).not.toMatch(/\d+ms/);
  });

  it('renders the finished page when motion is off', () => {
    // What a static render, a test, and a print get — and the reason every
    // other assertion in this file still reads the real copy.
    const out = html();
    expect(out).toContain('avp-reveal--revealed');
    // The bars are lit, not waiting to be.
    expect(out).toContain('scaleY(1)');
    expect(out).not.toContain('scaleY(0)');
  });

  it('keeps `animate` and `staggerDimensions` as separate questions', () => {
    // `staggerDimensions` is structural — it says what SHAPE the reveal takes,
    // and the landing page always wants the bar-by-bar one. `animate` says
    // whether any motion happens at all. So the modifier class is present even
    // with motion off, and the bars are simply already lit. Asserted because
    // the first version of the test above assumed the opposite and was wrong
    // about the component, not about the page.
    expect(html()).toContain('avp-ledger--staggered');
  });

  it('still says everything it said before, with motion on', () => {
    // The copy is in the markup either way. Revealing hides content visually
    // until it arrives; it must never remove it from the document.
    const out = live();
    expect(out).toContain('For SEO and digital marketing agencies');
    expect(out).toContain('$29');
    expect(out).toContain('One URL in, and nothing else to fill in');
    expect(out).toContain('Signing up is free and stays free');
  });
});

describe('it is built from the design system, not styled locally', () => {
  it('uses the shared section primitive rather than a hand-rolled hero', () => {
    const out = html();
    expect(out).toContain('avp-section');
    expect(out).toContain('avp-section--lead');
  });

  it('builds the pricing card from the shared card, not a bespoke panel', () => {
    expect(html()).toContain('avp-card');
  });

  it('shows the product own signature visualisation, not stock artwork', () => {
    const out = html();
    expect(out).toContain('avp-ledger');
    expect(out).toContain('avp-meter');
    // No external asset of any kind: the licensed-assets rule. The header wordmark is
    // type, not an image, for the same reason.
    expect(out).not.toContain('<img');
    expect(out).not.toContain('background-image');
    expect(out).not.toContain('<svg viewBox="0 0 24 24"');
  });

  it('carries no ad hoc colour or type value', () => {
    const out = html();
    // The Epic 9.8 failure mode: a class that looks plausible and resolves to
    // nothing. Arbitrary-value and raw-palette utilities are the two shapes
    // that would bypass the preset entirely.
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});
