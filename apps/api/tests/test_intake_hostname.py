"""Intake refuses an address whose host is not a valid public hostname — launch checklist 3.67.

The finding (pilot-fixes-4, S1, Low): `parse_domain` took whatever tldextract read as the
registrable domain and never asked whether it was a hostname. `exa mple.com`, `a"b.com` and
`ex<NUL>ample.com` all came back as a domain, were stored in `clients.domain` (a String(253)
with no CHECK) and went from there into model prompts (`Website: {domain}`) and the
co-citation subject.

The rule now. It is checked on the host as it was typed (every label of it, subdomains
included) and again on the registrable domain that is stored:

  * each label, after IDNA encoding, is 1-63 characters of a-z, 0-9 and "-", and neither
    starts nor ends with "-"; a label typed as punycode (`xn--...`) must decode, and encode
    back to exactly what was typed (review finding L2);
  * the whole name is at most 253 characters; one trailing dot (the root) is the same name,
    a second one is an empty label. Both lengths are checked BEFORE anything is encoded,
    because encoding never shortens a label or a name (L3);
  * the registrable domain has a public suffix (tldextract's, as before), which is what
    keeps an IP address, `localhost` and a bare suffix such as `co.uk` out.

An internationalised domain is encoded with the `idna` package. robots.txt is fetched with
httpx, which encodes a name with that same package, so "valid here" means "encodable there"
(`TestInternationalisedNames` proves the two agree); pages are fetched by Chromium from
`https://{domain}`. The domain is STORED as it always was, lower-case Unicode (`bücher.de`):
the punycode form is what is checked, not what is kept.

Refused means: 409 `invalid-url`, one of two plain sentences with no code, and neither quotes
anything the person typed (the "no public domain" one used to, and gave a user name and
password written in front of the host back in the 409 body: L1). Nothing is stored and
nothing is fetched. No network, no browser, no model: the crawl is replaced by one that fails
the test if it is called.
"""

from __future__ import annotations

import random
import re
import unicodedata
from typing import NoReturn

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from avp_api.models import Client
from avp_api.services import intake as intake_service
from avp_api.services.intake import InvalidUrl, encode_hostname, parse_domain

BASE = "/api/v1"

# The two sentences a person is shown, word for word. Pinned here rather than imported, so a
# change to either is a change somebody made on purpose. Neither names a code, neither quotes
# anything the person typed, and both say what to do.
NOT_A_HOSTNAME = (
    "That is not a valid website name. "
    "Names can only contain letters, numbers and hyphens, with dots between the parts. "
    "Enter an address such as example.com."
)
# For an address with no registrable domain in it. It used to be `f"{raw!r} does not contain
# ..."`, which handed whatever was typed back, a user name and password before the host included.
NO_PUBLIC_DOMAIN_SENTENCE = (
    "That address does not contain a public domain name. "
    "Enter a website address such as example.com."
)


# A name of exactly 253 characters (63 + 63 + 63 + 57 + 3, and four dots), the longest there is.
LONGEST = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 57, "com"])
TOO_LONG = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 58, "com"])

VALID = [
    pytest.param("example.com", "example.com", id="plain"),
    pytest.param("sub.example.co.uk", "example.co.uk", id="two-label-suffix"),
    pytest.param("EXAMPLE.COM", "example.com", id="upper-case-folded"),
    pytest.param("https://Sub.Example.Co.UK/Path?Q=1#Frag", "example.co.uk", id="url-folded"),
    pytest.param("example.com.", "example.com", id="root-dot"),
    pytest.param("https://user:secret@example.com:8443/x", "example.com", id="userinfo-and-port"),
    pytest.param("https://example.com/a path?q=x y", "example.com", id="space-after-the-host"),
    pytest.param("my-site.com", "my-site.com", id="hyphen-inside"),
    pytest.param("a--b.com", "a--b.com", id="two-hyphens-inside"),
    pytest.param("3m.com", "3m.com", id="starts-with-a-digit"),
    pytest.param("x.co", "x.co", id="one-character-label"),
    pytest.param("practice.github.io", "practice.github.io", id="private-suffix"),
    pytest.param("a" * 63 + ".com", "a" * 63 + ".com", id="label-of-63"),
    pytest.param(LONGEST, "d" * 57 + ".com", id="name-of-253"),
    # Typed as punycode, kept as typed (what is stored is never re-spelled: two spellings of
    # one IDN are two stored names, which is a decision for the founder and not this item).
    pytest.param("xn--bcher-kva.de", "xn--bcher-kva.de", id="punycode-as-typed"),
    pytest.param("shop.xn--bcher-kva.de", "xn--bcher-kva.de", id="punycode-with-a-subdomain"),
    pytest.param("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai", id="punycode-under-punycode"),
    pytest.param("XN--BCHER-KVA.DE", "xn--bcher-kva.de", id="punycode-upper-case-folded"),
    # An internationalised domain is stored as it always was: lower-case Unicode.
    pytest.param("bücher.de", "bücher.de", id="idn"),
    pytest.param("BÜCHER.DE", "bücher.de", id="idn-upper-case-folded"),
    pytest.param("shop.bücher.de", "bücher.de", id="idn-with-a-subdomain"),
    pytest.param("bücher。de", "bücher.de", id="idn-ideographic-full-stop"),
    pytest.param("straße.de", "straße.de", id="idn-sharp-s"),
    pytest.param("日本語.jp", "日本語.jp", id="idn-cjk"),
    pytest.param("пример.рф", "пример.рф", id="idn-under-an-idn-suffix"),
    # 55 characters, 61 once encoded: inside the limit that is counted on the encoded form.
    pytest.param("ü" * 55 + ".de", "ü" * 55 + ".de", id="idn-label-of-61-as-punycode"),
]

