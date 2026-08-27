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
 * ip-safety.md #2: every element comes from `@avp/design-system` — `Button`,
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

import { useState } from 'react';
import { Button, Card, CardBody, TextField } from '@avp/design-system';
import { api } from '@/lib/api';

type State =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'ready'; url: string; copied: boolean }
  | { kind: 'failed' };

export function ShareLinkBar({ scanId }: { scanId: string }) {
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function mint() {
    setState({ kind: 'working' });
    try {
      const link = await api.shareLink(scanId);
      setState({ kind: 'ready', url: link.url, copied: false });
    } catch {
      setState({ kind: 'failed' });
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setState({ kind: 'ready', url, copied: true });
    } catch {
      // Clipboard access is denied in some browsers and over plain HTTP. The
      // URL is already on screen and selectable, so this is not a failure
      // worth interrupting anyone over — it just means they copy it by hand.
      setState({ kind: 'ready', url, copied: false });
    }
  }

  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-ui-md font-medium text-text-primary">Send this report</p>
            <p className="mt-1 text-ui-sm leading-prose text-text-secondary">
              Creates a link anyone can open without an account. The link does not
              expire and cannot be withdrawn yet — only share it with the client
              this report is about.
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
              A real field rather than a span: the operator's next action is to
              select and copy this, and `TextField` brings the bound <label>
              and focus ring with it. `readOnly` rather than `disabled` — a
              disabled input cannot be selected, which defeats the point.
            */}
            <div className="min-w-0 flex-1">
              <TextField
                label="Public report link"
                readOnly
                value={state.url}
                onFocus={(e) => e.currentTarget.select()}
              />
            </div>
            <Button variant="secondary" onClick={() => copy(state.url)}>
              {state.copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        )}

        {state.kind === 'failed' && (
          <p className="mt-3 text-ui-sm text-text-secondary">
            The link could not be created. Try again.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
