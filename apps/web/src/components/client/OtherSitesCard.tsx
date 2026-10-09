'use client';

/**
 * "Sites with the same name" on a client's Sources tab — M1.
 *
 * A site that carries the client's or a rival's name under another country's ending is proposed
 * here; the agency says whether it is theirs. Any member of the agency may. Fetches its own state
 * (the pattern `AutoRescanPanel` uses) and draws the PUT's answer after a save.
 *
 * Buttons are `aria-disabled` while a save runs, not `disabled`, so a focused button keeps its
 * focus (`CompetitorEditor`'s reason). The status region is on the page from the first render,
 * empty, and says what a save did; focus moves to it when the row it was on has gone. A refusal
 * (409 while a scan runs, 422) shows the API's sentence in an alert and changes nothing.
 */

import { useEffect, useRef, useState, type JSX } from 'react';
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorState, LoadingState } from '@avp/design-system';
import type { OtherSites } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import {
  citationsLabel,
  otherSitesBody,
  OTHER_SITES_COPY as COPY,
  type OtherSitesAction,
} from '@/lib/client/otherSites';

type Read = { kind: 'loading' } | { kind: 'failed' } | { kind: 'ready'; sites: OtherSites };

export function OtherSitesCard({ clientId }: { clientId: string }): JSX.Element {
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
    // One client's lists, lines and refusals never show under another.
    setRead({ kind: 'loading' });
    setSaid('');
    setProblem(null);
    api
      .otherSites(clientId)
      .then((sites) => {
        if (!cancelled) setRead({ kind: 'ready', sites });
      })
      .catch(() => {
        if (!cancelled) setRead({ kind: 'failed' });
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  async function apply(sites: OtherSites, action: OtherSitesAction, sentence: string) {
    // Presses are ignored while saving; the buttons stay enabled so focus is not lost.
    if (saving) return;
    setSaving(true);
    setProblem(null);
    setSaid('');
    try {
      const answer = await api.replaceOtherSites(clientId, otherSitesBody(sites, action));
      if (!mounted.current) return;
      setRead({ kind: 'ready', sites: answer });
      setSaid(sentence);
      // The row that held focus is gone: say what happened where the reader is.
      queueMicrotask(() => status.current?.focus());
    } catch (err) {
      if (!mounted.current) return;
      setProblem(
        err instanceof ApiProblem ? err.problem.detail || err.problem.title : COPY.saveFailed,
      );
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  return (
    <Card elevation="seated">
      <CardHeader>
        <CardTitle>{COPY.title}</CardTitle>
      </CardHeader>
      <CardBody className="flex flex-col gap-4">
        <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">{COPY.intro}</p>
        {read.kind === 'loading' && <LoadingState message={COPY.loading} />}
        {read.kind === 'failed' && <ErrorState title={COPY.readFailed} />}
        {read.kind === 'ready' && <Lists sites={read.sites} saving={saving} apply={apply} />}
        <div ref={status} role="status" tabIndex={-1} data-testid="other-sites-status">
          {said && <p className="text-ui-sm leading-prose text-text-secondary">{said}</p>}
        </div>
        {problem && (
          <p role="alert" className="text-ui-sm leading-prose text-danger">
            {problem}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function Lists({
  sites,
  saving,
  apply,
}: {
  sites: OtherSites;
  saving: boolean;
  apply: (sites: OtherSites, action: OtherSitesAction, sentence: string) => Promise<void>;
}): JSX.Element {
  if (sites.proposals.length === 0 && sites.confirmed.length === 0) {
    return <p className="text-ui-sm leading-prose text-text-tertiary">{COPY.empty}</p>;
  }
  return (
    <>
      {sites.proposals.length > 0 && (
        <ul className="flex flex-col gap-3">
          {sites.proposals.map((p) => (
            <li
              key={p.domain}
              className="flex flex-wrap items-center justify-between gap-3 text-ui-sm text-text-primary"
            >
              <span>
                <span className="font-medium">{p.domain}</span>
                {' '}
                <span className="ml-2 text-text-tertiary">
                  {COPY.sameName(p.brandKind, p.brandName, p.brandDomain)}
                </span>{' '}
                <span className="ml-2 text-text-secondary">{citationsLabel(p.citations)}</span>
              </span>
              <span className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  aria-label={`${COPY.yes}: ${p.domain}`}
                  aria-disabled={saving}
                  onClick={() =>
                    void apply(sites, { kind: 'confirm', site: p }, COPY.confirmed(p.domain, p.brandName))
                  }
                >
                  {COPY.yes}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`${COPY.no}: ${p.domain}`}
                  aria-disabled={saving}
                  onClick={() =>
                    void apply(sites, { kind: 'dismiss', domain: p.domain }, COPY.dismissed(p.domain))
                  }
                >
                  {COPY.no}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {sites.confirmed.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-line-hairline pt-4">
          <h4 className="text-ui-sm font-medium text-text-primary">{COPY.confirmedHeading}</h4>
          <ul className="flex flex-col gap-3">
            {sites.confirmed.map((c) => (
              <li
                key={c.domain}
                className="flex flex-wrap items-center justify-between gap-3 text-ui-sm text-text-primary"
              >
                <span>
                  <span className="font-medium">{c.domain}</span>{' '}
                  <span className="ml-2 text-text-tertiary">{COPY.whose(c.brandKind, c.brandName)}</span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`${COPY.remove} ${c.domain}`}
                  aria-disabled={saving}
                  onClick={() =>
                    void apply(sites, { kind: 'remove', site: c }, COPY.removed(c.domain))
                  }
                >
                  {COPY.remove}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
