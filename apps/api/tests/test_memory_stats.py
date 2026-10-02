"""The API's memory, as numbers in the log — M1, 2026-10-02.

`memory_stats.snapshot()` reads this process's memory and the container's from
the files Linux keeps for them, and the log lines built on it carry nothing else.
These tests read those files from a fake tree, so the numbers are checked
against figures written here and not against whatever machine runs the suite;
and they run on a machine with no such files at all (macOS), where every value
must be None rather than an error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from avp_api import main
from avp_api.config import Settings
from avp_api.services import memory_stats

# Written out, not imported: renaming a field has to break this file, because
# the names are what someone searches the logs for.
FIELDS = {
    "process_tree_rss_mb",
    "cgroup_current_mb",
    "cgroup_anon_mb",
    "cgroup_peak_mb",
    "cgroup_limit_mb",
}
MIB = 1024 * 1024


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class TestOnThisMachine:
    def test_exactly_the_documented_keys_and_only_ints_or_none(self) -> None:
        found = memory_stats.snapshot()

        assert set(found) == FIELDS
        assert all(value is None or _is_count(value) for value in found.values()), found

    @pytest.mark.skipif(
        not Path("/proc/self/status").exists(), reason="Linux only: reads /proc"
    )
    def test_where_proc_exists_this_process_is_counted(self) -> None:
        # Not a fixed number — whatever this interpreter holds — but never zero.
        assert (memory_stats.snapshot()["process_tree_rss_mb"] or 0) > 0

    def test_the_fields_constant_is_the_set_logged(self) -> None:
        assert set(memory_stats.FIELDS) == FIELDS


# ---------------------------------------------------------------------------
# a fake /proc
# ---------------------------------------------------------------------------


def _process(
    proc: Path, pid: int, *, ppid: int, rss_kb: int | None, comm: str = "worker",
    state: str = "S",
) -> None:
    """One /proc/<pid>: the two files the reader opens. `rss_kb=None` is a zombie."""
    directory = proc / str(pid)
    directory.mkdir(parents=True)
    # The real layout: pid, (comm), state, ppid, then the numeric fields.
    (directory / "stat").write_text(
        f"{pid} ({comm}) {state} {ppid} {pid} {pid} 0 -1 4194560 100 0 0 0 1 1 0 0 20 0 1 0 "
        "12345 1000000 5000 18446744073709551615\n"
    )
    status = f"Name:\t{comm}\nState:\t{state} (sleeping)\nPid:\t{pid}\nPPid:\t{ppid}\n"
    if rss_kb is not None:  # a zombie has no address space, and no VmRSS line
        status += f"VmPeak:\t  999999 kB\nVmRSS:\t  {rss_kb} kB\nRssAnon:\t  1 kB\n"
    (directory / "status").write_text(status)


@pytest.fixture
def proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    monkeypatch.setattr(memory_stats, "PROC_ROOT", root)
    return root


class TestTheProcessTree:
    def test_this_process_and_every_descendant_are_summed_and_nothing_else(
        self, proc: Path
    ) -> None:
        me = os.getpid()
        _process(proc, me, ppid=1, rss_kb=102_400)  # 100 MB
        _process(proc, 900_001, ppid=me, rss_kb=51_200)  # a browser: 50 MB
        _process(proc, 900_002, ppid=900_001, rss_kb=25_600)  # its renderer: 25 MB
        _process(proc, 900_003, ppid=900_002, rss_kb=10_240)  # a grandchild's child: 10 MB
        _process(proc, 900_004, ppid=me, rss_kb=None, state="Z")  # a zombie: costs nothing
        # Not ours: a sibling under PID 1 holding far more than the tree.
        _process(proc, 900_005, ppid=1, rss_kb=4_000_000)
        # Entries that are not processes.
        (proc / "self").mkdir()
        (proc / "sys").mkdir()
        (proc / "meminfo").write_text("MemTotal: 1 kB\n")

        found = memory_stats.snapshot()

        assert found["process_tree_rss_mb"] == 185  # 100 + 50 + 25 + 10

    def test_a_process_name_with_brackets_and_spaces_does_not_break_the_parent_lookup(
        self, proc: Path
    ) -> None:
        me = os.getpid()
        _process(proc, me, ppid=1, rss_kb=10_240)
        # The name is whatever the program calls itself; the parser must read the
        # parent from after the LAST closing bracket.
        _process(proc, 900_010, ppid=me, rss_kb=20_480, comm="a b) S 999 (c")

        assert memory_stats.snapshot()["process_tree_rss_mb"] == 30

    def test_a_process_that_vanished_or_cannot_be_read_is_skipped(self, proc: Path) -> None:
        me = os.getpid()
        _process(proc, me, ppid=1, rss_kb=10_240)
        _process(proc, 900_020, ppid=me, rss_kb=20_480)
        # Listed, but its files went away or hold nothing readable.
        (proc / "900021").mkdir()
        (proc / "900022").mkdir()
        (proc / "900022" / "stat").write_text("not a stat line at all")
        (proc / "900023").mkdir()
        (proc / "900023" / "stat").write_text(f"900023 (x) S {me} 1 1 0\n")  # no status file

        assert memory_stats.snapshot()["process_tree_rss_mb"] == 30

    def test_a_tree_whose_every_process_is_unreadable_is_none_not_zero(
        self, proc: Path
    ) -> None:
        # Nothing under /proc: not even this process.
        assert memory_stats.snapshot()["process_tree_rss_mb"] is None

    def test_no_proc_at_all_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(memory_stats, "PROC_ROOT", tmp_path / "there-is-no-proc")

        assert memory_stats.snapshot()["process_tree_rss_mb"] is None

    def test_a_cycle_in_the_parent_links_does_not_loop(self, proc: Path) -> None:
        me = os.getpid()
        _process(proc, me, ppid=900_031, rss_kb=10_240)  # its "parent" is its own child
        _process(proc, 900_031, ppid=me, rss_kb=10_240)

        assert memory_stats.snapshot()["process_tree_rss_mb"] == 20


# ---------------------------------------------------------------------------
# a fake cgroup
# ---------------------------------------------------------------------------


@pytest.fixture
def cgroups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """(v2 root, v1 memory root), both empty and both pointed at by the module."""
    v2 = tmp_path / "cgroup"
    v1 = v2 / "memory"
    v2.mkdir()
    monkeypatch.setattr(memory_stats, "CGROUP_V2_ROOT", v2)
    monkeypatch.setattr(memory_stats, "CGROUP_V1_ROOT", v1)
    return v2, v1


def _cgroup_fields(found: dict[str, int | None]) -> dict[str, int | None]:
    return {key: value for key, value in found.items() if key.startswith("cgroup_")}


class TestCgroupV2:
    def test_every_figure_is_read_in_megabytes(self, cgroups: tuple[Path, Path]) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_text(f"{200 * MIB}\n")
        (v2 / "memory.stat").write_text(f"anon {100 * MIB}\nfile {50 * MIB}\nkernel 4096\n")
        (v2 / "memory.peak").write_text(f"{300 * MIB}\n")
        (v2 / "memory.max").write_text(f"{512 * MIB}\n")

        assert _cgroup_fields(memory_stats.snapshot()) == {
            "cgroup_current_mb": 200,
            "cgroup_anon_mb": 100,
            "cgroup_peak_mb": 300,
            "cgroup_limit_mb": 512,
        }

    def test_a_container_with_no_limit_has_none_for_it(self, cgroups: tuple[Path, Path]) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_text(f"{200 * MIB}\n")
        (v2 / "memory.max").write_text("max\n")

        found = memory_stats.snapshot()

        assert found["cgroup_limit_mb"] is None
        assert found["cgroup_current_mb"] == 200

    def test_the_figures_are_rounded_to_the_nearest_megabyte(
        self, cgroups: tuple[Path, Path]
    ) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_text(str(MIB + MIB // 2))  # exactly 1.5 MB
        (v2 / "memory.peak").write_text(str(MIB + MIB // 2 - 1))  # a byte under it
        (v2 / "memory.max").write_text(str(3 * MIB + 100))  # 3.0001 MB

        found = memory_stats.snapshot()

        assert (found["cgroup_current_mb"], found["cgroup_peak_mb"], found["cgroup_limit_mb"]) == (
            2, 1, 3,
        )

    def test_a_missing_file_costs_only_its_own_field(self, cgroups: tuple[Path, Path]) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_text(f"{64 * MIB}\n")  # no peak (an older kernel)
        (v2 / "memory.stat").write_text("file 1\n")  # no `anon` line

        assert _cgroup_fields(memory_stats.snapshot()) == {
            "cgroup_current_mb": 64,
            "cgroup_anon_mb": None,
            "cgroup_peak_mb": None,
            "cgroup_limit_mb": None,
        }

    @pytest.mark.parametrize("content", ["", "garbage", "-5", "12.5", "0x10"])
    def test_a_file_with_nothing_usable_is_none(
        self, cgroups: tuple[Path, Path], content: str
    ) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_text(content)

        assert memory_stats.snapshot()["cgroup_current_mb"] is None


class TestCgroupV1:
    def test_every_figure_falls_back_to_the_v1_files(self, cgroups: tuple[Path, Path]) -> None:
        _, v1 = cgroups
        v1.mkdir()
        (v1 / "memory.usage_in_bytes").write_text(f"{150 * MIB}\n")
        (v1 / "memory.stat").write_text(
            f"cache {40 * MIB}\nrss {75 * MIB}\ntotal_rss {999 * MIB}\n"
        )
        (v1 / "memory.max_usage_in_bytes").write_text(f"{220 * MIB}\n")
        (v1 / "memory.limit_in_bytes").write_text(f"{512 * MIB}\n")

        assert _cgroup_fields(memory_stats.snapshot()) == {
            "cgroup_current_mb": 150,
            "cgroup_anon_mb": 75,  # `rss`, not `total_rss`
            "cgroup_peak_mb": 220,
            "cgroup_limit_mb": 512,
        }

    def test_v1s_no_limit_is_a_number_near_two_to_the_sixty_third_and_means_none(
        self, cgroups: tuple[Path, Path]
    ) -> None:
        _, v1 = cgroups
        v1.mkdir()
        (v1 / "memory.limit_in_bytes").write_text("9223372036854771712\n")

        assert memory_stats.snapshot()["cgroup_limit_mb"] is None

    def test_v2_wins_when_both_exist(self, cgroups: tuple[Path, Path]) -> None:
        v2, v1 = cgroups
        v1.mkdir()
        (v2 / "memory.current").write_text(f"{10 * MIB}\n")
        (v1 / "memory.usage_in_bytes").write_text(f"{99 * MIB}\n")

        assert memory_stats.snapshot()["cgroup_current_mb"] == 10


class TestWhereThereIsNothingToRead:
    def test_a_machine_with_no_proc_and_no_cgroup_is_all_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # macOS, or a bare CI runner without cgroup files: every value None.
        monkeypatch.setattr(memory_stats, "PROC_ROOT", tmp_path / "no-proc")
        monkeypatch.setattr(memory_stats, "CGROUP_V2_ROOT", tmp_path / "no-cgroup")
        monkeypatch.setattr(memory_stats, "CGROUP_V1_ROOT", tmp_path / "no-cgroup" / "memory")

        assert memory_stats.snapshot() == dict.fromkeys(FIELDS)

    def test_it_never_raises_whatever_the_reads_do(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Exploding:
            """A path that fails in every way a path can."""

            def __truediv__(self, other: object) -> Exploding:
                raise RuntimeError("path arithmetic exploded")

            def iterdir(self) -> Any:
                raise RuntimeError("listing exploded")

            def read_text(self, *args: object, **kwargs: object) -> str:
                raise RuntimeError("read exploded")

        for name in ("PROC_ROOT", "CGROUP_V2_ROOT", "CGROUP_V1_ROOT"):
            monkeypatch.setattr(memory_stats, name, Exploding())

        assert memory_stats.snapshot() == dict.fromkeys(FIELDS)

    def test_a_file_that_is_not_text_is_none_and_the_rest_still_read(
        self, cgroups: tuple[Path, Path]
    ) -> None:
        v2, _ = cgroups
        (v2 / "memory.current").write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
        (v2 / "memory.peak").write_text(f"{7 * MIB}\n")

        found = memory_stats.snapshot()

        assert found["cgroup_current_mb"] is None
        assert found["cgroup_peak_mb"] == 7


# ---------------------------------------------------------------------------
# the log line at startup
# ---------------------------------------------------------------------------

SNAPSHOT = {
    "process_tree_rss_mb": 97,
    "cgroup_current_mb": 132,
    "cgroup_anon_mb": 90,
    "cgroup_peak_mb": 140,
    "cgroup_limit_mb": 512,
}


async def _nothing() -> None:
    return None


class TestAtStartup:
    async def test_the_app_logs_its_memory_once_and_only_numbers(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        monkeypatch.setattr(main, "close_redis", _nothing)
        monkeypatch.setattr(memory_stats, "snapshot", lambda: dict(SNAPSHOT))
        app = main.create_app(settings)

        with capture_logs() as logs:
            async with main.lifespan(app):
                pass

        started = [entry for entry in logs if entry["event"] == "app.memory_at_start"]
        assert started == [{"event": "app.memory_at_start", "log_level": "info", **SNAPSHOT}]

    async def test_a_snapshot_that_raises_does_not_stop_the_app_starting(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> dict[str, int | None]:
            raise RuntimeError("memory exploded")

        monkeypatch.setattr(main, "dispose_engine", _nothing)
        monkeypatch.setattr(main, "close_redis", _nothing)
        monkeypatch.setattr(memory_stats, "snapshot", boom)
        app = main.create_app(settings)

        with capture_logs() as logs:
            async with main.lifespan(app):
                pass  # reached: the app came up

        assert not [entry for entry in logs if entry["event"] == "app.memory_at_start"]
