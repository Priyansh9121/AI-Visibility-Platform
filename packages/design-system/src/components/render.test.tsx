import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Button } from './Button.js';
import { Card, CardBody } from './Card.js';
import { Badge, VisibilityBadge } from './Badge.js';
import { DataTable } from './Table.js';
import { ScoreDisplay } from './ScoreDisplay.js';
import { ScoreMeter } from './ScoreMeter.js';
import { PageSection } from './marketing/PageSection.js';
import { AppShell, NavItem } from './shell/AppShell.js';
import { LoadingState } from './state/LoadingState.js';
import { ErrorState } from './state/ErrorState.js';
import { EmptyState } from './state/EmptyState.js';
import { LuminanceLedger } from './chart/LuminanceLedger.js';
import { AnswerShelf } from './chart/AnswerShelf.js';
import { TrendChart } from './chart/TrendChart.js';
import { LocalNav, LocalNavItem } from './shell/LocalNav.js';
import type { ShelfRowInput } from './chart/answerShelfLayout.js';
import { Beat, Evidence, ReportPage, BEAT_SEQUENCE } from './report/ReportLayout.js';
import { visibility, beacon, oklch } from '../tokens/color.js';
import { DIMENSIONS, COMPETITORS, SUBJECT } from '../styleguide/fixtures.js';

const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

describe('LuminanceLedger stagger is opt-in — Epic 9.16', () => {
  /*
   * The component half of the report guardrail. The other half lives in
   * apps/web's ReportView.test.tsx, which asserts the report's actual markup;
   * this one asserts the component's own default, so the guarantee does not
   * rest on every future call site remembering.
   */
  it('emits no stagger markup by default', () => {
    const markup = html(<LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} />);
    expect(markup).not.toContain('avp-ledger--staggered');
    expect(markup).not.toContain('--avp-ledger-index');
  });

  it('emits no stagger markup when only `animate` is set', () => {
    // `animate` is TRUE on both report routes. If staggering rode on it, the
    // document would have acquired a page flourish by default.
    const markup = html(
      <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} animate />,
    );
    expect(markup).not.toContain('avp-ledger--staggered');
  });

  it('emits it only when asked, and indexes every dimension', () => {
    const markup = html(
      <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} staggerDimensions />,
    );
    expect(markup).toContain('avp-ledger--staggered');
    for (let i = 0; i < DIMENSIONS.length; i++) {
      expect(markup).toContain(`--avp-ledger-index:${i}`);
    }
  });

  it('computes no millisecond value in JavaScript', () => {
    const markup = html(
      <LuminanceLedger subjectName={SUBJECT} dimensions={DIMENSIONS} staggerDimensions />,
    );
    expect(markup).not.toMatch(/\d+ms/);
  });
});

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

describe('PageSection is the report voice without the report contract', () => {
  it('renders its parts and marks the lead section', () => {
    const out = html(
      <PageSection tone="lead" eyebrow="Kicker" heading="A claim." lead="A sentence.">
        <p>Body</p>
      </PageSection>,
    );
    expect(out).toContain('avp-section--lead');
    expect(out).toContain('Kicker');
    expect(out).toContain('A claim.');
    expect(out).toContain('Body');
  });

  it('omits every optional part rather than rendering an empty node', () => {
    const out = html(<PageSection heading="Only a heading." />);
    expect(out).toContain('Only a heading.');
    expect(out).not.toContain('avp-section__eyebrow');
    expect(out).not.toContain('avp-section__lead');
    expect(out).not.toContain('avp-section__body');
  });

  it('does NOT borrow the report beat numbering', () => {
    // Beat numbers itself from BEAT_SEQUENCE to enforce the narrative order
    // ip-safety.md #3 mandates. If a marketing section could take a step
    // number, that sequence would stop meaning anything where it matters.
    const out = html(<PageSection heading="x" />);
    expect(out).not.toContain('avp-beat');
    expect(out).not.toContain('avp-beat__step');
  });
});

describe('LoadingState is the one way this product says wait', () => {
  it('names the work, and announces itself to assistive tech', () => {
    const out = html(<LoadingState message="Assembling the report…" />);
    expect(out).toContain('Assembling the report…');
    // A visual-only change tells a screen reader nothing.
    expect(out).toContain('role="status"');
    expect(out).toContain('aria-live="polite"');
  });

  it('renders named steps when the caller knows them', () => {
    const out = html(
      <LoadingState message="Reading the site" steps={['Fetching pages', 'Reading schema']} hint="A few seconds." />,
    );
    expect(out).toContain('Fetching pages');
    expect(out).toContain('Reading schema');
    expect(out).toContain('A few seconds.');
  });

  it('omits the step list entirely when there are none', () => {
    // An empty <ol> is a bullet of nothing. A single-request wait has no steps
    // worth naming, and inventing some would imply progress we cannot observe.
    const out = html(<LoadingState message="Loading…" />);
    expect(out).not.toContain('avp-loading__steps');
    expect(out).not.toContain('avp-loading__hint');
  });

  it('has no spinner and no progress bar', () => {
    // The rule Epic 2 set and Epic 9.7 restated: this product cannot measure
    // real progress on any long operation, and a bar that fills on a timer is
    // a lie the user eventually catches.
    const out = html(<LoadingState message="Working" steps={['a', 'b']} />);
    expect(out).not.toContain('role="progressbar"');
    expect(out).not.toContain('aria-valuenow');
    expect(out).not.toContain('animate-spin');
  });
});

