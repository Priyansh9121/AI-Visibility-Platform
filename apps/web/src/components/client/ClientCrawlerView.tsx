'use client';

/**
 * A client's AI crawlers screen — Epic F.
 *
 * WHAT IT SHOWS, AND THE CLAIM IT REFUSES TO MAKE
 * ------------------------------------------------
 * What this site's robots.txt ASKS each AI crawler to do. It does not — and
 * must never appear to — report a crawler having visited.
 *
 * The roadmap called this section "Crawler activity" and meant server-log
 * evidence of AI bots hitting the site. Nothing in this product ingests server
 * logs: no drain, no collector, no model that could hold a bot hit. robots.txt
 * is the one first-party, fetchable statement of the same policy, and the
 * technical audit already reads it on every scan. So the screen answers the
 * weaker question honestly rather than the stronger one falsely.
 *
 * **Every word of copy on this screen is bound by that.** "Asks", "allows",
 * "blocks", "says nothing about" — never "is crawling", "has visited",
 * "traffic", or "activity". `ClientCrawlerView.test.tsx` asserts the
 * vocabulary rather than trusting it, because this is exactly the kind of
 * constraint that erodes one well-meaning copy edit at a time.
 *
 * A stated policy is also not proof of compliance: robots.txt is advisory, and
 * a block enforced at a CDN is invisible to it. The lead says so.
 *
 * WHY THE ROWS ARE GROUPED BY PURPOSE AND NOT BY VENDOR
 * ------------------------------------------------------
 * A vendor grouping (OpenAI, Anthropic, Google, …) is the obvious one and it
 * reads as a directory: it tells an operator who the crawlers belong to, which
 * is the thing they least need help with.
 *
 * The decision an agency is actually being paid for is what a block COSTS, and
 * that is a property of purpose, not of vendor:
 *
 *   * Blocking a SEARCH crawler removes the site from the retrieval index an
 *     engine cites from. That directly costs visibility — this product's whole
 *     subject.
 *   * Blocking a TRAINING crawler is a rights decision with no citation cost.
 *   * Blocking a USER-ACTION fetcher breaks "summarise this link" for the site.
 *
 * Those three sentences are the teaching, and grouping by purpose is what
 * gives them somewhere to live — one line per group, above the rows it
 * governs, rather than a legend the reader has to hold in their head. A
 * purpose column on a flat table would name the category without ever saying
 * what it implies, which is the version of this that looks tidy and teaches
 * nothing.
 *
 * Groups are ordered by what a block costs, worst first, and rows within a
 * group by verdict — so the one blocked row on a real client is the first
 * thing under the first heading.
 *
 * Pure and prop-driven, so every state is reachable by a static render.
 */

import { type JSX } from 'react';
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  ErrorState,
  LoadingState,
  SelectField,
  StatRow,
  StatTile,
} from '@avp/design-system';
import type { CrawlerAccess, CrawlerAgent, Me } from '@avp/shared-types';
import { ClientSpace } from '@/components/client/ClientSpace';
import { accentFor } from '@/components/client/clientNav';
import {
  latestComposite,
  latestScanId,
  type ClientDetailState,
} from '@/components/client/ClientDetailView';

/** The screen's accent, read from the nav table — never a second literal. */
const ACCENT = accentFor('crawler') ?? 0;

/**
 * The two accented tiles, and why they are +0 and +3 rather than +0 and +1.
 *
 * **Found in a browser, not in a test.** The first draft used
 * `[ACCENT, ACCENT + 1, ACCENT + 2]`, copying the shape the other screens use.
 * This section's accent is 5, so those resolved to bench-6 and bench-7 —
 * **hue 343 and hue 355, twelve degrees apart**, the tightest neighbouring
 * pair in the whole layer. On Notion, the one client with a real finding, the
 * two tiles beside each other both read `1` in what is visibly the same
 * red-pink.
 *
 * That is Epic B's crimson/magenta collision again, worse: it recorded 29
 * degrees as already reading "as the same pink", and `color.ts` puts the limit
 * of what hue alone can separate at about 11. Adjacent offsets are simply not
 * safe on a 97-degree arc, and they are least safe at its crowded end — which
 * is exactly where the last nav seat sits.
 *
 * A stride of 3 keeps the two accents at least 46 degrees apart for EVERY
 * possible value of `ACCENT`, not just this one, so the property survives the
 * nav table moving this screen. `ClientCrawlerView.test.tsx` asserts that
 * across all seven seats rather than trusting the arithmetic here.
 */
