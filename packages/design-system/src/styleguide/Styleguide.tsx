import { useState, type JSX, type ReactNode } from 'react';
import { ArrowRight, Download, Search, Trash2 } from 'lucide-react';
import {
  Button,
  Card,
  CardHeader,
  CardTitle,
  CardBody,
  CardFooter,
  Badge,
  VisibilityBadge,
  DataTable,
  ScoreDisplay,
  LuminanceLedger,
  AnswerShelf,
  TrendChart,
  ChartPatterns,
  ReportPage,
  ReportHeader,
  Beat,
  Prose,
  Evidence,
  FixList,
  layoutLedger,
  paper,
  ink,
  visibility,
  beacon,
  competitor,
  semantic,
  oklch,
  visibilityColor,
  onVisibility,
  fontSizeUi,
  fontSizeEditorial,
  space,
  elevation,
} from '../index.js';
import {
  SUBJECT,
  DIMENSIONS,
  COMPETITORS,
  COMPARISON_ROWS,
  EVIDENCE,
  FIXES,
  SHELF_ROWS,
  TREND_POINTS,
  TREND_SERIES,
  type CompetitorRow,
} from './fixtures.js';
import type {
  ShelfRowInput,
  ShelfSlotInput,
} from '../components/chart/answerShelfLayout.js';

