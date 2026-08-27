'use client';

import { useState, type FormEvent } from 'react';
import { ArrowRight, Globe } from 'lucide-react';
import { Button, TextField } from '@avp/design-system';
import type { ClientDetail } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';

/**
 * URL intake — §5.4 step 1, and the first customer-facing screen.
 *
 * Every visual element comes from `@avp/design-system` (ip-safety.md #2). The
 * only local styling is layout utilities from the Tailwind preset, which is
 * itself generated from the design tokens — Tailwind's stock palette is not
 * available in this app, so an off-system colour cannot compile.
 *
 * Validation is deliberately permissive client-side. The server owns the real
 * rule (services/intake.py:parse_domain, which resolves against the Public
 * Suffix List); duplicating it in the browser would produce two definitions of
 * "valid" that drift. The browser only catches "you typed nothing".
 */
export function IntakeForm({
  onClassified,
  onStarted,
  onFailed,
}: {
  onClassified: (client: ClientDetail) => void;
  onStarted: () => void;
  /**
   * The request failed and the form is showing why — Epic 9.11.
   *
   * Without this the screen showed BOTH states at once: `onStarted` moved the
   * page into `working`, nothing ever moved it back, so a rejected submission
   * rendered the field error and left "Reading the site" sitting underneath it
   * indefinitely. Found in a browser, not by a test — the two states live in
   * different components and each was correct on its own.
   *
   * **Required, not optional.** An optional callback would let a caller drop
   * the wiring again and reintroduce the exact bug, silently. Required, the
   * compiler is the guard — which is a stronger guarantee than a test, and one
   * this repo has no DOM-driving test library to write anyway.
   */
  onFailed: () => void;
}) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Enter a website address to scan.');
      return;
    }

    setBusy(true);
    setError(null);
    onStarted();
    try {
      onClassified(await api.createClient({ url: trimmed, classify: true }));
    } catch (err) {
      if (err instanceof ApiProblem) {
        // The server's problem detail is written for a person to read, so it
        // is shown as-is rather than being replaced with a generic message.
        setError(err.problem.detail);
      } else {
        setError('Could not reach the API. Is it running on port 8000?');
      }
      // Take the page out of "working" as well as showing the error here, or
      // the progress card stays on screen next to the failure that ended it.
      onFailed();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <TextField
        label="Website address"
        size="lg"
        placeholder="northaven-dental.com"
        prefix={<Globe aria-hidden="true" width={16} height={16} />}
        autoComplete="url"
        spellCheck={false}
        autoCapitalize="none"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        error={error ?? undefined}
        hint="Paste any page on the site — we read the homepage and a few key pages."
        disabled={busy}
      />
      <div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          disabled={busy}
          iconEnd={busy ? undefined : <ArrowRight />}
        >
          {busy ? 'Reading the site…' : 'Identify this business'}
        </Button>
      </div>
    </form>
  );
}
