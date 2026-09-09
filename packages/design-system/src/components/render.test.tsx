import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Button } from './Button.js';
import { Card, CardBody } from './Card.js';
import { Badge, VisibilityBadge } from './Badge.js';
import { DataTable } from './Table.js';
import { ScoreDisplay } from './ScoreDisplay.js';
import { ReportMetaItem, ScoreBlock } from './report/ReportLayout.js';
import { MetaChip } from './MetaChip.js';
import { ScoreMeter } from './ScoreMeter.js';
import { VerdictBar } from './VerdictBar.js';
import { PageSection } from './marketing/PageSection.js';
import { AppShell, NavItem } from './shell/AppShell.js';
import { LoadingState } from './state/LoadingState.js';
import { ErrorState } from './state/ErrorState.js';
import { EmptyState } from './state/EmptyState.js';
import { LuminanceLedger } from './chart/LuminanceLedger.js';
import { AnswerShelf } from './chart/AnswerShelf.js';
import { TrendChart } from './chart/TrendChart.js';
import { SentimentTide, negativePatternId } from './chart/SentimentTide.js';
import { LocalNav, LocalNavItem } from './shell/LocalNav.js';
import type { ShelfRowInput } from './chart/answerShelfLayout.js';
import { Beat, Evidence, ReportPage, BEAT_SEQUENCE } from './report/ReportLayout.js';
import { visibility, beacon, competitor, oklch, benchColor, benchColorDark, dark } from '../tokens/color.js';
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

  it('ScoreDisplay owns its badge when asked, from the rounded score — Epic 16', () => {
    const out = html(<ScoreDisplay score={58.24} animate={false} badge />);
    expect(out).toContain('avp-badge--visibility');
    expect(out).toContain('Emerging');
    const numeral = out.match(/avp-score__numeral" style="color:(oklch\([^)]*\))/)?.[1];
    const badge = out.match(/avp-score__badge" style="background:(oklch\([^)]*\))/)?.[1];
    expect(numeral).toBeDefined();
    expect(badge).toBe(numeral);
    // Off by default, and never for a null score: there is no band to name.
    expect(html(<ScoreDisplay score={58} />)).not.toContain('avp-badge');
    expect(html(<ScoreDisplay score={null} badge />)).not.toContain('avp-badge');
  });

  it('MetaChip gives a fact a shape and hides its glyph — Epic 16.1', () => {
    const out = html(
      <MetaChip icon={<svg data-glyph="" />} mono>
        example.com
      </MetaChip>,
    );
    expect(out).toContain('avp-chip avp-chip--mono');
    expect(out).toContain('<span class="avp-chip__icon" aria-hidden="true"><svg data-glyph=""></svg></span>');
    expect(out).toContain('example.com');
    // No glyph, no empty wrapper.
    expect(html(<MetaChip>3 prompts</MetaChip>)).not.toContain('avp-chip__icon');
    // A fact is never a state or a score: no tone class can be reached.
    expect(out).not.toContain('avp-badge');
  });

  it('ReportMetaItem is MetaChip wearing the report’s class — Epic 16', () => {
    const out = html(
      <ReportMetaItem icon={<svg data-glyph="" />} mono>
        example.com
      </ReportMetaItem>,
    );
    expect(out).toContain('avp-chip avp-chip--mono avp-report__meta-item');
    expect(out).toContain('<span class="avp-chip__icon" aria-hidden="true">');
  });

  it('ScoreBlock puts the figure before the explanation — Epic 16', () => {
    const out = html(
      <ScoreBlock figure={<b>figure</b>}>
        <p>explain</p>
      </ScoreBlock>,
    );
    expect(out.indexOf('avp-scoreblock__figure')).toBeLessThan(out.indexOf('avp-scoreblock__explain'));
    expect(out.indexOf('figure')).toBeLessThan(out.indexOf('explain'));
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
    // the narrative-report rule mandates. If a marketing section could take a step
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

  /*
   * The per-context palette — Epic 9.24.
   *
   * These are the assertions that make "one chart, two contexts" a fact rather
   * than an intention. The report keeps §1's neutral slate; a Working screen
   * gets hues an operator can actually tell apart at 1.5px; and everything that
   * makes the chart honest — the subject's brand colour, the dash patterns, the
   * hidden data table — is identical either way.
   */
  it('defaults to the restrained palette, so the report never opts in', () => {
    // Rendered WITHOUT a palette prop: exactly how the report calls it.
    const out = chart();
    expect(out).toContain(oklch(competitor['1']));
    for (let i = 0; i < 6; i++) expect(out).not.toContain(benchColor(i));
  });

  it('draws Working-screen competitors from the accent layer, as custom properties — Epic 15', () => {
    // The property resolves per theme in the stylesheet; a literal would be
    // right in one theme and wrong in the other.
    const out = chart({ palette: 'working' });
    expect(out).toContain('var(--avp-bench-1-600)');
    expect(out).not.toContain(benchColor(0));
    expect(out).not.toContain(benchColorDark(0));
    expect(out).not.toContain(oklch(competitor['1']));
  });

  it('keeps the client in beacon on a Working screen too — as the property', () => {
    // One brand, one HUE: the line an operator learns on the dashboard is the
    // line in the document they send, at hue 200 in every theme.
    expect(chart({ palette: 'working' })).toContain('var(--avp-beacon-600)');
    expect(chart({ palette: 'working' })).not.toContain(oklch(dark.beacon['600']));
  });

  it('fills the area under the client only when asked, and never by default', () => {
    // The report never passes `area`, so the default must draw no fill.
    expect(chart()).not.toContain('avp-trend__area');
    expect(chart({ palette: 'working' })).not.toContain('avp-trend__area');
    const filled = chart({ palette: 'working', area: true });
    expect(filled).toContain('avp-trend__area');
    expect(filled).toContain('<linearGradient');
    // One area — the subject's — however many rivals there are.
    expect((filled.match(/avp-trend__area/g) ?? []).length).toBe(1);
  });

  it('still never paints a Working competitor from the visibility ramp', () => {
    const out = chart({ palette: 'working' });
    for (const stop of Object.values(visibility)) {
      expect(out).not.toContain(oklch(stop));
    }
  });

  it('keeps the dash patterns, so greyscale and CVD survive the richer palette', () => {
    expect(chart({ palette: 'working' })).toContain('stroke-dasharray');
  });

  it('keeps its accessibility contract in the richer palette', () => {
    const out = chart({ palette: 'working', title: 'Share of voice' });
    expect(out).toContain('aria-label="Share of voice"');
    expect(out).toContain('avp-visually-hidden');
    expect(out).toContain('<table>');
    expect(out).toContain('not measured');
  });

  it('changes paint and nothing else — same geometry, same text, same table', () => {
    // The claim that makes this ONE chart rather than two: remove every paint
    // attribute and the two renderings must be the same document, character
    // for character. Geometry, labels, ticks, gaps and the hidden data table
    // are all produced by the same layout pass either way, so a fix to any of
    // them lands on both contexts at once and cannot drift between them.
    const geometry = (markup: string) => markup.replace(/ (?:fill|stroke)="[^"]*"/g, '');
    expect(geometry(chart({ palette: 'working' }))).toBe(geometry(chart()));
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

/**
 * The chart cannot render larger than it was drawn — Epic 9.21.
 *
 * An SVG with `width: 100%` over a fixed viewBox scales its TYPE with its box.
 * Measured on the live Rankings screen before this: a 720-unit chart stretched
 * across a 1200px Working column ran at 1.6x, so its 11px axis labels rendered
 * at 17.6px — larger than the 14px body copy above them — and its 2.5px subject
 * stroke came out at 4px. The page read as sparse and oversized because the
 * smallest type on it had become the biggest thing on it.
 *
 * That is the same failure Epic 9.19 fixed on the EmptyState ledger figure, in
 * the component built the epic after. These assert the invariant rather than a
 * number: whatever width the chart is laid out in, it is bounded at that width,
 * so one viewBox unit is never more than one CSS pixel.
 */

/**
 * SentimentTide — Epic A.
 *
 * The assertions that matter are the ones that are silent failures: an answer
 * that never named the client rendered as a neutral, an engine outage rendered
 * as a flat column on the waterline, and tone carried by hue alone.
 */
describe('SentimentTide', () => {
  const POINTS = [
    {
      label: '27 Aug',
      stamp: '2026-08-27T00:00:00Z',
      byEngine: {
        chatgpt: { positive: 6, neutral: 2, negative: 1, unclassified: 3 },
        claude: { positive: 4, neutral: 3, negative: 2, unclassified: 1 },
      },
    },
    {
      label: '29 Aug',
      stamp: '2026-08-29T00:00:00Z',
      byEngine: { chatgpt: { positive: 2, neutral: 1, negative: 7, unclassified: 2 } },
    },
  ];
  const tide = (extra: Record<string, unknown> = {}) =>
    html(<SentimentTide points={POINTS} ariaLabel="Tone by engine" {...extra} />);

  it('carries the accessibility contract every chart here carries', () => {
    const out = tide({ title: 'Tone by engine' });
    expect(out).toContain('aria-label="Tone by engine"');
    expect(out).toContain('avp-visually-hidden');
    expect(out).toContain('<table>');
  });

  it('gives the hidden table all four buckets, so nothing is chart-only', () => {
    const out = tide();
    for (const header of ['Positive', 'Neutral', 'Negative', 'Not named']) {
      expect(out).toContain(header);
    }
  });

  it('paints each engine’s negative hatch in that engine’s own colour', () => {
    /*
     * The defect this exists for rendered perfectly and was wrong: a single
     * shared `<pattern>` painted with `currentColor` resolves against the
     * `<defs>` that defines it, not against the `<g>` that references it — so
     * every engine's negative block came out the same `ink-800` grey while
     * every positive and neutral segment beside it was engine-coloured.
     *
     * The old test suite could not see it: it only checked that a pattern was
     * REFERENCED, never what colour the pattern resolved to. This asserts the
     * resolved fill, and asserts two engines differ.
     */
    const out = tide();
    const chatgpt = negativePatternId('chatgpt');
    const claude = negativePatternId('claude');
    expect(out).toContain(`id="${chatgpt}"`);
    expect(out).toContain(`id="${claude}"`);

    const colourOf = (id: string) => {
      const block = out.slice(out.indexOf(`id="${id}"`));
      return block.slice(0, block.indexOf('</pattern>')).match(/fill="([^"]+)"/)?.[1];
    };
    const a = colourOf(chatgpt);
    const b = colourOf(claude);
    expect(a).toBeTruthy();
    expect(a).not.toBe('currentColor');
    expect(a).not.toBe(b);

    // And each bar references its OWN engine's pattern, not a shared one.
    expect(out).toContain(`url(#${chatgpt})`);
    expect(out).toContain(`url(#${claude})`);
  });

  it('emits one pattern per engine, not one per bar', () => {
    // chatgpt appears in both scans; two patterns for it would be dead defs.
    const out = tide();
    const count = (needle: string) => out.split(needle).length - 1;
    expect(count(`id="${negativePatternId('chatgpt')}"`)).toBe(1);
  });

  it('says an engine "did not answer" in words rather than as zeros', () => {
    // A row of four zeros would read as "described you neutrally". This is the
    // difference between an outage and a finding.
    expect(tide()).toContain('did not answer');
  });

  it('draws no column for an engine that did not answer', () => {
    // Two engines at point 0, one at point 1 — three bars, not four.
    const groups = tide().match(/class="avp-tide__bar"/g) ?? [];
    expect(groups).toHaveLength(3);
  });

  it('never draws the "not named" bucket as a segment', () => {
    // Point 1 has 2 unclassified and 3 tone segments. If unclassified were
    // drawn there would be 4.
    const out = html(
      <SentimentTide
        points={[POINTS[1]!]}
        ariaLabel="x"
      />,
    );
    // Counting the MODIFIER class, which appears exactly once per rect —
    // `avp-tide__seg` alone matches twice per rect, once in the base class and
    // once inside the modifier.
    expect((out.match(/avp-tide__seg--/g) ?? []).length).toBe(3);
  });

  it('takes engine hue from the bench layer, so tone is not carried by colour', () => {
    const out = tide({ engineAccent: { chatgpt: 4, claude: 0 } });
    expect(out).toContain('--avp-bench-5-600'); // index 4 -> accent 5
    expect(out).toContain('--avp-bench-1-600'); // index 0 -> accent 1
  });

  it('distinguishes negative by pattern as well as by position', () => {
    // Greyscale and colour-blind readers get direction from geometry and a
    // hatch, never from hue — the rule §1 sets for competitor series.
    //
    // Resolved through `negativePatternId` rather than against a literal: this
    // assertion was written as `url(#avp-tide-negative)` and would have kept
    // passing while the hatch rendered in the wrong colour, because a
    // reference existing says nothing about what it resolves to.
    expect(tide()).toContain(`url(#${negativePatternId('chatgpt')})`);
  });

  it('names the engines rather than showing raw keys when given labels', () => {
    expect(tide({ engineLabel: { chatgpt: 'ChatGPT' } })).toContain('ChatGPT');
  });

  it('is bounded by its own viewBox, like every chart since Epic 9.21', () => {
    expect(tide()).toContain('max-width:720px');
    expect(tide()).toContain('width="100%"');
  });

  it('does not animate by default', () => {
    // Nothing on a Working screen performs on every load unless something
    // really changed.
    expect(tide()).not.toContain('avp-tide--animate');
    expect(tide({ animate: true })).toContain('avp-tide--animate');
  });

  it('renders an empty history without throwing', () => {
    const out = html(<SentimentTide points={[]} ariaLabel="Nothing yet" />);
    expect(out).toContain('aria-label="Nothing yet"');
  });
});


describe('TrendChart is bounded by its own viewBox', () => {
  const POINTS = [
    { label: 'a', stamp: '2026-08-27T00:00:00Z' },
    { label: 'b', stamp: '2026-08-28T00:00:00Z' },
  ];
  const SERIES = [{ key: 'me', label: 'Subject', isSubject: true, values: [1, 2] }];

  it('caps at the default layout width', () => {
    const out = html(<TrendChart points={POINTS} series={SERIES} ariaLabel="x" />);
    // 300 is `DEFAULTS.height`; the client screens pass 340 explicitly.
    expect(out).toContain('viewBox="0 0 720 300"');
    expect(out).toContain('max-width:720px');
  });

  it('the cap MOVES with the layout, so the two can never drift apart', () => {
    // The reason this is derived rather than declared. A caller widening the
    // chart with a constant cap in CSS would be squeezed by it instead —
    // scale < 1, type SMALLER than drawn, the same bug in the other direction.
    const out = html(
      <TrendChart points={POINTS} series={SERIES} ariaLabel="x" layoutOptions={{ width: 1040 }} />,
    );
    expect(out).toContain('viewBox="0 0 1040 300"');
    expect(out).toContain('max-width:1040px');
  });

  it('tracks a custom height too, so the aspect ratio is never forced', () => {
    const out = html(<TrendChart points={POINTS} series={SERIES} ariaLabel="x" height={260} />);
    expect(out).toContain('viewBox="0 0 720 260"');
    expect(out).toContain('max-width:720px');
  });

  it('is a MAX — the chart must still scale down to a narrow viewport', () => {
    // A fixed `width` here would break every viewport below the layout width.
    const out = html(<TrendChart points={POINTS} series={SERIES} ariaLabel="x" />);
    expect(out).toContain('width="100%"');
    expect(out).not.toMatch(/style="[^"]*[^-]width:720px/);
  });

  it('the ledger, which had this fixed in 9.19, is still bounded by its caller', () => {
    // Not a regression guard on TrendChart — a reminder that the Ledger solves
    // the same problem at the call site (EmptyState's figure slot) because it
    // has no single natural width the way a trend does.
    const out = html(
      <LuminanceLedger subjectName="x" dimensions={DIMENSIONS} animate={false} />,
    );
    expect(out).toContain('width="100%"');
  });
});

/**
 * VerdictBar — Epic 9.22.
 *
 * The two things worth asserting are both about honesty: that it never paints a
 * verdict from the visibility ramp, and that it never counts a check that does
 * not apply as one the site passed.
 */
describe('VerdictBar', () => {
  const bar = (counts: Record<string, number>) =>
    html(<VerdictBar counts={counts as never} ariaLabel="verdicts" />);

  it('paints verdicts from the SEMANTIC palette, never the visibility ramp', () => {
    // §1 keeps semantics for system state and the ramp for score values. A
    // check verdict is system state; a ramp colour here would imply it is a
    // score, which is exactly the confusion §1 exists to prevent.
    const out = bar({ pass: 3, warn: 1, fail: 2 });
    for (const stop of Object.values(visibility)) {
      expect(out).not.toContain(oklch(stop));
    }
    expect(out).toContain('avp-verdict__seg--pass');
    expect(out).toContain('avp-verdict__seg--fail');
  });

  it('sizes segments over the MEASURED checks only', () => {
    // 3 pass + 1 warn = 4 measured; `notApplicable` must not enter the
    // denominator or the bar reads as 3/5 passed when it is 3/4.
    const out = bar({ pass: 3, warn: 1, fail: 0, notApplicable: 6 });
    expect(out).toContain('width:75%');
    expect(out).toContain('width:25%');
  });

  it('draws an empty track when nothing was measured, never a full one', () => {
    const out = bar({ pass: 0, warn: 0, fail: 0 });
    expect(out).toContain('avp-verdict__empty');
    expect(out).not.toContain('avp-verdict__seg--pass');
  });

  it('omits a zero segment rather than rendering a hairline of colour', () => {
    expect(bar({ pass: 4, warn: 0, fail: 0 })).not.toContain('avp-verdict__seg--warn');
  });

  it('carries an accessible name, and the legend IS its data table', () => {
    const out = bar({ pass: 1, warn: 2, fail: 3 });
    expect(out).toContain('aria-label="verdicts"');
    expect(out).toContain('Passed');
    expect(out).toContain('Failed');
  });

  it('shows N/A only when there is one', () => {
    expect(bar({ pass: 1, warn: 0, fail: 0 })).not.toContain('>N/A<');
    expect(bar({ pass: 1, warn: 0, fail: 0, notApplicable: 2 })).toContain('>N/A<');
  });
});

/**
 * The Ledger's opt-in bound — Epic 9.22.
 *
 * Same failure as 9.19 and 9.21: a viewBox chart magnifies its own type when
 * its container outgrows it. `bounded` is opt-in precisely so turning it on
 * cannot change the report, whose layout is out of scope.
 */
describe('LuminanceLedger bounding', () => {
  const ledger = (extra: Record<string, unknown> = {}) =>
    html(<LuminanceLedger subjectName="x" dimensions={DIMENSIONS} animate={false} {...extra} />);

  it('is UNBOUNDED by default, so the report is untouched', () => {
    expect(ledger()).not.toContain('max-width');
  });

  it('bounds at its own drawn width when asked', () => {
    const out = ledger({ bounded: true });
    expect(out).toMatch(/max-width:\d+px/);
  });

  it('the bound matches the viewBox, so one unit is at most one pixel', () => {
    const out = ledger({ bounded: true });
    const vb = /viewBox="0 0 (\d+)/.exec(out)?.[1];
    const cap = /max-width:(\d+)px/.exec(out)?.[1];
    expect(vb).toBeDefined();
    expect(cap).toBe(vb);
  });

  it('still scales down — it is a max, not a width', () => {
    expect(ledger({ bounded: true })).toContain('width="100%"');
  });
});

/*
 * Compact and partial — Epic 13.
 *
 * Two opt-ins for a grid of columns, and the guardrail is the same one every
 * other ledger opt-in has: the default rendering is byte-for-byte what the
 * report gets. The second describe is the honesty rule — a rival is measured
 * on three of five dimensions, and a column on that basis must not speak a
 * composite.
 */
describe('LuminanceLedger, compact — Epic 13', () => {
  const full = () => html(<LuminanceLedger subjectName="Acme" dimensions={DIMENSIONS} animate={false} />);
  const compact = () =>
    html(<LuminanceLedger subjectName="Acme" dimensions={DIMENSIONS} animate={false} compact />);

  it('is off by default, so the report is untouched', () => {
    expect(full()).not.toContain('avp-ledger--compact');
    expect(full()).toContain('% weight');
  });

  it('drops the label gutter and narrows the drawing', () => {
    const out = compact();
    expect(out).toContain('avp-ledger--compact');
    expect(out).not.toContain('% weight');
    const wide = Number(/viewBox="0 0 (\d+)/.exec(full())?.[1]);
    const narrow = Number(/viewBox="0 0 (\d+)/.exec(out)?.[1]);
    expect(narrow).toBeLessThan(wide / 2);
  });

  it('bounds itself at its drawn width without being asked', () => {
    const out = compact();
    const vb = /viewBox="0 0 (\d+)/.exec(out)?.[1];
    const cap = /max-width:(\d+)px/.exec(out)?.[1];
    expect(cap).toBe(vb);
  });

  it('keeps the identity: at one height, lit heights are unchanged by being compact', () => {
    // Same dimensions, same height, so the lit rects must be the same heights;
    // only their x and width differ. A compact column that drew a different
    // column would not be comparable to the one beside it. (Compact DEFAULTS
    // to a shorter column, which is why the height is pinned here.)
    const at = (extra: Record<string, unknown>) =>
      html(<LuminanceLedger subjectName="Acme" dimensions={DIMENSIONS} animate={false} height={300} {...extra} />);
    const heights = (s: string) =>
      [...s.matchAll(/<rect x="[\d.]+" y="[\d.]+" width="[\d.]+" height="([\d.]+)" fill="[^"]+" class="avp-ledger__lit"/g)].map(
        (m) => m[1],
      );
    expect(heights(at({ compact: true }))).toEqual(heights(at({})));
    expect(heights(at({})).length).toBe(DIMENSIONS.length);
  });

  it('draws no gap annotation — there is nowhere to write it', () => {
    expect(compact()).not.toContain('avp-ledger__gap-label');
    expect(full()).toContain('avp-ledger__gap-label');
  });

  it('still carries the full data table, labels included', () => {
    const out = compact();
    for (const d of DIMENSIONS) expect(out).toContain(d.label);
    expect(out).toContain('<table>');
  });
});

describe('LuminanceLedger, partial — a rival is measured on three of five', () => {
  const RIVAL = DIMENSIONS.map((d) =>
    d.key === 'sentiment' || d.key === 'technical' ? { ...d, subscore: 0, measured: false } : d,
  );
  const rival = () =>
    html(<LuminanceLedger subjectName="Competitor A" dimensions={RIVAL} animate={false} compact />);

  it('never claims a composite for the column', () => {
    const out = rival();
    expect(out).not.toContain('out of 100');
    expect(out).not.toMatch(/AI Visibility Score/);
    expect(out).toContain('No combined score');
    expect(out).toContain('No composite — measured on 3 of 5 dimensions');
    expect(out).toContain('measured on 3 of 5 dimensions');
  });

  it('speaks the measured dimensions as figures and the others as not measured', () => {
    const out = rival();
    expect(out).toContain('Mention Rate 41 of 100, weighted 30 percent');
    expect(out).toContain('Sentiment: not measured for Competitor A');
    expect(out).toContain('Technical Foundation: not measured for Competitor A');
  });

  it('hatches exactly the unmeasured segments and lights none of them', () => {
    const out = rival();
    expect(out).toContain('avp-ledger--partial');
    expect((out.match(/avp-ledger__void--unmeasured/g) ?? []).length).toBe(2);
    expect(out).toContain('<pattern id="ledger-hatch-');
    // The two unmeasured segments have zero lit height, so no value is ever
    // printed for them: every figure on the column is one of the measured
    // sub-scores. (A small sub-score may not fit its label at all, which is
    // the component's existing rule, so this asserts membership, not count.)
    const values = [...out.matchAll(/class="avp-ledger__value"[^>]*>(\d+)</g)].map((m) => m[1]);
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(['41', '22', '15']).toContain(v);
    expect(values).not.toContain('0');
    expect(values).not.toContain('78');
    expect(values).not.toContain('64');
  });

  it('draws no gap annotation — the largest unlit area is not a gap this subject can close', () => {
    const out = html(<LuminanceLedger subjectName="Competitor A" dimensions={RIVAL} animate={false} />);
    expect(out).not.toContain('avp-ledger__gap-label');
  });

  it('is distinct from the whole-column unmeasured state', () => {
    const out = rival();
    expect(out).not.toContain('avp-ledger--unmeasured');
    expect(out).not.toContain('none of them measured yet');
  });

  it('a fully measured column carries none of it', () => {
    const out = html(<LuminanceLedger subjectName="Acme" dimensions={DIMENSIONS} animate={false} />);
    expect(out).not.toContain('avp-ledger--partial');
    expect(out).not.toContain('ledger-hatch');
    expect(out).not.toContain('Not measured');
  });
});
