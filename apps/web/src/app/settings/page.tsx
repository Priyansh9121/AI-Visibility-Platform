'use client';

/**
 * /settings — Epic 9.13, with two of its four "not built yet" lines removed in
 * Epic 9.14.
 *
 * The screen was built so the sidebar item would not be a lie: a destination
 * that says plainly what is missing and why, rather than a nav item whose click
 * does nothing or a form of switches wired to nothing. That is still the rule,
 * and it now cuts the other way — seat management shipped, so its line comes
 * off and the real thing takes its place.
 *
 * This file is now only the fetching shell. The screen itself is
 * `components/settings/SettingsView`, pure and prop-driven, which is what makes
 * it testable — the split `DashboardView` / `dashboard/page.tsx` established in
 * Epic 9.3. It is also why this screen shipped in 9.13 with zero tests: every
 * state that mattered lived inside the effect below, which a static render
 * never runs.
 */

import { useCallback, useEffect, useState, type JSX } from 'react';
import type { BillingStatus, SeatList } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import {
  SettingsView,
  type SettingsState,
} from '@/components/settings/SettingsView';

export default function SettingsRoute(): JSX.Element {
  const [state, setState] = useState<SettingsState>({ kind: 'loading' });

  const load = useCallback(async () => {
    const me = await api.me();
    // The roster is a SEPARATE failure from identity, and is allowed to fail
    // on its own: a member gets a 403 here by design, and losing the whole
    // settings screen over a permission they were never meant to have would
    // be the wrong trade. Identity failing is what makes this page unusable.
    let seats: SeatList | null = null;
    let seatsError: string | null = null;
    try {
      seats = await api.seats(me.agency.id);
    } catch (err) {
      seatsError =
        err instanceof ApiProblem && err.status === 403
          ? 'Only an owner or an admin can see and change who holds a seat.'
          : 'The seat list could not be loaded.';
    }

    // Billing is a THIRD independent failure, for the same reason the roster is
    // a second one: a member gets a 403 here by design, and one section they
    // were never meant to see must not take the page down with it. Epic 9.15.
    let billing: BillingStatus | null = null;
    let billingError: string | null = null;
    try {
      billing = await api.billing(me.agency.id);
    } catch (err) {
      billingError =
        err instanceof ApiProblem && err.status === 403
          ? 'Only an owner or an admin can see and change billing.'
          : 'Your billing status could not be loaded. Nothing has changed either way.';
    }

    setState({ kind: 'ready', me, seats, seatsError, billing, billingError });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: 'error',
          title:
            err instanceof ApiProblem && err.status === 401
              ? 'Sign in to see your settings'
              : 'Your settings could not be loaded',
          detail:
            err instanceof ApiProblem
              ? err.problem.detail
              : 'The request did not complete.',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  return <SettingsView state={state} onChanged={load} />;
}
