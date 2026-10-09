"""Probable rivals: the rule, as pure functions — F2 (2026-10-09), the founder's decision.

THE PROBLEM
-----------
RMIT University's report told the agency to "Get onto deakin.edu.au — the source these answers
keep citing". Deakin is not a source to get onto: it is a university the answers named, a rival.
The report counts a cited domain as unclaimed when it belongs to neither the client nor a
detected rival, and a rival that detection has not found yet looks exactly like a third party.

THE RULE (the founder's)
------------------------
A domain is a PROBABLE RIVAL only if (a) it has the client's own restricted ending (edu.au for
rmit.edu.au, and likewise the other restricted endings of the 14 markets) AND (b) the scan's
answers named a brand whose name matches its label ("Deakin University" for deakin.edu.au).

A probable rival is skipped by "Get onto <domain>" (the fix names the next unclaimed domain) and
is suggested on the client's Competitors page ("deakin.edu.au: cited 6 times, named in 4 answers.
Add it as a rival?"), never in the client-facing report. qilt.edu.au and uac.edu.au are edu.au
but are never named as options in the answers, so they stay valid "Get onto" targets.

WHAT IS HERE
------------
* `RESTRICTED_ENDINGS`: the sector endings a registry restricts to one kind of body, by market.
  A restricted ending says what KIND of body a domain is (a university, a government), and a
  rival of a university is another university. A `.com` says nothing, so a client with none has
  no probable rivals, ever.
* `restricted_ending`: a domain's ICANN suffix if it is in the table. The SAME ending means the
  same suffix: `vic.edu.au` is a suffix of its own in the Public Suffix List, so a school under
  it has no restricted ending, and `deakin.com` has none either.
* `rival_name`: the name a suggestion is added with ("UNSW", "Deakin").
* `named_domains`: the scan's fact. For every CITED domain with the client's ending, other than
  the client's own, how many ANSWERS named its label. Answers, not mentions, and only answers the
  engine gave. It is computed while the answer text exists (the text is never stored) by
  `extraction.find_brand`, the matcher the scan itself uses, which is NOT changed here, so
  MATCHER_VERSION does not move. Pure and total: it runs inside a scan after every answer has been
  paid for, so odd input gives a smaller answer and never an exception. A pair goes to the matcher
  only if `may_name` says the answer could hold the label (below), which changes no count.
* `flatten`, `needles` and `may_name`: the pre-filter that makes the count cheap, and why it can
  only skip what the matcher would reject.
* `domains_named_in_answers` and `named_hosts`: the stored fact read back, for the report and for
  the suggestions.

Facts only: the result is a map from a domain to a count. No answer text reaches it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from ..models.engine_result import ANSWERED_STATUSES
from .competitors import slugify
from .crawl import name_and_icann_suffix, name_and_suffix, registrable_domain
from .extraction import MIN_SLUG_MATCH_CHARS, find_brand, short_forms
from .intake import dns_form

if TYPE_CHECKING:
    from .engines import EngineAnswer
    from .extraction import ExtractedFacts

# The registry-restricted sector endings of the 14 markets (`serp.SEARCH_COUNTRY_BY_TLD`), by
# market. The founder's table of 2026-10-09: an explicit set and not a guess from a name, so
# adding a market or an ending is a line here and a decision on the record. A name under one of
# these is a body of that kind: only an accredited school may register under `edu.au`.
#
# `mod.uk` and `sch.uk` are in the table as the founder wrote it, and neither can match today: the
# Public Suffix List does not make `mod.uk` a suffix (it is an ordinary domain), and makes every
# `<authority>.sch.uk` a suffix of its own, so a school's registrable domain never sits directly
# under `sch.uk`. They do no harm, and they start to work if the list changes.
RESTRICTED_ENDINGS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "au": frozenset({"edu.au", "gov.au"}),
        "nz": frozenset(
            {"ac.nz", "cri.nz", "govt.nz", "health.nz", "iwi.nz", "mil.nz", "parliament.nz"}
        ),
        "uk": frozenset({"ac.uk", "gov.uk", "mod.uk", "nhs.uk", "police.uk", "sch.uk"}),
        "ie": frozenset({"gov.ie"}),
        "ca": frozenset({"gc.ca"}),
        "us": frozenset({"edu", "gov", "mil"}),
        "za": frozenset({"ac.za", "gov.za", "mil.za"}),
        "in": frozenset({"ac.in", "edu.in", "gov.in", "mil.in", "res.in"}),
        "sg": frozenset({"edu.sg", "gov.sg"}),
        "my": frozenset({"edu.my", "gov.my", "mil.my"}),
        "ph": frozenset({"edu.ph", "gov.ph", "mil.ph"}),
        "hk": frozenset({"edu.hk", "gov.hk"}),
        "id": frozenset({"ac.id", "go.id", "mil.id", "sch.id"}),
        "jp": frozenset({"ac.jp", "ed.jp", "go.jp", "lg.jp"}),
    }
)

_ALL_ENDINGS: Final[frozenset[str]] = frozenset().union(*RESTRICTED_ENDINGS.values())

# A label written in capitals as a name: two to four ASCII letters ("UNSW", "RMIT", "UTS").
_ACRONYM: Final = re.compile(r"[A-Za-z]{2,4}", re.ASCII)


def restricted_ending(domain: str) -> str | None:
    """The restricted ending `domain` has, or None.

    Its public suffix (the ICANN section of the list, in DNS form, so `bücher.edu.au` and
    `xn--bcher-kva.edu.au` are one host) when that suffix is in `RESTRICTED_ENDINGS`. A host is
    read as its registrable domain (`www.deakin.edu.au` is `deakin.edu.au`). Nothing that has no
    name before its suffix has one: a bare `edu.au`, an IP address, `localhost`, the empty string.
    """
    if not isinstance(domain, str):
        return None
    _name, suffix = name_and_icann_suffix(dns_form(domain.strip()))
    return suffix if suffix in _ALL_ENDINGS else None


def rival_name(domain: str) -> str:
    """The name a probable rival is added with: its label, in capitals when it is an acronym.

    "unsw.edu.au" is "UNSW", "deakin.edu.au" is "Deakin". Two to four letters are an acronym, as
    the matcher reads them (`extraction.short_forms`: found in capitals); any other label is
    capitalised. The agency can rename the rival in the competitor editor. A string with no
    registrable domain in it comes back as it was given.
    """
    label, _suffix = name_and_suffix(domain.strip()) if isinstance(domain, str) else ("", "")
    if not label:
        return domain if isinstance(domain, str) else ""
    return label.upper() if _ACRONYM.fullmatch(label) else label.capitalize()


def claimed_short_forms(
    subject_name: str,
    subject_domain: str,
    competitors: Iterable[tuple[str, str | None]],
) -> frozenset[str]:
    """The capitals short forms the client and its rivals already own, as `claimed_forms`.

    `extraction.extract_facts` gives a short form to the first brand that has it, the subject and
    then the rivals in list order, so that "RMIT" in an answer names RMIT University and not also
    RMIT Vietnam. A candidate is not a brand of that list, so every form any of them owns is
    claimed against it: "UNSW" in an answer names a rival that owns it, not a stranger's domain.
    """
    forms = set(short_forms(subject_name, subject_domain))
    for name, domain in competitors:
        forms.update(short_forms(name, domain))
    return frozenset(forms)


# --- the pre-filter -------------------------------------------------------------------------
#
# `named_domains` puts every (candidate domain, answer) pair to the matcher, which costs a few
# tenths of a millisecond a call, linear in the answer's length: 2,574 ms for 96 answers of ~5.4k
# characters over 60 candidates (the security review of 2026-10-10). Almost every pair is a no,
# because the answer does not hold the label. So each answer is flattened ONCE, and a pair goes to
# the matcher only if the flattened answer holds something the matcher could have found. This is a
# NECESSARY condition and not a test: a pass is not a match, and the matcher decides every pair
# that passes.
#
# WHY NO COUNT CAN CHANGE. `find_brand(text, name=None, domain=D)`, with the label being what is
# before D's first dot, finds D in these ways, and each needs the label's own letters in the text:
#   * the full domain, the label of five or more characters as a word, and the 2-4 letter capitals
#     short form: a regular expression of the label's characters, case-insensitive (the short form,
#     case-sensitive, in capitals). The text holds, one after the other, a character the regex
#     engine equates with each of the label's.
#   * the slug, `slugify(label)` of six characters or more, as a substring of the text with every
#     character `str.isalnum` refuses skipped and the rest lower-cased (`extraction._slug_index`).
# `flatten` puts a label and a text in one alphabet, and these facts hold it up. The test file
# checks them over every code point where it says so, and fuzzes the whole claim:
#   * the characters `str.isalnum` refuses are dropped FIRST, as the slug pass skips them. Folding
#     first would spell a sign out ("™" is "TM" under NFKD) and put letters between a label's.
#     (Checked over every code point.)
#   * the rest is folded (NFKD, then case) and reduced to [a-z0-9]. The regex engine's
#     case-insensitive equality is carried by that fold: characters it equates flatten alike. That
#     was checked once against the engine's own tables over every code point (1,427 classes of
#     cased characters, its extra-case table included), and the suite checks the case variants of
#     every cased code point. Four non-ASCII characters it equates with an ASCII letter, the dotted
#     capital I, the dotless i, the long s and the Kelvin sign, are i, i, s and k; the fold gives
#     i, nothing, s and k, so the dotless i is mapped by hand. (The four: checked over every code
#     point.)
#   * so a text that matches by the regular expressions holds the label's flattened letters, in
#     order, with nothing between them.
#   * the slug is its own needle because it is NOT the flattened label: it drops every non-ASCII
#     character without folding it, and strips a legal suffix, so it can be found where the
#     label is not ("marketco" is found as "market", "ébcdefg" as "bcdefg").
# If `find_brand` ever gains another way to find a domain alone, `may_name` must learn it; the
# fuzz in `test_probable_rivals.py` is what notices.

# What `str.isalnum` refuses: `\w` is exactly the alphanumeric characters and the underscore.
_NOT_ALNUM: Final = re.compile(r"[\W_]+")
_NOT_ASCII_ALNUM: Final = re.compile(r"[^a-z0-9]+")
# The one letter the regex engine reads as ASCII that no Unicode folding does (see above).
_DOTLESS_I: Final = str.maketrans("ı", "i")


def flatten(text: str) -> str:
    """`text` as the lower-case ASCII letters and digits it can be read as, and nothing else.

    "Deakin University, 2nd-floor" is "deakinuniversity2ndfloor"; "Zürich" is "zurich"; "La-Trobe"
    and "La Trobe" are both "latrobe". Total over any string. See the notes above for why a label
    flattened this way is in a text flattened this way whenever the matcher finds it there.
    """
    kept = _NOT_ALNUM.sub("", text)
    folded = unicodedata.normalize("NFKD", kept).casefold().translate(_DOTLESS_I)
    return _NOT_ASCII_ALNUM.sub("", folded)


def needles(domain: str) -> tuple[str, ...]:
    """What a flattened answer must hold for `find_brand` to find `domain`: any one of these.

    The flattened label, and the slug the matcher's last pass looks for when it is long enough for
    that pass to run and is not the same string. A label with nothing in it that flattens (a
    Cyrillic one) has the empty string for a needle, which every answer holds: nothing is skipped.
    """
    label = domain.split(".")[0]
    found = [flatten(label)]
    slug = slugify(label)
    if len(slug) >= MIN_SLUG_MATCH_CHARS and slug not in found:
        found.append(slug)
    return tuple(found)


def _holds_any(wanted: tuple[str, ...], flat_text: str) -> bool:
    return any(needle in flat_text for needle in wanted)


def may_name(domain: str, flat_text: str) -> bool:
    """False only when the matcher is certain to find nothing for `domain` in the answer.

    `flat_text` is `flatten(answer text)`. True means "ask the matcher", not "it is named".
    """
    return _holds_any(needles(domain), flat_text)


def _is_answered(facts: ExtractedFacts) -> bool:
    return getattr(facts, "status", None) in ANSWERED_STATUSES


def named_domains(
    results: Iterable[tuple[Any, ExtractedFacts, EngineAnswer]],
    *,
    client_domain: str,
    claimed_forms: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """For each cited domain with the client's restricted ending: how many answers named its label.

    `results` is the scan's `(engine, facts, answer)` tuples, as `scan_runner.run_prompt` returns
    them. Only ANSWERED ones are read (`ANSWERED_STATUSES`: an engine that failed, timed out, was
    cut off or stopped to wait for a tool is not an answer), for the cited domains and for the
    names alike.

    The candidates are every domain any answered result cited that has the client's own
    restricted ending (the same suffix; see `restricted_ending`), other than the client's own
    registrable domain, whatever its spelling. Each is counted over every answered answer of the
    scan, the answer that named it being not necessarily the answer that cited it: it is one
    answer, counted once for a domain however many times it names the label. A candidate no
    answer named is in the result with 0, because it was measured. `{}` when the client has no
    restricted ending, and when nothing qualifies.

    A label is named when `extraction.find_brand`, given the domain alone, finds it: the full
    domain, the 2-4 letter capitals short form of the label, a label of five or more letters as a
    word, or a separator-insensitive match ("La Trobe University" for latrobe.edu.au).
    `claimed_forms` are the short forms the client and its rivals own (`claimed_short_forms`).
    A pair reaches the matcher only if `may_name` allows it: each answer is flattened once, and an
    answer that cannot hold the label is never put to the matcher. That cannot change a count.

    The keys are the domains as the scan cited them. Pure, deterministic and total: it reads the
    answers' text and keeps none of it.
    """
    ending = restricted_ending(client_domain)
    if ending is None:
        return {}
    own = dns_form(registrable_domain(client_domain.strip()))

    candidates: dict[str, int] = {}
    texts: list[str] = []
    for _engine, facts, answer in results:
        if not _is_answered(facts):
            continue
        for cited in getattr(facts, "citations", None) or ():
            domain = getattr(cited, "domain", None)
            if (
                isinstance(domain, str)
                and restricted_ending(domain) == ending
                and dns_form(domain.strip()) != own
            ):
                candidates.setdefault(domain, 0)
        text = getattr(answer, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)

    if not candidates:
        return {}
    flat_texts = [flatten(text) for text in texts]
    for domain in candidates:
        wanted = needles(domain)
        candidates[domain] = sum(
            1
            for text, flat in zip(texts, flat_texts, strict=True)
            if _holds_any(wanted, flat)
            and find_brand(text, name=None, domain=domain, claimed_forms=claimed_forms) is not None
        )
    return dict(sorted(candidates.items()))


def domains_named_in_answers(stored: object) -> frozenset[str]:
    """The domains a stored `named_domains` says at least one answer named, as it keys them.

    Reads what `Scan.named_domains` holds. None (a scan from before the fact was recorded, which
    is never guessed), an empty map, and anything that is not a map of domains to counts name
    nobody. An entry that is not a whole count of at least one does not count: a boolean is not a
    count, and neither is a string or a float.
    """
    if not isinstance(stored, dict):
        return frozenset()
    return frozenset(
        domain
        for domain, answers in stored.items()
        if isinstance(domain, str) and type(answers) is int and answers >= 1
    )


def named_hosts(stored: object) -> frozenset[str]:
    """`domains_named_in_answers`, in DNS form: the form two spellings of one host compare in."""
    return frozenset(dns_form(domain) for domain in domains_named_in_answers(stored))
