'use client';

/**
 * "Probable rivals" on a client's Competitors page — F2 (2026-10-09), the founder's decision.
 *
 * A site with the client's own restricted ending (edu.au for rmit.edu.au) that the latest scan's
 * answers named ("Deakin University") is a probable rival, not a source to "Get onto". The report
 * skips it; this is where the agency is asked about it instead, one line each:
 * "deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?" with a button that adds it
 * through the existing `PUT /clients/{id}/competitors` (the client's current list plus this one,
 * `lib/client/rivalSuggestions.ts`) and says, in a polite status, "deakin.edu.au is now a rival. The
 * next scan measures it." A refusal (a platform or publisher, a scan running, a full list) shows its
 * own sentence in an alert and changes nothing. Any member of the agency may.
 *
 * It fetches its own state (the pattern `OtherSitesCard` uses) and draws NOTHING while it reads, when
 * the read failed and when there is nothing to suggest. A failed read is quiet on purpose: this is an
 * offer, not part of what the page was opened for, and an error here would sit on a page whose own
 * content loaded, about something the agency never asked for and cannot act on. The page is the
 * agency's alone; nothing here reaches the report, the PDF, the CSV or the shared link.
 *
 * A save here is the competitor editor's save: it REPLACES the client's whole list, marks every rival
 * on it as set by hand (the ones the agency did not touch included) and clears the corroboration
 * figure. The editor says so above its list, so the card says the same, once, above its suggestions,
 * in the editor's own sentence (`COMPETITOR_EDITOR_COPY.openNote`, imported and never copied), and
 * not at all when there is nothing to save.
 *
 * Buttons are `aria-disabled` while a save runs, not `disabled`, so a focused button keeps its focus
 * (`CompetitorEditor`'s reason). The status region is on the page from the first render, empty, and
 * focus moves to it when the row the press was on has gone. It and the alert sit ABOVE the rows:
 * from the status the next Tab is the next row's button, where below the rows it would leave the
 * card past every row still waiting.
 */

import { useEffect, useRef, useState, type JSX } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle } from '@avp/design-system';
import type { Competitor, RivalSuggestion } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { COMPETITOR_EDITOR_COPY } from '@/lib/report/strings';
import {
  alreadyARival,
  refusalBeforeSaving,
  rivalsBody,
  rowProblem,
  suggestionRest,
  RIVAL_SUGGESTIONS_COPY as COPY,
} from '@/lib/client/rivalSuggestions';

type Read =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'ready'; suggestions: RivalSuggestion[] };

export function RivalSuggestions({ clientId }: { clientId: string }): JSX.Element | null {
  const [read, setRead] = useState<Read>({ kind: 'loading' });
  const [saving, setSaving] = useState(false);
  const [said, setSaid] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const status = useRef<HTMLDivElement | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // One client's lines, status and refusals never show under another.
    setRead({ kind: 'loading' });
    setSaid('');
    setProblem(null);
    api
      .rivalSuggestions(clientId)
      .then((found) => {
        if (!cancelled) setRead({ kind: 'ready', suggestions: found.suggestions });
      })
      .catch(() => {
        if (!cancelled) setRead({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  /** The row has gone, and the press was on it: say what happened where the reader is. */
  function settle(domain: string, sentence: string) {
    setRead((now) =>
      now.kind === 'ready'
        ? { kind: 'ready', suggestions: now.suggestions.filter((s) => s.domain !== domain) }
        : now,
    );
    setSaid(sentence);
    queueMicrotask(() => status.current?.focus());
  }

  async function add(suggestion: RivalSuggestion) {
    // Presses are ignored while saving; the buttons stay enabled so focus is not lost.
    if (saving) return;
    setSaving(true);
    setProblem(null);
    setSaid('');
    try {
      // What a save replaces is the client's CURRENT list, read now: the editor reads it when it
      // opens, and an older read here could drop a rival somebody has just added.
      let current: Competitor[];
      try {
        current = (await api.competitorSet(clientId))?.competitors ?? [];
      } catch {
        if (mounted.current) setProblem(COPY.listUnreadable);
        return;
      }
      if (!mounted.current) return;
      if (alreadyARival(current, suggestion)) {
        settle(suggestion.domain, COPY.alreadyThere(suggestion.domain));
        return;
      }
      const refused = refusalBeforeSaving(current, suggestion);
      if (refused) {
        setProblem(refused);
        return;
      }
      const body = rivalsBody(current, suggestion);
      try {
        await api.replaceCompetitors(clientId, body);
      } catch (err) {
        if (!mounted.current) return;
        setProblem(
          err instanceof ApiProblem
            ? rowProblem(err.fieldErrors(), body) ||
              err.problem.detail ||
              err.problem.title ||
              COPY.saveFailed
            : COPY.saveFailed,
        );
        return;
      }
      if (!mounted.current) return;
      settle(suggestion.domain, COPY.added(suggestion.domain));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  if (read.kind !== 'ready') return null;
  if (read.suggestions.length === 0 && !said) return null;

  return (
    <Card elevation="seated">
      <CardHeader>
        {/* The page's h1 names the screen and this is its own section: the card's title is an h3
            element, read as a second-level heading. */}
        <CardTitle role="heading" aria-level={2}>
          {COPY.title}
        </CardTitle>
      </CardHeader>
      <CardBody className="flex flex-col gap-4">
        <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">{COPY.intro}</p>
        {read.suggestions.length > 0 && (
          // The save is the competitor editor's save, so the editor's own sentence says what it
          // does to the list: said once, above the lines, from the editor's copy.
          <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
            {COMPETITOR_EDITOR_COPY.openNote}
          </p>
        )}
        {/* The status and the alert come BEFORE the rows: focus moves to the status when a row has
            gone, and from there the next Tab is the next row's button, not the end of the card. */}
        <div ref={status} role="status" tabIndex={-1} data-testid="rival-suggestions-status">
          {said && <p className="text-ui-sm leading-prose text-text-secondary">{said}</p>}
        </div>
        {problem && (
          <p role="alert" className="text-ui-sm leading-prose text-danger">
            {problem}
          </p>
        )}
        {read.suggestions.length > 0 && (
          <ul className="flex flex-col gap-3">
            {read.suggestions.map((s) => (
              <li
                key={s.domain}
                className="flex flex-wrap items-center justify-between gap-3 text-ui-sm text-text-primary"
              >
                {/* A long domain is one unbreakable word: `min-w-0` lets this span shrink below it
                    and `break-words` breaks it, so at 320px the row wraps and the card stays in
                    the screen. */}
                <span className="min-w-0 break-words">
                  <span className="font-medium">{s.domain}</span>
                  {suggestionRest(s)}
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  aria-label={`${COPY.button}: ${s.domain}`}
                  aria-disabled={saving}
                  onClick={() => void add(s)}
                >
                  {COPY.button}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
