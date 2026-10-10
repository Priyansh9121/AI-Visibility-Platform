"""Repo-hygiene guards: workflow permissions, the deploy gate, install scripts, published
ports and key-shaped test fixtures. Plain text and regex checks; no YAML library needed."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"

KEY_SHAPE = re.compile(
    r"sk_(live|test)_[A-Za-z0-9]|rk_(live|test)_[A-Za-z0-9]|whsec_[A-Za-z0-9]"
    r"|sk-ant-[A-Za-z0-9]|pplx-[A-Za-z0-9]|sk-proj-[A-Za-z0-9]|AIza[0-9A-Za-z_-]{10}"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    """The indented lines under a column-0 `key:` line (and the line itself)."""
    m = re.search(rf"^{key}:.*(?:\n(?:[ \t]+.*|))*", text, re.M)
    assert m, f"no top-level `{key}:`"
    return m.group(0)


@pytest.mark.parametrize("name", ["ci.yml", "deploy.yml"])
def test_workflow_token_is_read_only_at_the_top_level(name: str) -> None:
    block = _top_level_block(_text(WORKFLOWS / name), "permissions")
    assert re.fullmatch(r"permissions:\s*\n\s+contents: read\s*", block), block


def test_deploy_gate_names_the_event_and_the_head_repository() -> None:
    text = _text(WORKFLOWS / "deploy.yml")
    m = re.search(r"^    if: >-\n((?:      .*\n)+)", text, re.M)
    assert m, "deploy job has no folded `if:`"
    cond = m.group(1)
    assert "github.event.workflow_run.event == 'push'" in cond
    assert (
        "github.event.workflow_run.head_repository.full_name == github.repository" in cond
    )
    assert "github.event.workflow_run.conclusion == 'success'" in cond


@pytest.mark.parametrize("name", ["ci.yml", "deploy.yml"])
def test_no_run_script_interpolates_an_expression(name: str) -> None:
    lines = _text(WORKFLOWS / name).splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if not m:
            continue
        indent, rest = len(m.group(1)), m.group(2)
        body = [rest]
        if rest.strip() in ("|", ">", "|-", ">-"):
            body = []
            for nxt in lines[i + 1 :]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                body.append(nxt)
        assert "${{" not in "\n".join(body), f"{name}:{i + 1} interpolates into a run script"


def test_pnpm_lists_the_packages_allowed_to_run_install_scripts() -> None:
    pkg = json.loads(_text(ROOT / "package.json"))
    allowed = pkg["pnpm"]["onlyBuiltDependencies"]
    assert isinstance(allowed, list) and "esbuild" in allowed


def test_compose_publishes_no_port_beyond_loopback() -> None:
    text = _text(ROOT / "infra" / "deploy" / "docker-compose.yml")
    ports = re.findall(r"^\s+ports:\s*\n((?:\s+- .*\n)+)", text, re.M)
    assert ports, "expected published ports in the compose file"
    for block in ports:
        for item in re.findall(r"- \"?([^\"\n]+)\"?", block):
            assert item.startswith("127.0.0.1:"), f"port {item} is not bound to loopback"


def test_no_test_file_holds_a_key_shaped_literal() -> None:
    files = subprocess.run(
        ["git", "ls-files", "--", "apps", "packages"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    pat = re.compile(r"(^|/)(tests?/|__tests__/)|(^|/)test_[^/]*\.py$|\.test\.[cm]?[jt]sx?$")
    hits = []
    for rel in files:
        if not pat.search(rel):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(body.splitlines(), 1):
            if KEY_SHAPE.search(line):
                hits.append(f"{rel}:{n}")
    assert not hits, hits
