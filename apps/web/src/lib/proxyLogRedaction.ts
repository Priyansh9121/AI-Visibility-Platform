/**
 * Keeps credentials out of the proxy's failure log.
 *
 * When the API is unreachable, Next's rewrite proxy calls
 * `console.error('Failed to proxy <target>', err)` with the full target URL.
 * That URL can carry the Google callback's `?code=&state=` or a legacy
 * `/reports/<token>` path, and the error can echo them. House rule: no secret,
 * token or email in a log. This mirrors `redact_path` in
 * apps/api/src/avp_api/logs.py. Every other console.error passes through.
 *
 * Next also warns, through console.warn, when a request body passes its 10 MB
 * clone limit, and that line carries the request's path and query. The same
 * reduction applies to it; every other console.warn passes through.
 */

const PREFIX = 'Failed to proxy ';
const BODY_LIMIT_PREFIX = 'Request body exceeded ';
// `Request body exceeded 10MB for /path?query. Only the first 10MB will be…`
const BODY_LIMIT_LINE = /^(Request body exceeded \S+ for )([\s\S]*?)(\. Only the first [\s\S]*)$/;
const REDACTED = '…';
const OPEN_REPORT_ROUTES = new Set(['shared', 'shared/pdf']);
const INSTALLED = Symbol.for('avp.proxyLogRedaction');

/** Origin plus path of a proxy target, with query, fragment and report tokens removed. */
export function redactProxyTarget(target: string): string {
  const cut = target.split(/[?#]/, 1)[0] ?? '';
  const marker = '/reports/';
  const at = cut.indexOf(marker);
  if (at === -1) return cut;
  const head = cut.slice(0, at + marker.length);
  const rest = cut.slice(at + marker.length);
  if (OPEN_REPORT_ROUTES.has(rest)) return cut;
  return head + REDACTED + (rest.endsWith('.pdf') ? '.pdf' : '');
}

function errorLabel(err: unknown): string {
  if (err && typeof err === 'object') {
    const { code, name } = err as { code?: unknown; name?: unknown };
    if (typeof code === 'string' && code !== '') return code;
    if (typeof name === 'string' && name !== '') return name;
  }
  return 'Error';
}

/** Next's body-limit warning with its path and query reduced the same way. */
export function redactBodyLimitWarning(line: string): string {
  const match = BODY_LIMIT_LINE.exec(line);
  if (!match) return `${BODY_LIMIT_PREFIX}the limit.`;
  return `${match[1] ?? ''}${redactProxyTarget(match[2] ?? '')}${match[3] ?? ''}`;
}

/** Wrap console.error and console.warn once. Calling it again does nothing. */
export function installProxyLogRedaction(): void {
  const currentWarn = console.warn as typeof console.warn & { [INSTALLED]?: true };
  if (!currentWarn[INSTALLED]) {
    const originalWarn = console.warn;
    const wrappedWarn = ((...args: unknown[]) => {
      const [first] = args;
      if (typeof first === 'string' && first.startsWith(BODY_LIMIT_PREFIX)) {
        originalWarn.call(console, redactBodyLimitWarning(first), ...args.slice(1));
        return;
      }
      originalWarn.apply(console, args);
    }) as typeof console.warn & { [INSTALLED]?: true };
    wrappedWarn[INSTALLED] = true;
    console.warn = wrappedWarn;
  }
  const current = console.error as typeof console.error & { [INSTALLED]?: true };
  if (current[INSTALLED]) return;
  const original = console.error;
  const wrapped = ((...args: unknown[]) => {
    const [first, second] = args;
    if (typeof first === 'string' && first.startsWith(PREFIX)) {
      const line = PREFIX + redactProxyTarget(first.slice(PREFIX.length));
      original.call(console, line, ...(args.length > 1 ? [errorLabel(second)] : []));
      return;
    }
    original.apply(console, args);
  }) as typeof console.error & { [INSTALLED]?: true };
  wrapped[INSTALLED] = true;
  console.error = wrapped;
}
