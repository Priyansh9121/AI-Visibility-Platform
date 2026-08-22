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
 */

import { useState, type JSX } from 'react';
import { Button, TextField } from '@avp/design-system';
import type { Competitor, CompetitorInput } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';

/** Mirrors the API's `max_length=10` on ReplaceCompetitorsRequest.competitors. */
const MAX_COMPETITORS = 10;

type Draft = { name: string; domain: string };

export interface CompetitorEditorProps {
  clientId: string;
  competitors: readonly Competitor[];
  /** Called after a successful save so the caller can refetch the report. */
  onSaved: () => void | Promise<void>;
}

function toDraft(competitor: Competitor): Draft {
  return { name: competitor.name, domain: competitor.domain ?? '' };
}

export function CompetitorEditor({
  clientId,
  competitors,
  onSaved,
}: CompetitorEditorProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>(() => competitors.map(toDraft));
  const [name, setName] = useState('');
  const [domain, setDomain] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function start() {
    // Re-seed from the current set each time, so an abandoned edit does not
    // resurface stale drafts over a set that has since been re-detected.
    setDrafts(competitors.map(toDraft));
    setName('');
    setDomain('');
    setError(null);
    setOpen(true);
  }

  function add() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (drafts.length >= MAX_COMPETITORS) {
      setError(`A set carries at most ${MAX_COMPETITORS} competitors.`);
      return;
    }
    if (drafts.some((d) => d.name.toLowerCase() === trimmed.toLowerCase())) {
      // The API enforces this too (uq_competitors_set_name), but a 422 after a
      // round trip is a worse way to learn it.
      setError(`${trimmed} is already on the list.`);
      return;
    }
    setDrafts([...drafts, { name: trimmed, domain: domain.trim() }]);
    setName('');
    setDomain('');
    setError(null);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload: CompetitorInput[] = drafts.map((d) => ({
        name: d.name,
        domain: d.domain.trim() || null,
      }));
      await api.replaceCompetitors(clientId, payload);
      await onSaved();
      setOpen(false);
    } catch (err) {
      setError(
        err instanceof ApiProblem
          ? err.problem.detail || err.problem.title
          : 'The change could not be saved.',
      );
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-4">
        <Button variant="secondary" size="sm" onClick={start}>
          Correct this list
        </Button>
        <p className="mt-2 text-ui-sm leading-prose text-text-tertiary">
          Detection is automated and imperfect. A rival you add or keep by hand stays in place
          when the scan is run again.
        </p>
      </div>
    );
  }

  const unchanged =
    drafts.length === competitors.length &&
    drafts.every((d, i) => {
      const original = competitors[i];
      return (
        original !== undefined &&
        d.name === original.name &&
        d.domain === (original.domain ?? '')
      );
    });

  return (
    <div className="mt-4 flex flex-col gap-4 border-t border-line-hairline pt-4">
      <div>
        <h4 className="text-ui-sm font-medium text-text-primary">Correct the competitor list</h4>
        <p className="mt-1 text-ui-sm leading-prose text-text-tertiary">
          Saving replaces the whole list and marks every entry on it as set by hand, including
          ones you did not change. The corroboration figure is cleared with it, because it
          measures agreement between two automated signals and neither produced this list.
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
              onClick={() => setDrafts(drafts.filter((_, i) => i !== index))}
            >
              Remove
            </Button>
          </li>
        ))}
        {drafts.length === 0 && (
          <li className="text-ui-sm leading-prose text-text-tertiary">
            No competitors. Saving an empty list clears the set, and Share of Voice becomes
            unmeasurable until the scan is run again.
          </li>
        )}
      </ol>

      <div className="flex flex-wrap items-end gap-3">
        <TextField
          label="Brand"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
        />
        <TextField
          label="Domain (optional)"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          maxLength={253}
        />
        <Button variant="secondary" size="md" onClick={add} disabled={!name.trim()}>
          Add
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-ui-sm leading-prose text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap gap-3">
        <Button variant="primary" size="md" onClick={save} disabled={saving || unchanged}>
          {saving ? 'Saving…' : 'Save the list'}
        </Button>
        <Button variant="ghost" size="md" onClick={() => setOpen(false)} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
