"""The API image reaps what Chromium leaves behind — M1, 2026-10-02.

Every browser run leaves one or two helper processes whose parent has gone.
Linux gives an orphan to PID 1, and the image's PID 1 was uvicorn (`exec uvicorn`
in `docker-entrypoint.sh`), which never reaps: each one stays a zombie until the
container restarts. They cost no memory, but they accumulate, and they fill the
process table. `tini` is PID 1 instead: it reaps them, and passes SIGTERM on to
the script that `exec`s uvicorn, so a deploy still stops the server cleanly.

The image is not built here, so this reads the Dockerfile and the script and
holds them to the arrangement that does it. CI's image job builds and runs the
real thing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1]
DOCKERFILE = API / "Dockerfile"
ENTRYPOINT_SCRIPT = API / "docker-entrypoint.sh"

TINI = "/usr/bin/tini"


def instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, the rest of it) for each Dockerfile instruction, in order.

    A comment line is dropped wherever it sits, as Docker does, even in the middle of
    a continued instruction (the `ENV` block has two); a trailing backslash joins the
    next line.
    """
    joined: list[str] = []
    pending = ""
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or (not line and not pending):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        joined.append(pending + line)
        pending = ""
    if pending:
        joined.append(pending.strip())
    out = []
    for line in joined:
        keyword, _, rest = line.partition(" ")
        out.append((keyword.upper(), rest.strip()))
    return out


def index_of(keyword: str, containing: str = "") -> int:
    for position, (word, rest) in enumerate(instructions()):
        if word == keyword and containing in rest:
            return position
    raise AssertionError(f"no {keyword} instruction containing {containing!r}")


class TestTiniIsPid1:
    def test_the_entrypoint_runs_the_script_under_tini(self) -> None:
        entrypoints = [rest for word, rest in instructions() if word == "ENTRYPOINT"]

        assert len(entrypoints) == 1, "one ENTRYPOINT, or the last silently wins"
        argv = json.loads(entrypoints[0])  # exec form: a shell form would be PID 1 itself
        assert argv == [TINI, "--", "/app/apps/api/docker-entrypoint.sh"]

    def test_tini_is_installed_from_apt_with_no_recommends_and_the_lists_removed(self) -> None:
        run = instructions()[index_of("RUN", "tini")][1]

        assert "apt-get update" in run
        assert re.search(r"apt-get install\s+-y\s+--no-install-recommends\s+tini\b", run), run
        assert "rm -rf /var/lib/apt/lists/*" in run

    def test_it_is_installed_as_root_so_before_the_user_line(self) -> None:
        installs = index_of("RUN", "tini")
        user = index_of("USER")

        assert installs < user
        assert not [w for w, _ in instructions()[:installs] if w == "USER"], (
            "an earlier USER line would make the apt-get run as a user that cannot"
        )

    def test_the_container_still_runs_as_the_non_root_user(self) -> None:
        users = [rest for word, rest in instructions() if word == "USER"]

        assert users == ["pwuser"]
        # ... and the install is not left to run after it.
        assert index_of("USER") > index_of("RUN", "tini")

    def test_the_why_is_written_next_to_it(self) -> None:
        text = DOCKERFILE.read_text()

        assert "PID 1" in text and "reap" in text
        assert "SIGTERM" in text

    def test_the_rest_of_the_image_is_what_it_was(self) -> None:
        steps = instructions()

        assert steps[index_of("FROM")][1] == "mcr.microsoft.com/playwright/python:v1.62.0-noble"
        assert index_of("HEALTHCHECK") > index_of("USER")
        assert "EXPOSE" in {word for word, _ in steps}
        # The two uv syncs the dependency cache layer depends on.
        assert sum("uv sync --frozen" in rest for word, rest in steps if word == "RUN") == 2


