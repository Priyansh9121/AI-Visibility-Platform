'use client';

/**
 * "Get shareable link" — Epic 9.8, operator chrome on the authenticated report.
 *
 * **Sharing is an explicit act.** A scan is a private measurement until an
 * agency decides otherwise; minting a token for every scan and relying on the
 * URL being unknown would make that decision for them. This button is the
 * moment they make it, which is why it is a click rather than a side effect of
 * the scan finishing.
 *
 * Rendered ABOVE `ReportView`, never inside it. `ReportView` is the document
 * that gets sent — a control for sending it must not appear in what is sent.
 *
 * The design-system-only rule: every element comes from `@avp/design-system` — `Button`,
 * `Card`, `CardBody` and `TextField` — and the only local classes are layout
 * utilities (flex, gap, margin) resolving to the preset's own spacing scale.
 *
 * **The URL field is a `TextField`, not a hand-styled `<input>`.** The first
 * draft of this component rolled its own bordered input and reached for
 * `border-border-subtle` — a class the preset does not define. Its colour
 * scale exposes `line.hairline` / `line.strong`, so there is no `border-*`
 * namespace at all and the border simply never rendered. `tsc` cannot catch
 * that: Tailwind classes are opaque strings to the type checker. That is
 * precisely why `TextField` exists rather than each screen styling its own
 * field. See build-log Epic 9.8.
 */

import { useId, useState } from 'react';
import { Button, Card, CardBody, TextField } from '@avp/design-system';
import { ApiProblem, api } from '@/lib/api';
import { apiWasAway } from '@/lib/apiAway';
import { RETENTION, counted } from '@/lib/legal/retention';

type Withdraw = 'idle' | 'confirming' | 'working';

type State =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'ready'; url: string; copied: boolean; withdraw: Withdraw; error: string | null }
  | { kind: 'withdrawn' }
  | { kind: 'failed'; detail: string | null };

/** Said in place of the button for a scan that failed (3.24); the API refuses it too. */
const FAILED_SCAN_SENTENCE = 'This scan failed, so its report cannot be shared.';

