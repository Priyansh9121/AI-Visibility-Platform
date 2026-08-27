import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Button } from './Button.js';
import { Card, CardBody } from './Card.js';
import { Badge, VisibilityBadge } from './Badge.js';
import { DataTable } from './Table.js';
import { ScoreDisplay } from './ScoreDisplay.js';
import { ScoreMeter } from './ScoreMeter.js';
import { LuminanceLedger } from './chart/LuminanceLedger.js';
import { AnswerShelf } from './chart/AnswerShelf.js';
import type { ShelfRowInput } from './chart/answerShelfLayout.js';
import { Beat, Evidence, ReportPage, BEAT_SEQUENCE } from './report/ReportLayout.js';
import { visibility, beacon, oklch } from '../tokens/color.js';
import { DIMENSIONS, COMPETITORS, SUBJECT } from '../styleguide/fixtures.js';

const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

describe('components render', () => {
  it('Button renders every variant without crashing', () => {
    for (const variant of ['primary', 'secondary', 'ghost', 'danger'] as const) {
      expect(html(<Button variant={variant}>Go</Button>)).toContain(`avp-btn--${variant}`);
    }
  });

  it('Card exposes only print-safe elevations', () => {
    for (const e of ['flat', 'seated', 'raised'] as const) {
      expect(html(<Card elevation={e}><CardBody>x</CardBody></Card>)).toContain(`avp-card--${e}`);
    }
  });

  it('Badge and VisibilityBadge render distinct palettes', () => {
    expect(html(<Badge tone="danger">Engine down</Badge>)).toContain('avp-badge--danger');
    const vb = html(<VisibilityBadge score={12} />);
    expect(vb).toContain('Absent');
    expect(vb).toContain('avp-badge--visibility');
  });

  it('DataTable marks the subject row with aria-current', () => {
    const out = html(
      <DataTable
        rows={[{ n: 'Ours' }, { n: 'Other' }]}
        rowKey={(r) => r.n}
        isSubject={(r) => r.n === 'Ours'}
        columns={[{ key: 'n', header: 'Name', render: (r) => r.n }]}
      />,
    );
    expect(out).toContain('aria-current="true"');
    expect(out.match(/aria-current/g)).toHaveLength(1);
  });

  it('DataTable renders its empty state rather than an empty tbody', () => {
    const out = html(
      <DataTable
        rows={[]}
        rowKey={(r: { n: string }) => r.n}
        emptyMessage="Nothing yet"
        columns={[{ key: 'n', header: 'Name', render: (r) => r.n }]}
      />,
    );
    expect(out).toContain('Nothing yet');
  });

  it('ScoreDisplay shows an em dash, not a zero, for a null score', () => {
    const out = html(<ScoreDisplay score={null} />);
    expect(out).toContain('—');
    expect(out).toContain('Not enough data to score this scan');
  });
});

describe('ScoreMeter carries the Ledger identity at list scale', () => {
  it('lit LENGTH is the score, not a rounded approximation of it', () => {
    // The Ledger's correctness condition is that lit height IS the composite.
    // This is that property at one dimension: the numeral rounds so it reads,
    // the BAR keeps the real value.
    const out = html(<ScoreMeter score={38.35} />);
    expect(out).toContain('width:38.35%');
    expect(out).toContain('>38<');
  });

  it('width tracks the value across the ramp, so the bar cannot be decorative', () => {
    // A meter that ignored its input would still render a bar and still pass a
    // test that only asserted a bar exists.
    for (const [score, width] of [[0, '0%'], [25, '25%'], [100, '100%']] as const) {
      expect(html(<ScoreMeter score={score} />)).toContain(`width:${width}`);
    }
  });

  it('clamps out-of-range input rather than overflowing the track', () => {
    expect(html(<ScoreMeter score={140} />)).toContain('width:100%');
    expect(html(<ScoreMeter score={-20} />)).toContain('width:0%');
  });

  it('a null score renders an EMPTY track and names the absence', () => {
    // Never a zero-width fill, which would read as a score of 0, and never a
    // bare dash, which reads as a rendering fault.
    const out = html(<ScoreMeter score={null} absence="unscored" />);
    expect(out).toContain('—');
    expect(out).toContain('Not scored');
    expect(out).toContain('avp-meter--empty');
    expect(out).not.toContain('avp-meter__lit');
  });

  it('distinguishes "no score yet" from "no score at all"', () => {
    // Both are null in the payload. Collapsing them loses a real fact: one
    // resolves on its own, the other never will without a re-run.
    expect(html(<ScoreMeter score={null} absence="measuring" />)).toContain('Measuring');
    expect(html(<ScoreMeter score={null} absence="unscored" />)).toContain('Not scored');
  });

  it('states the score to assistive tech, which cannot read a bar', () => {
    expect(html(<ScoreMeter score={72} />)).toContain(
      'aria-label="AI Visibility Score 72 out of 100 — Established"',
    );
  });

  it('never borrows a semantic tone for a score', () => {
    // tokens/color.ts keeps the semantic and visibility palettes disjoint so a
    // red error chip is not misread as a bad score. A meter reaching for
    // avp-badge--danger would breach that from the other side.
    const out = html(<ScoreMeter score={8} />);
    expect(out).not.toContain('avp-badge--danger');
    expect(out).not.toContain('avp-badge--warn');
  });
});

