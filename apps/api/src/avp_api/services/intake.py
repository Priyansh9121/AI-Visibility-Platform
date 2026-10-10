"""Intake — §5.4 step 1 end to end.

    URL submitted -> crawl homepage/key pages -> LLM classifies industry/niche

Runs synchronously inside the request. The §7 acceptance criterion is
"submitting a URL returns a correctly classified industry within 30 seconds",
and the measured path is a few seconds, so a synchronous call satisfies it
directly and keeps the intake screen a single round trip.

`run_classification` is deliberately independent of the HTTP layer, so Epic 4's
worker can call it for bulk and scheduled scans without going through the API.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import idna
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings, get_settings
from ..errors import Conflict, NotFound
from ..models import ClassificationStatus, Client, ClientKind
from .browser_slot import BROWSER_BUSY, crawler_busy
from .classify import ClassificationOutcome, classify
from .crawl import CrawlResult, crawl_site, normalise_url, registrable_domain

logger = structlog.get_logger(__name__)

_STATUS_MAP = {
    "classified": ClassificationStatus.CLASSIFIED,
    "ambiguous": ClassificationStatus.AMBIGUOUS,
    "unclassifiable": ClassificationStatus.UNCLASSIFIABLE,
}


class InvalidUrl(Conflict):
    """The address is not a website a client could have (409). `parse_domain` raises it.

    One problem type for both reasons, each with a plain sentence as its `detail`, no code,
    and nothing the person typed in it: the host is not a valid hostname, or there is no
    public domain name in it.
    """

    problem_type = "invalid-url"
    title = "That does not look like a website address"


# One label of a hostname as DNS carries it (RFC 1123): 1 to 63 letters, digits and hyphens,
# none at either end (1 + 61 + 1 below). Matched whole with `fullmatch`, because `$` would
# let a newline through.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_MAX_LABEL_LENGTH = 63
# The longest whole name, without the root dot (RFC 1035, 2.3.4).
_MAX_HOSTNAME_LENGTH = 253
# Full stops that are not "." and still end a label: tldextract, `idna` and a browser all read
# the ideographic, full-width and half-width ones that way.
_OTHER_FULL_STOPS = re.compile(
    "[\N{IDEOGRAPHIC FULL STOP}\N{FULLWIDTH FULL STOP}\N{HALFWIDTH IDEOGRAPHIC FULL STOP}]"
)
# Where an address's host ends.
_AFTER_THE_HOST = re.compile(r"[/?#]")

# The two sentences a person is shown. Neither quotes what was typed: the second one did,
# and gave a user name and password written in front of the host back in the 409 body.
_NOT_A_HOSTNAME = (
    "That is not a valid website name. "
    "Names can only contain letters, numbers and hyphens, with dots between the parts. "
    "Enter an address such as example.com."
)
_NO_PUBLIC_DOMAIN = (
    "That address does not contain a public domain name. "
    "Enter a website address such as example.com."
)


def _ascii_label(label: str) -> str:
    """One label of a name as DNS carries it, or `ValueError`."""
    if len(label) > _MAX_LABEL_LENGTH:
        # Encoding never shortens a label, so this one is refused without being encoded.
        raise ValueError("label too long")
    ascii_label = label if label.isascii() else idna.encode(label).decode("ascii")
    if _LABEL.fullmatch(ascii_label) is None:
        raise ValueError("not a hostname label")
    if label.isascii() and ascii_label.startswith("xn--"):
        # Typed as punycode, so it must BE punycode, for a label IDNA allows: decoding applies
        # the rules that encoding the Unicode spelling applies, so a label with a symbol in
        # it is refused spelled either way, and it must encode back to exactly what was
        # typed. tldextract guards this same call against IndexError, so this does too.
        try:
            decoded = idna.decode(ascii_label)
        except (UnicodeError, IndexError) as exc:
            raise ValueError("not punycode for a label IDNA allows") from exc
        if idna.encode(decoded).decode("ascii") != ascii_label:
            raise ValueError("not the canonical spelling of its label")
    return ascii_label


def encode_hostname(host: str) -> str:
    """`host` as DNS carries it: lower-case ASCII, each non-ASCII label IDNA-encoded.

    A label with a non-ASCII character is encoded with the `idna` package (`bücher` ->
    `xn--bcher-kva`). robots.txt is fetched with httpx, which encodes a name with that same
    package, so a name that passes here is one httpx can encode
    (`tests/test_intake_hostname.py` checks that the two agree). Pages are fetched by
    Chromium from `https://{domain}`.

    A label typed as punycode (`xn--...`) must decode, and encode back to exactly what was
    typed. Every label, after that, is 1 to 63 characters of a-z, 0-9 and "-", with no hyphen
    at either end, and the whole name is at most 253 characters. Both lengths are checked
    BEFORE anything is encoded, because encoding never shortens a label or a name: what a
    refusal costs does not grow with what was typed. One trailing dot, the root, names the
    same host and is dropped; a second is an empty label.

    Raises `ValueError` for anything else: a space, a quote, a bracket, a control
    character, an empty label, a label too long, a name too long, an IPv6 literal (its
    brackets and colons are not name characters). `UnicodeError`, which `idna` raises for a
    label it will not encode, is a `ValueError`. An IPv4 literal is a well-formed name, and
    is refused by `parse_domain` for having no public suffix.
    """
    name = _OTHER_FULL_STOPS.sub(".", host.lower()).removesuffix(".")
    if len(name) > _MAX_HOSTNAME_LENGTH:
        raise ValueError("hostname too long")  # and encoding would not make it shorter
    encoded = ".".join(_ascii_label(label) for label in name.split("."))
    if len(encoded) > _MAX_HOSTNAME_LENGTH:
        raise ValueError("hostname too long")
    return encoded


def dns_form(domain: str) -> str:
    """`domain` in DNS form: the one string a host has, however it was spelled.

    `encode_hostname`, or `domain` as it was given when that refuses it. A cited domain keeps the
    spelling the engine gave and `rival_domain` the spelling typed, so one host arrives as
    `bücher.de` and as `xn--bcher-kva.de`, or as `пример.рф` and as `пример.xn--p1ai`; each
    pair is the one string here, lower case ASCII. That makes it the form two domains are
    COMPARED in, and the form a domain is STORED in where a list must hold one entry per host
    (the other-sites lists, `services/other_sites.py`: JSONB, which a database whose encoding is
    not UTF8 will not hold a non-ASCII character in). `clients.domain` and `competitors.domain`
    keep the spelling they were typed in.

    It raises nothing. A name `encode_hostname` refuses (an underscore, a label over 63
    characters, an IP literal, a client stored before launch checklist 3.67) cannot be put in
    DNS form, and comes back unchanged to be compared as it is. It is a fixed point, so a form
    that was stored compares as itself: `dns_form(dns_form(x)) == dns_form(x)`.
    """
    try:
        return encode_hostname(domain)
    except ValueError:
        return domain


def _host_of(url: str) -> str:
    """The host of a normalised URL, as it was typed.

    Scheme, userinfo, port, path, query and fragment are cut off at the places tldextract
    cuts them, and this string is the one handed to it, so the name that is checked is the
    name it splits. The text itself is not normalised in any way: a tab or a newline inside
    it is still there to be refused (`urlsplit` would delete it).
    """
    authority = _AFTER_THE_HOST.split(url.partition("://")[2], maxsplit=1)[0]
    return authority.rpartition("@")[2].partition(":")[0]


def _require_hostname(name: str) -> None:
    try:
        encode_hostname(name)
    except ValueError as exc:
        raise InvalidUrl(detail=_NOT_A_HOSTNAME) from exc


def parse_domain(raw_url: str) -> tuple[str, str]:
    """Validate a submitted URL and return (normalised_url, registrable_domain).

    Refuses with `InvalidUrl` (409), before anything is stored or fetched, what cannot be a
    prospect's website:

    - an address whose host is not a valid hostname (`encode_hostname`): a space, a quote, a
      bracket, a control character, an empty label, a label over 63 characters or with a
      hyphen at either end, a name over 253. EVERY label of the host counts, subdomains
      included, and the name that is stored is checked again, so what reaches `clients.domain`
      and from there a model prompt is a name DNS would carry. The sentence says what a name
      is made of.
    - an address with no public domain name in it — an IP address, `localhost`, a bare
      suffix such as `co.uk`, or a bare word. Letting one through creates a client row that
      can never be scanned.

    Neither sentence quotes anything the person typed, a user name and password in front of
    the host included.

    The domain comes back as it always has: lower-case, and Unicode for an internationalised
    name (`bücher.de`). Its punycode form is what was checked, not what is kept.
    """
    try:
        url = normalise_url(raw_url)
    except ValueError as exc:
        # Empty, no host, or what `urlparse` raised ("Invalid IPv6 URL" for a bracket):
        # the library's words are not a sentence for a person.
        raise InvalidUrl(detail=_NOT_A_HOSTNAME) from exc

    host = _host_of(url)
    _require_hostname(host)

    domain = registrable_domain(host)
    if not domain or "." not in domain:
        raise InvalidUrl(detail=_NO_PUBLIC_DOMAIN)
    _require_hostname(domain)
    return url, domain


NOT_THE_RIVALS_OWN_DOMAIN = (
    "Enter just the domain, like rival.com: no https://, page path or subdomain."
)


def rival_domain(raw: str) -> str:
    """A competitor's domain typed by a person, in the form it is stored, or `InvalidUrl`.

    Accepted only when, after lower-casing, dropping ONE trailing dot and ONE leading `www.`, it
    equals its own registrable domain as `parse_domain` checks it. It is never reduced: a pasted
    page (a review site's product page), a path, a port or a subdomain would reduce to some
    OTHER site's domain (a review platform, the client's own), and what is stored there re-types
    that site's citations as the rival's. Refused with `InvalidUrl`: intake's own sentence when the
    name is not a hostname or has no public domain, else `NOT_THE_RIVALS_OWN_DOMAIN`. No sentence
    quotes what was typed.
    """
    candidate = raw.strip().lower().removesuffix(".").removeprefix("www.")
    _url, domain = parse_domain(candidate)
    if domain != candidate:
        raise InvalidUrl(detail=NOT_THE_RIVALS_OWN_DOMAIN)
    return domain


def checked_domain(raw: str) -> str | None:
    """The registrable domain `parse_domain` would store for `raw`, or None if it refuses it.

    Reduces what it is given (`docs.example.com/x` is `example.com`), which is right for the
    client's OWN domain, compared in its checked form by `PUT /clients/{clientId}/competitors`.
    It is NOT what a competitor's domain goes through: a typed domain, and a model's, go through
    `rival_domain`, which refuses a page, path or subdomain, because reducing one names some
    other site (a review platform, the client's own) and re-types that site's citations as the
    rival's. It does not say which way `raw` failed; `parse_domain` does.
    """
    try:
        return parse_domain(raw)[1]
    except InvalidUrl:
        return None


async def check_new_client(session: AsyncSession, *, agency_id: str, raw_url: str) -> str:
    """The read-only checks `create_client_from_url` makes first; returns the domain.

    Raises what it raises (`parse_domain`'s error for a bad URL, `Conflict` 409 for a client
    this agency already tracks) and writes nothing. F3: `POST /clients` with `classify` runs
    this BEFORE it takes a classification unit, so a junk or repeated URL, which never reaches
    a crawl or a model, spends none.
    """
    _url, domain = parse_domain(raw_url)

    existing = (
        await session.execute(
            select(Client).where(
                Client.agency_id == agency_id,
                Client.domain == domain,
                Client.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(
            detail=f"{domain} is already tracked by this agency.",
            clientId=existing.id,
        )
    return domain


async def create_client_from_url(
    session: AsyncSession,
    *,
    agency_id: str,
    raw_url: str,
    name: str | None = None,
) -> Client:
    """Create the client record. Classification is a separate step."""
    domain = await check_new_client(session, agency_id=agency_id, raw_url=raw_url)

    client = Client(
        id=ids.new_id(ids.CLIENT),
        agency_id=agency_id,
        # Provisional. Replaced by the extracted brand name once classified —
        # the domain is a placeholder, not a claim about the business's name.
        name=(name or domain).strip()[:200],
        domain=domain,
        kind=ClientKind.PROSPECT,
        classification_status=ClassificationStatus.PENDING,
    )
    session.add(client)
    await session.flush()
    return client


def apply_outcome(client: Client, outcome: ClassificationOutcome) -> Client:
    """Write a classification outcome onto a client row.

    The `industry` assignment is conditional on purpose: the model's brand name
    and confidence are still useful when the industry call was uncertain, but
    the industry itself is withheld unless the outcome is CLASSIFIED. The
    database enforces the same rule via
    `ck_clients_industry_matches_classification_status`, so a bug here fails
    loudly rather than storing a guess.

    **The model and the time are recorded together, and only when a model was asked**
    (launch checklist 3.71). `outcome.model` is the model `classify` asked, or None when it
    returned before asking one: a crawl that was refused or failed, a page with too little
    text. Then neither is recorded, and what an earlier classification recorded is cleared
    with the rest of it, since this outcome replaces that classification's industry and
    status. A call that was made and failed names the model and gets the time of the write:
    the attempt, with the status and reason code saying that nothing came back.
    """
    client.classification_status = _STATUS_MAP[outcome.status]
    client.classification_reason_code = outcome.reason_code
    client.industry_confidence = outcome.confidence
    client.industry_confidence_score = outcome.confidence_score
    client.classifier_model = outcome.model
    client.classified_at = datetime.now(UTC) if outcome.model is not None else None

    if outcome.status == "classified":
        client.industry = outcome.industry
        client.industry_niche = outcome.niche
    else:
        client.industry = None
        client.industry_niche = None

    if outcome.brand_name:
        client.brand_name = outcome.brand_name.strip()[:200]
        # Promote the real business name over the domain placeholder, but never
        # overwrite a name an operator typed in themselves.
        if client.name == client.domain:
            client.name = client.brand_name
    return client


async def run_classification(
    session: AsyncSession,
    client: Client,
    *,
    settings: Settings | None = None,
    max_pages: int = 3,
) -> tuple[Client, CrawlResult]:
    """Crawl and classify an existing client. Persists the outcome.

    Returns the crawl result alongside the client so the caller can surface
    crawl FACTS (pages fetched, schema types) without this function having to
    persist them — Epic 6's technical audit owns that table.

    **A crawl that never got the browser is refused, not recorded** (M1 round 3).
    When another run held the process's one browser for the whole wait
    (`BROWSER_BUSY`), the site was never read, so there is nothing to classify and
    nothing to write: this raises `RateLimited` (429, `Retry-After`; `crawler_busy`)
    BEFORE `classify` or `apply_outcome`, and the client is left exactly as it was
    (for a new client the caller's session rolls back its row, since nothing here
    commits). Recording it would leave a client `unclassifiable`, or replace a good
    classification, because the crawler happened to be busy, which says nothing about
    the client's site. So no client can carry `BROWSER_BUSY` as its reason.
    """
    settings = settings or get_settings()

    crawl = await crawl_site(f"https://{client.domain}", max_pages=max_pages, client_id=client.id)
    if crawl.error_code == BROWSER_BUSY:
        raise crawler_busy()
    outcome = await classify(crawl, settings=settings)
    apply_outcome(client, outcome)
    await session.flush()

    logger.info(
        "intake.classified",
        client_id=client.id,
        domain=client.domain,
        status=outcome.status,
        industry=outcome.industry,
        reason=outcome.reason_code,
        **crawl.redacted(),
    )
    return client, crawl


async def get_client(session: AsyncSession, *, agency_id: str, client_id: str) -> Client:
    """Fetch one client, scoped to the caller's agency.

    A client belonging to another agency returns 404, not 403 — confirming that
    an id exists is itself a leak across a tenant boundary.
    """
    if not ids.is_valid(client_id, ids.CLIENT):
        raise NotFound(detail="No client with that identifier.")

    client = (
        await session.execute(
            select(Client).where(
                Client.id == client_id,
                Client.agency_id == agency_id,
                Client.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="No client with that identifier.")
    return client
