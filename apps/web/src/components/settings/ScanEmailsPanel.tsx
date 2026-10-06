'use client';

/**
 * The scan-finished email switch in Settings — F1.
 *
 * Two buttons in a labelled group, On and Off, with `aria-pressed`: the control `ThemeControl`
 * uses for a choice, from the design system, so nothing new is built. A press saves at once, to
 * the API, for the signed-in user only (`api.setScanEmails`), and the pressed button is whatever
 * the API answered, so the screen never says a choice is saved before it is.
 *
 * The status region is on the page from the first render, empty, and "Saved." is added to it
 * (checklist 3.85): a screen reader announces what a live region gains.
 */

import { useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api } from '@/lib/api';

type Phase = 'idle' | 'saving' | 'saved' | 'failed';

export function ScanEmailsPanel({ enabled }: { enabled: boolean }): JSX.Element {
  const [on, setOn] = useState(enabled);
  const [phase, setPhase] = useState<Phase>('idle');

  async function choose(next: boolean) {
    // Presses are ignored while saving; the buttons stay enabled so keyboard focus is not lost.
    if (phase === 'saving') return;
    if (next === on && phase !== 'failed') return;
    setPhase('saving');
    try {
      const answer = await api.setScanEmails(next);
      setOn(answer.enabled);
      setPhase('saved');
    } catch {
      setPhase('failed');
    }
  }

  const busy = phase === 'saving';
  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="Scan-finished emails" className="flex flex-wrap gap-2">
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
      <p className="max-w-measure text-ui-sm text-text-secondary">
        {on
          ? 'On: you will get these emails for scans you start.'
          : 'Off: you will not get these emails.'}
      </p>
      <div role="status" data-testid="scan-emails-status">
        {phase === 'saved' && <p className="text-ui-sm text-text-secondary">Saved.</p>}
      </div>
      {phase === 'failed' && (
        <p role="alert" className="text-ui-sm leading-prose text-danger">
          The change did not go through. Check your connection and try again.
        </p>
      )}
    </div>
  );
}
