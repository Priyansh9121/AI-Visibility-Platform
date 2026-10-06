'use client';

/**
 * Competitor manual override — §7 Epic 3's "manual override/edit UI".
 *
 * Epic 3 shipped the mechanism and the endpoint: `Competitor.is_manual_override`,
 * `persist_detection`'s preserve-on-re-detection logic, and
 * `PUT /clients/{clientId}/competitors`. What it never shipped was any way for
 * an operator to reach them. This is that.
 *
 * Why it lives inside the report rather than on a screen of its own
 * ----------------------------------------------------------------
 * The report is where a bad competitor set becomes visible — it is the only
 * place the detected rivals are shown next to what they cost the subject. An
 * operator who spots "that is not a competitor" is looking at this table when
 * they think it, and sending them elsewhere to act on it is how corrections
 * stop being made.
 *
 * It is kept out of `ReportView` proper and passed in as a slot, for two
 * reasons. `ReportView` is rendered with `renderToStaticMarkup` in tests and is
 * intended to stay server-renderable for Epic 7.1's PDF path, which a hook
 * would break. And this is operator chrome, not part of the document: a report
 * rendered for a client carries no editor because no slot is passed.
 *
 * Wholesale, not per-row
 * ----------------------
 * The endpoint replaces the set rather than patching rows, and marks
 * EVERYTHING supplied as a manual override. That is deliberate upstream —
 * reconciling a correction against auto-detected rows one at a time invites a
 * half-applied state — but it means removing one rival also converts the
 * survivors into overrides. The UI says so plainly rather than letting an
 * operator discover it from a badge appearing on rows they never touched.
 *
 * A save is for later scans
 * -------------------------
 * `PUT` writes the client's NEXT LIST, which the next scan that starts adopts; a scan that has
 * started keeps the list it started with (launch checklist 3.78, 3.79). So nothing on the report
 * on screen changes when a save succeeds, and the editor says so in its status line (focus moves
 * there) rather than the page reloading a report that cannot have changed. What it replaces is the
 * client's CURRENT list, which on an older scan is not the list on screen, so it is read when the
 * editor opens.
 */

import { useEffect, useId, useRef, useState, type JSX } from 'react';
import { Button, TextField } from '@avp/design-system';
import type { Competitor, CompetitorInput } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { COMPETITOR_EDITOR_COPY as COPY } from '@/lib/report/strings';

/** Mirrors the API's `max_length=10` on ReplaceCompetitorsRequest.competitors. */
const MAX_COMPETITORS = 10;

type Draft = { name: string; domain: string };

export interface CompetitorEditorProps {
  clientId: string;
  /** The list of the scan on screen. Not what a save replaces: see `current` below. */
  competitors: readonly Competitor[];
  /**
   * The scan has no competitor set at all (the report's `competitorSet` is null), as opposed to a
   * set with no rivals. Only the words differ: the same `PUT` names rivals in both cases.
   */
  setMissing?: boolean;
  /**
   * The scan on screen has finished, so a saved list is for later scans and does not change it.
   * False for a scan still queued or running, which a save can still reach: a queued
   * scan takes the saved list when it runs, and the API refuses a save while one is running.
   */
  scanFinished?: boolean;
  /**
   * Called after a successful save, for a caller that wants to do something then. The report page
   * passes none: a save is for later scans, so the report on screen is what it was. A caller whose
   * reaction removes the editor is safe: nothing is set on it once it has gone.
   */
  onSaved?: () => void | Promise<void>;
}

/**
 * The line for a 422 that names a row's domain (`competitors.<i>.domain`): "<that row's name>:
 * <the API's sentence>". The operator cannot otherwise tell which row failed. The first such error
 * is enough. Empty when the problem names no row's domain.
 */
/** The server's sentence for a domain that is not just a domain (`NOT_THE_RIVALS_OWN_DOMAIN`). */
const DOMAIN_SHAPE_SENTENCE =
  'Enter just the domain, like rival.com: no https://, page path or subdomain.';

/**
 * Shape only: a character no bare domain has. The public-suffix check stays the server's, which is
 * the authority; this only spares the person a round trip for a pasted page.
 */
