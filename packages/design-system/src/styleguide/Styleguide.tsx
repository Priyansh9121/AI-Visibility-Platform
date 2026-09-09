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
  ScoreHero,
  StatRow,
  StatTile,
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
  signal,
  dark,
  competitor,
  semantic,
  oklch,
  visibilityColor,
  visibilityColorDark,
  onVisibility,
  heroGradient,
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
  // DARK IS THE DEFAULT — Epic 14. The toggle shows the paper scope the report
  // renders in; it is the report's skin, not a mode of the product.
  const [paper, setPaper] = useState(false);
  const layout = layoutLedger(DIMENSIONS, { competitors: COMPETITORS });

  return (
    <div className="sg" data-theme={paper ? 'light' : undefined}>
      <header className="sg__masthead">
        <div>
          <h1 className="sg__title">Design System</h1>
          <p className="sg__lede">
            The proprietary component and token library for the AI Visibility &amp; Competitive
            Intelligence Platform. Dark, data-dense, and built from the five scoring dimensions.
            The paper scope below is what the printed report keeps.
          </p>
        </div>
        <div className="sg-toggle">
          <Button size="sm" variant={paper ? 'primary' : 'secondary'} onClick={() => setPaper((d) => !d)}>
            {paper ? 'Paper (the report)' : 'Dark (the product)'}
          </Button>
        </div>
      </header>

      {/* ============================================================ */}
      <Section
        num="00"
        title="The hero — the score, drawn as light, at the top of a screen"
        note="The first thing on the dashboard and on a client's Overview. The figure is a visibility score; its gradient and its glow are derived from the ramp at that score, so a low score reads dim and warm and a high one reads lit and cool."
      >
        <div className="sg-stack">
          <ScoreHero
            label="Latest score"
            score={layout.composite}
            delta={4.2}
            meta={<span>Scanned 6 Sep 2026 · 24 prompts · 3 engines</span>}
            aside={
              <LuminanceLedger
                subjectName={SUBJECT}
                dimensions={DIMENSIONS}
                height={220}
                bounded
                annotateGap={false}
                palette="working"
                animate={false}
              />
            }
          />
          <div className="sg-split">
            <ScoreHero label="Portfolio visibility" score={14} meta={<span>Across 3 scored scans</span>} />
            <ScoreHero label="Latest score" score={null} absence="Not scored yet" meta={<span>No scan has produced a reading.</span>} />
          </div>
        </div>
        <p className="sg-sub">Hero gradients, by score</p>
        <div className="sg-ramp">
          {[5, 20, 35, 50, 65, 80, 95].map((s) => (
            <div key={s} className="sg-ramp__step" style={{ background: heroGradient(s), color: 'var(--avp-ink-900)' }}>
              {s}
            </div>
          ))}
        </div>

        <p className="sg-sub">Metric tiles — a card grid</p>
        <StatRow>
          <StatTile label="Clients" value="12" accent={1} />
          <StatTile label="Scans" value="47" accent={2} />
          <StatTile label="Running now" value="1" accent={3} note="This page is updating itself." />
          <StatTile label="Needs attention" value="2" accent={4} emphasis note="Failed or partial." />
          <StatTile label="Median visibility" value="41" note="Across 9 scored scans." />
        </StatRow>
        <p className="sg-section__note">
          A tile is the same surface a card is: seated, 16px corners, lit along its top edge, one tone
          brighter under the pointer. The accent is a dot beside the label — a category, never a hue
          around the value. A score takes no accent at all; the ramp is already its colour language.
        </p>
      </Section>

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

        <p className="sg-sub">The dark ground — Epic 14</p>
        <div className="sg-grid">
          {Object.entries(dark.surface).map(([k, v]) => (
            <Swatch key={k} name={`surface-${k}`} value={oklch(v)} />
          ))}
          {Object.entries(dark.text).map(([k, v]) => (
            <Swatch key={k} name={`text-${k}`} value={oklch(v)} />
          ))}
        </div>
        <p className="sg-section__note">
          Deep cool charcoal (hue 265), never pure black, with warm text (hue 75): the paper
          system&rsquo;s warm-light / cool-dark hand-off, inverted rather than abandoned.
        </p>

        <p className="sg-sub">The dark ramp — same five hues, lifted for near-black</p>
        <div className="sg-ramp">
          {[0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100].map((s) => (
            <div
              key={s}
              className="sg-ramp__step"
              style={{ background: visibilityColorDark(s), color: onVisibility(s) }}
            >
              {s}
            </div>
          ))}
        </div>

        <p className="sg-sub">Brand accent — the client is always beacon. Dark stops, then paper.</p>
        <div className="sg-grid">
          {Object.entries(dark.beacon).map(([k, v]) => (
            <Swatch key={k} name={`beacon-${k} (dark)`} value={oklch(v)} />
          ))}
          {Object.entries(beacon).map(([k, v]) => (
            <Swatch key={k} name={`beacon-${k} (paper)`} value={oklch(v)} />
          ))}
        </div>

        <p className="sg-sub">Signal — beacon's gradient partner, and nothing else</p>
        <div className="sg-grid">
          {Object.entries(dark.signal).map(([k, v]) => (
            <Swatch key={k} name={`signal-${k}`} value={oklch(v)} />
          ))}
          <div className="sg-swatch">
            <div className="sg-swatch__chip" style={{ background: 'var(--avp-gradient-accent)' }} />
            <div className="sg-swatch__meta">
              <div className="sg-swatch__name">gradient-accent</div>
              <div className="sg-swatch__value">signal-600 → beacon-600</div>
            </div>
          </div>
          {Object.entries(signal).map(([k, v]) => (
            <Swatch key={k} name={`signal-${k} (paper)`} value={oklch(v)} />
          ))}
        </div>
        <p className="sg-section__note">
          Two accents, both saturated, carrying real weight: the primary action and the glow behind a
          hero are the only places signal appears. It never encodes a value, a category or a state on
          its own.
        </p>

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
        note="Four faces, four jobs, all OFL-licensed. Space Grotesk is the product's voice on every Working screen; Fraunces is the report's, and only the report's; IBM Plex Sans runs the interface; IBM Plex Mono marks verbatim machine output."
      >
        <p className="sg-sub">Display — Space Grotesk, on the editorial sizes</p>
        {Object.entries(fontSizeEditorial).map(([k, v]) => (
          <div className="sg-type-row" key={k}>
            <span className="sg-type-row__key">{`ed-${k} · ${v}`}</span>
            <span
              style={{
                fontFamily: 'var(--avp-font-display)',
                fontSize: v,
                fontWeight: 700,
                letterSpacing: 'var(--avp-tracking-display)',
                color: 'var(--avp-text-primary)',
              }}
            >
              Invisible in the answer 47
            </span>
          </div>
        ))}
        <p className="sg-section__note">
          A geometric grotesk with genuine character in its figures — the flat-based 1, the open 4,
          the squared 0 — and true tabular figures, so a row of KPI tiles aligns. Deliberately not
          Inter, and deliberately not the serif below.
        </p>

        <p className="sg-sub">Editorial — Fraunces, the report only</p>
        {(['xs', 'md', 'xl'] as const).map((k) => (
          <div className="sg-type-row" key={k}>
            <span className="sg-type-row__key">{`ed-${k} · ${fontSizeEditorial[k]}`}</span>
            <span
              style={{
                fontFamily: 'var(--avp-font-editorial)',
                fontSize: fontSizeEditorial[k],
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
        title="Elevation — a card sits in the dark, lit along its top edge"
        note="A raised surface is a plate with light on it: a 1px inner highlight, a shadow tinted to the ground, a lighter tone. The report keeps Epic 0's borders-and-offsets set through its paper scope, because blurred shadows vanish in print."
      >
        <div className="sg-elev">
          {(Object.keys(elevation) as (keyof typeof elevation)[]).map((k) => (
            <div key={k} className="sg-elev__box" style={{ boxShadow: elevation[k] }}>
              <strong>{k}</strong>
              <br />
              <span style={{ color: 'var(--avp-text-tertiary)', fontSize: 'var(--avp-text-ui-xs)' }}>
                {k === 'flat' || k === 'seated' || k === 'raised' ? 'a surface' : 'transient UI only'}
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
        note="The primary action carries the accent gradient with dark text; secondary lifts one tone and takes a beacon edge on hover. Focus is a beacon ring and halo. Buttons and inputs take the shape lock's 8px; cards take 16px; chips are pills."
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
      <Section num="06" title="Cards & badges" note="A card is a seated surface with 16px corners and a lit top edge; raised adds a ground-tinted shadow. Badges carry system state — never a score.">
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
            palette="working"
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

        <p className="sg-sub">Compact, in a grid — and partial, for a rival</p>
        <div className="sg-row" style={{ gap: 'var(--avp-space-6)', alignItems: 'flex-start' }}>
          <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} compact animate={false} />
          {COMPETITORS.map((c) => (
            <LuminanceLedger
              key={c.name}
              subjectName={c.name}
              dimensions={DIMENSIONS.map((d) =>
                d.key === 'sentiment' || d.key === 'technical'
                  ? { ...d, subscore: 0, measured: false }
                  : (c.dimensions.find((x) => x.key === d.key) ?? d)
              )}
              compact
              animate={false}
            />
          ))}
        </div>
        <p className="sg-section__note">
          The same identity at a third of the width, with the label gutter gone: a grid of columns
          carries one legend beside it rather than five labels per column. A rival is measured on
          three of the five dimensions — sentiment is classified toward the subject only and the
          technical audit is of the subject&rsquo;s own site — so a rival&rsquo;s column keeps the
          subject&rsquo;s five-segment shape, hatches the two nobody measured, and makes{' '}
          <em>no</em> composite claim: no number is spoken for it, and its table says so. A rival
          composite over 75% of the weight would draw every rival shorter than they are, which is
          the weight-basis error the ghost columns above also avoid by being outlines.
        </p>

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
