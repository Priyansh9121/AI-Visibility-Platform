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
 * A save names the list it read: the card reads the client's list as it saves and sends that list's
 * `version` with it (launch checklist 3.113). If the list changed in between, the API writes nothing
 * and the card says what changed, in one sentence (`lib/competitorListChange`), with a control that
 * reads the suggestions again. The next press then reads the list again as it always did. If that
 * read fails, the sentence stays (the add was not saved) and a line of its own says the suggestions
 * could not be refreshed; it is put back on the page by each failure, so each is announced.
 *
 * Buttons are `aria-disabled` while a save runs or the suggestions are being read again, not
 * `disabled`, so a focused button keeps its focus (`CompetitorEditor`'s reason); a press on an add
 * button is ignored in both. The status region is on the page from the first render, empty, and
 * focus moves to it when the row the press was on has gone. It and the alert sit ABOVE the rows:
 * from the status the next Tab is the next row's button, where below the rows it would leave the
 * card past every row still waiting.
 */

import { useEffect, useRef, useState, type JSX } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle } from '@avp/design-system';
import type { Competitor, RivalSuggestion } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import {
  LIST_CHANGED_COPY,
  listChangedProblem,
  listChangedSentenceFor,
} from '@/lib/competitorListChange';
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
  // The problem is the sentence for a save refused because the list had changed, so the control
  // that reads the suggestions again is offered beside it.
  const [listChanged, setListChanged] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // The suggestions could not be read again. It is a line of its own, not the `problem`: the
  // sentence about the refused save must stay on the page, because the person has not yet read
  // that the add was not saved if all they see is that a refresh failed.
  const [refreshFailed, setRefreshFailed] = useState(false);
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
    setListChanged(false);
    setRefreshFailed(false);
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
    // Presses are ignored while saving, and while the suggestions are being read again (`refresh`):
    // the Refresh control sits beside the sentence about a refused add, so an add can be pressed
    // while it reads, and the read landing after that press would clear the refusal the press had
    // just reported. The buttons stay enabled so focus is not lost.
    if (saving || refreshing) return;
    setSaving(true);
    setProblem(null);
    setListChanged(false);
    setRefreshFailed(false);
    setSaid('');
    try {
      // What a save replaces is the client's CURRENT list, read now: the editor reads it when it
      // opens, and an older read here could drop a rival somebody has just added. The save names
      // the version of the list it read, so one that changes before the save lands is refused.
      let current: Competitor[];
      let version: string | null;
      try {
        const found = await api.competitorSet(clientId);
        current = found?.competitors ?? [];
        version = found?.version ?? null;
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
        await api.replaceCompetitors(clientId, body, version);
      } catch (err) {
        if (!mounted.current) return;
        const refusal = listChangedProblem(err);
        if (refusal) {
          // The list changed between the read and the save: nothing was saved.
          setProblem(listChangedSentenceFor(current, refusal, LIST_CHANGED_COPY.justNow));
          setListChanged(true);
          return;
        }
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

  /** The suggestions again, beside a refused save: the list has moved, and so may they. */
  async function refresh() {
    // Not `disabled` while it reads, so the focused button keeps its focus.
    if (refreshing || saving) return;
    // Cleared first, so that a second failure is a line put back on the page and is announced
    // again: setting a state to the value it already holds changes nothing, and the press would
    // look dead.
    setRefreshFailed(false);
    setRefreshing(true);
    try {
      const found = await api.rivalSuggestions(clientId);
      if (!mounted.current) return;
      setRead({ kind: 'ready', suggestions: found.suggestions });
      setProblem(null);
      setListChanged(false);
      // The card stays up because it has just said something; with no rows under it, it does not
      // say it is showing suggestions.
      setSaid(found.suggestions.length > 0 ? COPY.refreshed : COPY.refreshedNone);
      queueMicrotask(() => status.current?.focus());
    } catch {
      // The sentence about the list stays, with its control: the add was not saved, and the person
      // still reads that. A line of its own says the press did nothing.
      if (mounted.current) setRefreshFailed(true);
    } finally {
      if (mounted.current) setRefreshing(false);
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
          // The sentences carry user-entered domains (up to 253 characters, no break): at 320px
          // `break-words` keeps the card in the screen, as it does for the rows.
          <p role="alert" className="text-ui-sm leading-prose text-danger break-words">
            {problem}
          </p>
        )}
        {refreshFailed && (
          <p role="alert" className="text-ui-sm leading-prose text-danger break-words">
            {COPY.refreshFailed}
          </p>
        )}
        {listChanged && (
          <div>
            <Button
              size="sm"
              variant="secondary"
              aria-disabled={refreshing}
              onClick={() => void refresh()}
            >
              {COPY.refresh}
            </Button>
          </div>
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
                  aria-disabled={saving || refreshing}
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