const NOT_A_BARE_DOMAIN = /[/:@?#\s]/;

function domainProblem(err: ApiProblem, drafts: readonly Draft[]): string {
  for (const [field, message] of Object.entries(err.fieldErrors())) {
    const match = /^competitors\.(\d+)\.domain$/.exec(field);
    const row = match ? drafts[Number(match[1])] : undefined;
    if (row) return `${row.name}: ${message}`;
  }
  return '';
}

function toDraft(competitor: Competitor): Draft {
  return { name: competitor.name, domain: competitor.domain ?? '' };
}

const sameList = (a: readonly Draft[], b: readonly Draft[]) =>
  a.length === b.length && a.every((d, i) => d.name === b[i]!.name && d.domain === b[i]!.domain);

export function CompetitorEditor({
  clientId,
  competitors,
  setMissing = false,
  scanFinished = true,
  onSaved,
}: CompetitorEditorProps): JSX.Element {
  // What a save replaces is the client's CURRENT list (its next list, else its latest scan's set),
  // which on an older scan is not the list on screen. It is read when the editor opens, and the
  // drafts and the "unchanged" test start from it.
  const [current, setCurrent] = useState<Draft[]>([]);
  const [open, setOpen] = useState(false);
  const [reading, setReading] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [name, setName] = useState('');
  const [domain, setDomain] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();

  const mounted = useRef(true);
  const opener = useRef<HTMLDivElement | null>(null);
  const addRow = useRef<HTMLDivElement | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const savedNote = useRef<HTMLParagraphElement | null>(null);
  const restoreOpener = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Focus moves with the editor: into it when it opens, back to the button that opened it when it
  // closes without a save, and to the status line that says what a save did when it closes with one.
  useEffect(() => {
    if (open) heading.current?.focus();
    else if (restoreOpener.current) {
      restoreOpener.current = false;
      opener.current?.querySelector('button')?.focus();
    } else if (saved) savedNote.current?.focus();
  }, [open, saved]);

  // No rivals on the list on screen, or no list: the editor is then how rivals get named.
  const naming = competitors.length === 0;
  // Once open, the words follow the list the save replaces.
  const namingCurrent = current.length === 0;
  const differs = !sameList(competitors.map(toDraft), current);

  async function start() {
    // Not `disabled` while it reads: a focused button that disables can lose its focus.
    if (reading) return;
    setError(null);
    setSaved(false);
    setReading(true);
    let seeded: Draft[];
    try {
      const found = await api.competitorSet(clientId);
      seeded = (found?.competitors ?? []).map(toDraft);
    } catch {
      if (mounted.current) {
        setError('The client’s current list could not be read. Try again.');
        setReading(false);
        opener.current?.querySelector('button')?.focus();
      }
      return;
    }
    if (!mounted.current) return;
    // Re-seeded each time, so an abandoned edit does not resurface stale drafts.
    setCurrent(seeded);
    setDrafts(seeded);
    setName('');
    setDomain('');
    setReading(false);
    setOpen(true);
  }

  function close() {
    // An error from adding or saving belongs to the open editor: left set, the closed editor
    // would announce it again as a fresh alert.
    setError(null);
    restoreOpener.current = true;
    setOpen(false);
  }

  // The Brand field is refocused after an Add (the Add button disables itself when the field
  // empties, which would strand focus) and marked invalid only for an error add() raised.
  const [addFailed, setAddFailed] = useState(false);
  // An error add() raised on the Domain field: it is the one marked invalid, and focused.
  const [domainFailed, setDomainFailed] = useState(false);

  function add() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (NOT_A_BARE_DOMAIN.test(domain.trim())) {
      setError(DOMAIN_SHAPE_SENTENCE);
      setAddFailed(false);
      setDomainFailed(true);
      addRow.current?.querySelectorAll('input')[1]?.focus(); // the Domain field, second in the row
      return;
    }
    if (drafts.length >= MAX_COMPETITORS) {
      setError(`A set carries at most ${MAX_COMPETITORS} competitors.`);
      setAddFailed(true);
      setDomainFailed(false);
      return;
    }
    if (drafts.some((d) => d.name.toLowerCase() === trimmed.toLowerCase())) {
      // The API enforces this too (uq_competitors_set_name), but a 422 after a
      // round trip is a worse way to learn it.
      setError(`${trimmed} is already on the list.`);
      setAddFailed(true);
      setDomainFailed(false);
      return;
    }
    setDrafts([...drafts, { name: trimmed, domain: domain.trim() }]);
    setName('');
    setDomain('');
    setError(null);
    setAddFailed(false);
    setDomainFailed(false);
    addRow.current?.querySelector('input')?.focus(); // the Brand field, first in the row
  }

  async function save() {
    setSaving(true);
    setError(null);
    setAddFailed(false);
    setDomainFailed(false);
    try {
      const payload: CompetitorInput[] = drafts.map((d) => ({
        name: d.name,
        domain: d.domain.trim() || null,
      }));
      await api.replaceCompetitors(clientId, payload);
      await onSaved?.();
      // A caller's own reaction took the editor away: nothing below touches state once it is gone.
      if (!mounted.current) return;
      // The editor is still here: it says what the save did, and focus moves to that line. The
      // report above is not touched, because the saved list is for later scans.
      setSaved(true);
      setOpen(false);
    } catch (err) {
      if (mounted.current) {
        setError(
          err instanceof ApiProblem
            ? domainProblem(err, drafts) || err.problem.detail || err.problem.title
            : 'The change could not be saved.',
        );
      }
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  if (!open) {
    const lead = setMissing ? COPY.missingLead : COPY.emptyLead;
    // Under the status line a save just printed, the "does not change" sentence is not said twice.
    const tail = scanFinished
      ? saved
        ? COPY.namingTailSaved
        : COPY.namingTail
      : COPY.namingTailRunning;
    const correctTail = scanFinished
      ? saved
        ? COPY.correctTailSaved
        : COPY.correctTail
      : COPY.correctTailRunning;
    return (
      <div className="mt-4" ref={opener}>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void start()}
          aria-disabled={reading ? true : undefined}
        >
          {naming ? COPY.openButton : 'Correct this list'}
        </Button>
        {reading && (
          <p role="status" className="mt-2 text-ui-sm leading-prose text-text-tertiary">
            {COPY.reading}
          </p>
        )}
        {error && (
          <p role="alert" className="mt-2 text-ui-sm leading-prose text-danger">
            {error}
          </p>
        )}
        {saved && (
          <p
            ref={savedNote}
            role="status"
            tabIndex={-1}
            className="mt-2 text-ui-sm leading-prose text-text-secondary"
          >
            {scanFinished ? COPY.saved : COPY.savedRunning}
          </p>
        )}
        <p className="mt-2 text-ui-sm leading-prose text-text-tertiary">
          {naming ? `${lead} ${tail}` : `${COPY.correctLead} ${correctTail}`}
        </p>
      </div>
    );
  }

  const unchanged = sameList(drafts, current);

  return (
    <div className="mt-4 flex flex-col gap-4 border-t border-line-hairline pt-4">
      <div>
        <h4
          ref={heading}
          tabIndex={-1}
          className="text-ui-sm font-medium text-text-primary"
        >
          {namingCurrent ? COPY.openHeading : 'Correct the competitor list'}
        </h4>
        {differs && (
          <p className="mt-1 text-ui-sm leading-prose text-text-secondary">{COPY.differsFromScan}</p>
        )}
        <p className="mt-1 text-ui-sm leading-prose text-text-tertiary">
          {COPY.openNote} {scanFinished ? COPY.openNoteFinished : COPY.openNoteRunning}
        </p>
      </div>

      <ol className="flex flex-col gap-2">
        {drafts.map((draft, index) => (
          <li
            key={`${draft.name}-${index}`}
            className="flex items-baseline justify-between gap-4 text-ui-sm text-text-primary"
          >
            <span>
              {draft.name}
              {draft.domain && (
                <span className="ml-2 text-text-tertiary">{draft.domain}</span>
              )}
            </span>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Remove ${draft.name}`}
              onClick={() => {
                setDrafts(drafts.filter((_, i) => i !== index));
                // An error from before is about a list that has changed.
                setError(null);
                setAddFailed(false);
                setDomainFailed(false);
              }}
            >
              Remove
            </Button>
          </li>
        ))}
        {drafts.length === 0 && (
          <li className="text-ui-sm leading-prose text-text-tertiary">
            {namingCurrent ? COPY.noneYet : COPY.emptiedStaysEmpty}
          </li>
        )}
      </ol>

      <div ref={addRow} className="flex flex-wrap items-end gap-3">
        <TextField
          label="Brand"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
          aria-invalid={error && addFailed ? true : undefined}
          aria-describedby={error && addFailed ? errorId : undefined}
        />
        <TextField
          label="Domain (optional)"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          maxLength={253}
          aria-invalid={error && domainFailed ? true : undefined}
          aria-describedby={error && domainFailed ? errorId : undefined}
        />
        <Button variant="secondary" size="md" onClick={add} disabled={!name.trim()}>
          Add
        </Button>
      </div>

      {error && (
        <p id={errorId} role="alert" className="text-ui-sm leading-prose text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap gap-3">
        <Button variant="primary" size="md" onClick={() => void save()} disabled={saving || unchanged}>
          {saving ? 'Saving…' : 'Save the list'}
        </Button>
        <Button variant="ghost" size="md" onClick={close} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
