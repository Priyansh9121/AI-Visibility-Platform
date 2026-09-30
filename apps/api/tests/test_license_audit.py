"""The Python licence audit: MPL-2.0 is its own category, never part of ALLOWED.

Founder decision 2 (2026-10-01): MPL-2.0 is allowed for UNMODIFIED use. The
verdict is `ok-unmodified`: it does not fail the run and is printed apart from
plain `ok`. GPL/LGPL/AGPL still block; other MPL versions are not covered.
"""

from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("license_audit", SCRIPTS / "license_audit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MPL_CLASSIFIER = "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)"
MPL_SPELLINGS = [
    "MPL-2.0",
    "MPL 2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
]


def test_mpl_is_its_own_category_not_allowed():
    mod = _load()
    assert "MPL-2.0" not in mod.ALLOWED
    assert not (mod.ALLOWED & mod.ALLOWED_UNMODIFIED)
    for spelling in MPL_SPELLINGS:
        assert spelling in mod.ALLOWED_UNMODIFIED


@pytest.mark.parametrize("lic", MPL_SPELLINGS)
def test_each_mpl_spelling_is_ok_unmodified(lic):
    assert _load().verdict_of(lic) == "ok-unmodified"


def test_classifier_form_is_ok_unmodified():
    """certifi and pathspec declare it as a classifier; licence_of keeps the last segment."""
    mod = _load()
    meta = Message()
    meta["Name"] = "certifi"
    meta["Classifier"] = "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)"

    class Dist:
        metadata = meta

    assert mod.licence_of(Dist()) == "Mozilla Public License 2.0 (MPL 2.0)"
    assert mod.verdict_of(mod.licence_of(Dist())) == "ok-unmodified"


def test_mpl_1_1_is_not_covered_and_does_not_fail():
    assert _load().verdict_of("MPL-1.1") == "REVIEW"
    assert _load().verdict_of("Mozilla Public License 1.1 (MPL 1.1)") == "REVIEW"


@pytest.mark.parametrize("lic", ["LGPL-3.0-or-later", "GPL-3.0", "AGPL-3.0-only"])
def test_gpl_family_still_blocks(lic):
    assert _load().verdict_of(lic) == "BLOCK"


def test_mpl_and_gpl_blocks():
    assert _load().verdict_of("MPL-2.0 AND GPL-3.0") == "BLOCK"


def test_mpl_or_gpl_passes():
    assert _load().verdict_of("MPL-2.0 OR GPL-3.0") == "ok-unmodified"


def test_mpl_or_mit_is_plain_ok():
    assert _load().verdict_of("MPL-2.0 OR MIT") == "ok"


def test_mpl_and_mit_is_ok_unmodified():
    assert _load().verdict_of("MPL-2.0 AND MIT") == "ok-unmodified"


def test_plain_and_undeclared_verdicts_unchanged():
    mod = _load()
    assert mod.verdict_of("MIT") == "ok"
    assert mod.verdict_of("UNDECLARED") == "BLOCK"
    assert mod.verdict_of("CC-BY-4.0") == "REVIEW"


def _fake_dist(name: str, classifier: str):
    meta = Message()
    meta["Name"] = name
    meta["Classifier"] = classifier

    class Dist:
        metadata = meta
        version = "1.0"

    return Dist()


def test_main_exits_zero_and_prints_ok_unmodified_separately(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(
        mod,
        "distributions",
        lambda: [
            _fake_dist("certifi", MPL_CLASSIFIER),
            _fake_dist("plain", "License :: OSI Approved :: MIT License"),
        ],
    )
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "ok-unmodified" in out and "certifi" in out
    assert "REVIEW" not in out
    assert "  ok" in out  # plain ok still counted on its own line


def test_main_still_fails_on_lgpl(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(
        mod,
        "distributions",
        lambda: [_fake_dist("bad", "License :: OSI Approved :: LGPL-3.0-or-later")],
    )
    assert mod.main() == 1
