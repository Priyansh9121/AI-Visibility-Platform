"""The API's memory, as numbers — M1, 2026-10-02.

WHAT THIS IS FOR
----------------
The API runs scans in its own process and drives a real Chromium for the crawl
and the audit, on a host with a memory limit. Measured on the API image: the idle
server holds about 97 MB of its own (about 100-160 MB charged to its container),
one heavy page's audit peaks near 490 MB, and a crawl and an audit running together
reach about 586 MB.
Nothing in the logs said any of that, so a scan killed by the limit looked like
a scan that vanished (`EXECUTOR_LOST`). `snapshot()` is what the log lines that
say it are made of (`scan.phase_memory`, `browser.memory`, `app.memory_at_start`).
The runbook's "Reading the API's memory" explains how to read them.

WHAT IT READS, AND ONLY THAT
----------------------------
Five figures, each in MB (1 MB here is 1,048,576 bytes), each an `int` or `None`:

  process_tree_rss_mb   this process and every descendant of it (the browsers),
                        VmRSS summed from /proc
  cgroup_current_mb     what the container's cgroup is charged, page cache included
  cgroup_anon_mb        the part of it that is not cache
  cgroup_peak_mb        the highest `cgroup_current_mb` the cgroup has reached
  cgroup_limit_mb       the container's limit; None when there is none

`None` means unreadable, and that is common: macOS has neither /proc nor cgroups,
a bare CI runner may have no cgroup files, and `memory.peak` needs a recent kernel.
**Never raises.** A reading that fails is `None` and the others still stand,
because a memory figure is never worth the scan, the request or the start-up that
asked for it.

Numbers only, by construction: nothing here returns a name, a path, a command
line or a process's arguments, so nothing a client or a page said can reach a log
through it. The paths below are module constants so a test can point them at a
fake tree.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

PROC_ROOT = Path("/proc")
# cgroup v2: one tree, the container's own cgroup at its root.
CGROUP_V2_ROOT = Path("/sys/fs/cgroup")
# cgroup v1: memory has its own hierarchy.
CGROUP_V1_ROOT = Path("/sys/fs/cgroup/memory")

# The fields, in the order they are logged.
FIELDS: tuple[str, ...] = (
    "process_tree_rss_mb",
    "cgroup_current_mb",
    "cgroup_anon_mb",
    "cgroup_peak_mb",
    "cgroup_limit_mb",
)

_MIB = 1024 * 1024
# cgroup v1 says "no limit" with a number just under 2**63. Anything this large is
# not a limit anybody set.
_V1_NO_LIMIT_BYTES = 1 << 60


def snapshot() -> dict[str, int | None]:
    """The five figures above. Every value an int in MB, or None when it cannot be read."""
    return {
        "process_tree_rss_mb": _megabytes(_process_tree_rss_bytes),
        "cgroup_current_mb": _megabytes(_cgroup_current_bytes),
        "cgroup_anon_mb": _megabytes(_cgroup_anon_bytes),
        "cgroup_peak_mb": _megabytes(_cgroup_peak_bytes),
        "cgroup_limit_mb": _megabytes(_cgroup_limit_bytes),
    }


def _megabytes(read: Callable[[], int | None]) -> int | None:
    """`read()` in whole megabytes, rounded half up. None if it fails or is absurd."""
    try:
        value = read()
    except Exception:  # noqa: BLE001 - one figure unreadable must not take the others
        return None
    if value is None or value < 0:
        return None
    return (value + _MIB // 2) // _MIB


# ---------------------------------------------------------------------------
# this process and everything under it
# ---------------------------------------------------------------------------


def _process_tree_rss_bytes() -> int | None:
    """VmRSS of this process plus all its descendants, found by walking the parent links.

    One pass over /proc/*/stat builds who-is-whose-child; the walk down from this
    process then reads VmRSS for just those. A process that exits between the two
    is skipped, which is also how a zombie, which has no address space, costs
    nothing. None only when no process in the tree could be read at all.
    """
    children: dict[int, list[int]] = {}
    for entry in PROC_ROOT.iterdir():
        if not entry.name.isdigit():
            continue  # self, sys, meminfo ...
        parent = _parent_of(entry / "stat")
        if parent is not None:
            children.setdefault(parent, []).append(int(entry.name))

    me = os.getpid()
    tree = {me}
    frontier = [me]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in tree:  # a cycle in the links must not loop
                tree.add(child)
                frontier.append(child)

    total = 0
    readable = False
    for pid in tree:
        kilobytes = _vm_rss_kb(PROC_ROOT / str(pid) / "status")
        if kilobytes is not None:
            total += kilobytes * 1024
            readable = True
    return total if readable else None


def _parent_of(stat_file: Path) -> int | None:
    """The parent pid in a /proc/<pid>/stat, or None if it cannot be read or understood.

    The line is `pid (name) state ppid ...`. The name is whatever the program calls
    itself and can hold spaces and brackets, so the fields start after the LAST
    closing bracket.
    """
    try:
        text = stat_file.read_text()
        fields = text[text.rindex(")") + 1 :].split()
        return int(fields[1])
    except (OSError, ValueError, IndexError):
        return None


def _vm_rss_kb(status_file: Path) -> int | None:
    """VmRSS in kB from a /proc/<pid>/status. 0 for a readable file with no such line (a zombie)."""
    try:
        text = status_file.read_text()
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            try:
                return int(line.split()[1])
            except (ValueError, IndexError):
                return None
    return 0


# ---------------------------------------------------------------------------
# the container
# ---------------------------------------------------------------------------


def _cgroup_current_bytes() -> int | None:
    return _first(
        _integer(CGROUP_V2_ROOT / "memory.current"),
        _integer(CGROUP_V1_ROOT / "memory.usage_in_bytes"),
    )


def _cgroup_anon_bytes() -> int | None:
    # v2 calls the non-cache part `anon`; v1 calls it `rss`.
    return _first(
        _stat_value(CGROUP_V2_ROOT / "memory.stat", "anon"),
        _stat_value(CGROUP_V1_ROOT / "memory.stat", "rss"),
    )


def _cgroup_peak_bytes() -> int | None:
    return _first(
        _integer(CGROUP_V2_ROOT / "memory.peak"),
        _integer(CGROUP_V1_ROOT / "memory.max_usage_in_bytes"),
    )


def _cgroup_limit_bytes() -> int | None:
    limit = _first(
        _integer(CGROUP_V2_ROOT / "memory.max"),  # "max" is no limit: _integer says None
        _integer(CGROUP_V1_ROOT / "memory.limit_in_bytes"),
    )
    if limit is not None and limit >= _V1_NO_LIMIT_BYTES:
        return None
    return limit


def _first(*readings: int | None) -> int | None:
    for reading in readings:
        if reading is not None:
            return reading
    return None


def _integer(path: Path) -> int | None:
    """A file holding one non-negative integer, or None for anything else (absent, "max", junk)."""
    try:
        return _as_int(path.read_text())
    except (OSError, ValueError):
        return None


def _stat_value(path: Path, key: str) -> int | None:
    """The value on the `<key> <bytes>` line of a cgroup memory.stat."""
    try:
        text = path.read_text()
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == key:
            return _as_int(value)
    return None


def _as_int(text: str) -> int | None:
    """`text` as a non-negative integer. Only plain ASCII digits: not a sign, a point, a "0x"."""
    text = text.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    try:
        return int(text)
    except ValueError:  # a digit string past Python's conversion limit
        return None
