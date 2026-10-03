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
 * utilities (flex, gap, margin) resolving to the preset's own spacing scale, and the design
 * system's own `avp-visually-hidden` for the two messages that are for screen readers: that the
 * link is ready (3.38) and that it was copied (3.52).
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

import { useEffect, useId, useState } from 'react';
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
  // How many times Copy has worked on this link in a row (3.52): 0 until it has, and again once a
  // copy is refused or the link is being withdrawn. Above 0 the button says "Copied" and the status
  // region says "Link copied.". The count is that sentence's key, so each copy is a new element in
  // the region, which a screen reader announces, where the same words in the same element are no change.
  copies: number;
  // The link was live before this bar did anything: the report loaded with it, or a press found it
  // and was given it back (3.28). The lead then says there is a live link, not that the bar creates
  // one (3.52). A link the bar made from nothing is false here, and its lead stays as it was.
  existing: boolean;
  withdraw: Withdraw;
  // What the alert region says beside the link, and which press it is about: on a live link a
  // failed mint and a failed withdraw both land here, and only the mint's is Get's to be
  // described by (3.33).
  error: { from: 'mint' | 'withdraw'; text: string } | null;
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
/**
 * Added to it when a link made before the scan failed is still on the scan (3.29). The public
 * report refuses a failed scan's link like a withdrawn one, so the link stays only to be
 * withdrawn: no end date is shown for it, and nothing says it works.
 */
const FAILED_SCAN_LINK_SENTENCE = 'Its existing link no longer opens the report.';
/**
 * What a mint that failed says when no link is live, in the alert region and, through
 * `aria-describedby`, to Get (3.33).
 */
const LINK_NOT_CREATED_SENTENCE = 'The link could not be created. Try again.';
/**
 * What a press that failed says when a link is live (3.52), said where and to whom
 * `LINK_NOT_CREATED_SENTENCE` is. Nothing was being created: the press asks for a link that
 * exists, and a failure of that leaves the link as it was (3.28).
 */
const LINK_NOT_GOT_SENTENCE = 'We could not get the link. Try again.';
/**
 * Get's busy label while a link is live (3.52). A press on a live link returns it as it is (3.28),
 * so nothing is created and "Creating…" would be untrue; with no link live a press does make one,
 * and the label stays "Creating…".
 */
const GETTING_LINK_LABEL = 'Getting the link…';
/**
 * Said in the status region once a copy has worked (3.52), beside the ready sentence. The button's
 * new name ("Copied") is not reliably announced, and a copy that is refused says nothing: the link
 * is on screen and selectable, and nothing on the bar claims a copy that did not happen.
 */
const LINK_COPIED_SENTENCE = 'Link copied.';

/**
 * The two sentences every lead ends with, live link or not: how long links last, and who gets
 * one. Read at render, not at load, because the days are `RETENTION`'s, which a test changes.
 */
function linkRulesSentences(): string {
  return `Links last ${counted(RETENTION.shareLinkDays, 'day')} from when they are created. Only share it with the client this report is about.`;
}

/** The lead with no link live: what Get shareable link will do. */
function noLinkLead(): string {
  return `Creates a link anyone can open without an account. ${linkRulesSentences()}`;
}

/**
 * The lead for a link that was live before the bar did anything (3.52). It says what is true of
 * that link, not that the bar creates one: since 3.28 a press of Get shareable link returns it as
 * it is. "The link" is the address, so whoever holds it can open the report. It ends as the other
 * lead does, so the days still come from the one constant.
 */
function liveLinkLead(): string {
  return `This report has a live link. Anyone with the link can open the report without an account. ${linkRulesSentences()}`;
}

/**
 * Said in the status region once Get shareable link has made a link (3.33), and the one
 * announcement of its end date: the "works until" line is not a live region. The date is
 * `formatStamp`'s, as in that line. A response with no end date says only that the link is
 * ready, never a date it was not given. Visually hidden (3.38): it is for a screen reader,
 * and a sighted reader has the date once, in that line.
 */