export function Styleguide(): JSX.Element {
  const [dark, setDark] = useState(false);
  const layout = layoutLedger(DIMENSIONS, { competitors: COMPETITORS });

  return (
    <div className="sg" data-theme={dark ? 'dark' : undefined}>
      <header className="sg__masthead">
        <div>
          <h1 className="sg__title">Design System</h1>
          <p className="sg__lede">
            The proprietary component and token library for the AI Visibility &amp; Competitive
            Intelligence Platform. Every customer-facing surface is built from these primitives.
          </p>
        </div>
        <div className="sg-toggle">
          <Button size="sm" variant={dark ? 'primary' : 'secondary'} onClick={() => setDark((d) => !d)}>
            {dark ? 'Dark' : 'Light'}
          </Button>
        </div>
      </header>

      {/* ============================================================ */}
      <Section
        num="01"
        title="Colour — Lit / Unlit"
        note="The product measures one thing: presence or absence in an AI answer. So the organising metaphor is literal — visibility is luminance. Dim and desaturated means invisible; bright and saturated means cited."
      >
        <p className="sg-sub">Neutrals — warm paper, cool ink</p>
        <div className="sg-grid">
          {Object.entries(paper).map(([k, v]) => (
            <Swatch key={k} name={`paper-${k}`} value={oklch(v)} />
          ))}
          {Object.entries(ink).map(([k, v]) => (
            <Swatch key={k} name={`ink-${k}`} value={oklch(v)} />
          ))}
        </div>
        <p className="sg-section__note">
          The light end is warm (hue 75) and the dark end is cool (hue 265). Warm dark greys print
          muddy; cool dark greys read like ink on paper. Nobody notices consciously; everybody feels it.
        </p>

        <p className="sg-sub">Visibility ramp — fill only, never text</p>
        <div className="sg-ramp">
          {[0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100].map((s) => (
            <div
              key={s}
              className="sg-ramp__step"
              style={{ background: visibilityColor(s), color: onVisibility(s) }}
            >
              {s}
            </div>
          ))}
        </div>
        <p className="sg-section__note">
          Monotonic in lightness, so it survives greyscale printing — the traffic-light ramp does not,
          because red and green resolve to nearly the same grey. The warm-to-cool traverse is also the
          axis that colour-vision-deficient viewers reliably keep.
        </p>
        <div className="sg-grid" style={{ marginTop: 'var(--avp-space-4)' }}>
          {Object.entries(visibility).map(([k, v]) => (
            <Swatch key={k} name={`vis-${k}`} value={oklch(v)} />
          ))}
        </div>

        <p className="sg-sub">Brand accent — the client is always beacon</p>
        <div className="sg-grid">
          {Object.entries(beacon).map(([k, v]) => (
            <Swatch key={k} name={`beacon-${k}`} value={oklch(v)} />
          ))}
        </div>

        <p className="sg-sub">Competitor series — neutral by rule</p>
        <div className="sg-grid">
          {Object.entries(competitor).map(([k, v]) => (
            <Swatch key={k} name={`competitor-${k}`} value={oklch(v)} />
          ))}
        </div>
        <p className="sg-section__note">
          Competitors never take a ramp colour. Rendering a competitor in &ldquo;good green&rdquo;
          implies an endorsement; rendering one in &ldquo;bad red&rdquo; makes the report read as a
          hatchet job and costs it credibility with the client. They are separated by lightness and
          fill pattern instead, which also keeps them distinguishable in black and white.
        </p>

        <p className="sg-sub">Semantics — system state only, never score values</p>
        <div className="sg-grid">
          {Object.entries(semantic).map(([k, v]) => (
            <Swatch key={k} name={k} value={oklch(v)} />
          ))}
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="02"
        title="Typography"
        note="Three faces, three jobs, all OFL-licensed. Fraunces carries the argument, IBM Plex Sans runs the interface, IBM Plex Mono marks verbatim machine output."
      >
        <p className="sg-sub">Editorial track — ratio 1.25</p>
        {Object.entries(fontSizeEditorial).map(([k, v]) => (
          <div className="sg-type-row" key={k}>
            <span className="sg-type-row__key">{`ed-${k} · ${v}`}</span>
            <span
              style={{
                fontFamily: 'var(--avp-font-editorial)',
                fontSize: v,
                fontWeight: 600,
                letterSpacing: 'var(--avp-tracking-display)',
                color: 'var(--avp-text-primary)',
              }}
            >
              Invisible in the answer
            </span>
          </div>
        ))}

        <p className="sg-sub">Interface track — ratio 1.125</p>
        {Object.entries(fontSizeUi).map(([k, v]) => (
          <div className="sg-type-row" key={k}>
            <span className="sg-type-row__key">{`ui-${k} · ${v}`}</span>
            <span style={{ fontSize: v, color: 'var(--avp-text-body)' }}>
              Share of Voice 22% · 1,284 citations tracked
            </span>
          </div>
        ))}
        <p className="sg-section__note">
          Two ratios, on purpose. Interface work needs 13px and 14px to be genuinely different things;
          editorial work needs headlines that leap across a conference table. One ratio over both makes
          UI text bloated or headlines timid.
        </p>

        <p className="sg-sub">Evidence — monospace</p>
        <div className="sg-type-row">
          <span className="sg-type-row__key">mono · ui-sm</span>
          <span style={{ fontFamily: 'var(--avp-font-mono)', fontSize: 'var(--avp-text-ui-sm)' }}>
            best family dentist in the northaven area
          </span>
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="03"
        title="Spacing & rhythm"
        note="A 4px base with a non-linear tail: fine-grained where components need it, jumping where sections need it."
      >
        {Object.entries(space)
          .filter(([k]) => k !== '0' && k !== 'px')
          .map(([k, v]) => (
            <div className="sg-space-row" key={k}>
              <span className="sg-type-row__key">{`space-${k} · ${v}`}</span>
              <span className="sg-space-row__bar" style={{ width: v }} />
            </div>
          ))}
        <p className="sg-section__note">
          Two extra tokens matter more than the ramp: <code>--avp-rhythm</code> (8px) is the grid the
          report&rsquo;s narrative beats snap to, and <code>--avp-beat</code> (72px) is the gap between
          beats. Because <code>beat</code> exceeds any spacing used inside a beat, the five-part
          structure of a report is legible from the page thumbnail.
        </p>
      </Section>

      {/* ============================================================ */}
      <Section
        num="04"
        title="Elevation — paper doesn't float"
        note="Elevation is borders and hard offsets, not blurred halos. Blurred shadows vanish when a report is printed, and this product's main artifact is a PDF someone reads on paper."
      >
        <div className="sg-elev">
          {(Object.keys(elevation) as (keyof typeof elevation)[]).map((k) => (
            <div key={k} className="sg-elev__box" style={{ boxShadow: elevation[k] }}>
              <strong>{k}</strong>
              <br />
              <span style={{ color: 'var(--avp-text-tertiary)', fontSize: 'var(--avp-text-ui-xs)' }}>
                {k === 'flat' || k === 'seated' || k === 'raised' ? 'prints correctly' : 'transient UI only'}
              </span>
            </div>
          ))}
        </div>

        <p className="sg-sub">Emphasis is light, not lift</p>
        <div className="sg-row">
          <Card elevation="flat" selected>
            <CardBody>Selected — lit from within</CardBody>
          </Card>
          <Button variant="secondary">Tab to me for the focus ring</Button>
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="05"
        title="Buttons"
        note="Hover darkens rather than raising. Focus is a beacon ring and halo. Icons are Lucide (ISC) or custom-drawn."
      >
        <p className="sg-sub">Variants</p>
        <div className="sg-row">
          <Button variant="primary">Run scan</Button>
          <Button variant="secondary">Edit competitors</Button>
          <Button variant="ghost">Cancel</Button>
          <Button variant="danger" iconStart={<Trash2 />}>Delete scan</Button>
        </div>

        <p className="sg-sub">Sizes</p>
        <div className="sg-row">
          <Button size="sm" variant="primary">Small</Button>
          <Button size="md" variant="primary">Medium</Button>
          <Button size="lg" variant="primary" iconEnd={<ArrowRight />}>Large</Button>
        </div>

        <p className="sg-sub">With icons, and disabled</p>
        <div className="sg-row">
          <Button variant="secondary" iconStart={<Search />}>Find competitors</Button>
          <Button variant="primary" iconStart={<Download />}>Export PDF</Button>
          <Button variant="primary" disabled>Scanning…</Button>
        </div>
      </Section>

      {/* ============================================================ */}
      <Section num="06" title="Cards & badges" note="Cards separate by tone and hairline. Badges carry system state — never a score.">
        <div className="sg-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {(['flat', 'seated', 'raised'] as const).map((e) => (
            <Card key={e} elevation={e}>
              <CardHeader>
                <CardTitle>{`Elevation: ${e}`}</CardTitle>
              </CardHeader>
              <CardBody>Scan completed 4 hours ago across 28 prompts and 2 engines.</CardBody>
              <CardFooter>
                <Button size="sm" variant="ghost">Dismiss</Button>
                <Button size="sm" variant="primary">Open</Button>
              </CardFooter>
            </Card>
          ))}
        </div>

        <p className="sg-sub">Badges — system state</p>
        <div className="sg-row">
          <Badge tone="neutral">Queued</Badge>
          <Badge tone="beacon">Re-scan scheduled</Badge>
          <Badge tone="success">Scan complete</Badge>
          <Badge tone="warn">Quota 85% used</Badge>
          <Badge tone="danger">Engine unavailable</Badge>
        </div>

        <p className="sg-sub">Visibility badges — the ordinal read of a score</p>
        <div className="sg-row">
          {[8, 28, 48, 68, 92].map((s) => (
            <VisibilityBadge key={s} score={s} />
          ))}
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="07"
        title="Tables"
        note="Tabular figures, right-aligned numerics, hairline rules. The subject row is accented with the brand colour and marked with aria-current; competitor rows stay neutral."
      >
        <DataTable<CompetitorRow>
          caption="Composite score and supporting metrics across the detected competitor set"
          rows={COMPARISON_ROWS}
          rowKey={(r) => r.name}
          isSubject={(r) => r.isSubject}
          columns={[
            { key: 'name', header: 'Brand', render: (r) => r.name },
            {
              key: 'score',
              header: 'Score',
              align: 'end',
              render: (r) => <VisibilityBadge score={r.score} />,
            },
            { key: 'mention', header: 'Mention rate', align: 'end', render: (r) => `${r.mentionRate}%` },
            { key: 'citations', header: 'Cited domains', align: 'end', render: (r) => r.citations },
          ]}
        />

        <p className="sg-sub">Empty state</p>
        <DataTable<CompetitorRow>
          rows={[]}
          rowKey={(r) => r.name}
          emptyMessage="No competitors detected yet. Run a scan to populate this table."
          columns={[
            { key: 'name', header: 'Brand', render: (r) => r.name },
            { key: 'score', header: 'Score', align: 'end', render: (r) => r.score },
          ]}
        />
      </Section>

      {/* ============================================================ */}
      <Section
        num="08"
        title="The Luminance Ledger — signature visualisation"
        note="The score, drawn as light. Each sub-score is a segment whose height is its weight (points available) and whose lit portion is its value (points earned). The unearned remainder is left as a dim void."
      >
        <div className="sg-panel">
          <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
            <ChartPatterns />
          </svg>
          <LuminanceLedger
            subjectName={SUBJECT}
            dimensions={DIMENSIONS}
            competitors={COMPETITORS}
          />
        </div>

        <p className="sg-section__note">
          Because segment height tracks weight and the lit fraction tracks value, the total lit height
          of the column is <em>exactly</em> the composite score — the chart is the number, not a picture
          of it. That identity is asserted in <code>ledgerLayout.test.ts</code> rather than trusted.
          The dashed region marks the largest recoverable point gain, and that annotation is what the
          report&rsquo;s &ldquo;biggest gap&rdquo; headline is generated from:{' '}
          <strong>{layout.biggestGap?.label}</strong>, worth{' '}
          <strong>{layout.biggestGap?.gap.toFixed(1)} points</strong>.
        </p>
        <p className="sg-section__note">
          Explicitly not a gauge and not a radar chart. A gauge discards the per-dimension structure
          that makes a score actionable; a radar implies the axes are commensurable and equally
          weighted, which under the §6 formula they are not.
        </p>

        <p className="sg-sub">Without competitors, and at a smaller size</p>
        <div className="sg-split">
          <div className="sg-panel">
            <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} height={280} animate={false} />
          </div>
          <div className="sg-panel">
            <p className="sg-sub" style={{ marginTop: 0 }}>Insufficient data</p>
            <LuminanceLedger subjectName={SUBJECT} dimensions={[]} />
            <p className="sg-section__note">
              A scan with no prompts renders as INSUFFICIENT_DATA, never as a zero. An unrunnable scan
              must not be shown to a client as a bad score.
            </p>
          </div>
        </div>

        <p className="sg-sub">Score display</p>
        <div className="sg-row" style={{ gap: 'var(--avp-space-14)' }}>
          <ScoreDisplay score={layout.composite} />
          <ScoreDisplay score={91} label="Competitor A" />
          <ScoreDisplay score={null} />
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="09"
        title="The Answer Shelf — the proof beat"
        note="An AI answer has a limited number of slots. Who is standing in them? One row per answer, brands in the order the engine named them. The subject is the beacon marker; rivals are neutral slate with a print pattern; a citation in that same answer hangs as a tick beneath."
      >
        <div className="sg-panel">
          <AnswerShelf
            subjectName={SUBJECT}
            rows={SHELF_ROWS}
            title="Where this practice stands in the answers buyers see"
            caption="Each row is one answer. A dashed ring is an answer that named other practices and not this one."
          />
        </div>

        <p className="sg-section__note">
          The load-bearing rule: <strong>every answered row carries exactly one subject mark</strong> —
          a filled marker at the ordinal the answer gave it, or an explicit empty notch. Never nothing.
          Stack the rows and the notches line up into a vertical band, so you see <em>the shape of
          absence</em> before reading a word. A row that silently rendered nothing when the subject was
          missing would not look like a bug; it would look like a clean report. That identity is
          asserted in <code>answerShelfLayout.test.ts</code> rather than trusted.
        </p>
        <p className="sg-section__note">
          The notch sits in a fixed column rather than at a guessed ordinal, for two reasons: a brand
          that was not named <em>has</em> no ordinal, and inventing one would state a fact the answer
          never gave; and a fixed column is what makes the absences align. The last row shows an
          engine that never answered — drawn as a dash, not a hole, because we have no answer to be
          absent from and a hole there would blame the client for our own failed request.
        </p>
        <p className="sg-section__note">
          Rivals are never painted from the visibility ramp. A rival in &ldquo;good green&rdquo; reads
          as an endorsement; one in &ldquo;bad red&rdquo; makes the report look like a hatchet job and
          costs it credibility with the client&rsquo;s CMO — the one thing the report cannot afford.
          Each rival keeps the same slate shade in every row, so a brand never changes colour down
          the page.
        </p>

        <p className="sg-sub">A scan where the subject is never named</p>
        <div className="sg-panel">
          <AnswerShelf
            subjectName={SUBJECT}
            rows={SHELF_ROWS.filter((row) => row.answered).map((row): ShelfRowInput => ({
              ...row,
              subjectPresent: false,
              subjectPosition: null,
              subjectCited: false,
              slots: row.slots.filter((slot: ShelfSlotInput) => !slot.isSubject),
            }))}
            caption="The band of holes is the finding."
          />
        </div>
      </Section>

      {/* ============================================================ */}
      <Section
        num="09b"
        title="The Trend — a client across its own scan history"
        note="The first time-series shape in this system. One line per series, oldest scan on the left. The client is always the beacon; rivals are neutral slate separated by dash rather than hue, exactly as the Ledger's ghost columns are."
      >
        <div className="sg-panel">
          <TrendChart
            points={TREND_POINTS}
            series={TREND_SERIES}
            unit="%"
            yMax={100}
            title="Share of voice"
            caption="A rival's rise is the client's fall — the lines sum across the field."
            ariaLabel="Share of voice across four scans."
          />
        </div>

        <p className="sg-section__note">
          <strong>A gap is not a zero.</strong> A competitor set is re-detected per scan, so a rival
          can be present, absent, then present again. Joining through zero would assert a collapse
          nobody measured; dropping the line would show fewer rivals than the client has, silently.
          The line breaks instead, and the hidden data table reads <em>&ldquo;not measured&rdquo;</em>.
          Two rivals above do exactly this.
        </p>
        <p className="sg-sub">The same chart, on a Working screen</p>
        <div className="sg-panel">
          <TrendChart
            points={TREND_POINTS}
            series={TREND_SERIES}
            unit="%"
            yMax={100}
            palette="working"
            title="Share of voice — Working palette"
            caption="Identical layout, identical data table, identical dashes. Only the competitor hues differ."
            ariaLabel="Share of voice across four scans, drawn in the Working-screen palette."
          />
        </div>

        <p className="sg-section__note">
          <strong>One chart, two contexts.</strong> §0 splits every screen into
          Presenting and Working, and this is the first component to render
          differently for each. The report gets the panel above: competitors in
          neutral slate, because a rival drawn in a hue reads as a judgement in
          front of a CMO. An operator gets this one, because eight neutral greys
          at 1.5px are genuinely hard to follow and nobody is being pitched to.
          The client stays <code>beacon-600</code> in both — one brand, one
          colour — and the dash patterns are unconditional, so greyscale and
          colour-blind reading survive the richer palette. It is a{' '}
          <code>palette</code> prop on one component, defaulting to{' '}
          <code>report</code>: a chart added to the document tomorrow is
          restrained by omission rather than by anybody remembering.
        </p>

        <p className="sg-section__note">
          <strong>It cannot render larger than it was drawn.</strong> An SVG at{' '}
          <code>width: 100%</code> over a fixed viewBox scales its <em>type</em> with its box —
          measured live in Epic 9.21, a 720-unit chart stretched across a 1200px column ran at 1.6x
          and rendered its 11px axis labels at 17.6px, larger than the body copy beside them. The
          figure is bounded at <code>layout.width</code>, so one unit is at most one pixel. Widen
          this window: the chart stops growing, and the labels stay the size they are here.
        </p>
      </Section>

      {/* ============================================================ */}
      <Section
        num="10"
        title="Report primitives — narrative, not dashboard"
        note="Primary screens are structured as an argument: score, biggest gap, proof, fix, pitch. These primitives make that structure the path of least resistance, and the spacing between beats exceeds any spacing inside them so the shape is visible at a glance."
      >
        <div className="sg-panel" style={{ padding: 0 }}>
          <ReportPage
            brand={
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontSize: 'var(--avp-text-ui-md)' }}>[ Agency logo ]</strong>
                <span style={{ fontSize: 'var(--avp-text-ui-xs)', color: 'var(--avp-text-tertiary)' }}>
                  White-label surface — Epic 7
                </span>
              </div>
            }
          >
            <ReportHeader
              subject={SUBJECT}
              subtitle="How this practice appears when buyers ask AI assistants for a recommendation."
              meta={
                <>
                  <span>28 prompts</span>
                  <span>2 engines</span>
                  <span>3 competitors</span>
                  <span>Scanned 20 Aug 2026</span>
                </>
              }
            />

            <Beat id="score" heading="You appear in fewer than half the answers buyers see.">
              <div className="sg-split">
                <ScoreDisplay score={layout.composite} />
                <Prose>
                  <p>
                    Across the tracked prompt set, this practice is named in a minority of answers, and
                    is rarely the source those answers cite. The score below is the weighted composite
                    of five measured dimensions.
                  </p>
                </Prose>
              </div>
            </Beat>

            <Beat
              id="gap"
              heading={`${layout.biggestGap?.label} is costing the most — ${layout.biggestGap?.gap.toFixed(1)} points.`}
            >
              <LuminanceLedger
                subjectName={SUBJECT}
                dimensions={DIMENSIONS}
                competitors={COMPETITORS}
                height={340}
                animate={false}
              />
            </Beat>

            <Beat id="proof" heading="Here is what the engines actually returned.">
              <Prose>
                <p>
                  Each entry records the prompt that was asked and the structured facts extracted from
                  the answer. Engine prose is never stored or reproduced.
                </p>
              </Prose>
              <div style={{ marginTop: 'var(--avp-space-5)' }}>
                {EVIDENCE.map((e) => (
                  <Evidence key={e.prompt} engine={e.engine} prompt={e.prompt} findings={e.findings} />
                ))}
              </div>
            </Beat>

            <Beat id="fix" heading="Three changes recover most of the gap.">
              <FixList items={FIXES} />
            </Beat>

            <Beat id="pitch" heading="What this is worth over the next two quarters.">
              <Prose>
                <p>
                  The fixes above target the two dimensions carrying the largest recoverable point
                  totals. This beat is where the agency&rsquo;s own commercial framing goes — generated
                  in Epic 8 and white-labelled in Epic 7.
                </p>
              </Prose>
              <div className="sg-row" style={{ marginTop: 'var(--avp-space-5)' }}>
                <Button variant="primary" iconEnd={<ArrowRight />}>Generate proposal</Button>
                <Button variant="secondary" iconStart={<Download />}>Export PDF</Button>
              </div>
            </Beat>
          </ReportPage>
        </div>
      </Section>
    </div>
  );
}

function Section({
  num,
  title,
  note,
  children,
}: {
  num: string;
  title: string;
  note: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="sg-section">
      <p className="sg-section__num">{num}</p>
      <h2 className="sg-section__title">{title}</h2>
      <p className="sg-section__note">{note}</p>
      <div className="sg-section__body">{children}</div>
    </section>
  );
}

function Swatch({ name, value }: { name: string; value: string }): JSX.Element {
  return (
    <div className="sg-swatch">
      <div className="sg-swatch__chip" style={{ background: value }} />
      <div className="sg-swatch__meta">
        <div className="sg-swatch__name">{name}</div>
        <div className="sg-swatch__value">{value}</div>
      </div>
    </div>
  );
}
