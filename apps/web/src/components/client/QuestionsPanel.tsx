'use client';

/**
 * "The questions this client's scans ask", at the top of the Prompts tab — F6 section 3.
 *
 * A rescan asks the client's earlier questions again, so a trend compares like with like. This
 * says where that set came from, says so when the client's rivals have changed since the
 * questions were written, and holds the one button that asks for new questions at the next scan.
 * A press saves at once and the screen shows what the API answered; the Undo takes it back.
 *
 * Fetches its own state. A failed read says so in a sentence and offers nothing to press.
 *
 * KEYBOARD AND SCREEN READER
 * --------------------------
 * There is ONE button and its words change ("Write new questions for the next scan", then
 * "Undo"). It stays the same element in the same place, so the focus a press had is still there
 * when the answer arrives; two buttons swapped for one another would have dropped focus to the
 * page. It stays enabled while a save runs (`aria-disabled`, and a second press is ignored) for
 * the same reason `AutoRescanPanel`'s do.
 *
 * "The next scan will ask new questions." is ADDED to a status region that is on the page from
 * the first render, empty, as 3.85 did for the password change: a screen reader announces what a
 * live region gains, and mostly not a region that arrives with its text already in it. Each
 * element brings its own top margin instead of the card body having a gap, so the empty region
 * leaves no space of its own. Taking the request back puts a sentence in the same region, so the
 * Undo is not silent: the same questions again when the client has a set, else that the next scan
 * writes them. (In the rare no-set case that sentence is also the one above the region; that is
 * accepted.)
 *
 * The button stays on the page once it has been pressed (`touched`), even when the answer leaves
 * the client with no set and no request. Taking it away would drop the focus it had to the page.
 */

import { useEffect, useId, useState, type JSX } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle } from '@avp/design-system';
import type { ClientQuestions } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import {
  LOADING_QUESTIONS,
  NEW_QUESTIONS_ASKED,
  NEW_QUESTIONS_TAKEN_BACK,
  NO_QUESTIONS_YET,
  QUESTIONS_NOT_LOADED,
  RIVALS_CHANGED,
  UNDO_NEW_QUESTIONS,
  WRITE_NEW_QUESTIONS,
  originSentence,
} from '@/lib/client/questions';

export function QuestionsPanel({ clientId }: { clientId: string }): JSX.Element {
  const [state, setState] = useState<ClientQuestions | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // The button has been pressed on this client: it stays, whatever the answer leaves (see above).
  const [touched, setTouched] = useState(false);
  // The last saved answer took a request back: the status region says what that did.
  const [tookBack, setTookBack] = useState(false);
  const statusId = useId();

  useEffect(() => {
    let cancelled = false;
    // One client's state and refusals never show under another.
    setState(null);
    setLoadFailed(false);
    setProblem(null);
    setTouched(false);
    setTookBack(false);
    api
      .clientQuestions(clientId)
      .then((answer) => {
        if (!cancelled) setState(answer);
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  async function choose(next: boolean) {
    // The button stays enabled while saving so keyboard focus is not lost; a second press is ignored.
    if (saving) return;
    setSaving(true);
    setProblem(null);
    setTouched(true);
    try {
      setState(await api.setNewQuestions(clientId, next));
      setTookBack(!next);
    } catch (err) {
      setProblem(
        err instanceof ApiProblem
          ? err.problem.detail
          : 'The change did not go through. Check your connection and try again.',
      );
    } finally {
      setSaving(false);
    }
  }

  const asked = state?.newQuestionsNextScan === true;
  // Before the client has any set the next scan writes one anyway, so there is nothing to ask for;
  // a request already made (by another route) can still be taken back, and the button then stays.
  const offered = state !== null && (asked || state.currentSet !== null || touched);
  // What the status region says: the request, or what taking it back did.
  const announcement = asked
    ? NEW_QUESTIONS_ASKED
    : tookBack
      ? state?.currentSet
        ? NEW_QUESTIONS_TAKEN_BACK
        : NO_QUESTIONS_YET
      : null;

  return (
    <Card elevation="seated">
      <CardHeader>
        <CardTitle>The questions this client&rsquo;s scans ask</CardTitle>
      </CardHeader>
      <CardBody>
        {loadFailed ? (
          <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
            {QUESTIONS_NOT_LOADED}
          </p>
        ) : state === null ? (
          <p className="text-ui-sm text-text-tertiary">{LOADING_QUESTIONS}</p>
        ) : (
          <>
            <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
              {state.currentSet ? originSentence(state.currentSet) : NO_QUESTIONS_YET}
            </p>
            {/* Once new questions are asked for, "write new questions" has been done. */}
            {state.currentSet?.rivalsChanged && !asked && (
              <p
                data-testid="rivals-changed"
                className="mt-3 max-w-measure text-ui-sm leading-prose text-text-secondary"
              >
                {RIVALS_CHANGED}
              </p>
            )}
          </>
        )}
        <div role="status" id={statusId} data-testid="questions-status">
          {announcement !== null && (
            <p className="mt-3 text-ui-sm font-medium text-text-primary">{announcement}</p>
          )}
        </div>
        {offered && (
          <div className="mt-3">
            <Button
              size="sm"
              variant="secondary"
              // `.avp-btn` never wraps; these words are wider than the card at 320px.
              className="whitespace-normal"
              aria-disabled={saving}
              // "Undo" is read with the sentence it undoes.
              aria-describedby={asked ? statusId : undefined}
              onClick={() => void choose(!asked)}
            >
              {asked ? UNDO_NEW_QUESTIONS : WRITE_NEW_QUESTIONS}
            </Button>
          </div>
        )}
        {problem !== null && (
          <p role="alert" className="mt-3 max-w-measure text-ui-sm leading-prose text-danger">
            {problem}
          </p>
        )}
      </CardBody>
    </Card>
  );
}
