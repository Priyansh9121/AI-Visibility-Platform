import { describe, it, expect } from 'vitest';
import { skipSentence } from './autoRescan';

describe('skipSentence', () => {
  it('says each reason in plain words with the day it is tried again', () => {
    const again = '2026-11-01T00:00:00Z';
    expect(skipSentence('AGENCY_NOT_PAID', again)).toBe(
      'The last monthly rescan did not run: the agency does not have an active subscription. It will be tried again from 1 Nov 2026 (UTC), within about 10 minutes of then. Turn it off and on after subscribing to try sooner.',
    );
    expect(skipSentence('ALLOWANCE_USED', again)).toContain("already used this month's scans");
    expect(skipSentence('ALLOWANCE_USED', again)).not.toContain('Turn it off and on after subscribing');
    expect(skipSentence('USER_NOT_ADMIN', again)).toContain('no longer an owner or admin');
    expect(skipSentence('COULD_NOT_START', again)).toContain('could not be started');
  });

  it('never shows a code it does not know, and leaves the day out when the API sent none', () => {
    const s = skipSentence('SOMETHING_NEW', null);
    expect(s).toBe('The last monthly rescan did not run: the scan was not started.');
    expect(s).not.toContain('SOMETHING_NEW');
  });
});
