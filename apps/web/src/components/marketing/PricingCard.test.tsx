/**
 * The pricing card — Epic 9.15.
 *
 * Static-render assertions, the pattern LandingView.test.tsx and
 * SettingsView.test.tsx already use. There is no DOM in this suite, so what is
 * asserted is which control is rendered in which state, not what clicking it
 * does; the click destinations are covered by the live browser pass.
 *
 * Both states are tested because both are live: the landing page renders the
 * signed-out card, and Settings renders the signed-in one wherever the API will
 * start a checkout (no subscription, canceled, incomplete_expired). Neither
 * branch exists only for this file.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { PricingCard, PLAN_PRICE_USD, PLAN_SEATS } from './PricingCard';

const render = (props: Parameters<typeof PricingCard>[0] = {}) =>
  renderToStaticMarkup(<PricingCard {...props} />);

describe('the price itself', () => {
  it('is $150 a month, stated as a number and a period', () => {
    const out = render();
    expect(out).toContain('$150');
    expect(out).toContain('per month');
    // The founder repriced it on 2026-10-04; the old figure is nowhere on the card.
    expect(out).not.toContain('$160');
  });

  it('exports the number rather than burying it in copy', () => {
    // Two places on the landing page quote this. A card that hard-coded the
    // string would let the hero and the card disagree, which is the one thing
    // a published price may not do.
    expect(PLAN_PRICE_USD).toBe(150);
    expect(render()).toContain(`$${PLAN_PRICE_USD}`);
  });

  it('names the seat count and ties it to the seat limit that already exists', () => {
    const out = render();
    expect(PLAN_SEATS).toBe(3);
    expect(out).toContain('3 seats per agency.');
    expect(out).not.toContain('the same seat limit every agency already gets');
  });

  it('promises one plan and no tier above it', () => {
    const out = render();
    expect(out).toContain('One plan');
    expect(out).toContain('there is no tier above this one');
  });
});

describe('signed out', () => {
  it('offers sign-up, because you cannot subscribe an agency that does not exist', () => {
    const out = render({ signedIn: false });
    expect(out).toContain('Get started');
    expect(out).not.toContain('>Subscribe<');
    expect(out).not.toContain('Opening checkout');
  });
});

describe('signed in', () => {
  it('offers checkout instead of sign-up', () => {
    const out = render({ signedIn: true });
    expect(out).toContain('Subscribe');
    expect(out).not.toContain('>Get started<');
  });

  it('says the button is working rather than going quiet', () => {
    const out = render({ signedIn: true, busy: true });
    expect(out).toContain('Opening checkout…');
    expect(out).toContain('disabled');
  });

  it('is not disabled when idle', () => {
    expect(render({ signedIn: true })).not.toContain('disabled');
  });

  it('can be disabled without saying it is busy — 2026-10-01', () => {
    // Settings sets this while the billing portal is opening. The button must
    // not look pressable and do nothing, and checkout is not what is opening,
    // so it must not say "Opening checkout…" either.
    const out = render({ signedIn: true, disabled: true });
    expect(out).toContain('disabled');
    expect(out).toContain('Subscribe</span></button>');
    expect(out).not.toContain('Opening checkout');
  });

  it('busy is the card’s own call: it says so, and disables, with or without `disabled`', () => {
    for (const props of [{ busy: true }, { busy: true, disabled: true }]) {
      const out = render({ signedIn: true, ...props });
      expect(out).toContain('Opening checkout…');
      expect(out).toContain('disabled');
    }
  });

  it('renders a checkout failure as a sentence, in an alert', () => {
    const out = render({
      signedIn: true,
      error: 'Billing is not configured on this server.',
    });
    expect(out).toContain('Billing is not configured on this server.');
    expect(out).toContain('role="alert"');
  });

  it('shows no error region when there is no error', () => {
    expect(render({ signedIn: true })).not.toContain('role="alert"');
  });
});

describe('the footnote is sign-up copy — 2026-10-01', () => {
  // "Signing up is free. Your first scan is free with a Google sign-in.
  // Subscribe to keep scanning." answers somebody deciding whether to sign up.
  // Signed in, the person has signed up, and may have scanned or subscribed
  // before; in Settings it was wrong for them. So the signed-in card has none,
  // and no other copy in its place.
  const FOOTNOTE =
    'Signing up is free. Your first scan is free with a Google sign-in. Subscribe to keep scanning.';

  it('is on the public card, word for word', () => {
    expect(render({ signedIn: false })).toContain(FOOTNOTE);
    // The default is the public card.
    expect(render()).toContain(FOOTNOTE);
  });

  it('is not on the signed-in card, in any of its states', () => {
    for (const props of [
      { signedIn: true },
      { signedIn: true, busy: true },
      { signedIn: true, error: 'x' },
    ]) {
      const out = render(props);
      expect(out).not.toContain('Signing up is free');
      expect(out).not.toContain('Your first scan is free');
      expect(out).not.toContain('Subscribe to keep scanning');
    }
  });

  it('leaves the signed-in action row to the button: nothing follows it', () => {
    const out = render({ signedIn: true });
    const afterButton = out.slice(out.indexOf('Subscribe</span></button>'));
    expect(out).toContain('Subscribe</span></button>');
    expect(afterButton).not.toContain('<p');
  });
});

describe('what the card is not allowed to say', () => {
  it('states the cap and what is never limited — 2026-09-28', () => {
    const out = render();
    // The plan gates STARTING a scan: forty a month, one free for life. It
    // never gates reading. The card says both, and claims nothing unlimited.
    expect(out).toContain('40 scans a month');
    expect(out).toContain('Viewing, sharing and exporting reports is never limited');
    expect(out.toLowerCase()).not.toContain('unlimited');
    expect(out).not.toContain('not metered');
  });

  it('says what is free and what the plan is for', () => {
    const out = render();
    // The free scan needs a Google sign-in since 2026-09-29.
    expect(out).toContain('Signing up is free. Your first scan is free with a Google sign-in.');
    expect(out).not.toContain('includes one full scan and report');
    expect(out).toContain('Subscribe to keep scanning');
    expect(out).not.toContain('Nothing in the product is behind');
    expect(out.toLowerCase()).not.toContain('free trial');
    expect(out.toLowerCase()).not.toContain('upgrade to unlock');
  });

  it('carries no fabricated social proof, the same as the page around it', () => {
    const out = render().toLowerCase();
    for (const tell of ['most popular', 'best value', 'recommended', 'trusted by', 'customers say']) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('promises no date for anything it does not do', () => {
    const out = render().toLowerCase();
    for (const tell of ['coming soon', 'shortly', 'next release', 'roadmap']) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('the free trial — 2026-10-04', () => {
  // The API says how long the trial is (`GET /billing/plan`); the card says so only for a
  // positive number of days. A static render never runs the request, so what it shows is the
  // card while the API has not answered (nothing about a trial) and, through `initialTrialDays`
  // (the seam BillingPanel's `initialError` is the precedent for), the card once it has.
  // `lib/billing/trial.test.tsx` mounts the card and drives the request itself.
  const SENTENCE =
    'The first 30 days after you subscribe are free. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';
  // The signed-out card cannot know the visitor, and a trial needs a Google sign-in on the user
  // who subscribes (2026-10-05): it says the condition.
  const SENTENCE_SIGNED_OUT =
    'The first 30 days after you subscribe are free, if you subscribe from an account signed in with Google. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';

  it('says nothing about a trial before the API has answered, signed in or out', () => {
    for (const out of [render(), render({ signedIn: false }), render({ signedIn: true })]) {
      expect(out.toLowerCase()).not.toContain('trial');
      expect(out).not.toContain('after you subscribe');
      expect(out).not.toContain('charged on day');
      expect(out).not.toContain('unless you cancel');
    }
  });

  it('says it, word for word, once the API has said 30 days', () => {
    expect(render({ initialTrialDays: 30 })).toContain(SENTENCE_SIGNED_OUT);
  });

  it('says it on the signed-in card for an agency that can still have the trial', () => {
    expect(render({ signedIn: true, initialTrialDays: 30 })).toContain(SENTENCE);
    expect(render({ signedIn: true, trialEligible: true, initialTrialDays: 30 })).toContain(
      SENTENCE,
    );
  });

  it('does not say it to an agency that cannot have it: checkout gives one trial per agency', () => {
    const out = render({ signedIn: true, trialEligible: false, initialTrialDays: 30 });
    expect(out).not.toContain('after you subscribe');
    expect(out.toLowerCase()).not.toContain('trial');
    // The plan is still offered, at its price.
    expect(out).toContain('Subscribe');
    expect(out).toContain(`$${PLAN_PRICE_USD}`);
  });

  it('takes every number in it from the days the API gave', () => {
    const out = render({ initialTrialDays: 14 });
    expect(out).toContain(
      'The first 14 days after you subscribe are free, if you subscribe from an account signed in with Google.',
    );
    expect(out).toContain('charged on day\u00a015 unless you cancel first');
    // No 30 left over from anywhere: the length is not typed on this card.
    expect(out).not.toContain('30 days');
    expect(out).not.toContain('day\u00a031');
    expect(out).not.toContain('day 31');
  });

  it('a single day is a day, not "1 days"', () => {
    const out = render({ initialTrialDays: 1 });
    expect(out).toContain(
      'The first day after you subscribe is free, if you subscribe from an account signed in with Google.',
    );
    expect(out).toContain('charged on day\u00a02 unless you cancel first');
    expect(out).not.toContain('1 days');
  });

  it('says nothing for 0, or for anything that is not a positive whole number', () => {
    for (const days of [0, -1, -30, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      const out = render({ initialTrialDays: days });
      expect(out.toLowerCase(), `${days}`).not.toContain('trial');
      expect(out, `${days}`).not.toContain('after you subscribe');
    }
  });

  it('puts the terms with the price, above the footnote and the button', () => {
    const out = render({ initialTrialDays: 30 });
    expect(out.indexOf('per month')).toBeLessThan(out.indexOf(SENTENCE_SIGNED_OUT));
    expect(out.indexOf(SENTENCE_SIGNED_OUT)).toBeLessThan(out.indexOf('Get started'));
  });

  it('tells a visitor the trial is for an account signed in with Google; the signed-in card does not', () => {
    // The public card cannot know the visitor, so it says the condition. The signed-in card is
    // shown the trial only where the API says THIS user would get it, so it has no condition.
    expect(render({ initialTrialDays: 30 })).toContain('signed in with Google');
    expect(render({ signedIn: false, initialTrialDays: 30 })).not.toContain(SENTENCE);
    const signedIn = render({ signedIn: true, trialEligible: true, initialTrialDays: 30 });
    expect(signedIn).toContain(SENTENCE);
    expect(signedIn).not.toContain('Google');
  });

  it('is exactly as true as it sounds: nothing is charged at checkout, and no card number here', () => {
    const out = render({ initialTrialDays: 30 });
    // The card is "entered", not "taken": nothing is charged until the day after the trial.
    expect(out).not.toMatch(/charged (now|today|at checkout|immediately)/i);
    expect(out).not.toMatch(/\bcard (is|will be) taken\b/i);
    expect(out.toLowerCase()).not.toContain('no card');
    expect(out.toLowerCase()).not.toContain('no credit card');
    expect(out.toLowerCase()).not.toContain('cancel anytime');
  });

  it('adds no figure with a dollar sign: the price is still PLAN_PRICE_USD alone', () => {
    for (const out of [render({ initialTrialDays: 30 }), render({ signedIn: true, initialTrialDays: 30 })]) {
      const prices = out.match(/\$\d[\d,]*/g) ?? [];
      expect(new Set(prices)).toEqual(new Set([`$${PLAN_PRICE_USD}`]));
    }
  });
});

