"""The JS licence audit shares ONE policy with the Python audit.

`scripts/js_license_audit.py` reads `pnpm licenses list --json` and applies
ALLOWED / BLOCKING / NOT_COPYLEFT imported from `scripts/license_audit.py`.
A pending-decision file may hold ONE blocked finding for the founder; it is
never an allow-list (a stale or over-broad entry fails the run).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "js_license_audit", SCRIPTS / "js_license_audit.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pnpm_json(tmp_path: Path, pkgs: list[tuple[str, str, str]]) -> Path:
    """pnpm groups by licence string; a missing licence is grouped under UNKNOWN."""
    grouped: dict[str, list[dict]] = {}
    for name, version, lic in pkgs:
        entry = {"name": name, "versions": [version], "paths": [], "license": lic}
        grouped.setdefault(lic or "UNKNOWN", []).append(entry)
        if not lic:
            entry.pop("license")
    p = tmp_path / "js.json"
    p.write_text(json.dumps(grouped))
    return p


def _pending(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "pending.json"
    p.write_text(json.dumps(entries))
    return p


def _run(tmp_path, pkgs, pending=None, capsys=None):
    mod = _load()
    args = [str(_pnpm_json(tmp_path, pkgs)), "--pending", str(_pending(tmp_path, pending or []))]
    rc = mod.main(args)
    return rc, capsys.readouterr().out


def _entry(package="@img/sharp-libvips-*", licence="LGPL-3.0-or-later"):
    return {"package": package, "licence": licence, "raised": "2026-09-30", "question": "q?"}


def test_mit_is_ok(tmp_path, capsys):
    rc, out = _run(tmp_path, [("a", "1.0.0", "MIT")], capsys=capsys)
    assert rc == 0
    assert "BLOCK" not in out


@pytest.mark.parametrize("lic", ["GPL-3.0", "LGPL-3.0-or-later", "AGPL-3.0-only"])
def test_copyleft_blocks(tmp_path, capsys, lic):
    rc, out = _run(tmp_path, [("bad", "2.0.0", lic)], capsys=capsys)
    assert rc == 1
    assert "bad" in out and "2.0.0" in out


def test_or_passes_if_any_alternative_is_allowed(tmp_path, capsys):
    rc, _ = _run(tmp_path, [("a", "1.0.0", "(MIT OR GPL-3.0)")], capsys=capsys)
    assert rc == 0


def test_and_needs_every_part(tmp_path, capsys):
    rc, out = _run(tmp_path, [("both", "1.0.0", "MIT AND GPL-2.0")], capsys=capsys)
    assert rc == 1
    assert "both" in out


@pytest.mark.parametrize("lic", ["", "UNKNOWN", "Unlicensed"])
def test_undeclared_blocks(tmp_path, capsys, lic):
    rc, out = _run(tmp_path, [("nolic", "1.0.0", lic)], capsys=capsys)
    assert rc == 1
    assert "nolic" in out


def test_unlisted_permissive_is_review_not_failure(tmp_path, capsys):
    rc, out = _run(tmp_path, [("caniuse-lite", "1.0.0", "CC-BY-4.0")], capsys=capsys)
    assert rc == 0
    assert "REVIEW" in out and "caniuse-lite" in out


def test_pending_entry_holds_only_the_matching_blocked_package(tmp_path, capsys):
    rc, out = _run(
        tmp_path,
        [("@img/sharp-libvips-linux-x64", "1.2.4", "LGPL-3.0-or-later"), ("a", "1.0.0", "MIT")],
        pending=[_entry()],
        capsys=capsys,
    )
    assert rc == 0
    assert "PENDING FOUNDER DECISION" in out
    assert "@img/sharp-libvips-linux-x64" in out


def test_pending_entry_with_different_licence_does_not_hold(tmp_path, capsys):
    rc, out = _run(
        tmp_path,
        [("@img/sharp-libvips-linux-x64", "1.2.4", "GPL-3.0")],
        pending=[_entry()],
        capsys=capsys,
    )
    assert rc == 1
    assert "@img/sharp-libvips-linux-x64" in out


def test_pending_entry_does_not_hold_other_blocked_packages(tmp_path, capsys):
    rc, _ = _run(
        tmp_path,
        [
            ("@img/sharp-libvips-linux-x64", "1.2.4", "LGPL-3.0-or-later"),
            ("other", "1.0.0", "LGPL-3.0-or-later"),
        ],
        pending=[_entry()],
        capsys=capsys,
    )
    assert rc == 1


def test_pending_entry_matching_a_non_blocking_package_fails(tmp_path, capsys):
    rc, out = _run(
        tmp_path,
        [("@img/sharp-libvips-linux-x64", "1.2.4", "MIT")],
        pending=[_entry(licence="MIT")],
        capsys=capsys,
    )
    assert rc == 1
    assert "stale" in out.lower() or "not blocking" in out.lower()


def test_pending_entry_matching_nothing_fails(tmp_path, capsys):
    rc, out = _run(tmp_path, [("a", "1.0.0", "MIT")], pending=[_entry()], capsys=capsys)
    assert rc == 1
    assert "@img/sharp-libvips-*" in out


def test_real_pending_file_has_exactly_the_one_entry():
    data = json.loads((REPO / "infra" / "ci" / "licence-pending-decisions.json").read_text())
    assert len(data) == 1
    e = data[0]
    assert e["package"] == "@img/sharp-libvips-*"
    assert e["licence"] == "LGPL-3.0-or-later"
    assert e["raised"] == "2026-09-30"
    assert e["question"].strip()


def test_policy_is_imported_not_copied():
    js = _load()
    spec = importlib.util.spec_from_file_location("license_audit", SCRIPTS / "license_audit.py")
    assert spec and spec.loader
    py = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(py)
    assert js.ALLOWED == py.ALLOWED
    assert js.BLOCKING.pattern == py.BLOCKING.pattern
    assert js.NOT_COPYLEFT.pattern == py.NOT_COPYLEFT.pattern