# What each of those is, as DNS carries it and as httpx encodes it for robots.txt.
IDN_AS_ASCII = [
    ("bücher.de", "xn--bcher-kva.de"),
    ("BÜCHER.DE", "xn--bcher-kva.de"),
    ("shop.bücher.de", "shop.xn--bcher-kva.de"),
    # IDNA 2008 keeps the sharp s. The older codec turns it into "ss", which is another name.
    ("straße.de", "xn--strae-oqa.de"),
    ("日本語.jp", "xn--wgv71a119e.jp"),
    ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
    ("bücher。de", "xn--bcher-kva.de"),
]

# A label typed as punycode that is not what it says it is (review finding L2). Each of these
# is letters, digits and hyphens, which is all that was once asked of an ASCII label.
INVALID_PUNYCODE = {
    "punycode-that-does-not-decode": "xn--zzzz.com",
    "punycode-with-no-valid-a-label": "xn--6224611.com",
    "punycode-of-a-code-point-no-label-may-hold": "xn--a.com",
    "punycode-of-code-points-idna-forbids": "xn--5bwoatz2-bdob3r3r.com",
    "punycode-that-breaks-the-bidi-rule": "xn--c9aca9ddzadc9cb-cbc-czzd.com",
    "punycode-of-a-symbol": "xn--i-7iq.ws",
    "punycode-in-a-subdomain": "xn--zzzz.example.com",
    "punycode-upper-case": "XN--ZZZZ.COM",
}

# Not a hostname: the host, as typed, has a label that DNS would not carry.
NOT_A_NAME = {
    "space": "exa mple.com",
    "space-with-a-scheme-and-a-path": "https://exa mple.com/about",
    "a-phrase": "not a url",
    "underscore": "exa_mple.com",
    "double-quote": 'ex"ample.com',
    "single-quote": "ex'ample.com",
    "open-bracket": "exa[mple.com",
    "close-bracket": "exa]mple.com",
    "brackets": "a[b].com",
    "parentheses": "ex(ample).com",
    "angle-brackets": "ex<ample>.com",
    "braces": "ex{ample}.com",
    "backslash": "exa\\mple.com",
    "pipe": "exa|mple.com",
    "percent-escape": "exa%20mple.com",
    "trailing-comma": "example.com,",
    "comma": "exa,mple.com",
    "nul": "ex\x00ample.com",
    "bell": "ex\x07ample.com",
    "escape": "ex\x1bample.com",
    "delete": "ex\x7fample.com",
    "newline-inside": "exa\nmple.com",
    "tab-inside": "exa\tmple.com",
    "carriage-return-inside": "exa\rmple.com",
    "c1-control": "ex\x85ample.com",
    "zero-width-space": "ex\N{ZERO WIDTH SPACE}ample.com",
    "no-break-space": "ex\N{NO-BREAK SPACE}ample.com",
    "soft-hyphen": "ex\N{SOFT HYPHEN}ample.com",
    "empty-label-inside": "a..b.com",
    "empty-label-before-the-domain": "www..example.com",
    "leading-dot": ".example.com",
    "two-trailing-dots": "example.com..",
    "label-of-64": "a" * 64 + ".com",
    "subdomain-label-of-64": "a" * 64 + ".example.com",
    # 58 characters as typed, and more than 63 once encoded.
    "idn-label-whose-punycode-is-over-63": "ü" * 58 + ".de",
    "leading-hyphen": "-example.com",
    "trailing-hyphen": "example-.com",
    "subdomain-leading-hyphen": "sub.-a.example.com",
    "subdomain-trailing-hyphen": "a-.example.com",
    "name-of-254": TOO_LONG,
    "symbol-idna-forbids": "i❤.ws",
    "full-width-letters": "ｅｘａｍｐｌｅ.com",
    "idn-not-in-normalisation-form-c": "bu\N{COMBINING DIAERESIS}cher.de",
    "idn-label-of-64": "ü" * 64 + ".de",
    **INVALID_PUNYCODE,
}