describe('ErrorState is the one way this product says that failed', () => {
  it('states the problem and offers the way out', () => {
    const out = html(
      <ErrorState title="No report for that scan" detail="It may belong to another agency." action={<Button>Back</Button>} />,
    );
    expect(out).toContain('No report for that scan');
    expect(out).toContain('It may belong to another agency.');
    expect(out).toContain('Back');
    expect(out).toContain('role="alert"');
    // Seated, not raised: an error is not a thing to lift off the page.
    expect(out).toContain('avp-card--seated');
  });

  it('drops every optional part rather than rendering an empty one', () => {
    const out = html(<ErrorState title="Something went wrong" />);
    expect(out).not.toContain('avp-errorstate__detail');
    expect(out).not.toContain('avp-errorstate__code');
    expect(out).not.toContain('avp-errorstate__action');
  });

  it('treats an empty-string code as no code', () => {
    // `code=""` is what a stringly-typed API field gives you on a good day.
    expect(html(<ErrorState title="x" code="" />)).not.toContain('avp-errorstate__code');
    expect(html(<ErrorState title="x" code="E_NOPE" />)).toContain('E_NOPE');
  });

  it('never puts a raw status code in the title position', () => {
    // The title is passed in, so this asserts the CONTRACT the docstring states
    // by checking the component does not decorate it with one.
    const out = html(<ErrorState title="No report for that scan" />);
    expect(out).not.toContain('404');
    expect(out).not.toContain('500');
  });
});

describe('AppShell frames a screen without restyling it', () => {
  const shell = (wide = false) =>
    html(
      <AppShell
        brand={<span>Northlight</span>}
        nav={<NavItem href="/dashboard" label="Dashboard" current />}
        footer={<span>Sign out</span>}
        wide={wide}
      >
        <p id="payload">the screen</p>
      </AppShell>,
    );

  it('renders brand, nav, footer and the content untouched', () => {
    const out = shell();
    expect(out).toContain('Northlight');
    expect(out).toContain('Dashboard');
    expect(out).toContain('Sign out');
    // The child is passed through verbatim — the shell is a frame, not a
    // wrapper that decorates what it holds.
    expect(out).toContain('<p id="payload">the screen</p>');
  });

  it('names the navigation landmark for assistive tech', () => {
    const out = shell();
    expect(out).toContain('<nav');
    expect(out).toContain('aria-label="Main"');
    // One <main> per document. The shell owns it, so wrapped pages must not
    // also render one — that is why the routes were changed to drop theirs.
    expect(out.match(/<main/g)).toHaveLength(1);
  });

  it('widens only when asked', () => {
    expect(shell(false)).not.toContain('avp-shell__content--wide');
    expect(shell(true)).toContain('avp-shell__content--wide');
  });

  it('omits the footer entirely when there is none', () => {
    const out = html(<AppShell brand={<span>x</span>} nav={<span>y</span>}>z</AppShell>);
    expect(out).not.toContain('avp-shell__footer');
  });
});

describe('NavItem is a link, never a dead control', () => {
  it('renders an anchor with a real href', () => {
    const out = html(<NavItem href="/clients" label="Clients" />);
    expect(out).toContain('<a href="/clients"');
    // Not a button: every destination is a real URL, so it must be
    // middle-clickable and bookmarkable.
    expect(out).not.toContain('<button');
  });

  it('marks the current page for assistive tech, not by colour alone', () => {
    expect(html(<NavItem href="/x" label="X" current />)).toContain('aria-current="page"');
    expect(html(<NavItem href="/x" label="X" />)).not.toContain('aria-current');
  });

  it('has no disabled state to reach for', () => {
    // A nav item that looks like a destination and goes nowhere is the
    // "button that does nothing" this epic's brief ruled out. There is no
    // prop that would produce one.
    const out = html(<NavItem href="/x" label="X" note="a note" />);
    expect(out).not.toContain('disabled');
    expect(out).not.toContain('aria-disabled');
    expect(out).toContain('a note');
  });

  it('hides its icon from the accessibility tree', () => {
    const out = html(<NavItem href="/x" label="X" icon={<svg />} />);
    expect(out).toContain('aria-hidden="true"');
  });
});