describe('LuminanceLedger renders', () => {
  const out = html(
    <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} competitors={COMPETITORS} />,
  );

  it('carries an accessible description of every dimension', () => {
    expect(out).toContain('role="img"');
    for (const d of DIMENSIONS) expect(out).toContain(d.label);
    expect(out).toContain('out of 100');
  });

  it('ships a screen-reader data table alongside the SVG', () => {
    expect(out).toContain('avp-visually-hidden');
    expect(out).toContain('<caption>');
    expect(out).toContain('Points available');
  });

  it('names the largest recoverable gap in the accessible label', () => {
    // Share of Voice: 25 x (100-22)/100 = 19.5 pts — the largest here.
    expect(out).toContain('Largest recoverable gap: Share of Voice');
    expect(out).toContain('19.5 points');
  });

  it('draws one ghost column per competitor', () => {
    expect(out.match(/avp-ledger__ghost-cap/g)).toHaveLength(COMPETITORS.length);
  });

  it('never paints a competitor with a visibility-ramp colour', () => {
    // The credibility rule: competitors are neutral, never coloured by quality.
    const ghostBlock = out.slice(out.indexOf('avp-ledger__ghost'));
    for (const stop of Object.values(visibility)) {
      expect(ghostBlock).not.toContain(oklch(stop));
    }
  });

  it('renders INSUFFICIENT_DATA copy for an empty scan', () => {
    const empty = html(<LuminanceLedger subjectName={SUBJECT} dimensions={[]} />);
    expect(empty).toContain('Not enough data to score');
    expect(empty).not.toContain('avp-ledger__svg');
  });
});

describe('report primitives enforce the narrative', () => {
  it('numbers each beat by its position in the score-gap-proof-fix-pitch sequence', () => {
    expect(BEAT_SEQUENCE).toEqual(['score', 'gap', 'proof', 'fix', 'pitch']);
    const out = html(
      <ReportPage>
        {BEAT_SEQUENCE.map((id) => (
          <Beat key={id} id={id} heading={`Heading for ${id}`}>
            body
          </Beat>
        ))}
      </ReportPage>,
    );
    for (const [i, id] of BEAT_SEQUENCE.entries()) {
      expect(out).toContain(`avp-beat--${id}`);
      expect(out).toContain(String(i + 1).padStart(2, '0'));
    }
  });

  it('Evidence renders prompts and facts, and has no slot for scraped prose', () => {
    const out = html(
      <Evidence
        engine="Engine 1"
        prompt="best family dentist in the northaven area"
        findings={[{ label: 'Brand mentioned', value: 'No' }]}
      />,
    );
    expect(out).toContain('best family dentist in the northaven area');
    expect(out).toContain('Brand mentioned');
    // The prop surface is prompt + engine + label/value facts. There is no
    // free-text body prop and no children, so a paragraph of scraped answer
    // text has nowhere to go — the constraint is enforced by the type, not by
    // a reviewer noticing.
    expect(out).not.toContain('undefined');
  });
});

describe('AnswerShelf renders', () => {
  const shelfRows: ShelfRowInput[] = [
    {
      promptId: 'p1', promptText: 'best help desk software', promptPosition: 1,
      engine: 'claude', answered: true, subjectPresent: true, subjectPosition: 2,
      subjectCited: true,
      slots: [
        { position: 1, entityName: 'Zendesk', isSubject: false, cited: true },
        { position: 2, entityName: 'Help Scout', isSubject: true, cited: true },
      ],
    },
    {
      promptId: 'p2', promptText: 'zendesk alternatives for small teams', promptPosition: 2,
      engine: 'claude', answered: true, subjectPresent: false, subjectPosition: null,
      subjectCited: false,
      slots: [{ position: 1, entityName: 'Front', isSubject: false, cited: false }],
    },
    {
      promptId: 'p3', promptText: 'shared inbox tools', promptPosition: 3,
      engine: 'claude', answered: false, subjectPresent: false, subjectPosition: null,
      slots: [],
    },
  ];

  it('mounts inside ChartFrame and inherits the accessibility contract', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).toContain('avp-chart-frame');
    expect(out).toContain('role="img"');
    expect(out).toMatch(/aria-label="[^"]+"/);
    // The spoken label is the finding, not "a chart".
    expect(out).toContain('named in 1 and absent from 1');
  });

  it('carries a visually-hidden table equivalent, for PDF accessibility audits', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).toContain('avp-visually-hidden');
    expect(out).toContain('<table>');
    expect(out).toContain('Not named');
    expect(out).toContain('Named 2nd');
    expect(out).toContain('No answer returned');
  });

  it('draws an explicit notch for an absence rather than nothing', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).toContain('avp-shelf__notch');
    // And distinguishes "no answer" from "answered without naming you".
    expect(out).toContain('avp-shelf__unanswered');
  });

  it('paints the subject beacon and rivals neutral, never a ramp colour', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).toContain(oklch(beacon['600']));
    for (const stop of Object.values(visibility)) {
      expect(out).not.toContain(oklch(stop));
    }
  });

  it('emits no raw hex or rgb() — every colour comes from the token ramp', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(out).not.toMatch(/\brgba?\(/);
  });

  it('renders our own question as the row label and no answer text', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={shelfRows} />);
    expect(out).toContain('best help desk software');
    expect(out).toContain('zendesk alternatives for small teams');
  });

  it('degrades to a sentence rather than an empty frame', () => {
    const out = html(<AnswerShelf subjectName="Help Scout" rows={[]} />);
    expect(out).toContain('No answers were recorded');
    expect(out).not.toContain('role="img"');
  });
});
