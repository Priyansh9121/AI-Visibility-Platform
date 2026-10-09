/**
 * "Sites with the same name" — the body a save sends, and the words around it (M1).
 *
 * `PUT /clients/{id}/other-sites` REPLACES all three lists, and a rival left out of `rivals` has
 * its sites cleared. So every button builds its body from the whole current state: it changes the
 * one thing it is for and sends every rival that holds a confirmed site, so a save can never
 * clear another rival's sites by leaving it out.
 */

import type { ConfirmedSite, OtherSites, ProposedSite, ReplaceOtherSites } from '@avp/shared-types';

export type OtherSitesAction =
  | { kind: 'confirm'; site: ProposedSite }
  | { kind: 'dismiss'; domain: string }
  | { kind: 'undismiss'; domain: string }
  | { kind: 'remove'; site: ConfirmedSite };

const plus = (list: readonly string[], domain: string): string[] =>
  list.includes(domain) ? [...list] : [...list, domain];

export function otherSitesBody(current: OtherSites, action: OtherSitesAction): ReplaceOtherSites {
  const client: string[] = [];
  // Insertion order is the API's order, so the body reads the way the lists do.
  const rivals = new Map<string, string[]>();
  for (const site of current.confirmed) {
    if (action.kind === 'remove' && site.domain === action.site.domain) continue;
    if (site.brandKind === 'client') client.push(site.domain);
    else rivals.set(site.brandDomain, plus(rivals.get(site.brandDomain) ?? [], site.domain));
  }
  let dismissed = [...current.dismissed];
  if (action.kind === 'confirm') {
    const { site } = action;
    if (site.brandKind === 'client') {
      if (!client.includes(site.domain)) client.push(site.domain);
    } else {
      rivals.set(site.brandDomain, plus(rivals.get(site.brandDomain) ?? [], site.domain));
    }
  } else if (action.kind === 'dismiss') {
    dismissed = plus(dismissed, action.domain);
  } else if (action.kind === 'undismiss') {
    dismissed = dismissed.filter((d) => d !== action.domain);
  }
  return {
    client,
    rivals: [...rivals].map(([rivalDomain, sites]) => ({ rivalDomain, sites })),
    dismissed,
  };
}

export const citationsLabel = (n: number): string => `${n} ${n === 1 ? 'citation' : 'citations'}`;

export const OTHER_SITES_COPY = {
  title: 'Sites with the same name',
  intro:
    "A site listed here carries this client's or a rival's name under another country's ending. Once you confirm it, it counts as that brand's own site from the next scan, never as an independent source.",
  confirmedHeading: 'Confirmed',
  empty: 'No site with the same name was cited in the latest scan.',
  nothingWaiting: 'No site with the same name is waiting for a decision.',
  loading: 'Looking for sites with the same name…',
  readFailed: 'The sites with the same name could not be read. Reload the page to try again.',
  saveFailed: 'The change could not be saved.',
  yes: "It's theirs",
  no: 'Not theirs',
  remove: 'Remove',
  sameName: (kind: 'client' | 'rival', brand: string, brandDomain: string) =>
    `Same name as ${brand} (${brandDomain})${kind === 'rival' ? ', a rival' : ''}`,
  whose: (kind: 'client' | 'rival', brand: string) => (kind === 'rival' ? `${brand} (a rival)` : brand),
  confirmed: (domain: string, brand: string) =>
    `${domain} now counts as ${brand}'s own site from the next scan.`,
  dismissed: (domain: string) => `${domain} will not be suggested again.`,
  removed: (domain: string) => `${domain} was removed.`,
  undo: 'Undo',
  dismissedToggle: (n: number) => `Dismissed (${n})`,
  undone: (domain: string) => `${domain} can be suggested again.`,
} as const;