function linkReadySentence(expiresAt: string | null): string {
  return expiresAt === null
    ? 'Link ready.'
    : `Link ready. It works until ${formatStamp(expiresAt)}.`;
}

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
          copies: 0,
          existing: true,
          withdraw: 'idle',
          error: null,
        }
      : { kind: 'idle' },
  );
  // The API's own sentence once it has refused a mint for a failed scan (409): the bar
  // then shows the failed-scan state, as it does for `scanStatus === 'failed'`.
  const [refusal, setRefusal] = useState<string | null>(null);
  // A failed scan cannot be shared (3.24), and a link made before it failed no longer opens
  // its report (3.29), but that link can still be withdrawn (3.25): the sentences replace the
  // lead and the Get button, and a live link keeps its Withdraw but not its end date, its
  // field or its Copy. Read by the handlers below as well, so it is settled before them.
  const failedScan = scanStatus === 'failed' || refusal !== null;
  // The question the Withdraw button answers, so the button can be described by it.
  const questionId = useId();
  // Set when the question is answered without a withdrawal: by "Keep link", and
  // by a withdrawal that failed. Asking the question takes the Withdraw link
  // button off the page and closing it brings it back, and focus should be on it
  // when it returns rather than on <body> (the button that was pressed disables
  // while the request is out, so on a failure focus has nowhere else to be).
  // Cleared when the question is asked again, and when a mint starts.
  const [refocusTrigger, setRefocusTrigger] = useState(false);
  // The Get button's id, so focus can be put back on it: Button takes an id and no ref.
  const getButtonId = useId();
  // The id of the sentence a failed mint says, which Get is described by while it shows.
  const mintFailureId = useId();
  // Counts mints that have failed (3.33). The Get button disables while its request is out,
  // which takes focus off the page, and it is the same element when the request fails, so
  // there is no remount for an autoFocus (as the Withdraw link has) to act on: the effect
  // below puts focus back on it once the commit that re-enables it is done.
  const [getFocusRequest, setGetFocusRequest] = useState(0);
  useEffect(() => {
    if (getFocusRequest === 0) return;
    const get = document.getElementById(getButtonId);
    const active = document.activeElement;
    // Only if focus is still lost, or still on the button: a person who has moved on to
    // something else while the request was out keeps their place.
    if (get !== null && (active === null || active === document.body || active === get)) {
      get.focus();
    }
  }, [getFocusRequest, getButtonId]);

  async function mint() {
    // A link already known to be live (from the payload or an earlier mint) is
    // still live when this press fails, so it is put back with the failure beside it.
    const live = state.kind === 'ready' ? state : null;
    // This press replaces what the Withdraw question was last used for. Focus goes to the new
    // link's field when it is made (3.33) and back to this button when it is not, so the
    // Withdraw link that comes back with either must not claim it first, as it would if a
    // Keep link or a failed withdraw on the link being replaced had left this flag set.
    setRefocusTrigger(false);
    setState({ kind: 'working', live });
    try {
      const link = await api.shareLink(scanId);
      setState({
        kind: 'ready',
        url: link.url,
        expiresAt: link.expiresAt ?? null,
        copies: 0,
        // A press made with a link live was given it back (3.28), whatever the lead said before.
        existing: live !== null,
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
      // With a link live the press asked for it and nothing was being created, so the failure
      // does not say it was (3.52). It is still a mint's failure: Get is described by it (3.33).
      setState(
        live
          ? { ...live, withdraw: 'idle', error: { from: 'mint', text: LINK_NOT_GOT_SENTENCE } }
          : { kind: 'failed' },
      );
      // Not for the refusal above: there Get leaves the page, and there is nothing to retry.
      setGetFocusRequest((n) => n + 1);
    }
  }

  async function withdraw(from: Ready) {
    setState({ ...from, copies: 0, withdraw: 'working', error: null });
    try {
      await api.withdrawShareLink(scanId);
      // The URL leaves state entirely: a withdrawn link must not stay in the DOM.
      setState({ kind: 'withdrawn' });
    } catch (e) {
      setRefocusTrigger(true);
      setState({
        ...from,
        copies: 0,
        withdraw: 'idle',
        // A 4xx is the API refusing, and its own sentence says why. Anything
        // else (no answer, a proxy's 502, a 5xx) says nothing about the link,
        // which may well still open — and that is what the person needs to know.
        // Except for a failed scan's (3.29): the API refuses that link whatever
        // happened here, so "may still open" would be untrue of it.
        error: {
          from: 'withdraw',
          text:
            e instanceof Error && !apiWasAway(e)
              ? e.message
              : failedScan
                ? 'The link could not be withdrawn. Try again.'
                : 'The link could not be withdrawn and may still open. Try again.',
        },
      });
    }
  }

  async function copy(url: string) {
    // The clipboard settles whenever the browser gets round to it, so by then
    // the state can be somewhere else: a Withdraw may have finished (the link is
    // gone and its URL must not come back), may be under way (a fresh `ready`
    // here would reset it to idle and offer a second DELETE), or the bar may be
    // showing a newer link. So the answer is applied only to the state that is
    // still this link's, and changes `copies` and nothing else about it.
    //
    // A copy that worked adds one, and that puts "Link copied." in the status region
    // (3.52). The same guard keeps a late answer from saying so for a link that has been
    // withdrawn or replaced since.
    const mark = (worked: boolean) =>
      setState((s) =>
        s.kind === 'ready' && s.url === url ? { ...s, copies: worked ? s.copies + 1 : 0 } : s,
      );
    try {
      await navigator.clipboard.writeText(url);
      mark(true);
    } catch {
      // Clipboard access is denied in some browsers and over plain HTTP. The
      // URL is already on screen and selectable, so this is not a failure
      // worth interrupting anyone over — it just means they copy it by hand.
      // Said nowhere (3.52): "Link copied." is only for a copy that happened.
      mark(false);
    }
  }

  // The link known to be live: shown, or being replaced by a mint that is out.
  const live = state.kind === 'ready' ? state : state.kind === 'working' ? state.live : null;
  // That link was live before the bar did anything about it (3.52): it picks the lead and the
  // lead's key. It stays true through a press, a mint and Copy, and ends with a withdraw.
  const linkWasLive = live?.existing === true;
  const withdrawing = state.kind === 'ready' && state.withdraw !== 'idle';
  // The alert region is saying that the last mint failed (3.33). A failed withdraw beside Get is
  // not this: it is about the link, and Get is not described by it. Focus goes back to Get in the
  // same tick as the alert appears, and a screen reader may cut the alert off for the focus
  // change, so Get carries the sentence itself (as DownloadPdfButton's does for its failure).
  const mintFailed =
    state.kind === 'failed' || (state.kind === 'ready' && state.error?.from === 'mint');

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

              What the lead says of the link follows what the bar knows (3.52): a link that
              was live before the bar did anything gets the sentence about a live link, and
              any other the one about what Get does. A press that succeeds leaves it as it
              was, because any change to these words is read out, and the whole lead would
              be read on top of "Link ready".

              When the live link is withdrawn the lead goes back to the sentence about Get,
              and that is a NEW element: the key follows whether a link was live to begin
              with. The change is then not read out beside "Link withdrawn…", which has said
              what happened. A refusal does not change the key, so it still edits this region
              in place and is read out.
            */}
            <p
              key={linkWasLive ? 'live-link' : 'no-link'}
              aria-live="polite"
              data-testid="share-lead"
              className="mt-1 text-ui-sm leading-prose text-text-secondary"
            >
              {failedScan
                ? `${refusal ?? FAILED_SCAN_SENTENCE}${live ? ` ${FAILED_SCAN_LINK_SENTENCE}` : ''}`
                : linkWasLive
                  ? liveLinkLead()
                  : noLinkLead()}
            </p>
          </div>

          {!failedScan && (state.kind !== 'ready' || state.url === null) && (
            <Button
              id={getButtonId}
              variant="secondary"
              onClick={mint}
              disabled={state.kind === 'working' || withdrawing}
              aria-describedby={mintFailed ? mintFailureId : undefined}
            >
              {state.kind === 'working'
                ? state.live !== null
                  ? GETTING_LINK_LABEL
                  : 'Creating…'
                : 'Get shareable link'}
            </Button>
          )}
        </div>

        {/* Never for a failed scan: its link is refused, so it does not "work" until anything. */}
        {!failedScan && live !== null && live.expiresAt !== null && (
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
            {/* Not for a failed scan: a link made earlier in the visit opens nothing now. */}
            {!failedScan && state.url !== null && (
              <ReadyLink
                url={state.url}
                copied={state.copies > 0}
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
            {/* A failed scan's link opens nothing already, so "will no longer see" would be untrue of it. */}
            <p id={questionId} className="text-ui-sm leading-prose text-text-secondary">
              {failedScan
                ? 'Withdraw this link? It will be removed permanently.'
                : 'Withdraw this link? Anyone who opens it will no longer see the report.'}
            </p>
            <Button
              variant="danger"
              aria-describedby={questionId}
              disabled={state.withdraw === 'working'}
              onClick={() => withdraw(state)}
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

          `status` is atomic unless it is told otherwise, and a screen reader reads an
          atomic region whole whenever any part of it changes. Once a link has been copied
          this one holds two sentences (3.52), so it is not atomic: what is read is what is
          added, and "Link copied." does not bring "Link ready. It works until …" with it.
        */}
        <div role="status" aria-atomic="false">
          {state.kind === 'withdrawn' && (
            <p className="mt-3 text-ui-sm text-text-secondary">
              Link withdrawn. It no longer opens this report.
            </p>
          )}
          {/*
            The link a mint has made, from the mint until it is withdrawn (3.33). Only a
            mint gives the bar a URL, so this is not said for a link the page was loaded
            with. Never for a failed scan, whose link opens nothing (3.29): nothing on the
            bar says it works. The "works until" line above is not live, so this is the
            one announcement of the date.

            For screen readers only (3.38): that line already shows the date, so a
            sighted reader would see it twice. The text is clipped, not removed, so it is
            still read when it arrives. The region stays visible as it was, and so does the
            withdrawn message beside it. `display: none`, `hidden` or `visibility: hidden`
            on this text or on the region would silence the announcement.
          */}
          {!failedScan && state.kind === 'ready' && state.url !== null && (
            <>
              <p className="avp-visually-hidden">{linkReadySentence(state.expiresAt)}</p>
              {/*
                A copy that worked (3.52), in the same way and beside it: a sentence of its own,
                so the ready sentence is not touched. The key is the number of copies, so a
                second copy takes this element out and puts a new one in. The same words in
                the same element would be no change to the region, and nothing would be read.
                A refused copy takes it away again and says nothing.
              */}
              {state.copies > 0 && (
                <p key={state.copies} className="avp-visually-hidden">
                  {LINK_COPIED_SENTENCE}
                </p>
              )}
            </>
          )}
        </div>
        <div role="alert">
          {state.kind === 'ready' && state.error !== null && (
            <p
              id={state.error.from === 'mint' ? mintFailureId : undefined}
              className="mt-3 max-w-measure text-ui-sm leading-prose text-danger"
            >
              {state.error.text}
            </p>
          )}
          {state.kind === 'failed' && (
            <p id={mintFailureId} className="mt-3 max-w-measure text-ui-sm leading-prose text-danger">
              {LINK_NOT_CREATED_SENTENCE}
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
          // Focus lands here when the link is made (3.33), because the button that was
          // pressed has just left the page. The field mounts only then: the token is never
          // sent back, so a link the page was loaded with has none. `onFocus` selects it.
          autoFocus
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