# A well-formed name with nothing a person could scan: no public suffix, so no registrable
# domain. These had a refusal before this item and keep it; the sentence no longer quotes them.
NO_PUBLIC_DOMAIN = {
    "bare-two-label-suffix": "co.uk",
    "bare-tld": "com",
    "bare-private-suffix": "github.io",
    "localhost": "localhost",
    "localhost-subdomain": "thing.localhost",
    "reserved-tld": "a.example",
    "invalid-tld": "site.invalid",
    "ipv4": "1.2.3.4",
    "ipv4-loopback-with-a-port": "http://127.0.0.1:8000",
    "ipv4-private": "10.0.0.1",
    "ipv4-metadata": "169.254.169.254",
    "ipv4-broadcast": "255.255.255.255",
    "ipv4-hex-octet": "0x7f.0.0.1",
    "ipv4-short": "127.1",
    "ipv4-as-one-number": "2130706433",
}

# An IPv6 literal is in brackets, which a name cannot hold: refused as not a name.
IPV6_LITERALS = ["[::1]", "http://[::1]:8080/", "[2001:db8::1]", "[::ffff:127.0.0.1]"]

# Refused addresses with a sentinel in the user name, the password, the path and the query,
# one for each way an address is refused. Nothing a refusal says may contain it (review
# finding L1: the "no public domain" sentence gave the typed address back, userinfo included).
SENTINEL = "qzsentinelx"
_S = SENTINEL
CARRYING_A_SENTINEL = {
    "bare-suffix": f"https://{_S}:{_S}@co.uk/{_S}?q={_S}",
    "ipv4-with-a-port": f"https://{_S}:{_S}@127.0.0.1:8000/{_S}?q={_S}",
    "localhost": f"https://{_S}:{_S}@localhost/{_S}?q={_S}",
    "no-scheme": f"{_S}:{_S}@localhost/{_S}?q={_S}",
    "space-in-the-host": f"https://{_S}:{_S}@exa mple.com/{_S}?q={_S}",
    "empty-label": f"https://{_S}:{_S}@a..b.com/{_S}?q={_S}",
    "label-of-64": f"https://{_S}:{_S}@{'a' * 64}.com/{_S}?q={_S}",
    "leading-hyphen": f"https://{_S}:{_S}@-a.com/{_S}?q={_S}",
    "brackets": f"https://{_S}:{_S}@a[b].com/{_S}?q={_S}",
    "ipv6-literal": f"https://{_S}:{_S}@[::1]:8000/{_S}?q={_S}",
    "no-host": f"https://{_S}:{_S}@/{_S}?q={_S}",
    "punycode-that-does-not-decode": f"{_S}:{_S}@xn--zzzz.com/{_S}?q={_S}",
    "name-of-254": f"{_S}:{_S}@{TOO_LONG}/{_S}?q={_S}",
}


class TestValidAddresses:
    @pytest.mark.parametrize(("raw", "stored"), VALID)
    def test_accepted_and_stored_as_before(self, raw: str, stored: str) -> None:
        _, domain = parse_domain(raw)
        assert domain == stored

    def test_the_url_is_returned_as_it_always_was(self) -> None:
        assert parse_domain("  Example.COM/Path ") == ("https://Example.COM/Path", "example.com")


class TestInternationalisedNames:
    @pytest.mark.parametrize(("raw", "ascii_name"), IDN_AS_ASCII)
    def test_is_checked_as_punycode(self, raw: str, ascii_name: str) -> None:
        assert encode_hostname(raw) == ascii_name

    @pytest.mark.parametrize(("raw", "ascii_name"), IDN_AS_ASCII)
    def test_the_punycode_is_what_httpx_encodes_for_robots_txt(
        self, raw: str, ascii_name: str
    ) -> None:
        """robots.txt is fetched with httpx, so "valid here" has to mean "httpx can encode it".

        httpx encodes a name with the same `idna` package `encode_hostname` uses; this is
        the test that the two agree. (Pages are fetched by Chromium from `https://{domain}`.)
        """
        on_the_wire = httpx.URL(f"https://{raw}").raw_host.decode("ascii")
        assert on_the_wire == ascii_name
        assert encode_hostname(raw) == on_the_wire

    def test_the_stored_name_is_the_unicode_one(self) -> None:
        """How the code stored an IDN before this item, and still does."""
        _, domain = parse_domain("bücher.de")
        assert domain == "bücher.de"
        assert encode_hostname(domain) == "xn--bcher-kva.de"

    def test_a_name_httpx_cannot_encode_is_refused_here(self) -> None:
        """`i❤.ws` was stored, but httpx raises on it: robots.txt could not be fetched."""
        with pytest.raises(httpx.InvalidURL):
            httpx.URL("https://i❤.ws")
        with pytest.raises(InvalidUrl):
            parse_domain("i❤.ws")