/**
 * EmptyState — Epic 9.19.
 *
 * The third member of the set LoadingState and ErrorState opened in Epic 9.11.
 * The assertions worth having are the ones about what it refuses to become: a
 * decorative box, and a dead end.
 */
describe('EmptyState is a statement with a way out', () => {
  it('renders the title as the statement it is', () => {
    const out = html(<EmptyState title="No scans yet" />);
    expect(out).toContain('avp-empty');
    expect(out).toContain('No scans yet');
  });

  it('carries no figure, eyebrow, note or action unless given one', () => {
    // A slot that renders an empty box when unused is how a treatment starts
    // looking like furniture.
    const out = html(<EmptyState title="Nothing here" />);
    expect(out).not.toContain('avp-empty__figure');
    expect(out).not.toContain('avp-empty__eyebrow');
    expect(out).not.toContain('avp-empty__note');
    expect(out).not.toContain('avp-empty__action');
    expect(out).not.toContain('avp-empty--figured');
  });

  it('switches to the two-column reading only when a figure is actually passed', () => {
    const out = html(<EmptyState title="x" figure={<svg />} />);
    expect(out).toContain('avp-empty--figured');
    expect(out).toContain('avp-empty__figure');
  });

  it('is a plain block, not a Card — it is framed by a dashed rule instead', () => {
    // ErrorState is seated because an error is a thing that happened. An
    // absence is drawn with the same dashed stroke the shelf notch and the
    // ledger's gap zone use, so it must NOT pick up card chrome as well.
    const out = html(<EmptyState title="x" />);
    expect(out).not.toContain('avp-card');
  });
});

/**
 * The live badge — Epic 9.19.
 *
 * The only motion this product runs on a Working screen without anybody doing
 * anything, so the two things that matter are that it is opt-in and that it is
 * invisible to assistive tech (the label already says "Running").
 */
describe('Badge live dot', () => {
  it('is off by default, so no existing badge acquires motion', () => {
    expect(html(<Badge tone="beacon">Running</Badge>)).not.toContain('avp-badge__pulse');
  });

  it('adds one decorative dot when asked', () => {
    const out = html(
      <Badge tone="beacon" live>
        Running
      </Badge>,
    );
    expect(out).toContain('avp-badge__pulse');
    expect(out).toContain('aria-hidden="true"');
    expect(out).toContain('Running');
  });
});

/**
 * The unmeasured Ledger — Epic 9.19.
 *
 * Sub-scores of zero already draw the right picture. The whole reason this
 * mode exists is that they would also make CLAIMS nobody measured, and those
 * claims are only reachable through the accessible name and the data table.
 */
describe('LuminanceLedger, unmeasured', () => {
  const UNLIT = DIMENSIONS.map((d) => ({ ...d, subscore: 0 }));

  it('never asserts a score of zero to assistive tech', () => {
    const out = html(
      <LuminanceLedger subjectName="Acme" dimensions={UNLIT} unmeasured animate={false} />,
    );
    expect(out).not.toContain('0 out of 100');
    expect(out).toContain('none of them measured yet');
  });

  it('tabulates points AVAILABLE rather than five measured zeroes', () => {
    const out = html(
      <LuminanceLedger subjectName="Acme" dimensions={UNLIT} unmeasured animate={false} />,
    );
    expect(out).toContain('What an AI Visibility scan measures');
    expect(out).toContain('Not yet');
    expect(out).not.toContain('Composite');
  });

  it('still names every real dimension and weight — it is the shape of a scan', () => {
    const out = html(
      <LuminanceLedger subjectName="Acme" dimensions={UNLIT} unmeasured animate={false} />,
    );
    for (const d of DIMENSIONS) {
      expect(out).toContain(d.label);
      expect(out).toContain(`${d.weight}% weight`);
    }
  });

  it('draws no gap annotation, because a gap from nothing is not a finding', () => {
    const out = html(
      <LuminanceLedger subjectName="Acme" dimensions={UNLIT} unmeasured animate={false} />,
    );
    expect(out).not.toContain('avp-ledger__gap-label');
  });

  it('defaults OFF, so a measured ledger is unchanged', () => {
    const out = html(<LuminanceLedger subjectName="Acme" dimensions={DIMENSIONS} animate={false} />);
    expect(out).not.toContain('avp-ledger--unmeasured');
    expect(out).toContain('out of 100');
    expect(out).toContain('Composite');
  });
});

/**
 * TrendChart — Epic 9.20, the first time-series shape in this system.
 *
 * The assertions that matter are the palette rule (design-direction.md §1) and
 * the gap rule, because both are silent failures: a rival drawn in the brand
 * colour reads as an endorsement, and a gap drawn as zero reads as a collapse.
 */
