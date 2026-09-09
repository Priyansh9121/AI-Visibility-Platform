/**
 * The sidebar's mode primitives — Epic 13.
 *
 * Structure and states only: whether a disclosure says it is open, whether a
 * closed panel is out of the tab order, whether a row with no score prints no
 * number. Which items appear in which mode is `WorkspaceShell`'s business and
 * is asserted in apps/web.
 */
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  NavDisclosure,
  NavGroup,
  NavHead,
  NavItem,
  NavPanel,
  NavScore,
  NavSlot,
  NavSubItem,
} from './AppShell.js';

const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

describe('NavDisclosure opens rather than navigates', () => {
  it('is a button that states its expansion and what it controls', () => {
    const closed = html(
      <NavDisclosure label="Clients" expanded={false} onToggle={() => {}} controls="clients" />,
    );
    expect(closed).toContain('<button');
    expect(closed).toContain('aria-expanded="false"');
    expect(closed).toContain('aria-controls="clients"');
    expect(closed).not.toContain('href=');
    const open = html(
      <NavDisclosure label="Clients" expanded onToggle={() => {}} controls="clients" />,
    );
    expect(open).toContain('aria-expanded="true"');
  });

  it('takes an accent the same way a link row does', () => {
    const markup = html(
      <NavDisclosure label="Clients" accent={2} expanded={false} onToggle={() => {}} controls="c" />,
    );
    expect(markup).toContain('has-accent');
    expect(markup).toContain('--avp-nav-accent:var(--avp-bench-3-600)');
  });
});

describe('NavPanel hides a closed list from everyone', () => {
  it('is inert and aria-hidden while closed, and neither while open', () => {
    const closed = html(
      <NavPanel id="clients" open={false} label="Clients">
        <NavSlot>
          <NavSubItem href="/clients/1" label="Pirsch" score={34} />
        </NavSlot>
      </NavPanel>,
    );
    expect(closed).toContain('aria-hidden="true"');
    expect(closed).toContain('inert=""');
    expect(closed).not.toContain('is-open');
    const open = html(
      <NavPanel id="clients" open label="Clients">
        <NavSlot>
          <NavSubItem href="/clients/1" label="Pirsch" score={34} />
        </NavSlot>
      </NavPanel>,
    );
    expect(open).toContain('is-open');
    expect(open).toContain('aria-hidden="false"');
    expect(open).not.toContain('inert=""');
  });

  it('names its list for assistive tech', () => {
    expect(html(<NavPanel id="p" open label="Clients"><NavSlot>x</NavSlot></NavPanel>)).toContain(
      'aria-label="Clients"',
    );
  });
});

describe('NavSubItem prints a number only when it has one', () => {
  it('lights the dot and prints the figure for a scored client', () => {
    const markup = html(<NavSubItem href="/clients/1" label="Pirsch" score={33.93} />);
    expect(markup).toContain('avp-nav__dot');
    expect(markup).not.toContain('avp-nav__dot--empty');
    expect(markup).toContain('>34<');
    expect(markup).toContain('score 34 out of 100');
  });

  it('draws a hollow dot and no figure for a client scored as having no reading', () => {
    const markup = html(<NavSubItem href="/clients/1" label="Fathom" score={null} note="…" />);
    expect(markup).toContain('avp-nav__dot--empty');
    expect(markup).not.toContain('score ');
    expect(markup).toContain('…');
  });

  it('draws no dot at all when the list does not know', () => {
    const markup = html(<NavSubItem href="/clients/1" label="Fathom" />);
    expect(markup).not.toContain('avp-nav__dot');
    expect(markup).not.toContain('avp-nav__figure');
  });

  it('marks the current row for a screen reader, not by colour alone', () => {
    expect(html(<NavSubItem href="/clients" label="All clients" current />)).toContain(
      'aria-current="page"',
    );
  });
});

describe('NavHead and NavScore say whose space it is', () => {
  it('carries the way back, the name and the domain', () => {
    const markup = html(
      <NavHead back={{ href: '/clients', label: 'All clients' }} title="Pirsch Analytics" subtitle="pirsch.io">
        <NavScore score={33.93} />
      </NavHead>,
    );
    expect(markup).toContain('href="/clients"');
    expect(markup).toContain('All clients');
    expect(markup).toContain('Pirsch Analytics');
    expect(markup).toContain('pirsch.io');
    expect(markup).toContain('>34<');
    expect(markup).toContain('Barely visible');
  });

  it('renders an unscored client as an absence, never a zero', () => {
    const markup = html(<NavScore score={null} />);
    expect(markup).toContain('Not scored yet');
    expect(markup).not.toContain('>0<');
  });
});

describe('NavGroup and NavItem external', () => {
  it('groups items in a named list', () => {
    const markup = html(
      <NavGroup label="Measurement">
        <NavSlot>
          <NavItem href="/clients/1" label="Overview" accent={0} current />
        </NavSlot>
        <NavSlot>
          <NavItem href="/scans/s/report" label="Report" external />
        </NavSlot>
      </NavGroup>,
    );
    expect(markup).toContain('<ul class="avp-nav__grouplist" aria-label="Measurement">');
    expect((markup.match(/<li /g) ?? []).length).toBe(2);
    expect(markup).toContain('avp-nav__out');
    expect(markup).toContain('aria-current="page"');
  });
});