const TILE = [ACCENT, ACCENT + 3] as const;

/**
 * A count tile drops its accent at zero — the design review's rule, and the
 * same helper `ClientAnswerGapsView` carries.
 *
 * It lives in the screen and not in `StatTile` because the primitive cannot
 * know what zero means for a given caller. Here "BLOCKED 0" is a genuine
 * all-clear and should read plainly; a zero that is not a finding must not
 * carry the same weight as one that is.
 */
function countAccent(value: number, accent: number): { accent?: number } {
  return value > 0 ? { accent } : {};
}

/**
 * The three purposes, in the order a block costs the client.
 *
 * The `cost` line is the reason this grouping exists — see the module note.
 */
const PURPOSE: ReadonlyArray<{
  key: string;
  label: string;
  cost: string;
}> = [
  {
    key: 'search',
    label: 'Search and citation',
    cost:
      'These build the index an engine cites from. Blocking one takes this site out of the answers it could have been cited in.',
  },
  {
    key: 'training',
    label: 'Model training',
    cost:
      'These collect pages models are trained on. Blocking one is a rights decision — it does not cost citations on its own.',
  },
  {
    key: 'user_action',
    label: 'Fetched on request',
    cost:
      'These fetch a page when someone asks an assistant about it. Blocking one breaks “summarise this link” for this site.',
  },
];

/**
 * How each verdict is drawn.
 *
 * A BADGE MEANS A RULE APPLIES; ITS ABSENCE MEANS NONE DOES. That is the
 * literal semantic difference between `allowed` and `unspecified`, and drawing
 * it structurally is what keeps the two apart without inventing a hierarchy
 * between them. `unspecified` is the quietest thing on the row because it is
 * the state nobody decided — and when it is universal, `NoPolicyNote` says so
 * in words, which is where that finding belongs.
 *
 * **`allowed` is `neutral`, deliberately not `success`.** Green would read as
 * "you are doing this right", and for a TRAINING crawler that is a rights
 * decision this product takes no position on. The group heading already
 * carries what a block would cost; the badge only reports the state.
 */
const VERDICT: Record<string, { label: string; tone: 'danger' | 'warn' | 'neutral' }> = {
  // Danger, not warn: this is the one state with a cost attached, and on a
  // real client it is a single row among fourteen. It has to carry.
  blocked: { label: 'Blocked', tone: 'danger' },
  unknown: { label: 'Not readable', tone: 'warn' },
  allowed: { label: 'Allowed', tone: 'neutral' },
};

export type CrawlerAccessState =
  | { kind: 'loading' }
  /**
   * `pending` is a scan being SWAPPED, not a first load — the distinction
   * Epic B's live pass added after switching scans unmounted the picker that
   * had just been used. The last good reading stays on screen, dimmed.
   */
  | { kind: 'ready'; data: CrawlerAccess | null; pending?: boolean }
  | { kind: 'error'; title: string; detail: string };

