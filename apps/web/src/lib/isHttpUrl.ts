/**
 * Whether a data-driven value may be a link: scheme http or https, then a host, and nothing a
 * browser would strip or read differently (white space, control characters, DEL).
 *
 * Cited URLs come from AI answers and the API already blanks any other scheme
 * (`links.http_url_or_none`); this is the second check, in the one place every data-driven
 * `href` or `src` in the report and share views goes through. Nothing is trimmed first: a
 * leading space or control character is how `javascript:` gets past a check that looks at the
 * start of the string while the browser looks past it. A value that fails renders as text.
 */
// The excluded set is the same text as `_EXCLUDED` in the API's `services/links.py`, so the two
// agree: controls and space, DEL and C1 controls (U+0085), Unicode spaces, zero-width and bidi
// marks, line and paragraph separators, the byte-order mark, and the BACKSLASH (a browser reads
// it as `/`). The authority (up to the first `/`, `?` or `#`) may carry no `@` (userinfo).
// Without the `u` flag the `i` flag folds ASCII only, so `httpſ://` is refused.
const EXCLUDED =
  '\\u0000-\\u0020\\u007f-\\u009f\\u00a0\\u1680\\u2000-\\u200f\\u2028-\\u202f\\u205f-\\u2064\\u3000\\ufeff\\\\';
const HTTP_URL = new RegExp(`^https?://(?![^/?#]*@)[^${EXCLUDED}/?#][^${EXCLUDED}]*$`, 'i');

export function isHttpUrl(value: string | null | undefined): value is string {
  return typeof value === 'string' && HTTP_URL.test(value);
}
