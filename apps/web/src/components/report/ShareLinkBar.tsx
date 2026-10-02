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
import { formatStamp } from '@/lib/dates';
import { RETENTION, counted } from '@/lib/legal/retention';

type Withdraw = 'idle' | 'confirming' | 'working';

type Ready = {
  kind: 'ready';
  url: string | null;
  expiresAt: string | null;
  copied: boolean;
  withdraw: Withdraw;
  error: string | null;
};

type State =
  | { kind: 'idle' }
  // `live` is the link known to be live while a mint is out, so its line stays on screen.
  | { kind: 'working'; live: Ready | null }
  // `url` is null for a link the report payload says is live (`shareExpiresAt`):
  // the token is never sent back, so there is nothing to show or copy, only to withdraw.
  | Ready
  | { kind: 'withdrawn' }
  | { kind: 'failed' };

/** Said in place of the button for a scan that failed (3.24); the API refuses it too. */
const FAILED_SCAN_SENTENCE = 'This scan failed, so its report cannot be shared.';

export function ShareLinkBar({
  scanId,
  scanStatus,
  shareExpiresAt,
}: {
  scanId: string;
  scanStatus?: string | undefined;
  /** When the scan's live link ends, from the report payload; null when none is live. */
  shareExpiresAt?: string | null | undefined;
}) {
  const [state, setState] = useState<State>(() =>
    shareExpiresAt
      ? {
          kind: 'ready',
          url: null,
          expiresAt: shareExpiresAt,
          copied: false,
          withdraw: 'idle',
          error: null,
        }
      : { kind: 'idle' },
  );
  // The API's own sentence once it has refused a mint for a failed scan (409): the bar
  // then shows the failed-scan state, as it does for `scanStatus === 'failed'`.
  const [refusal, setRefusal] = useState<string | null>(null);
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
    // A link already known to be live (from the payload or an earlier mint) is
    // still live when this press fails, so it is put back with the failure beside it.
    const live = state.kind === 'ready' ? state : null;
    setState({ kind: 'working', live });
    try {
      const link = await api.shareLink(scanId);
      setState({
        kind: 'ready',
        url: link.url,
        expiresAt: link.expiresAt ?? null,
        copied: false,
        withdraw: 'idle',
        error: null,
      });
    } catch (e) {
      // The API's own sentence when it refuses a failed scan (409).
      const detail =
        e instanceof ApiProblem && e.type === '/problems/failed-scan-not-shareable'
          ? e.message
          : null;
      if (detail !== null) {
        // A live link (shared before the scan failed) stays, with its Withdraw.
        setRefusal(detail);
        setState(live ? { ...live, withdraw: 'idle', error: null } : { kind: 'idle' });
        return;
      }
      setState(
        live
          ? { ...live, withdraw: 'idle', error: 'The link could not be created. Try again.' }
          : { kind: 'failed' },
      );
    }
  }

  async function withdraw(url: string | null, expiresAt: string | null) {
    setState({ kind: 'ready', url, expiresAt, copied: false, withdraw: 'working', error: null });
    try {
      await api.withdrawShareLink(scanId);
      // The URL leaves state entirely: a withdrawn link must not stay in the DOM.
      setState({ kind: 'withdrawn' });
    } catch (e) {
      setRefocusTrigger(true);
      setState({
        kind: 'ready',
        url,
        expiresAt,
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

  // A failed scan cannot be shared (3.24), but one shared before it failed can
  // still be withdrawn (3.25): the sentence replaces the lead and the Get
  // button, and a live link keeps its end date and its Withdraw.
  const failedScan = scanStatus === 'failed' || refusal !== null;
  // The link known to be live: shown, or being replaced by a mint that is out.
  const live = state.kind === 'ready' ? state : state.kind === 'working' ? state.live : null;
  const withdrawing = state.kind === 'ready' && state.withdraw !== 'idle';

  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-ui-md font-medium text-text-primary">Send this report</p>
            {/*
              A live region that is in the page from the start, so a refusal that
              replaces the lead (and takes the Get button, and its focus, off the page)
              is announced rather than silently swapped in.
            */}
            <p
              aria-live="polite"
              data-testid="share-lead"
              className="mt-1 text-ui-sm leading-prose text-text-secondary"
            >
              {failedScan
                ? `${refusal ?? FAILED_SCAN_SENTENCE}${live ? ' Its existing link works until it is withdrawn or ends.' : ''}`
                : `Creates a link anyone can open without an account. Links last ${counted(RETENTION.shareLinkDays, 'day')} from when they are created. Only share it with the client this report is about.`}
            </p>
          </div>

          {!failedScan && (state.kind !== 'ready' || state.url === null) && (
            <Button
              variant="secondary"
              onClick={mint}
              disabled={state.kind === 'working' || withdrawing}
            >
              {state.kind === 'working' ? 'Creating…' : 'Get shareable link'}
            </Button>
          )}
        </div>

        {live !== null && live.expiresAt !== null && (
          <p className="mt-4 text-ui-sm leading-prose text-text-secondary">
            The link works until {formatStamp(live.expiresAt)}.
          </p>
        )}

        {state.kind === 'working' && state.live !== null && (
          <div className="mt-4 flex flex-wrap items-end gap-3">
            <Button variant="ghost" disabled>
              Withdraw link
            </Button>
          </div>
        )}

        {state.kind === 'ready' && (
          <div className="mt-4 flex flex-wrap items-end gap-3">
            {state.url !== null && (
              <ReadyLink
                url={state.url}
                copied={state.copied}
                disabled={state.withdraw === 'working'}
                onCopy={copy}
              />
            )}
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
              onClick={() => withdraw(state.url, state.expiresAt)}
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
              The link could not be created. Try again.
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

/**
 * The minted link: a read-only field and Copy.
 */
function ReadyLink({
  url,
  copied,
  disabled,
  onCopy,
}: {
  url: string;
  copied: boolean;
  disabled: boolean;
  onCopy: (url: string) => void;
}) {
  return (
    <>
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
          value={url}
          onFocus={(e) => e.currentTarget.select()}
        />
      </div>
      {/* Not while the link is being withdrawn: there is nothing left to copy. */}
      <Button variant="secondary" onClick={() => onCopy(url)} disabled={disabled}>
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </>
  );
}
