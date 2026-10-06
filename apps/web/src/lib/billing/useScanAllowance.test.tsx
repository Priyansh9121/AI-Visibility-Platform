// @vitest-environment jsdom

/**
 * What a screen is told about the person at it — `useScanStanding`.
 *
 * The scan-limit notice leaves Subscribe out for a member, because Checkout is
 * owner/admin only and refuses a member with a 403. It learns the role from
 * `GET /auth/me`. That call can fail, and a member's billing read (also
 * owner/admin only) is a 403 as well, so a failed `/auth/me` used to leave the
 * notice with nothing to go on, and it offered Subscribe to the one person who
 * cannot use it. A 403 from the billing read can mean one thing — a member
 * (another agency's id is a 404) — so it stands in for the role when `/auth/me`
 * cannot say. Mounted in jsdom, the API mocked at its edge.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { BillingStatus } from '@avp/shared-types';

const billing = vi.fn();
const me = vi.fn();
const label = vi.hoisted(() => ({ throwNext: false }));

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      billing: (id: string) => billing(id),
      me: () => me(),
    },
  };
});

// The real label, except when a test asks for the one it cannot read.
vi.mock('./allowance', async (importOriginal) => {
  const real = await importOriginal<typeof import('./allowance')>();
  return {
    ...real,
    allowanceLabel: (b: BillingStatus) => {
      if (label.throwNext) {
        label.throwNext = false;
        throw new Error('a billing response the label cannot read');
      }
      return real.allowanceLabel(b);
    },
  };
});

const { useScanStanding } = await import('./useScanAllowance');
const { ApiProblem } = await import('@/lib/api');
const { ScanLimitNotice } = await import('@/components/billing/ScanLimitNotice');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BILLING: BillingStatus = {
  subscriptionStatus: null,
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: false,
  scansUsedThisPeriod: 1,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
  trialEndsAt: null,
  trialEligible: false,
  trialRequiresGoogle: false,
};

const FREE_USED = {
  code: 'SCAN_LIMIT_FREE_USED' as const,
  detail: 'used',
  scansUsed: 1,
  scanLimit: 1,
  periodResetsAt: null,
};

const MEMBER_FREE =
  "Your free scan is used. Ask your agency's owner or an admin to subscribe to keep scanning. The reports you already have stay open.";

const problem = (status: number) =>
  new ApiProblem({
    type: `/problems/status-${status}`,
    title: 'Refused',
    status,
    detail:
      status === 403
        ? 'This action requires one of: admin, owner. Your role is member.'
        : 'Refused.',
    instance: '/x',
  });

const signedInAs = (role: 'owner' | 'admin' | 'member') => ({ user: { role } });

let root: Root | null = null;

/** Mount, and let every promise the hook is waiting on settle. */
async function mount(node: ReactElement): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(node);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return el;
}

function Probe({ agencyId }: { agencyId: string | null }) {
  return <pre data-testid="standing">{JSON.stringify(useScanStanding(agencyId))}</pre>;
}

/** The notice as a screen builds it: the hook's answer handed to it. */
function Screen({ agencyId }: { agencyId: string }) {
  const { billing: b, role } = useScanStanding(agencyId);
  return <ScanLimitNotice problem={FREE_USED} agencyId={agencyId} billing={b} role={role} />;
}

const standing = (el: HTMLElement) =>
  JSON.parse(el.querySelector('[data-testid="standing"]')!.textContent!) as {
    label: string | null;
    billing: BillingStatus | null;
    role: string | null;
    feedbackEmail: string | null;
  };

const offersSubscribe = (el: HTMLElement) =>
  [...el.querySelectorAll('button')].some((b) => b.textContent === 'Subscribe');

beforeEach(() => {
  billing.mockReset();
  me.mockReset();
  label.throwNext = false;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the role, and where it comes from', () => {
  it('an owner: the billing, the label from it, and the role from /auth/me', async () => {
    billing.mockResolvedValue(BILLING);
    me.mockResolvedValue(signedInAs('owner'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(billing).toHaveBeenCalledWith('agcy_01');
    expect(standing(el)).toEqual({
      label: 'Free scan used',
      billing: BILLING,
      role: 'owner',
      feedbackEmail: null,
    });
  });

  it('a member whose /auth/me answers: no billing, no label, and the role it gave', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockResolvedValue(signedInAs('member'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: 'member', feedbackEmail: null });
  });

  it('a member whose /auth/me FAILS: the 403 from the billing read says member', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: 'member', feedbackEmail: null });
  });

  it('a member whose /auth/me is refused too: still a member', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockRejectedValue(problem(500));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el).role).toBe('member');
  });

  it.each([
    ['a server error', problem(500)],
    ['a 401', problem(401)],
    ['a 404', problem(404)],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])('a failed /auth/me beside %s from billing does not guess a member', async (_what, failure) => {
    billing.mockRejectedValue(failure);
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: null, feedbackEmail: null });
  });

  it('a failed /auth/me beside a billing read that worked leaves the role unknown', async () => {
    billing.mockResolvedValue(BILLING);
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({
      label: 'Free scan used',
      billing: BILLING,
      role: null,
      feedbackEmail: null,
    });
  });

  it('the role /auth/me gives is never overruled by the inference', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockResolvedValue(signedInAs('admin'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el).role).toBe('admin');
  });

  it('asks nothing until there is an agency', async () => {
    const el = await mount(<Probe agencyId={null} />);
    expect(billing).not.toHaveBeenCalled();
    expect(me).not.toHaveBeenCalled();
    expect(standing(el)).toEqual({ label: null, billing: null, role: null, feedbackEmail: null });
  });
});

