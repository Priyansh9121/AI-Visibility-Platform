'use client';

/**
 * "Rescan every month" on a client's overview — F2.
 *
 * Fetches its own state and draws NOTHING until the API says the switch is available (automatic
 * rescans on, and the caller an owner or admin), and nothing if that read fails: with the
 * feature off the screen is as it was. Two buttons in a labelled group, On and Off, with
 * `aria-pressed` (the control `ScanEmailsPanel` uses): a press saves at once and the pressed
 * button is whatever the API answered. A skipped month is shown in plain words under the switch.
 *
 * The status region is on the page from the first render, empty, and "Saved." is added to it.
 */

import { useEffect, useState, type JSX } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle } from '@avp/design-system';
import type { AutoRescan } from '@avp/shared-types';
import { api } from '@/lib/api';
import { PLAN_SCANS_PER_MONTH } from '@/components/marketing/PricingCard';
import { skipSentence } from '@/lib/client/autoRescan';

type Phase = 'idle' | 'saving' | 'saved' | 'failed';

export function AutoRescanPanel({ clientId }: { clientId: string }): JSX.Element | null {
  const [state, setState] = useState<AutoRescan | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');

  useEffect(() => {
    let cancelled = false;
    api
      .autoRescan(clientId)
      .then((answer) => {
        if (!cancelled) setState(answer);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (state === null || !state.available) return null;

  async function choose(next: boolean) {
    // Presses are ignored while saving; the buttons stay enabled so keyboard focus is not lost.
    if (phase === 'saving') return;
    if (next === state?.enabled && phase !== 'failed') return;
    setPhase('saving');
    try {
      setState(await api.setAutoRescan(clientId, next));
      setPhase('saved');
    } catch {
      setPhase('failed');
    }
  }

  const on = state.enabled;
  const busy = phase === 'saving';
  return (
    <Card elevation="seated">
      <CardHeader>
        <CardTitle>Rescan every month</CardTitle>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        <div role="group" aria-label="Rescan every month" className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={on ? 'primary' : 'secondary'}
            aria-pressed={on}
            aria-disabled={busy}
            onClick={() => void choose(true)}
          >
            On
          </Button>
          <Button
            size="sm"
            variant={on ? 'secondary' : 'primary'}
            aria-pressed={!on}
            aria-disabled={busy}
            onClick={() => void choose(false)}
          >
            Off
          </Button>
        </div>
        <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
          {on
            ? `On: when this client's latest scan is 30 or more days old (checked about every 10 minutes, so switching on now may start a scan straight away), a scan starts by itself as the person who turned this on. It is a normal scan: it counts toward the agency's ${PLAN_SCANS_PER_MONTH} scans for the calendar month (UTC).`
            : 'Off: this client is only scanned when someone starts a scan.'}
        </p>
        {on && state.skipReason && (
          <p data-testid="auto-rescan-skip" className="max-w-measure text-ui-sm leading-prose text-text-secondary">
            {skipSentence(state.skipReason, state.retriesAt)}
          </p>
        )}
        <div role="status" data-testid="auto-rescan-status">
          {phase === 'saved' && <p className="text-ui-sm text-text-secondary">Saved.</p>}
        </div>
        {phase === 'failed' && (
          <p role="alert" className="text-ui-sm leading-prose text-danger">
            The change did not go through. Check your connection and try again.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