describe('the fix list is described as the report orders it — launch checklist 3.63', () => {
  // The card said "Named fixes, ordered by what they would move and what they would cost you".
  // The report ranks its fixes by the points each would recover and draws an effort of S, M or L
  // beside each; cost ranks nothing. `claimsAgainstCode.test.tsx` reads the report back to hold
  // these words to it, and `LandingView.test.tsx` holds the landing page's own sentence to the
  // same clause.
  const SENTENCE =
    'Named fixes, ordered by the points each would recover, with the effort of each shown beside it.';
  const states = () => [
    render(),
    render({ signedIn: true }),
    render({ signedIn: true, trialEligible: false }),
    render({ initialTrialDays: 30 }),
    render({ signedIn: true, busy: true, error: 'x' }),
  ];

  it('says it as one line of what the plan includes, word for word, signed in or out', () => {
    for (const out of states()) {
      expect(out).toContain(SENTENCE);
      expect(out).toMatch(
        /<li[^>]*>Named fixes, ordered by the points each would recover, with the effort of each shown beside it\.<\/li>/,
      );
      expect(out.split('Named fixes').length - 1).toBe(1);
    }
  });

  it('no longer says they are ordered by what they would move or what they would cost you', () => {
    for (const out of states()) {
      for (const old of ['what they would move', 'what they would cost', 'would cost you']) {
        expect(out, `found "${old}"`).not.toContain(old);
      }
      expect(out).not.toMatch(/ordered by what/i);
    }
  });
});

describe('no ad hoc styling', () => {
  it('is built from the shared Card', () => {
    expect(render()).toContain('avp-card');
  });

  it('emits no arbitrary-value and no raw-palette class with the trial on', () => {
    const out = render({ signedIn: true, error: 'x', initialTrialDays: 30 });
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('emits no arbitrary-value and no raw-palette class', () => {
    const out = render({ signedIn: true, error: 'x' });
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('the price on the card is the constant and nothing else', () => {
  it('carries no figure other than PLAN_PRICE_USD, and no price in words', () => {
    for (const out of [render(), render({ signedIn: true })]) {
      const prices = out.match(/\$\d[\d,]*/g) ?? [];
      expect(new Set(prices)).toEqual(new Set([`$${PLAN_PRICE_USD}`]));
      expect(out).not.toContain('$29');
      expect(out.toLowerCase()).not.toContain('twenty-nine');
      expect(out.toLowerCase()).not.toMatch(/\b(nineteen|twenty|thirty|forty|fifty|sixty|hundred)\b[^.]*\bdollars/);
    }
  });
});
