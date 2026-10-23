"""The numbers `PUT /clients/{clientId}/other-sites` is bounded by — A8b, 2026-10-11.

That request replaces three lists of domains, and its schema (`schemas/other_sites.py`) bounds them
with the numbers here. They are in a module of their own, importing nothing of the API, because
`config.py` checks the size limit of the route against `largest_plain_body_bytes()` when the API
starts (`Settings`, `MAX_OTHER_SITES_BODY_BYTES`), and `config.py` cannot import the schema module:
that pulls in every schema and every service, and `config.py` is read by all of them, and by the
migration step the container runs before it serves, which needs none of it.

Why the lists are bounded this low. FastAPI reads and parses a whole body before the session is
looked at, so the most a stranger can make the one worker hold on this route is its size limit
(`body_limit.py`). The schema once let a list carry 1,000 domains: a body of 3 MB. The service's own
caps are 20 sites for a brand and 200 dismissed (`services/other_sites.py`), so the schema now cuts
a list off a little above them, and the largest body is about 200 KB.
"""

from __future__ import annotations

import json
from functools import cache

# The longest hostname DNS carries (`clients.domain` and `competitors.domain` are 253 too). A bound
# on what can be typed into the request, not a rule about domains: `intake.rival_domain` is that.
MAX_DOMAIN_LENGTH = 253

# The most rivals a client's list can hold (`ReplaceCompetitorsRequest.competitors`). A request that
# names more can only name some that are not on the list, so it is refused at the door.
MAX_RIVALS = 10

# The longest list of sites one brand may be sent with, the client's or a rival's: a little above
# the service's own cap (20), so a list of 21 to 50 is still answered with the service's sentence.
MAX_SITES_PER_BRAND_LIST = 50

# The longest dismissed list: a little above the service's own cap (200), for the same reason.
MAX_DISMISSED_LIST = 250


@cache
def largest_plain_body_bytes() -> int:
    """The largest body the other-sites request allows, in bytes, in plain characters.

    Every list full, every domain 253 ASCII characters, compact JSON (no space after a comma or a
    colon), the keys the web app sends. Computed from the numbers above, so it moves when they do.
    `tests/test_request_body_limit.py` holds it equal to the size built from the request model's
    own JSON schema, which is what would catch a key renamed in the model and not here.
    """
    domain = "a" * MAX_DOMAIN_LENGTH
    body = {
        "client": [domain] * MAX_SITES_PER_BRAND_LIST,
        "rivals": [
            {"rivalDomain": domain, "sites": [domain] * MAX_SITES_PER_BRAND_LIST}
        ]
        * MAX_RIVALS,
        "dismissed": [domain] * MAX_DISMISSED_LIST,
    }
    return len(json.dumps(body, separators=(",", ":")).encode())
