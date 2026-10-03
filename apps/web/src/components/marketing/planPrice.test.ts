/**
 * The price the pages state is the price the API checks Stripe against — 2026-10-04.
 *
 * The web states the plan's price in `PLAN_PRICE_USD` (`PricingCard.tsx`) and builds every
 * sentence that quotes it from that constant. The API states it a second time in
 * `services/billing.py`, because at start, and every hour after, it reads the configured Stripe
 * Price and warns (`billing.price_mismatch`) when what Stripe would charge is not what the pages
 * say. Two languages, so two copies. This reads the API's source and fails when either moves
 * alone, which is how a page comes to quote a price Stripe does not charge.
 *
 * The same device `legal/legalPages.test.tsx` uses for the scan counts and the retention
 * periods. The API's half is `apps/api/tests/test_price_check.py`.
 *
 * The founder decided US$150 a month on 2026-10-04 (it was $160 from 2026-09-22). The figure is
 * written out below as well as compared, so a repricing has to change this file on purpose.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { PLAN_PRICE_USD, PricingCard } from './PricingCard';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
const BILLING = 'apps/api/src/avp_api/services/billing.py';
const API_SOURCE = readFileSync(join(ROOT, BILLING), 'utf8');

/** What a module-level constant is assigned in the API's billing source, found exactly once. */
function apiConstant(name: string): string {
  const found = [...API_SOURCE.matchAll(new RegExp(`^${name} = (\\S+)[ \\t]*(?:#.*)?$`, 'gm'))];
  expect(found, `${name} is assigned exactly once in ${BILLING}`).toHaveLength(1);
  return found[0]![1]!;
}

describe('the web’s price is the one the API checks Stripe against', () => {
  it('PLAN_PRICE_USD is the same number on both sides', () => {
    expect(Number(apiConstant('PLAN_PRICE_USD'))).toBe(PLAN_PRICE_USD);
  });

  it('and it is 150, the founder’s decision of 2026-10-04', () => {
    expect(PLAN_PRICE_USD).toBe(150);
    expect(Number(apiConstant('PLAN_PRICE_USD'))).toBe(150);
  });

  it('the card says what the API compares the Price to: US dollars, per month', () => {
    // The card quotes a currency and a period as well as an amount, and the API's check reads
    // all three from the Price. If either side changes the period or the currency alone, a
    // page would promise one thing and Stripe bill another.
    const card = renderToStaticMarkup(createElement(PricingCard));
    expect(apiConstant('PLAN_INTERVAL')).toBe('"month"');
    expect(card).toContain('per month');
    expect(apiConstant('PLAN_CURRENCY')).toBe('"usd"');
    expect(card).toContain('US dollars');
  });
});
