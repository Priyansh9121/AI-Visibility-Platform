'use client';

import { useState, type FormEvent } from 'react';
import { ArrowRight, Globe } from 'lucide-react';
import { Button, ErrorState, TextField } from '@avp/design-system';
import type { ClientDetail } from '@avp/shared-types';
import { api } from '@/lib/api';
import { apiAnsweredWithWords, API_AWAY_SENTENCE } from '@/lib/apiAway';

/**
 * URL intake — §5.4 step 1, and the first customer-facing screen.
 *
 * Every visual element comes from `@avp/design-system` (the design-system-only rule). The
 * only local styling is layout utilities from the Tailwind preset, which is
 * itself generated from the design tokens — Tailwind's stock palette is not
 * available in this app, so an off-system colour cannot compile.
 *
 * Validation is deliberately permissive client-side. The server owns the real
 * rule (services/intake.py:parse_domain, which resolves against the Public
 * Suffix List); duplicating it in the browser would produce two definitions of
 * "valid" that drift. The browser only catches "you typed nothing".
 *
 * WHERE AN ERROR IS SHOWN — launch checklist 3.39
 * ------------------------------------------------
 * The address field shows only what is wrong with the address: nothing typed, a
 * 409 (`invalid-url`, or the address is already tracked) and a 422 (the `url`
 * field). Everything else the server or the network says is not about the
 * address, so it is shown at the top of the form, above the field, as an
 * `ErrorState`, as `SignInPanel` shows what is not a wrong credential. That is a
 * 429 (`Your agency already has a site being read`, `The crawler is busy`: nothing
 * was read and nothing was created), a 401 or a 403 (the session or the origin),
 * any other 4xx, and the sentence for an API that did not answer. Until now every
 * one of them was drawn on the field: red, `aria-invalid`, for an address that
 * was fine.
 */
const ADDRESS_PROBLEM_STATUSES: ReadonlySet<number> = new Set([409, 422]);

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
  // About the address: shown on the field.
  const [error, setError] = useState<string | null>(null);
  // Not about the address: shown above the form.
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Enter a website address to scan.');
      // What an earlier attempt said above the form (a 429, the connection) is not about this
      // one: nothing was sent. Left up, it sat beside a field error as a second, stale one.
      setFormError(null);
      return;
    }

    setBusy(true);
    setError(null);
    setFormError(null);
    onStarted();
    try {
      onClassified(await api.createClient({ url: trimmed, classify: true }));
    } catch (err) {
      if (apiAnsweredWithWords(err)) {
        // The server's problem detail is written for a person to read, so it
        // is shown as-is rather than being replaced with a generic message. On
        // the field only if it is about the address; anywhere else, above the form.
        if (ADDRESS_PROBLEM_STATUSES.has(err.status)) setError(err.problem.detail);
        else setFormError(err.problem.detail);
      } else {
        // No answer, or a 5xx: nothing the API said is about this address.
        setFormError(API_AWAY_SENTENCE);
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
      {/* What is not about the address: first in the form, above the field it is not about. */}
      {formError != null && (
        <ErrorState title="Could not start reading the site" detail={formError} />
      )}
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