export function ClientCrawlerView({
  state,
  access,
  me,
  onSelectScan,
}: {
  state: ClientDetailState;
  access: CrawlerAccessState;
  me: Me | null;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  if (state.kind === 'loading' || state.kind === 'error') {
    return (
      <ClientSpace
        client={{ id: '', name: '—', brandName: null, domain: '' }}
        me={me}
        current="crawler"
        latestReportScanId={null}
      >
        {state.kind === 'loading' ? (
          <LoadingState message="Loading this client…" />
        ) : (
          <ErrorState title={state.title} detail={state.detail} />
        )}
      </ClientSpace>
    );
  }

  const { client, history } = state;
  return (
    <ClientSpace
      client={client}
      me={me}
      current="crawler"
      latestReportScanId={latestScanId(history)}
      latestScore={latestComposite(history)}
    >
      <CrawlerBody access={access} onSelectScan={onSelectScan} />
    </ClientSpace>
  );
}

function CrawlerBody({
  access,
  onSelectScan,
}: {
  access: CrawlerAccessState;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  if (access.kind === 'loading') return <LoadingState message="Loading crawler policy…" />;
  if (access.kind === 'error') {
    return <ErrorState title={access.title} detail={access.detail} />;
  }
  if (access.data === null) return <NeverRead />;

  return (
    <Policy
      data={access.data}
      pending={access.pending ?? false}
      onSelectScan={onSelectScan}
    />
  );
}

function Policy({
  data,
  pending,
  onSelectScan,
}: {
  data: CrawlerAccess;
  pending: boolean;
  onSelectScan?: ((scanId: string) => void) | undefined;
}): JSX.Element {
  const { summary } = data;
  const silent = summary.unspecified === summary.total;

  return (
    <section
      className="flex flex-col gap-6 transition-opacity duration-state ease-standard"
      style={{ opacity: pending ? 'var(--avp-dim-pending)' : 1 }}
      aria-busy={pending || undefined}
    >
      <div className="flex flex-col gap-2">
        <h2 className="max-w-headline font-display text-ed-xs leading-display tracking-display text-text-primary">
          What this site asks AI crawlers to do
        </h2>
        <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
          {/*
            "Read from" ONLY when it was actually read.

            Caught by driving the unreachable-domain client: the lead said
            "Read from …/robots.txt on Aug 22, 2026" directly above a card
            saying the file could not be read. The two sentences contradicted
            each other, and the confident one came first.
          */}
          {data.robotsReadable ? 'Read from ' : 'This scan tried to read '}
          <code className="text-ui-sm">{data.urlAudited}/robots.txt</code>
          {/*
            The DATE, which the first draft left out entirely.

            Every figure on this screen is a point-in-time claim about a file
            that can be edited any day, and the scan picker offers "2 scans
            ago" without ever saying when that was. "When this scan ran" is
            only meaningful if the screen says when that was — otherwise a
            reading from this morning and one from two months ago are
            presented identically.
          */}
          {data.scannedAt != null ? (
            <>
              {' '}on <time dateTime={data.scannedAt}>{fmtDate(data.scannedAt)}</time>
            </>
          ) : (
            ' when this scan ran'
          )}
          .{' '}
          {data.robotsReadable ? (
            <>
              This is the site’s stated policy — what it <em>asks</em> for. It is
              not a record of any crawler having visited, and a crawler that
              ignores robots.txt, or a block applied at a CDN, would not show
              here either way.
            </>
          ) : (
            <>
              Nothing below is a record of any crawler having visited, and a
              crawler that ignores robots.txt, or a block applied at a CDN,
              would not show here either way.
            </>
          )}
        </p>
      </div>

      {!data.robotsReadable ? (
        <Unreadable url={data.urlAudited} />
      ) : (
        <StatRow min="11rem">
          <StatTile
            label="Blocked"
            value={String(summary.blocked)}
            {...countAccent(summary.blocked, TILE[0])}
            emphasis={summary.blocked > 0}
            note="Asked not to crawl this site."
          />
          <StatTile
            label="Costing citations"
            value={String(summary.searchBlocked)}
            {...countAccent(summary.searchBlocked, TILE[1])}
            emphasis={summary.searchBlocked > 0}
            note="Blocked crawlers that build the index engines cite from."
          />
          {/*
            UNACCENTED, with "Not mentioned" below it — the design review's
            rule applied to which tiles are FINDINGS rather than only to which
            are zero.

            Two of these four are findings: something is blocked, and some of
            it costs citations. The other two are the context that explains
            them, and when a site names nobody at all the finding is a
            sentence (`NoPolicyNote`), not a figure. Accenting all four also
            meant four hues off a 97-degree arc, which is what produced the
            collision documented on `TILE`.
          */}
          <StatTile
            label="Named directly"
            value={`${summary.explicit} / ${summary.total}`}
            note="Crawlers this site’s rules mention by name."
          />
          {/*
            DELIBERATELY UNACCENTED — the tile that is not a finding, matching
            how `ClientAnswerGapsView` draws "Named no one".

            A crawler falling under a `*` rule is the ordinary case, not a
            problem, and on most real clients this number IS the whole roster.
            Accenting the largest and least actionable figure on the screen
            would make the row read as though it were the headline.
          */}
          <StatTile
            label="Not mentioned"
            value={String(summary.unspecified)}
            note="No rule names them. They may crawl."
          />
        </StatRow>
      )}

      {silent && <NoPolicyNote />}

      {data.availableScanIds.length > 1 && onSelectScan != null && (
        <div className="flex flex-wrap gap-4">
          <SelectField
            label="Scan"
            value={data.scanId}
            onChange={(e) => onSelectScan(e.target.value)}
            options={data.availableScanIds.map((id, i) => ({
              value: id,
              label: i === 0 ? 'Latest' : `${i + 1} scans ago`,
            }))}
          />
        </div>
      )}

      {PURPOSE.map((group) => {
        const rows = data.agents.filter((a) => a.purpose === group.key);
        if (rows.length === 0) return null;
        return <PurposeGroup key={group.key} group={group} rows={rows} />;
      })}
    </section>
  );
}

function PurposeGroup({
  group,
  rows,
}: {
  group: { key: string; label: string; cost: string };
  rows: readonly CrawlerAgent[];
}): JSX.Element {
  const captionId = `avp-crawler-${group.key}`;
  return (
    <section className="flex flex-col gap-2">
      <h3
        id={captionId}
        className="text-ui-sm font-medium uppercase tracking-caps text-text-primary"
      >
        {group.label}
      </h3>
      {/* The line that makes the grouping worth its own heading. */}
      <p className="max-w-measure text-ui-xs leading-prose text-text-secondary">
        {group.cost}
      </p>
      {/*
        `max-w-report` on the LIST, not on the section — found by driving this
        at 1440px. The rows put the agent's name hard left and its badge hard
        right, so at full app width the eye had to cross about 1,100px of empty
        space to connect "Amazonbot" to "Blocked". Fourteen times.

        Capping the list keeps the badges in one scannable column while
        bringing the two ends of a row back within a single fixation. The stat
        tiles above deliberately keep the full width — four figures genuinely
        use it; a two-item row does not.
      */}
      <ul aria-labelledby={captionId} className="mt-1 flex max-w-report flex-col gap-1">
        {rows.map((row) => (
          <li key={row.agent}>
            <AgentRow row={row} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function AgentRow({ row }: { row: CrawlerAgent }): JSX.Element {
  // Absent for `unspecified` — no rule applies, so there is no badge to draw.
  const verdict = VERDICT[row.verdict];
  // See the note below: the deciding rule is worth printing when the site
  // named this agent itself, or when the verdict is a block. Otherwise it is
  // the default restated on every row.
  const showRule =
    row.matchedToken != null &&
    (row.matchedToken !== '*' || row.verdict === 'blocked');

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line-hairline py-2 last:border-b-0">
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className="text-ui-base text-text-primary">{row.agent}</span>
        {/*
          Secondary, not tertiary — Epic E's finding, applied before it could
          repeat. With three OpenAI rows carrying three different verdicts,
          the vendor is what tells an operator whose policy they are looking
          at, and a discriminator cannot be the quietest thing in the row.
        */}
        <span className="text-ui-sm text-text-secondary">{row.vendor}</span>
      </span>
      <span className="flex flex-wrap items-center gap-2">
        {/*
          The rule that decided it, so a verdict can be argued with. An agency
          saying "you are blocking Perplexity" needs to know whether the client
          typed the name or inherited a blanket rule written for SEO crawlers
          years ago — different conversations, different fixes.

          SHOWN ONLY WHEN IT TELLS THE OPERATOR SOMETHING, which the live pass
          made obvious: on a real client thirteen of fourteen rows repeated
          "via User-agent: *" — the default, restated until it was wallpaper,
          and loud enough to bury the one row that said "named as amazonbot".
          Both remaining cases are the ones worth reading: an agent the site
          named itself, or a block, where inheriting a blanket rule IS the
          finding.
        */}
        {showRule && (
          <span className="text-ui-2xs text-text-tertiary">
            {row.matchedToken === '*'
              ? 'via User-agent: *'
              : `named as ${row.matchedToken}`}
          </span>
        )}
        {verdict != null ? (
          <Badge tone={verdict.tone}>{verdict.label}</Badge>
        ) : (
          <span className="text-ui-sm text-text-tertiary">Not mentioned</span>
        )}
      </span>
    </div>
  );
}

/**
 * robots.txt could not be read — which is not the same as it permitting
 * everything, and must not render as a permissive grid.
 *
 * The house rule Epics A, B and E all landed on: a state where the
 * measurement was never taken is never the same as a measurement that came
 * back clean.
 */
function Unreadable({ url }: { url: string }): JSX.Element {
  /*
   * Card + Badge, NOT a tinted panel.
   *
   * The first draft drew this as `border-warn/40 bg-warn/5`, which was wrong
   * twice over and is the same defect as Epic E's dead colour utility on the
   * Alerts screen (named in that epic's build-log entry — it is not spelled
   * here, because `reportIsolation.test.ts` greps raw source and a mention in
   * a comment fails it exactly like a real use, which is the right trade for a
   * guard whose whole job is to be unmissable):
   * `warn` has never been used as a surface anywhere in this product — it is a
   * Badge tone and nothing else — and the palette's colours are bare
   * `var(--avp-*)` with no `<alpha-value>`, so `/40` and `/5` cannot compile
   * to anything at all. It would have rendered as an untinted, unbordered
   * block that looked deliberate.
   */
  return (
    <Card>
      <CardBody>
        <div className="flex flex-col gap-2">
          <span>
            <Badge tone="warn">Not readable</Badge>
          </span>
          <p className="max-w-measure text-ui-base text-text-primary">
            This site’s <code className="text-ui-sm">robots.txt</code> could not
            be read when this scan ran, so nothing is known about its crawler
            policy either way.
          </p>
          <p className="max-w-measure text-ui-sm text-text-secondary">
            A missing file usually does mean every crawler is permitted — but
            that is an inference, not a reading, and it is not reported here as
            one. Check <code className="text-ui-sm">{url}/robots.txt</code>{' '}
            directly.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

/**
 * Every agent unspecified — the most common real state, and a finding rather
 * than an absence.
 *
 * Measured on the live client domains: most name no AI crawler at all. That is
 * not "nothing to report", it is "this site has made no decision about any of
 * them", which is precisely what an agency is there to point out.
 */
function NoPolicyNote(): JSX.Element {
  return (
    <p className="max-w-measure text-ui-sm leading-prose text-text-secondary">
      No rule on this site names a single AI crawler. Every one of them may
      crawl it, by default rather than by decision — so there is nothing here to
      undo, and nothing on record that anyone chose.
    </p>
  );
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** No scan has ever recorded a policy — every scan predating this feature. */
function NeverRead(): JSX.Element {
  return (
    <EmptyState
      eyebrow="AI crawlers"
      title="No crawler policy recorded yet"
      body="A scan reads this site’s robots.txt as part of its technical audit. None of this client’s scans has done so yet, so there is nothing to show — which is not a finding about the site."
      action={
        <Button variant="primary" onClick={() => window.location.assign('/')}>
          Run a scan
        </Button>
      }
    />
  );
}