class TestEncodeHostname:
    @pytest.mark.parametrize(
        ("host", "ascii_name"),
        [
            ("example.com", "example.com"),
            ("EXAMPLE.Com", "example.com"),
            ("example.com.", "example.com"),
            ("a-b.c-d.example.org", "a-b.c-d.example.org"),
            (LONGEST, LONGEST),
        ],
    )
    def test_ascii_names_come_back_lower_case_without_the_root_dot(
        self, host: str, ascii_name: str
    ) -> None:
        assert encode_hostname(host) == ascii_name

    @pytest.mark.parametrize("host", list(NOT_A_NAME.values()), ids=list(NOT_A_NAME))
    def test_a_name_that_is_not_one_raises(self, host: str) -> None:
        with pytest.raises(ValueError):
            encode_hostname(host)

    @pytest.mark.parametrize("host", ["", ".", "..", "[::1]", "::1"])
    def test_nothing_and_ip_literals_raise(self, host: str) -> None:
        with pytest.raises(ValueError):
            encode_hostname(host)


class TestDnsForm:
    """`dns_form`: `encode_hostname`, or the name as it was given when that refuses it.

    The one form two spellings of a host are compared in, and the form the other-sites lists are
    stored in (M1 task 1.1): `bücher.de` and `xn--bcher-kva.de` are both `xn--bcher-kva.de`. It is
    public beside `encode_hostname` so that extraction, the scan runner and scoring (task 1.2)
    compare as the lists hold. It raises nothing: a domain stored before launch checklist 3.67
    may not be a hostname, and a comparison must still be made with it.
    """

    @pytest.mark.parametrize(("raw", "ascii_name"), IDN_AS_ASCII)
    def test_a_name_that_can_be_encoded_comes_back_as_encode_hostname_gives_it(
        self, raw: str, ascii_name: str
    ) -> None:
        assert intake_service.dns_form(raw) == ascii_name == encode_hostname(raw)

    @pytest.mark.parametrize(
        ("one", "other"),
        [
            ("bücher.de", "xn--bcher-kva.de"),
            ("BÜCHER.DE", "xn--bcher-kva.de"),
            ("пример.рф", "пример.xn--p1ai"),
            ("xn--e1afmkfd.xn--p1ai", "пример.рф"),
            ("example.com", "EXAMPLE.COM"),
            ("example.com.", "example.com"),
        ],
    )
    def test_two_spellings_of_one_host_come_to_one_form(self, one: str, other: str) -> None:
        assert intake_service.dns_form(one) == intake_service.dns_form(other)

    @pytest.mark.parametrize(
        "name",
        [*NOT_A_NAME.values(), *INVALID_PUNYCODE.values(), TOO_LONG, "Exa_Mple.COM", "", "[::1]"],
    )
    def test_a_name_that_is_not_a_hostname_comes_back_as_it_was_given(self, name: str) -> None:
        """Not lower-cased, not stripped, and no exception: it is compared as it is."""
        assert intake_service.dns_form(name) == name

    @pytest.mark.parametrize(
        "name",
        ["bücher.de", "xn--bcher-kva.de", "EXAMPLE.com", "пример.рф", "Exa_Mple.COM", "", LONGEST],
    )
    def test_it_is_a_fixed_point_so_a_stored_form_compares_as_itself(self, name: str) -> None:
        once = intake_service.dns_form(name)
        assert intake_service.dns_form(once) == once

    def test_an_encodable_name_comes_back_as_ascii_and_lower_case(self) -> None:
        for raw, _ascii_name in IDN_AS_ASCII:
            form = intake_service.dns_form(raw)
            assert form.isascii() and form == form.lower()