export function ShareLinkBar({ scanId, scanStatus }: { scanId: string; scanStatus?: string | undefined }) {
  const [state, setState] = useState<State>({ kind: 'idle' });
  // The question the Withdraw button answers, so the button can be described by it.
  const questionId = useId();
  // Set when the question is answered without a withdrawal: by "Keep link", and
  // by a withdrawal that failed. Asking the question takes the Withdraw link
  // button off the page and closing it brings it back, and focus should be on it
  // when it returns rather than on <body> (the button that was pressed disables
  // while the request is out, so on a failure focus has nowhere else to be).
  // Cleared when the question is asked again.
  const [refocusTrigger, setRefocusTrigger] = useState(false);

  async function mint() {
    setState({ kind: 'working' });
    try {
      const link = await api.shareLink(scanId);
      setState({ kind: 'ready', url: link.url, copied: false, withdraw: 'idle', error: null });
    } catch (e) {
      // The API's own sentence when it refuses a failed scan (409).
      setState({
        kind: 'failed',
        detail:
          e instanceof ApiProblem && e.type === '/problems/failed-scan-not-shareable'
            ? e.message
            : null,
      });
    }
  }

  async function withdraw(url: string) {
    setState({ kind: 'ready', url, copied: false, withdraw: 'working', error: null });
    try {
      await api.withdrawShareLink(scanId);
      // The URL leaves state entirely: a withdrawn link must not stay in the DOM.
      setState({ kind: 'withdrawn' });
    } catch (e) {
      setRefocusTrigger(true);
      setState({
        kind: 'ready',
        url,
        copied: false,
        withdraw: 'idle',
        // A 4xx is the API refusing, and its own sentence says why. Anything
        // else (no answer, a proxy's 502, a 5xx) says nothing about the link,
        // which may well still open — and that is what the person needs to know.
        error:
          e instanceof Error && !apiWasAway(e)
            ? e.message
            : 'The link could not be withdrawn and may still open. Try again.',
      });
    }
  }

  async function copy(url: string) {
    // The clipboard settles whenever the browser gets round to it, so by then
    // the state can be somewhere else: a Withdraw may have finished (the link is
    // gone and its URL must not come back), may be under way (a fresh `ready`
    // here would reset it to idle and offer a second DELETE), or the bar may be
    // showing a newer link. So the answer is applied only to the state that is
    // still this link's, and changes `copied` and nothing else about it.
    const mark = (copied: boolean) =>
      setState((s) => (s.kind === 'ready' && s.url === url ? { ...s, copied } : s));
    try {
      await navigator.clipboard.writeText(url);
      mark(true);
    } catch {
      // Clipboard access is denied in some browsers and over plain HTTP. The
      // URL is already on screen and selectable, so this is not a failure
      // worth interrupting anyone over — it just means they copy it by hand.
      mark(false);
    }
  }

  // The API's refusal (409) turns the bar into the same state, so the button does
  // not stay enabled for a scan the server says cannot be shared.
  const refusal = state.kind === 'failed' ? state.detail : null;
  if (scanStatus === 'failed' || refusal !== null) {
    return (
      <Card elevation="seated">
        <CardBody>
          <p className="text-ui-md font-medium text-text-primary">Send this report</p>
          <p className="mt-1 text-ui-sm leading-prose text-text-secondary">
            {refusal ?? FAILED_SCAN_SENTENCE}
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-ui-md font-medium text-text-primary">Send this report</p>
            <p className="mt-1 text-ui-sm leading-prose text-text-secondary">
              Creates a link anyone can open without an account. Links expire after{' '}
              {counted(RETENTION.shareLinkDays, 'day')}. Only share it with the client this
              report is about.
            </p>
          </div>

          {state.kind !== 'ready' && (
            <Button
              variant="secondary"
              onClick={mint}
              disabled={state.kind === 'working'}
            >
              {state.kind === 'working' ? 'Creating…' : 'Get shareable link'}
            </Button>
          )}
        </div>

        {state.kind === 'ready' && (
          <div className="mt-4 flex flex-wrap items-end gap-3">
            {/*
              The whole row on a phone, a share of it from `sm` up. A bare
              `flex-1` has a zero basis, so with Withdraw in the row it was
              squeezed to nothing at 320px (WCAG 1.4.10, reflow): the field
              takes a full line there and the two buttons wrap beneath it.
            */}
            <div className="min-w-0 basis-full sm:flex-1 sm:basis-0">
              {/*
                A real field rather than a span: the operator's next action is to
                select and copy this, and `TextField` brings the bound <label>
                and focus ring with it. `readOnly` rather than `disabled` — a
                disabled input cannot be selected, which defeats the point.
              */}
              <TextField
                label="Public report link"
                readOnly
                value={state.url}
                onFocus={(e) => e.currentTarget.select()}
              />
            </div>
            {/* Not while the link is being withdrawn: there is nothing left to copy. */}
            <Button
              variant="secondary"
              onClick={() => copy(state.url)}
              disabled={state.withdraw === 'working'}
            >
              {state.copied ? 'Copied' : 'Copy'}
            </Button>
            {state.withdraw === 'idle' && (
              <Button
                variant="ghost"
                autoFocus={refocusTrigger}
                onClick={() => {
                  setRefocusTrigger(false);
                  setState({ ...state, withdraw: 'confirming', error: null });
                }}
              >
                Withdraw link
              </Button>
            )}
          </div>
        )}

        {state.kind === 'ready' && state.withdraw !== 'idle' && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <p id={questionId} className="text-ui-sm leading-prose text-text-secondary">
              Withdraw this link? Anyone who opens it will no longer see the report.
            </p>
            <Button
              variant="danger"
              aria-describedby={questionId}
              disabled={state.withdraw === 'working'}
              onClick={() => withdraw(state.url)}
            >
              {state.withdraw === 'working' ? 'Withdrawing…' : 'Withdraw link'}
            </Button>
            {/*
              Focus lands on the safe answer, not on the one that cannot be undone,
              so it is the button read first: it carries the question too.
            */}
            <Button
              variant="ghost"
              autoFocus
              aria-describedby={questionId}
              disabled={state.withdraw === 'working'}
              onClick={() => {
                setRefocusTrigger(true);
                setState({ ...state, withdraw: 'idle' });
              }}
            >
              Keep link
            </Button>
          </div>
        )}

        {/*
          What happened is said in two regions that are in the page from the
          start, empty, and filled as it happens. A screen reader announces a
          change to a live region, and mostly NOT one that arrives with its text
          already in it, so a message mounted as a new element is often never
          read. Each step here takes the control that was focused off the page,
          which is the other reason the messages cannot be left to focus.
        */}
        <div role="status">
          {state.kind === 'withdrawn' && (
            <p className="mt-3 text-ui-sm text-text-secondary">
              Link withdrawn. It no longer opens this report.
            </p>
          )}
        </div>
        <div role="alert">
          {state.kind === 'ready' && state.error !== null && (
            <p className="mt-3 max-w-measure text-ui-sm leading-prose text-danger">{state.error}</p>
          )}
          {state.kind === 'failed' && (
            <p className="mt-3 max-w-measure text-ui-sm leading-prose text-danger">
              {state.detail ?? 'The link could not be created. Try again.'}
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
