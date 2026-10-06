/**
 * The "Send feedback" link's email — 2026-10-06.
 *
 * A signed-in user can open an email to the address the API's `FEEDBACK_EMAIL` names, from the
 * foot of the sidebar and from Settings, with the page they were on in the subject. The API
 * sends the address on `GET /auth/me` (`feedbackEmail`, null when unset: no link at all), and
 * this module is everything the web does with it. Pure, so every rule below is tested without
 * a browser.
 *
 * THE SUBJECT CARRIES THE ROUTE'S SHAPE, NEVER ITS VALUES
 * -------------------------------------------------------
 * `/clients/clnt_01J8…/report` is written `/clients/…/report`. A path segment is kept only when
 * it is one of the app's OWN fixed segment names (`ROUTE_SEGMENTS`, the folders of `src/app`);
 * any other segment becomes "…", and the query and the fragment are dropped. So no client id, scan
 * id, share token, invitation or reset token, and no client's name can reach the subject: they
 * are not in the list, and a value is never added to the list. A new route's name is "…" until
 * somebody adds it, which fails closed; `subject.test.ts` holds the list to the route tree in
 * both directions, so it cannot go stale in either.
 *
 * It is a pathname and nothing else that is read (`usePathname`), never `location.href`, which is
 * where a query and a fragment live; `/share#<token>` and `/reset-password#<token>` carry their
 * token in the fragment, and are not drawn inside the shell in any case.
 *
 * THE ADDRESS IS CHECKED AND ENCODED, NOT TRUSTED
 * -----------------------------------------------
 * The API checks it as one email address at start, and the web still builds the link as if it had
 * not been: `?`, `&`, `,` or a newline in the address would otherwise be a second recipient or a
 * header of the message (`mailto:` takes `to`, `cc`, `bcc` and `body` after a `?`). So an address
 * with any of those, or any whitespace, is no address here and draws no link, and what is left is
 * percent-encoded as the subject is. `feedbackMailto` is the only way to a link and takes what the
 * API sent as it came, so there is no route to one that skips the check.
 */

/**
 * The app's own fixed route segments: every folder of `apps/web/src/app` that is not a dynamic
 * `[segment]`. Hand-kept, and held to the tree by `subject.test.ts` both ways: a folder that is
 * missing here fails the suite, and so does an entry that is not a folder, so a value can never
 * be added to this list by accident or dressed up as a name.
 */
export const ROUTE_SEGMENTS: readonly string[] = [
  'alerts',
  'clients',
  'competitors',
  'crawler',
  'dashboard',
  'gaps',
  'google',
  'invite',
  'privacy',
  'prompts',
  'rankings',
  'report',
  'reset-password',
  'scans',
  'sentiment',
  'settings',
  'share',
  'sign-up',
  'sources',
  'technical',
  'terms',
  'welcome',
];

/** What stands for a segment that is not one of the app's own names (U+2026). */
export const ELIDED = '…';

/** What the subject starts with. */
export const SUBJECT_PREFIX = 'AVP feedback';

const KEPT: ReadonlySet<string> = new Set(ROUTE_SEGMENTS);

/**
 * A pathname with every segment that is not one of the app's own names replaced by "…", and
 * its query and fragment dropped: `/clients/clnt_1/alerts?x=1#y` is `/clients/…/alerts`.
 * `/` stays `/`. Empty segments (a doubled or a trailing slash) are not segments.
 */
export function routeShape(pathname: string): string {
  const path = pathname.split(/[?#]/, 1)[0] ?? '';
  const segments = path.split('/').filter((segment) => segment !== '');
  return `/${segments.map((segment) => (KEPT.has(segment) ? segment : ELIDED)).join('/')}`;
}

/**
 * The subject: `AVP feedback: /clients/…/alerts`. With no pathname to go on (null: not inside
 * the router) it is `AVP feedback` and says nothing about where the user was.
 */
export function feedbackSubject(pathname: string | null | undefined): string {
  if (typeof pathname !== 'string') return SUBJECT_PREFIX;
  return `${SUBJECT_PREFIX}: ${routeShape(pathname)}`;
}

/**
 * Characters that make a string something other than ONE address in a `mailto:`: whitespace and
 * control characters, and the ones `mailto:` gives a meaning (`?` and `&` start and join the
 * headers and the body, `,` and `;` join recipients, `<` `>` `"` `\` wrap or escape a name, `#`
 * starts a fragment). The API's own check lets a few of these through in an address's local part
 * (RFC 5322 allows `?` and `&` there); nobody's feedback address has one, and refusing is the
 * safe direction here.
 */
const NOT_IN_ONE_ADDRESS = /[\s\u0000-\u001f\u007f,;<>"\\?&#]/u;

/**
 * The address the API sent, or null when there is no link to draw: not a string, blank, or not
 * one address (see `NOT_IN_ONE_ADDRESS`; exactly one `@`, text on both sides of it). Anything but
 * a usable address is "no link", never a broken one.
 */
export function feedbackAddress(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const address = value.trim();
  if (address === '' || NOT_IN_ONE_ADDRESS.test(address)) return null;
  const parts = address.split('@');
  if (parts.length !== 2 || parts[0] === '' || parts[1] === '') return null;
  return address;
}

/**
 * The link, from what the API sent: `mailto:<address>?subject=<subject>`, both percent-encoded (a
 * space is `%20`, which `mailto:` needs, and not `+`), the one `@` left as it is. Null when there
 * is no usable address (see `feedbackAddress`), and then nothing is drawn.
 */
export function feedbackMailto(address: unknown, pathname: string | null | undefined): string | null {
  const to = feedbackAddress(address);
  if (to === null) return null;
  return `mailto:${encodeURIComponent(to).replace(/%40/g, '@')}?subject=${encodeURIComponent(
    feedbackSubject(pathname),
  )}`;
}
