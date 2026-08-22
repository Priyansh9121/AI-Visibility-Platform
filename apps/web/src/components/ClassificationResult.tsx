'use client';

import { Badge, Button, Card, CardBody } from '@avp/design-system';
import type { ClientDetail } from '@avp/shared-types';

/**
 * The result of an intake classification.
 *
 * Three distinct outcomes get three distinct treatments, because the whole
 * point of the AMBIGUOUS status is that it must not look like a result:
 *
 *  - classified     -> the industry, stated plainly
 *  - ambiguous      -> "we could not tell", with the reason and a retry
 *  - unclassifiable -> what went wrong, with a retry
 *
 * A null industry is never rendered as an empty string or a dash-in-a-field
 * that reads like data. Same discipline as a null score in the report.
 */
export function ClassificationResult({
  client,
  onReset,
}: {
  client: ClientDetail;
  onReset: () => void;
}) {
  const status = client.classificationStatus;

  return (
    <Card elevation="raised">
      <CardBody>
        <div className="flex flex-col gap-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-mono text-ui-xs text-text-tertiary">{client.domain}</p>
              <h2 className="mt-1 font-editorial text-ed-xs text-text-primary">
                {client.brandName ?? client.name}
              </h2>
            </div>
            <StatusBadge status={status} />
          </div>

          {status === 'classified' && (
            <dl className="flex flex-col gap-4">
              <Fact label="Industry" value={client.industry} />
              {client.industryNiche && <Fact label="Niche" value={client.industryNiche} />}
              <Fact
                label="Confidence"
                value={`${client.industryConfidence ?? '—'}${
                  client.industryConfidenceScore
                    ? ` · ${Math.round(Number(client.industryConfidenceScore) * 100)}%`
                    : ''
                }`}
              />
            </dl>
          )}

          {status === 'ambiguous' && (
            <Explain
              heading="We could not classify this business confidently."
              body="The site did not give a clear enough signal. Rather than guess — a wrong
                    industry would skew every competitor and prompt we generate from it — we
                    have left it unset."
              code={client.classificationReasonCode}
            />
          )}

          {status === 'unclassifiable' && (
            <Explain
              heading="We could not read this site."
              body="Nothing was classified. This usually means the site did not load, or the
                    page carried no description of a business."
              code={client.classificationReasonCode}
            />
          )}

          {client.crawl && (
            <div className="border-t border-line-hairline pt-4">
              <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
                What we read
              </p>
              <p className="mt-2 text-ui-sm text-text-secondary">
                {client.crawl.pagesFetched} page
                {client.crawl.pagesFetched === 1 ? '' : 's'} · {client.crawl.wordCount} words
                {client.crawl.schemaTypes.length > 0 &&
                  ` · structured data: ${client.crawl.schemaTypes.slice(0, 4).join(', ')}`}
              </p>
            </div>
          )}

          <div className="flex gap-2">
            <Button variant="secondary" onClick={onReset}>
              Scan another site
            </Button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function StatusBadge({ status }: { status: ClientDetail['classificationStatus'] }) {
  if (status === 'classified') return <Badge tone="success">Identified</Badge>;
  if (status === 'ambiguous') return <Badge tone="warn">Needs review</Badge>;
  if (status === 'unclassifiable') return <Badge tone="danger">Could not read</Badge>;
  return <Badge tone="neutral">Pending</Badge>;
}

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className="text-ui-2xs uppercase tracking-caps text-text-tertiary">{label}</dt>
      <dd className="mt-1 text-ui-md font-medium text-text-primary">{value ?? '—'}</dd>
    </div>
  );
}

function Explain({
  heading,
  body,
  code,
}: {
  heading: string;
  body: string;
  code: string | null | undefined;
}) {
  return (
    <div>
      <p className="text-ui-md font-medium text-text-primary">{heading}</p>
      <p className="mt-2 max-w-measure text-ui-base leading-prose text-text-secondary">{body}</p>
      {code && <p className="mt-3 font-mono text-ui-2xs text-text-tertiary">Reason: {code}</p>}
    </div>
  );
}
