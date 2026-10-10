"""Links that carry a credential — the one place their shape is decided.

A share, invitation or password-reset token rides in the URL FRAGMENT
(`/share#<token>`), never in the path. A fragment is not sent to the server, so
it stays out of Render's request log, the access log and any proxy's; the web
page reads it in the browser and posts it in a request body.

Every such link is built here so the three cannot drift back into a path.
"""

from __future__ import annotations

import re


def token_link(base_url: str, page: str, token: str) -> str:
    """`{base}/{page}#{token}`. `page` is a bare segment such as `share`."""
    return f"{base_url.rstrip('/')}/{page.strip('/')}#{token}"


def share_link(base_url: str, token: str) -> str:
    return token_link(base_url, "share", token)


def invite_link(base_url: str, token: str) -> str:
    return token_link(base_url, "invite", token)


def reset_link(base_url: str, token: str) -> str:
    return token_link(base_url, "reset-password", token)


# A URL a person may be sent to by a click: scheme http or https, then a host, and nothing in it
# that a browser would strip or read differently (white space, control characters, DEL). Nothing
# is trimmed first: a leading space or control character is how `javascript:` gets past a
# check that looks at the start of the string while the browser looks past it.
#
# ONE excluded set, written the same in `apps/web/src/lib/isHttpUrl.ts` so the two agree: C0
# controls and space, DEL and C1 controls (U+0085 among them), the no-break and other Unicode
# spaces, zero-width and bidi marks, line and paragraph separators, the byte-order mark, and the
# BACKSLASH (a browser reads `\` as `/`, so an address with a backslash before an `@` opens a
# different host from the one its text shows). The authority (up to the first `/`, `?` or `#`)
# may carry no `@`: userinfo is how an address shows one host and goes to another. ASCII-only
# case folding, so a scheme spelled with the long s (U+017F) is not the https scheme.
# (Example addresses are described in words here: `providers.test.ts` reads every address
# written in this source tree, comments included, as a host the code reaches.)
_EXCLUDED = (
    r"\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200f\u2028-\u202f\u205f-\u2064\u3000\ufeff\\"
)
_HTTP_URL = re.compile(
    rf"https?://(?![^/?#]*@)[^{_EXCLUDED}/?#][^{_EXCLUDED}]*", re.IGNORECASE | re.ASCII
)


def http_url_or_none(value: object) -> str | None:
    """`value` when it is a plain http or https URL, otherwise None.

    Cited URLs come from AI answers and are stored with whatever scheme they carried, so every
    URL the report serves (JSON, shared report, PDF, CSV) goes through this: `javascript:`,
    `data:`, `vbscript:`, a relative path, a URL with a leading space or control character, or
    no host at all becomes None. The web has its own `isHttpUrl` with the same rule.
    """
    if isinstance(value, str) and _HTTP_URL.fullmatch(value):
        return value
    return None