class TestTheScriptHandsOverToUvicorn:
    def test_it_is_executable(self) -> None:
        """tini runs it directly; a script that lost its mode would stop the container starting."""
        assert os.access(ENTRYPOINT_SCRIPT, os.X_OK)

    def test_it_still_execs_uvicorn_so_tini_signals_the_server_itself(self) -> None:
        lines = [
            line.strip() for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        ]

        assert any(line.startswith("exec uvicorn avp_api.main:app") for line in lines)

    @staticmethod
    def exec_argv() -> list[str]:
        """The words of the `exec uvicorn ...` line, as the shell would split them."""
        (line,) = [
            line.strip() for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if line.strip().startswith("exec uvicorn")
        ]
        return shlex.split(line)

    def test_uvicorn_is_told_to_run_one_worker(self) -> None:
        """Scans run in this process and the browser slot is per process: one worker.

        The flag has to be there. uvicorn reads `WEB_CONCURRENCY` only when `--workers`
        is absent, so a script that merely never said "workers" would start as many as
        the host's environment asked for, each with its own browser slot.
        """
        argv = self.exec_argv()

        assert argv.count("--workers") == 1
        assert argv[argv.index("--workers") + 1] == "1"

    def test_the_script_never_reads_web_concurrency_itself(self) -> None:
        """The flag, not the variable, is what pins it: nothing in the code reads the variable."""
        code = "\n".join(
            line for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        )

        assert "WEB_CONCURRENCY" not in code

    def test_uvicorn_still_hears_the_flag_as_one_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What uvicorn does with the words above, asked of uvicorn rather than assumed."""
        from uvicorn.config import Config

        argv = self.exec_argv()
        workers = int(argv[argv.index("--workers") + 1])
        monkeypatch.setenv("WEB_CONCURRENCY", "4")  # what a host could set

        def config(workers: int | None) -> Config:
            # `workers=None` is the flag being absent, which is what the CLI passes then.
            # No log config: building a `Config` must not reconfigure this process's logging.
            return Config("avp_api.main:app", workers=workers, log_config=None)

        assert config(None).workers == 4  # the trap: the variable decides when the flag is absent
        assert config(workers).workers == 1  # and with the flag, it is not read

    def test_uvicorn_is_told_not_to_announce_itself(self) -> None:
        """`--no-server-header`: no `Server: uvicorn` on any response (launch checklist 3.1).

        It tells a stranger what runs here; the web app's twin, `X-Powered-By`, is off for
        the same reason. Asked of uvicorn's own command-line parser, given every word on
        the line as the shell would pass it: it accepts the line (a mistyped flag is
        refused there, and CI's image job would be the only other place to notice), the
        flag is what turns the header off (it is on without it), and the worker count and
        the proxy setting are still read as they were.
        """
        from uvicorn.main import main as uvicorn_cli

        argv = self.exec_argv()
        assert argv.count("--no-server-header") == 1
        # The shell expands the port; shlex does not. Dropped: `exec uvicorn`.
        words = [word.replace("${PORT:-8000}", "8000") for word in argv[2:]]

        parsed = uvicorn_cli.make_context("uvicorn", list(words)).params
        without = uvicorn_cli.make_context(
            "uvicorn", [word for word in words if word != "--no-server-header"]
        ).params

        assert parsed["server_header"] is False
        assert without["server_header"] is True, "the flag is not what turns it off"
        assert parsed["workers"] == 1
        assert parsed["proxy_headers"] is True

    @staticmethod
    def code_lines() -> list[str]:
        """The script's lines that are code: not blank, not a comment."""
        return [
            line.strip() for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_it_unsets_stripe_log_before_it_starts_anything(self) -> None:
        """The Stripe SDK PRINTS each request, the bodies and Stripe's error messages to stderr
        when `STRIPE_LOG` is `info` or `debug`, and reads the variable once, as it is imported:
        it has to be gone before alembic or uvicorn is started. The behaviour, with the real
        script run under stubs, is `tests/test_secrets_stay_out_of_logs.py`; this pins the line
        and its place, so it cannot drift below the `alembic` call or the `exec`."""
        code = self.code_lines()

        assert code.count("unset STRIPE_LOG") == 1
        unset = code.index("unset STRIPE_LOG")
        assert unset < code.index("alembic upgrade head")
        assert unset < next(i for i, line in enumerate(code) if line.startswith("exec uvicorn"))

    def test_the_why_of_the_unset_is_written_next_to_it(self) -> None:
        comments = "\n".join(
            line for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if line.strip().startswith("#")
        )

        assert "STRIPE_LOG" in comments
        assert "stderr" in comments

    def test_it_unsets_pydantic_settings_debug_before_it_starts_anything(self) -> None:
        """`PYDANTIC_SETTINGS_DEBUG` makes pydantic-settings write every source it reads, secrets
        included, to its logger at DEBUG. The behaviour, with the real script run under stubs, is
        `tests/test_secrets_stay_out_of_logs.py`; this pins the line and its place, as for
        `STRIPE_LOG`, so it cannot drift below the `alembic` call or the `exec`."""
        code = self.code_lines()

        assert code.count("unset PYDANTIC_SETTINGS_DEBUG") == 1
        unset = code.index("unset PYDANTIC_SETTINGS_DEBUG")
        assert unset < code.index("alembic upgrade head")
        assert unset < next(i for i, line in enumerate(code) if line.startswith("exec uvicorn"))

    def test_the_why_of_the_pydantic_settings_unset_is_written_right_above_it(self) -> None:
        lines = ENTRYPOINT_SCRIPT.read_text().splitlines()
        at = next(
            i for i, line in enumerate(lines) if line.strip() == "unset PYDANTIC_SETTINGS_DEBUG"
        )
        above: list[str] = []
        for line in reversed(lines[:at]):
            if not line.startswith("#"):
                break
            above.append(line)
        said = "\n".join(above)

        assert "PYDANTIC_SETTINGS_DEBUG" in said
        assert "stderr" in said

    def test_the_one_worker_is_said_to_be_deliberate(self) -> None:
        comments = "\n".join(
            line for line in ENTRYPOINT_SCRIPT.read_text().splitlines()
            if line.strip().startswith("#")
        )

        assert "ONE worker" in comments
        assert "browser slot" in comments
        # ... and why the flag is there and not only the absence of a variable.
        assert "WEB_CONCURRENCY" in comments
