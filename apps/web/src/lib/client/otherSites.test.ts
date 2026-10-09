import { describe, it, expect } from 'vitest';
import type { OtherSites } from '@avp/shared-types';
import { otherSitesBody, citationsLabel, OTHER_SITES_COPY as COPY } from './otherSites';

// The RMIT shape: rmit.edu.vn is proposed for the client, monash.edu.my for a rival that already
// holds a confirmed site.
const state: OtherSites = {
  confirmed: [
    { domain: 'monash.edu.sg', brandKind: 'rival', brandName: 'Monash University', brandDomain: 'monash.edu' },
    { domain: 'unimelb.edu.my', brandKind: 'rival', brandName: 'Melbourne', brandDomain: 'unimelb.edu.au' },
    { domain: 'rmit.edu.sg', brandKind: 'client', brandName: 'RMIT University', brandDomain: 'rmit.edu.au' },
  ],
  proposals: [
    { domain: 'rmit.edu.vn', citations: 4, brandKind: 'client', brandName: 'RMIT University', brandDomain: 'rmit.edu.au' },
    { domain: 'monash.edu.my', citations: 1, brandKind: 'rival', brandName: 'Monash University', brandDomain: 'monash.edu' },
  ],
  dismissed: ['old.example'],
};

describe('otherSitesBody', () => {
  it('confirming the client’s proposal adds it to the client list and keeps every rival', () => {
    expect(otherSitesBody(state, { kind: 'confirm', site: state.proposals[0]! })).toEqual({
      client: ['rmit.edu.sg', 'rmit.edu.vn'],
      rivals: [
        { rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] },
        { rivalDomain: 'unimelb.edu.au', sites: ['unimelb.edu.my'] },
      ],
      dismissed: ['old.example'],
    });
  });

  it('confirming a rival’s proposal adds it to that rival and keeps the other rival and the client', () => {
    expect(otherSitesBody(state, { kind: 'confirm', site: state.proposals[1]! })).toEqual({
      client: ['rmit.edu.sg'],
      rivals: [
        { rivalDomain: 'monash.edu', sites: ['monash.edu.sg', 'monash.edu.my'] },
        { rivalDomain: 'unimelb.edu.au', sites: ['unimelb.edu.my'] },
      ],
      dismissed: ['old.example'],
    });
  });

  it('confirming for a rival with no site yet adds that rival', () => {
    const body = otherSitesBody(
      { ...state, confirmed: [] },
      { kind: 'confirm', site: state.proposals[1]! },
    );
    expect(body).toEqual({
      client: [],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.my'] }],
      dismissed: ['old.example'],
    });
  });

  it('dismissing adds the domain to dismissed and changes nothing else', () => {
    expect(otherSitesBody(state, { kind: 'dismiss', domain: 'rmit.edu.vn' })).toEqual({
      client: ['rmit.edu.sg'],
      rivals: [
        { rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] },
        { rivalDomain: 'unimelb.edu.au', sites: ['unimelb.edu.my'] },
      ],
      dismissed: ['old.example', 'rmit.edu.vn'],
    });
  });

  it('removing a rival’s only site leaves that rival out, and keeps the others', () => {
    expect(otherSitesBody(state, { kind: 'remove', site: state.confirmed[1]! })).toEqual({
      client: ['rmit.edu.sg'],
      rivals: [{ rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] }],
      dismissed: ['old.example'],
    });
  });

  it('removing the client’s site empties the client list', () => {
    expect(otherSitesBody(state, { kind: 'remove', site: state.confirmed[2]! }).client).toEqual([]);
  });

  it('undismissing removes only that domain, keeps the order of the rest, and keeps every rival and the client', () => {
    const many: OtherSites = { ...state, dismissed: ['a.example', 'old.example', 'z.example'] };
    expect(otherSitesBody(many, { kind: 'undismiss', domain: 'old.example' })).toEqual({
      client: ['rmit.edu.sg'],
      rivals: [
        { rivalDomain: 'monash.edu', sites: ['monash.edu.sg'] },
        { rivalDomain: 'unimelb.edu.au', sites: ['unimelb.edu.my'] },
      ],
      dismissed: ['a.example', 'z.example'],
    });
  });

  it('undismissing the last one leaves dismissed empty; an unknown domain changes nothing', () => {
    expect(otherSitesBody(state, { kind: 'undismiss', domain: 'old.example' }).dismissed).toEqual([]);
    expect(otherSitesBody(state, { kind: 'undismiss', domain: 'nope.example' }).dismissed).toEqual([
      'old.example',
    ]);
  });

  it('never lists a domain twice', () => {
    const again = otherSitesBody(state, { kind: 'dismiss', domain: 'old.example' });
    expect(again.dismissed).toEqual(['old.example']);
  });
});

describe('copy', () => {
  it('counts citations in the singular and plural', () => {
    expect(citationsLabel(1)).toBe('1 citation');
    expect(citationsLabel(4)).toBe('4 citations');
    expect(citationsLabel(0)).toBe('0 citations');
  });

  it('says each outcome naming the domain and the brand', () => {
    expect(COPY.confirmed('rmit.edu.vn', 'RMIT University')).toBe(
      "rmit.edu.vn now counts as RMIT University's own site from the next scan.",
    );
    expect(COPY.dismissed('rmit.edu.vn')).toBe('rmit.edu.vn will not be suggested again.');
    expect(COPY.removed('rmit.edu.vn')).toBe('rmit.edu.vn was removed.');
    expect(COPY.undone('rmit.edu.vn')).toBe('rmit.edu.vn can be suggested again.');
    expect(COPY.dismissedToggle(2)).toBe('Dismissed (2)');
    expect(COPY.undo).toBe('Undo');
    expect(COPY.nothingWaiting).toBe('No site with the same name is waiting for a decision.');
  });
});