describe('TrendChart', () => {
  const POINTS = [
    { label: '27 Aug', stamp: '2026-08-27T00:00:00Z' },
    { label: '28 Aug', stamp: '2026-08-28T00:00:00Z' },
  ];
  const SERIES = [
    { key: 'me', label: 'Plausible', isSubject: true, values: [36, 35] },
    { key: 'r1', label: 'Matomo', values: [21, 24] },
    { key: 'r2', label: 'Fathom', values: [9, null] },
  ];
  const chart = (extra: Record<string, unknown> = {}) =>
    html(<TrendChart points={POINTS} series={SERIES} ariaLabel="Share of voice" {...extra} />);

  it('paints the client in the brand accent', () => {
    expect(chart()).toContain(oklch(beacon['600']));
  });

  it('never paints a competitor from the visibility ramp', () => {
    // §1: a rival in "good green" implies an endorsement and one in "bad red"
    // reads as a hatchet job. Competitors come from the neutral slate family.
    const out = chart();
    for (const stop of Object.values(visibility)) {
      expect(out).not.toContain(oklch(stop));
    }
  });

  it('separates competitors by dash as well as by lightness', () => {
    // Five neutral greys are one grey in greyscale print; five dash patterns
    // are five lines. Same reason ChartPatterns exists for fills.
    expect(chart()).toContain('stroke-dasharray');
  });

  it('draws the client solid — only rivals are dashed', () => {
    const single = html(
      <TrendChart
        points={POINTS}
        series={[SERIES[0]!]}
        ariaLabel="Share of voice"
      />,
    );
    expect(single).not.toContain('stroke-dasharray');
  });

  it('carries the accessibility contract every chart here carries', () => {
    const out = chart({ title: 'Share of voice' });
    expect(out).toContain('aria-label="Share of voice"');
    expect(out).toContain('avp-visually-hidden');
    expect(out).toContain('<table>');
  });

  it('says "not measured" in the data table rather than printing a zero', () => {
    // The hidden table is the only way a screen reader reads this chart, so the
    // gap has to be a word there, not an absent cell or a 0.
    expect(chart()).toContain('not measured');
  });

  it('draws a broken series as two strokes, not one through the gap', () => {
    const withGap = html(
      <TrendChart
        points={[...POINTS, { label: '29 Aug', stamp: '2026-08-29T00:00:00Z' }]}
        series={[{ key: 'r', label: 'Fathom', values: [9, null, 11] }]}
        ariaLabel="x"
      />,
    );
    // Two separate move commands means two separate strokes.
    expect((withGap.match(/ d="M/g) ?? []).length).toBe(2);
  });

  it('renders every series name, so no line is anonymous', () => {
    const out = chart();
    for (const label of ['Plausible', 'Matomo', 'Fathom']) {
      expect(out).toContain(label);
    }
  });
});

/**
 * LocalNav — Epic 9.20. Navigation scoped to one record.
 */
describe('LocalNav', () => {
  const nav = (current?: string) =>
    html(
      <LocalNav title="Plausible" subtitle="plausible.io" back={{ href: '/clients', label: 'All clients' }}>
        <LocalNavItem href="/clients/x" label="Overview" current={current === 'overview'} />
        <LocalNavItem href="/scans/s/report" label="Report" external />
        <LocalNavItem href="/clients/x/sources" label="Sources" current={current === 'sources'} />
      </LocalNav>,
    );

  it('names whose space this is and how to get back out', () => {
    const out = nav();
    expect(out).toContain('Plausible');
    expect(out).toContain('plausible.io');
    expect(out).toContain('href="/clients"');
    expect(out).toContain('All clients');
  });

  it('marks the current item for assistive tech, not by weight alone', () => {
    expect(nav('sources')).toContain('aria-current="page"');
    expect((nav('sources').match(/aria-current="page"/g) ?? []).length).toBe(1);
  });

  it('is a real nav landmark with a name', () => {
    expect(nav()).toContain('<nav');
    expect(nav()).toContain('aria-label=');
  });

  it('uses anchors, never buttons — these are URLs', () => {
    const out = nav();
    expect(out).toContain('<a href="/clients/x"');
    expect(out).not.toContain('<button');
  });

  it('has no disabled variant to reach for', () => {
    // Same rule as NavItem: an item either goes somewhere real or is absent.
    expect(nav()).not.toContain('disabled');
    expect(nav()).not.toContain('aria-disabled');
  });

  it('flags a destination that leaves the record space', () => {
    // The Report item is a path into the existing document, not a copy of it.
    expect(nav()).toContain('avp-localnav__out');
  });
});