const ADDRESS = 'pilot-feedback@avp-pilot.example';

/**
 * The address the shell's "Send feedback" link writes to (2026-10-06, round 2). The dashboard reads
 * no `me` of its own and used to ask for the address with a second `GET /auth/me`, on every load,
 * whether or not the feature was on. It takes it from THIS read instead: `/auth/me` carries
 * `feedbackEmail`, and this hook already asks for the role. One request, as before.
 */
describe('the address for the "Send feedback" link, from the /auth/me this hook already reads', () => {
  it('is the address /auth/me carries, and /auth/me is still read once', async () => {
    billing.mockResolvedValue(BILLING);
    me.mockResolvedValue({ ...signedInAs('owner'), feedbackEmail: ADDRESS });
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({
      label: 'Free scan used',
      billing: BILLING,
      role: 'owner',
      feedbackEmail: ADDRESS,
    });
    expect(me).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['null: the setting is off, the default', { feedbackEmail: null }],
    ['an API with no such field yet', {}],
    ['something that is not an address', { feedbackEmail: 'pilot.example' }],
    ['two addresses', { feedbackEmail: 'a@pilot.example, b@pilot.example' }],
    ['a number', { feedbackEmail: 25 }],
  ])('is null for %s', async (_what, extra) => {
    billing.mockResolvedValue(BILLING);
    me.mockResolvedValue({ ...signedInAs('owner'), ...extra });
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el).feedbackEmail).toBeNull();
    expect(standing(el).role).toBe('owner');
  });

  it('is null when /auth/me fails, and the role is inferred as it always was', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: 'member', feedbackEmail: null });
  });

  it('does not wait for the billing read, which may never answer', async () => {
    billing.mockReturnValue(new Promise(() => {}));
    me.mockResolvedValue({ ...signedInAs('owner'), feedbackEmail: ADDRESS });
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: null, feedbackEmail: ADDRESS });
  });

  it('is kept when the billing read is refused, as a member’s is', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockResolvedValue({ ...signedInAs('member'), feedbackEmail: ADDRESS });
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: 'member', feedbackEmail: ADDRESS });
  });

  it('is kept when a billing response cannot be read: that standing is unknown, the address is not', async () => {
    label.throwNext = true;
    billing.mockResolvedValue(BILLING);
    me.mockResolvedValue({ ...signedInAs('owner'), feedbackEmail: ADDRESS });
    const el = await mount(<Probe agencyId="agcy_01" />);
    expect(standing(el)).toEqual({ label: null, billing: null, role: null, feedbackEmail: ADDRESS });
  });

  it('asks nothing until there is an agency: no read, and no address', async () => {
    me.mockResolvedValue({ ...signedInAs('owner'), feedbackEmail: ADDRESS });
    const el = await mount(<Probe agencyId={null} />);
    expect(me).not.toHaveBeenCalled();
    expect(standing(el).feedbackEmail).toBeNull();
  });
});

describe('a label that cannot be read', () => {
  it('is caught: nothing is left unhandled, and the standing stays unknown', async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on('unhandledRejection', onUnhandled);
    try {
      label.throwNext = true;
      billing.mockResolvedValue(BILLING);
      me.mockResolvedValue(signedInAs('owner'));
      const el = await mount(<Probe agencyId="agcy_01" />);
      // Node reports an unhandled rejection a turn after it happens.
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(unhandled).toEqual([]);
      expect(standing(el)).toEqual({ label: null, billing: null, role: null, feedbackEmail: null });
    } finally {
      process.off('unhandledRejection', onUnhandled);
    }
  });
});

describe('the scan-limit notice, when /auth/me is down', () => {
  it('a member gets the owner-or-admin sentence and no Subscribe', async () => {
    billing.mockRejectedValue(problem(403));
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Screen agencyId="agcy_01" />);
    expect(el.textContent).toContain(MEMBER_FREE);
    expect(offersSubscribe(el)).toBe(false);
    expect(el.textContent).not.toContain('Open Settings');
  });

  it('an agency whose billing is down for another reason still gets Subscribe', async () => {
    billing.mockRejectedValue(problem(500));
    me.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount(<Screen agencyId="agcy_01" />);
    expect(offersSubscribe(el)).toBe(true);
    expect(el.textContent).not.toContain(MEMBER_FREE);
  });

  it('an owner gets Subscribe', async () => {
    billing.mockResolvedValue(BILLING);
    me.mockResolvedValue(signedInAs('owner'));
    const el = await mount(<Screen agencyId="agcy_01" />);
    expect(offersSubscribe(el)).toBe(true);
  });
});
