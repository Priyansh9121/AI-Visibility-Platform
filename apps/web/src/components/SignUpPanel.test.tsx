/**
 * Create an agency — Epic 9.12.
 *
 * Static render only, per Epic 9.11's finding: this repo has no DOM-driving
 * test library, and adding one would need a licence review under
 * ip-safety.md #6. Submit-path behaviour is asserted where it is testable —
 * the API's own auth tests already cover 201, 409 and 422.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SignUpPanel } from './SignUpPanel';

const render = () =>
  renderToStaticMarkup(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} />);

describe('it collects exactly what the endpoint requires', () => {
  it('has a field for each of the four required properties', () => {
    // SignUpRequest.required = [agencyName, fullName, email, password].
    // Read off the generated OpenAPI schema, not guessed.
    const out = render();
    expect(out).toContain('Agency name');
    expect(out).toContain('Your name');
    expect(out).toContain('Email');
    expect(out).toContain('Password');
  });

  it('asks for nothing the endpoint does not accept', () => {
    const out = render();
    // No plan picker, no billing, no seat count. north-star.md §5.4 has not
    // decided pricing, and putting a plan in front of a sign-up form would
    // imply a decision that has not been made.
    expect(out).not.toMatch(/\$\s?\d/);
    expect(out).not.toContain('Plan');
    expect(out).not.toContain('Card number');
    expect(out).not.toContain('Seats');
    expect(out).not.toContain('Confirm password');
  });

  it('marks every field required so the browser catches an empty submit', () => {
    expect(render().match(/required=""/g)).toHaveLength(4);
  });
});

describe('it describes the password rule without re-implementing it', () => {
  it('states the server minimum', () => {
    // api-contracts.md: 12-256 characters, NIST SP 800-63B.
    expect(render()).toContain('At least 12 characters');
  });

  it('imposes no composition rule of its own', () => {
    const out = render();
    // Composition rules push people toward predictable substitutions, which is
    // exactly why the API does not have any. The form must not invent one.
    expect(out).not.toContain('uppercase');
    expect(out).not.toContain('special character');
    expect(out).not.toMatch(/pattern="/);
  });

  it('tells the password manager this is a NEW credential', () => {
    // `new-password` rather than `current-password`, or a manager offers the
    // existing one instead of generating.
    expect(render().toLowerCase()).toContain('autocomplete="new-password"');
  });
});

describe('it is honest about what an agency is', () => {
  it('says the agency name is what appears on reports', () => {
    expect(render()).toContain('appears on every report you send');
  });

  it('says the account is the owner seat', () => {
    // The API makes the first user of an agency the owner, always.
    expect(render()).toContain('the owner');
  });
});

describe('it offers a route back to sign-in', () => {
  it('links rather than describing an offline process', () => {
    const out = render();
    expect(out).toContain('Already have an account?');
    expect(out).toContain('avp-btn');
  });
});

describe('no ad hoc styling', () => {
  it('uses the width token and emits no off-system class', () => {
    const out = render();
    expect(out).toContain('max-w-form');
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('shows no error state before anything is submitted', () => {
    const out = render();
    expect(out).not.toContain('avp-errorstate');
    expect(out).not.toContain('avp-field__error');
  });
});
