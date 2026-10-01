"""Links that carry a credential — the one place their shape is decided.

A share, invitation or password-reset token rides in the URL FRAGMENT
(`/share#<token>`), never in the path. A fragment is not sent to the server, so
it stays out of Render's request log, the access log and any proxy's; the web
page reads it in the browser and posts it in a request body.

Every such link is built here so the three cannot drift back into a path.
"""

from __future__ import annotations


def token_link(base_url: str, page: str, token: str) -> str:
    """`{base}/{page}#{token}`. `page` is a bare segment such as `share`."""
    return f"{base_url.rstrip('/')}/{page.strip('/')}#{token}"


def share_link(base_url: str, token: str) -> str:
    return token_link(base_url, "share", token)


def invite_link(base_url: str, token: str) -> str:
    return token_link(base_url, "invite", token)


def reset_link(base_url: str, token: str) -> str:
    return token_link(base_url, "reset-password", token)
