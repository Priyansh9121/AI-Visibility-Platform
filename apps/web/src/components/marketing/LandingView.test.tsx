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
import { compositeScore, visibilityBandLabel } from '@avp/design-system';
import { EXAMPLE_DIMENSIONS } from './LandingView';
import { PLAN_PRICE_USD } from './PricingCard';
import { SCAN_DURATION_SENTENCE } from '@/lib/scans/scanOutcome';

/** Every price a hand could plausibly type in words. Any of them beside "dollars" fails. */
const PRICE_WORDS = [
  'nineteen', 'twenty', 'twenty-nine', 'thirty', 'thirty-nine', 'forty', 'forty-nine', 'fifty',
  'fifty-nine', 'sixty', 'seventy', 'seventy-nine', 'eighty', 'ninety', 'ninety-nine',
  'one hundred', 'a hundred', 'one hundred and fifty', 'one hundred fifty', 'a hundred and fifty',
  'one hundred and sixty', 'two hundred',
];
import { renderToStaticMarkup } from 'react-dom/server';
import { LandingView } from './LandingView';

const html = () => renderToStaticMarkup(<LandingView animate={false} />);

describe('the page explains the product', () => {
  it('names the buyer it is written for', () => {
    expect(html()).toContain('For SEO and digital marketing agencies');
  });

  it('describes the scan in terms of what is actually built', () => {
    const out = html();
    // Twenty since 2026-09-12 (twenty-four from Epic 4.1 until then): the
    // founder cut to the spec's floor, and the copy moved with the constant.
    expect(out).toContain('Twenty questions, tagged by buying stage');
    expect(out).not.toContain('Twenty-four');
    // Four default engines since the same day; the grounded Claude engine is
    // no longer a default, so the copy must not promise Claude "in two modes".
    expect(out).toContain('Claude, ChatGPT and Gemini');
    expect(out).not.toContain('two modes');
    // "A scan usually takes a few minutes" since launch checklist 3.39. "Under five
    // minutes" (2026-09-12) rested on a 300s budget nothing enforces: 9 of the 25
    // finished scans in the dev database took longer. "six minutes" was Epic 9.2's
    // figure and "about ten minutes" a pre-measurement hedge. No figure is promised.
    expect(out).toContain('usually takes a few minutes');
    expect(out).not.toContain('five minutes');
    expect(out).not.toContain('six minutes');
    expect(out).not.toContain('ten minutes');
    // The same words as the dashboard and the intake card (`SCAN_DURATION_SENTENCE`).
    expect(out).toContain(SCAN_DURATION_SENTENCE.replace(/\.$/, ''));
    // The engines are asked together, never "one at a time".
    expect(out).not.toContain('one at a time');
    // What the sentence goes on to say is what the code says (checked in round 2 of 3.39).
    expect(out).toContain(
      'twenty questions, put to four AI answer engines. Your first scan is free with a Google sign-in.',
    );
  });

  it('walks the whole pipeline, not only the parts that photograph well', () => {
    const out = html();
    // product-spec.md §5.4's seven stages. The 9.10 page described four and
    // skipped intake, competitor detection and the technical audit — the three
    // that answer "what do I have to fill in?", which is a prospect's first
    // question.
    expect(out).toContain('One URL in, and nothing else to fill in');
    expect(out).toContain('Rivals found rather than guessed at');
    expect(out).toContain('The homepage checked for structured data, crawlability and freshness');
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
      `One plan. $${PLAN_PRICE_USD} a month.`,
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
  it('states $150 a month on the pricing card', () => {
    const out = html();
    expect(out).toContain('$150');
    expect(out).toContain('per month');
    // Repriced by the founder on 2026-10-04: the old figure is nowhere on the page.
    expect(out).not.toContain('$160');
  });

  it('says what the plan includes, seats named rather than implied', () => {
    const out = html();
    expect(out).toContain('3 seats per agency.');
  });

  it('agrees with itself everywhere a number appears', () => {
    const out = html();
    // A page that says $150 in one place and something else in another is worse
    // than a page with no price. Every mention comes from one constant; this
    // asserts no hand-typed one crept in — as a figure OR in words. The pricing
    // heading read "Twenty-nine dollars a month" above a $160 card for six
    // days (read live 2026-09-28); the words escaped the `$\d+` match below.
    const prices = out.match(/\$\d[\d,]*/g) ?? [];
    expect(prices.length).toBeGreaterThan(0);
    expect(new Set(prices)).toEqual(new Set([`$${PLAN_PRICE_USD}`]));
    expect(out).not.toContain('$29');
    expect(out.toLowerCase()).not.toContain('twenty-nine');
    expect(out.toLowerCase()).not.toContain('twenty nine');
    for (const words of PRICE_WORDS) {
      expect(out.toLowerCase(), `a price in words: "${words}"`).not.toMatch(
        new RegExp(`\\b${words}\\b\\s+dollars`),
      );
    }
  });

  it('shows one example score, computed from the example ledger, banded by the real function', () => {
    const out = html();
    // 37.7 at one decimal; both the ledger and the meter round it to 38 for
    // display, by the same `Math.round` the design system applies everywhere.
    const composite = compositeScore(EXAMPLE_DIMENSIONS);
    expect(Math.round(composite ?? NaN)).toBe(38);
    // The meter and the ledger agree because the meter reads the ledger's
    // arithmetic; until 2026-09-28 the meter said 41 Emerging beside a
    // worked example that summed to 38.
    expect(out).toContain(visibilityBandLabel(38));
    expect(out).not.toContain('Emerging');
    expect(out).not.toMatch(/>41</);
  });

  it('offers sign-up, not checkout, to a visitor with no account', () => {
    const out = html();
    // You cannot subscribe an agency that does not exist yet.
    expect(out).toContain('Get started');
    expect(out).not.toContain('>Subscribe<');
  });

  it('says the first scan is free and the plan is for the rest — 2026-09-28', () => {
    const out = html();
    // With a Google sign-in since 2026-09-29, and said so in public.
    expect(out).toContain('Your first scan is free with a Google sign-in.');
    expect(out).not.toContain('Your first scan is free.');
    expect(out).toContain(`$${PLAN_PRICE_USD}/month after that`);
    expect(out).not.toContain('when you are ready to pay for it');
    // The honest-limits list no longer claims scans are unmetered: they are.
    expect(out).not.toContain('Scan volume is not metered');
    expect(out).toContain('Signing up is free. Your first scan is free with a Google sign-in.');
    expect(out).not.toContain('includes one full scan and report');
  });
});

describe('the free trial — 2026-10-04, and only when the API says there is one', () => {
  // The pricing card reads the trial's length from the API (`GET /billing/plan`) when it
  // mounts. A static render never runs the request, so `html()` is the page while the API has
  // not answered (and so is the page when the request fails, or says 0): no trial, anywhere.
  // `initialTrialDays` renders the page as it is once the API has said so; the mounted
  // behaviour, one request for the whole page and every failure, is `lib/billing/trial.test.tsx`.
  const SENTENCE =
    'The first 30 days after you subscribe are free, if you subscribe from an account signed in with Google. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';
  const on = () => renderToStaticMarkup(<LandingView animate={false} initialTrialDays={30} />);

  it('off: says nothing about a trial, or about free days, anywhere on the page', () => {
    for (const out of [html(), renderToStaticMarkup(<LandingView animate={false} initialTrialDays={0} />)]) {
      expect(out.toLowerCase()).not.toContain('trial');
      expect(out).not.toContain('after you subscribe');
      expect(out).not.toContain('charged on day');
    }
  });

  it('on: the pricing card says it, in the pricing section, once, word for word', () => {
    const out = on();
    expect(out).toContain(SENTENCE);
    expect(out.indexOf(SENTENCE)).toBeGreaterThan(out.indexOf(`One plan. $${PLAN_PRICE_USD} a month.`));
    expect(out.indexOf(SENTENCE)).toBeLessThan(out.indexOf('Signing up is free.'));
    expect(out.split('after you subscribe').length - 1).toBe(1);
  });

  it('the pricing lead no longer says the plan is "not a trial", whatever the state', () => {
    // That clause sat beside a card that may now say the first days after you subscribe are free.
    // What the plan is is still said: the whole product, and not a cut-down tier.
    for (const out of [html(), on()]) {
      expect(out.toLowerCase()).not.toContain('not a trial');
      expect(out).toContain('It buys the whole product as described above, and not a cut-down tier');
    }
  });

  it('the hero’s sentence is the same on or off: its price is still true with a trial', () => {
    for (const out of [html(), on()]) {
      expect(out).toContain(
        'twenty questions, put to four AI answer engines. Your first scan is free with a Google sign-in.',
      );
      expect(out).toContain(`$${PLAN_PRICE_USD}/month after that.`);
    }
  });

  it('on, the page still agrees with itself about money: one price, no figure of its own', () => {
    const out = on();
    const prices = out.match(/\$\d[\d,]*/g) ?? [];
    expect(new Set(prices)).toEqual(new Set([`$${PLAN_PRICE_USD}`]));
    expect(out).not.toContain('$0');
    expect(out.toLowerCase()).not.toContain('no card');
    expect(out.toLowerCase()).not.toContain('no credit card');
  });

  it('on, it is still a page that invents nothing: no scale, no testimonial', () => {
    const out = on().toLowerCase();
    for (const tell of ['trusted by', 'customers say', 'join thousands', 'most popular', 'best value']) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
    expect(out).not.toMatch(/\d[\d,]*\s*\+?\s*(brands|agencies|customers|users|companies|teams)/i);
  });
});

describe('the fix list is described as the report orders it — launch checklist 3.63', () => {
  // The report ranks its fixes by the points each would recover (a fix for a noindex tag, a
  // `Disallow: /` rule or a missing robots.txt first, and fixes with no point figure after those
  // that have one) and draws an effort of S, M or L beside each. The page said "ordered by what
  // they would move and what they would cost to do", and the pricing card, which this page
  // contains, said "ordered by what they would move and what they would cost you". Cost ranks
  // nothing. `claimsAgainstCode.test.tsx` reads the report back to hold these words to it.
  const CLAUSE = 'ordered by the points each would recover, with the effort of each shown beside it';

  it('says it in the last pipeline step, word for word', () => {
    expect(html()).toContain(
      'Five weighted dimensions into one number out of 100, the biggest gap named in points, and specific changes ' +
        `${CLAUSE}.`,
    );
  });

  it('says it again on the pricing card the page carries, so the two agree', () => {
    const out = html();
    expect(out).toContain(`Named fixes, ${CLAUSE}.`);
    expect(out.split(CLAUSE).length - 1).toBe(2);
  });

  it('no longer says they are ordered by what they would move or what they would cost', () => {
    for (const out of [html(), renderToStaticMarkup(<LandingView animate={false} initialTrialDays={30} />)]) {
      for (const old of ['what they would move', 'what they would cost', 'cost to do', 'would cost you']) {
        expect(out, `found "${old}"`).not.toContain(old);
      }
      // The only ordering the page names is by points.
      expect(out).not.toMatch(/ordered by what/i);
    }
  });
});

describe('sentences corrected against the code — launch checklist 3.63', () => {
  // Each of these was read against the code and was not true; the new wording says less, and
  // each old claim is asserted gone so it cannot come back unnoticed. React escapes the
  // apostrophe, so the text is compared with it put back.
  const text = () => html().replaceAll('&#x27;', "'");

  it('says a hand-corrected rival list stays in place for later scans, and names no re-detection', () => {
    // No screen starts a re-detection, and a later scan carries the list forward rather than
    // detecting again (`services/competitors.py`, `carry_forward_set`; proved by
    // `tests/test_scan_chain.py::TestCompetitorSetsCarryForward`).
    const out = text();
    expect(out).toContain('and you can overrule the result, which then stays in place for every later scan.');
    expect(out).not.toContain('re-detection');
    expect(out).not.toContain('survives every later');
  });

  it('names one AI model, not the four engines, as the second signal that finds rivals', () => {
    // `services/cocitation.py` asks one model; the four engines are what a scan measures.
    const out = text();
    expect(out).toContain('which brands an AI model names in the same breath as your prospect.');
    expect(out).not.toContain('which brands the AI engines name');
  });

  it('does not say the search signal is about the niche alone', () => {
    // Three of the six queries are about the prospect by name ("<brand> alternatives",
    // "<brand> competitors", "<brand> vs"), and three are about its field (`services/serp.py`,
    // `build_queries`). The sentence says who ranks in search, and not for what.
    const out = text();
    expect(out).toContain(
      'Two independent signals: who ranks in search, and which brands an AI model names in the same breath as your prospect.',
    );
    expect(out).not.toContain('who ranks for the niche');
    expect(out).not.toContain('for the niche');
  });

  it('says the audit loads the homepage and reads robots.txt and a sitemap, with no claim about quoting', () => {
    // `services/audit_runner.py` audits `https://{client.domain}`, and `technical_audit.py` reads
    // its robots.txt and looks for a sitemap: one page, not "the site". Structured data,
    // crawlability and freshness are the inputs to Technical Foundation (the report's own
    // sentence, `FIX_FOR_DIMENSION`), one of the five weighted dimensions. Nothing measures that
    // these signals "decide whether a page can be quoted", and nothing shows the audit would
    // otherwise be one "nobody reads".
    const out = text();
    expect(out).toContain('The homepage checked for structured data, crawlability and freshness');
    expect(out).toContain(
      'It loads the homepage, reads the site\'s robots.txt and checks for a sitemap. The result is Technical Foundation, one of the five weighted dimensions of the score.',
    );
    for (const old of [
      'The site itself checked',
      'what the engines need',
      'decide whether a page can be quoted',
      'quoted at all',
      'nobody reads',
      'a separate audit',
      'page structure',
    ]) {
      expect(out, `found "${old}"`).not.toContain(old);
    }
  });

  it('says it works out what the business sells, and not who it sells to', () => {
    // `services/classify.py` returns an industry, a niche and a brand name, and no audience.
    const out = text();
    expect(out).toContain(
      'works out for itself what the business sells — so the first thing you do is not a form about your prospect\'s industry.',
    );
    expect(out).not.toContain('and to whom');
  });

  it('says the questions are what buyers would ask, not what they actually ask', () => {
    // A model writes them (`services/prompts.py`); nothing here has seen what buyers type.
    const out = text();
    expect(out).toContain('It asks the questions your client\'s buyers would ask.');
    expect(out).toContain('a spread across the buying stages.');
    expect(out).not.toContain('actually ask');
    expect(out).not.toContain('actually shop');
  });

  it('claims no frequency for the engines disagreeing, only that they do not always agree', () => {
    // Nothing measures how often. The report has a "Where the engines disagree" section.
    const out = text();
    expect(out).toContain('They do not always agree, and where they differ is itself a finding.');
    expect(out).not.toContain('more often than you would expect');
  });

  it('says what the facts-only tests cover, and not every table, every response or every field', () => {
    // `tests/test_facts_only.py` covers a named list of the tables that hold anything taken from
    // an engine's answer or a website, and sweeps the schemas the report module defines. The
    // report does show two free-text fields, a fix's title and detail, by design: they are the
    // product's own wording, and `ActionItem` is the documented exception to the rule.
    const out = text();
    expect(out).toContain(
      'It is enforced by tests over the tables that hold anything taken from an engine\'s answer or a website, and over the report\'s own response schemas, so a field that could carry someone else\'s prose fails the build rather than reaching a client\'s report.',
    );
    expect(out).not.toContain('every table');
    expect(out).not.toContain('every response shape');
    expect(out).not.toContain('every field');
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
    // Four vendors since Epic 21 (the copy said "one vendor" until 2026-09-12,
    // stale by then); React escapes the apostrophe, so match around it.
    expect(out).toContain('measures four vendors');
    expect(out).toContain('models today');
    expect(out).toContain('only Perplexity searches the web');
    expect(out).not.toContain('one vendor');
    expect(out).toContain('Nothing tracks whether a fix was actually done');
  });

  it('says what is true of logo, colour and re-measuring, and no more — launch checklist 3.86', () => {
    const out = html();
    // An API sets a logo and colour but no screen does; a new scan re-measures
    // but nothing does it on its own.
    expect(out).toContain('No screen sets a logo or colour yet.');
    expect(out).not.toContain('Logo and colour control are not built yet');
    expect(out).toContain(
      'nothing re-measures on its own; a new scan does. The report ends at the recommendation.',
    );
    expect(out).not.toContain('or re-measures what it changed');
  });

  it('makes no claim about how buyers behave: the hero lead is only what the product measures — launch checklist 3.86', () => {
    const out = html();
    expect(out).not.toContain('Buyers increasingly ask');
    expect(out).not.toContain('before they ask a search engine');
    expect(out).toContain(
      'A scan measures whether your client is named in those answers, who is named instead, and what to change',
    );
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
    expect(out).toContain('$150');
    expect(out).toContain('One URL in, and nothing else to fill in');
    expect(out).toContain('Signing up is free. Your first scan is free with a Google sign-in.');
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