class TestRefusals:
    @pytest.mark.parametrize("raw", list(NOT_A_NAME.values()), ids=list(NOT_A_NAME))
    def test_a_host_that_is_not_a_name_is_refused_with_a_plain_sentence(self, raw: str) -> None:
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        assert excinfo.value.detail == NOT_A_HOSTNAME

    @pytest.mark.parametrize(
        "raw", list(NO_PUBLIC_DOMAIN.values()), ids=list(NO_PUBLIC_DOMAIN)
    )
    def test_an_address_with_no_public_domain_is_refused_with_its_sentence(
        self, raw: str
    ) -> None:
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        assert excinfo.value.detail == NO_PUBLIC_DOMAIN_SENTENCE

    @pytest.mark.parametrize("raw", IPV6_LITERALS)
    def test_an_ipv6_literal_is_refused(self, raw: str) -> None:
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        assert excinfo.value.detail == NOT_A_HOSTNAME

    @pytest.mark.parametrize("raw", ["", "   ", "https://", "http:///path", "https://@"])
    def test_an_address_with_no_host_at_all_is_refused_with_the_same_sentence(
        self, raw: str
    ) -> None:
        """These had a message of the library's or of `normalise_url`'s own."""
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        assert excinfo.value.detail == NOT_A_HOSTNAME

    def test_the_sentence_quotes_nothing_the_person_typed(self) -> None:
        typed = "qzxv wjkb.com/<b>"
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(typed)
        assert excinfo.value.detail == NOT_A_HOSTNAME
        assert "qzxv" not in excinfo.value.detail
        assert "wjkb" not in excinfo.value.detail
        assert typed not in excinfo.value.detail

    @pytest.mark.parametrize(
        "raw", list(CARRYING_A_SENTINEL.values()), ids=list(CARRYING_A_SENTINEL)
    )
    def test_neither_sentence_quotes_what_was_typed(self, raw: str) -> None:
        """A sentinel in the user name, password, path and query is in no member of the problem.

        The "no public domain" sentence used to be `f"{raw!r} does not contain ..."`, which gave
        the whole address back, `user:password@` and all, in the body of the 409.
        """
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        problem = excinfo.value.to_dict("/api/v1/clients")
        assert problem["detail"] in {NOT_A_HOSTNAME, NO_PUBLIC_DOMAIN_SENTENCE}
        for member, value in problem.items():
            assert SENTINEL not in member, member
            assert SENTINEL not in str(value), member

    def test_the_stored_name_is_checked_too_not_only_the_host_it_was_cut_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever the domain reader hands back is what would be stored, so it is checked."""
        monkeypatch.setattr(intake_service, "registrable_domain", lambda url: "exa mple.com")
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain("example.com")
        assert excinfo.value.detail == NOT_A_HOSTNAME

    def test_it_is_the_existing_invalid_url_problem(self) -> None:
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain("exa mple.com")
        problem = excinfo.value.to_dict("/api/v1/clients")
        assert problem == {
            "type": "/problems/invalid-url",
            "title": "That does not look like a website address",
            "status": 409,
            "detail": NOT_A_HOSTNAME,
            "instance": "/api/v1/clients",
        }


class TestPunycodeLabels:
    """A label typed as punycode is held to what its Unicode spelling is held to — finding L2.

    `xn--zzzz.com` was only ever asked to be letters, digits and hyphens, so a label that is
    not punycode at all, or is the punycode of one IDNA 2008 forbids (`i❤` is `xn--i-7iq`),
    was accepted and stored. It must now decode, and encode back to exactly what was typed.
    What is stored is still what was typed: two spellings of one IDN stay two stored names,
    which is the founder's to decide and not this item's.
    """

    @pytest.mark.parametrize("raw", list(INVALID_PUNYCODE.values()), ids=list(INVALID_PUNYCODE))
    def test_a_label_that_is_not_valid_punycode_is_refused(self, raw: str) -> None:
        with pytest.raises(ValueError):
            encode_hostname(raw)
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(raw)
        assert excinfo.value.detail == NOT_A_HOSTNAME

    @pytest.mark.parametrize(
        "unicode_name",
        [
            "i❤.ws",
            "ｅｘａｍｐｌｅ.com",
            "ex\N{ZERO WIDTH SPACE}ample.com",
            "ex\N{SOFT HYPHEN}ample.com",
            "bu\N{COMBINING DIAERESIS}cher.de",
            "ü" * 58 + ".de",
        ],
        ids=["symbol", "full-width", "zero-width-space", "soft-hyphen", "not-nfc", "too-long"],
    )
    def test_the_punycode_of_a_refused_label_is_refused_the_same(self, unicode_name: str) -> None:
        """Both spellings of a label the Unicode path refuses get the same refusal."""
        label, _, rest = unicode_name.partition(".")
        in_punycode = f"xn--{label.encode('punycode').decode('ascii')}.{rest}"
        for raw in (unicode_name, in_punycode):
            with pytest.raises(InvalidUrl) as excinfo:
                parse_domain(raw)
            assert excinfo.value.detail == NOT_A_HOSTNAME, raw

    def test_upper_case_xn_is_read_like_lower_case(self) -> None:
        assert parse_domain("XN--BCHER-KVA.DE")[1] == "xn--bcher-kva.de"
        assert encode_hostname("Xn--BcHeR-kVa.De") == "xn--bcher-kva.de"
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain("XN--ZZZZ.DE")
        assert excinfo.value.detail == NOT_A_HOSTNAME

    def test_valid_punycode_is_still_accepted_and_stored_as_typed(self) -> None:
        _, domain = parse_domain("xn--bcher-kva.de")
        assert domain == "xn--bcher-kva.de"  # not re-spelled as bücher.de
        assert encode_hostname("xn--bcher-kva.de") == "xn--bcher-kva.de"

    def test_a_label_that_does_not_encode_back_to_itself_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No real label is known that decodes and does not encode back to itself.

        (Several thousand edits of valid ones were tried and every one that decoded came
        back as it was.) The clause is held with a decoder that answers with another name.
        """
        monkeypatch.setattr(intake_service.idna, "decode", lambda label: "bücher")
        with pytest.raises(ValueError):
            encode_hostname("xn--zzzz.com")
        with pytest.raises(InvalidUrl):
            parse_domain("xn--zzzz.com")

    def test_a_decoder_that_raises_is_a_refusal_and_not_a_server_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tldextract guards this same `idna.decode` call against IndexError, so this does too."""

        def broken(label: str) -> str:
            raise IndexError("string index out of range")

        monkeypatch.setattr(intake_service.idna, "decode", broken)
        with pytest.raises(ValueError):
            encode_hostname("xn--bcher-kva.de")
        with pytest.raises(InvalidUrl):
            parse_domain("xn--bcher-kva.de")


class TestCostIsBoundedBeforeEncoding:
    """Nothing is encoded that cannot be valid — finding L3.

    Encoding never shortens a label or a name, so a name over 253 characters, or a label over
    63, is refused before `idna.encode` is called once, and what a refusal costs no longer
    grows with what was typed.
    """

    @pytest.fixture
    def encode_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        calls: list[str] = []
        real = intake_service.idna.encode

        def counting(label: str, *args: object, **kwargs: object) -> bytes:
            calls.append(label)
            return real(label, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(intake_service.idna, "encode", counting)
        return calls

    def test_a_single_label_of_2000_characters(self, encode_calls: list[str]) -> None:
        label = "ü" * 2000
        with pytest.raises(ValueError):
            encode_hostname(label)
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(f"{label}.de")
        assert excinfo.value.detail == NOT_A_HOSTNAME
        assert encode_calls == []

    def test_a_thousand_short_labels(self, encode_calls: list[str]) -> None:
        name = ".".join(["é"] * 1000)
        with pytest.raises(ValueError):
            encode_hostname(name)
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(name)
        assert excinfo.value.detail == NOT_A_HOSTNAME
        assert encode_calls == []

    def test_a_label_over_63_in_a_name_that_is_not_too_long(self, encode_calls: list[str]) -> None:
        name = "ü" * 64 + ".de"
        assert len(name) < 253
        with pytest.raises(ValueError):
            encode_hostname(name)
        with pytest.raises(InvalidUrl) as excinfo:
            parse_domain(name)
        assert excinfo.value.detail == NOT_A_HOSTNAME
        assert encode_calls == []

    def test_what_is_valid_is_still_encoded(self, encode_calls: list[str]) -> None:
        """The guards refuse only what encoding could not have made valid."""
        assert encode_hostname(LONGEST) == LONGEST
        assert encode_calls == []  # ASCII is not encoded at all

        encoded = encode_hostname("ü" * 55 + ".de")
        assert encoded.endswith(".de") and len(encoded.split(".")[0]) == 61
        assert encode_calls == ["ü" * 55]


# What the S1 finding listed, and a little more: nothing the stored name holds may be one of
# these, whatever else it is.
UNSAFE_IN_A_STORED_NAME = set(" \"'()<>[]{}\\|^`%,;!*:@/?#_")

SEEDS = [
    "example.com",
    "https://www.shop.example.co.uk/a/b?x=1#y",
    "bücher.de",
    "xn--bcher-kva.de",
    "user:pw@example.com:8080",
    "a.b.c.d.example.org",
    "my-site.com.",
    "日本語.jp",
    "EXAMPLE.COM",
    "http://1.2.3.4/",
    "[::1]",
    "localhost",
    "co.uk",
    "practice.github.io",
    "straße.de",
    # Right-to-left labels, which IDNA 2008 accepts whole and refuses mixed with Latin ones.
    "\N{HEBREW LETTER ALEF}\N{HEBREW LETTER BET}\N{HEBREW LETTER GIMEL}.com",
    "\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}\N{ARABIC LETTER TEH}.com",
    # A non-joiner between two Arabic letters that join on both sides: where IDNA 2008 allows it.
    "\N{ARABIC LETTER BEH}\N{ZERO WIDTH NON-JOINER}\N{ARABIC LETTER BEH}.com",
]
HOSTILE = [
    " ", "_", '"', "'", "[", "]", "(", ")", "<", ">", "{", "}", "\\", "|", "^", "`", "%", ",",
    ";", "!", "*", "\x00", "\x07", "\n", "\t", "\r", "\x7f", "\x85", "-", ".", ":", "@", "/", "?",
    "#", "0", "a", "é", "ß", "ü", "❤", "ｅ",
    "\N{ZERO WIDTH SPACE}", "\N{NO-BREAK SPACE}", "\N{SOFT HYPHEN}",
    "\N{IDEOGRAPHIC FULL STOP}", "\N{FULLWIDTH FULL STOP}",
    # Bidi controls, a byte order mark, joiners, a combining mark and right-to-left letters.
    "\N{RIGHT-TO-LEFT OVERRIDE}", "\N{LEFT-TO-RIGHT ISOLATE}", "\N{ZERO WIDTH NO-BREAK SPACE}",
    "\N{ZERO WIDTH NON-JOINER}", "\N{ZERO WIDTH JOINER}", "\N{COMBINING ACUTE ACCENT}",
    "\N{HEBREW LETTER ALEF}", "\N{HEBREW LETTER BET}",
    "\N{ARABIC LETTER ALEF}", "\N{ARABIC LETTER BEH}",
]
# The two format characters IDNA 2008 lets through, in the contexts its rules name (a Persian
# or an Indic name cannot be written without them). No other invisible character is accepted.
JOINERS = {"\N{ZERO WIDTH NON-JOINER}", "\N{ZERO WIDTH JOINER}"}


def _mutated(raw: str, rng: random.Random) -> str:
    for _ in range(rng.randint(1, 3)):
        at = rng.randint(0, len(raw))
        kind = rng.choice(("insert", "insert", "delete", "replace", "dot", "stretch"))
        if kind == "insert":
            raw = raw[:at] + rng.choice(HOSTILE) + raw[at:]
        elif kind == "delete":
            raw = raw[:at] + raw[at + 1 :]
        elif kind == "replace":
            raw = raw[:at] + rng.choice(HOSTILE) + raw[at + 1 :]
        elif kind == "dot":
            raw = raw[:at] + "." + raw[at:]
        else:
            # A run of one letter, at the 63 / 64 character edge of a label.
            raw = raw[:at] + "a" * rng.choice((60, 62, 63, 64, 65)) + raw[at:]
    return raw


class TestWhatIsStored:
    def test_nothing_gets_past_intake_as_a_name_that_is_not_one(self) -> None:
        """Whatever is typed, intake refuses it or returns a name DNS would carry.

        Seeded, so a failure is the same failure on every run. Both outcomes have to
        happen often, or this proves nothing about either, and so do two kinds of accepted
        name that are easy to get wrong: a right-to-left label, and one with a joiner.
        What is refused is refused with one of the two plain sentences.
        """
        rng = random.Random(367)
        accepted = refused = right_to_left = with_a_joiner = 0
        for _ in range(6000):
            raw = _mutated(rng.choice(SEEDS), rng)
            try:
                _, domain = parse_domain(raw)
            except InvalidUrl as exc:
                assert exc.detail in {NOT_A_HOSTNAME, NO_PUBLIC_DOMAIN_SENTENCE}, raw
                refused += 1
                continue
            accepted += 1
            right_to_left += any(unicodedata.bidirectional(c) in {"R", "AL"} for c in domain)
            with_a_joiner += any(c in JOINERS for c in domain)

            ascii_name = encode_hostname(domain)  # raises if the stored name is not one
            assert re.fullmatch(r"[a-z0-9.-]+", ascii_name), (raw, domain)
            assert len(ascii_name) <= 253, (raw, domain)
            assert all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in ascii_name.split(".")
            ), (raw, domain)
            assert "." in domain and domain == domain.lower(), (raw, domain)
            for char in domain:
                assert char not in UNSAFE_IN_A_STORED_NAME, (raw, domain)
                assert unicodedata.category(char)[0] not in {"C", "Z"} or char in JOINERS, (
                    raw,
                    domain,
                )

        assert accepted > 200, accepted
        assert refused > 200, refused
        assert right_to_left > 20, right_to_left
        assert with_a_joiner > 2, with_a_joiner


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Hostname Test Agency",
            "fullName": "Operator",
            "email": "hostname@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def nothing_is_fetched(monkeypatch):  # noqa: ANN001, ANN201
    """The crawl fails the test if it is reached. Refused means before anything is read."""
    calls: list[str] = []

    async def crawl_site(url: str, **kwargs: object) -> NoReturn:
        calls.append(url)
        raise AssertionError(f"intake fetched {url!r}")

    monkeypatch.setattr(intake_service, "crawl_site", crawl_site)
    return calls


AT_THE_ENDPOINT = [
    pytest.param("exa mple.com", id="space"),
    pytest.param("exa_mple.com", id="underscore"),
    pytest.param('ex"ample.com', id="quote"),
    pytest.param("a[b].com", id="brackets"),
    pytest.param("ex\x00ample.com", id="control-character"),
    pytest.param("a..b.com", id="empty-label"),
    pytest.param("a" * 64 + ".com", id="label-of-64"),
    pytest.param("-example.com", id="leading-hyphen"),
    pytest.param("example-.com", id="trailing-hyphen"),
    pytest.param(TOO_LONG, id="name-of-254"),
    pytest.param("co.uk", id="bare-suffix"),
    pytest.param("http://127.0.0.1:8000", id="ip-address"),
]


class TestAtTheEndpoint:
    @pytest.mark.parametrize("classify", [False, True], ids=["no-classify", "classify"])
    @pytest.mark.parametrize("raw", AT_THE_ENDPOINT)
    async def test_refused_before_anything_is_stored_or_fetched(
        self,
        client: AsyncClient,
        session,  # noqa: ANN001
        nothing_is_fetched: list[str],
        raw: str,
        classify: bool,
    ) -> None:
        await _sign_up(client)

        resp = await client.post(f"{BASE}/clients", json={"url": raw, "classify": classify})

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert set(body) == {"type", "title", "status", "detail", "instance"}  # no code
        assert body["type"] == "/problems/invalid-url"
        assert body["title"] == "That does not look like a website address"
        assert body["detail"] in {NOT_A_HOSTNAME, NO_PUBLIC_DOMAIN_SENTENCE}
        assert nothing_is_fetched == []

        stored = (await session.execute(select(func.count()).select_from(Client))).scalar_one()
        assert stored == 0
        assert (await client.get(f"{BASE}/clients")).json()["data"] == []

    async def test_nothing_typed_comes_back_in_the_response(
        self, client: AsyncClient, nothing_is_fetched: list[str]
    ) -> None:
        """Whole response, headers included: a user name, password, path and query are not in it."""
        await _sign_up(client)
        for name, raw in CARRYING_A_SENTINEL.items():
            resp = await client.post(f"{BASE}/clients", json={"url": raw, "classify": False})
            assert resp.status_code == 409, (name, resp.text)
            assert SENTINEL not in resp.text, name
            assert SENTINEL not in str(resp.headers), name
            assert resp.json()["detail"] in {NOT_A_HOSTNAME, NO_PUBLIC_DOMAIN_SENTENCE}, name
        assert nothing_is_fetched == []

    async def test_a_bad_name_is_refused_with_the_sentence_and_not_the_librarys(
        self, client: AsyncClient, nothing_is_fetched: list[str]
    ) -> None:
        """Brackets used to come back as "Invalid IPv6 URL", the standard library's words."""
        await _sign_up(client)
        resp = await client.post(f"{BASE}/clients", json={"url": "a[b].com", "classify": False})
        assert resp.status_code == 409
        assert resp.json()["detail"] == NOT_A_HOSTNAME
        assert nothing_is_fetched == []

    async def test_an_international_name_is_stored_as_it_always_was(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)

        created = await client.post(
            f"{BASE}/clients", json={"url": "BÜCHER.DE", "classify": False}
        )
        assert created.status_code == 201, created.text
        assert created.json()["domain"] == "bücher.de"

        fetched = await client.get(f"{BASE}/clients/{created.json()['id']}")
        assert fetched.json()["domain"] == "bücher.de"

        # Typed again in lower case, it is the same client, not a second one.
        again = await client.post(
            f"{BASE}/clients", json={"url": "https://www.bücher.de/", "classify": False}
        )
        assert again.status_code == 409
        assert again.json()["clientId"] == created.json()["id"]

    async def test_an_ordinary_address_still_creates_a_client(self, client: AsyncClient) -> None:
        await _sign_up(client)
        resp = await client.post(
            f"{BASE}/clients",
            json={"url": "https://Sub.Example.Co.UK/path", "classify": False},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["domain"] == "example.co.uk"
