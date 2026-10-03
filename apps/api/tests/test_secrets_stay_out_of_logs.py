"""Live tokens never reach the logs, and a blank key is no key — 2026-09-29.

Until this date, with `RESEND_API_KEY` unset, the reset and invitation paths
logged the full link, live token included, to stdout, which Render keeps: anyone
who could read the logs could take over any account that asked for a reset.
And a blank `RESEND_API_KEY=` counted as a key, so the message went to Resend
with an empty credential.

The log tests capture everything a run can write — structlog's events
(`capture_logs`), stdout (`capsys`, where structlog prints in this project) and
stdlib logging (`caplog`, where httpx writes) — and search all of it. The token
is made findable by minting a known sentinel in place of the random one.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr

import avp_api
from avp_api.config import PROVIDER_SETTINGS, Settings
from avp_api.logs import configure_logging
from avp_api.main import create_app
from avp_api.services import email as email_service
from avp_api.services import invitations as invitation_service
from avp_api.services import password_reset as reset_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
OWNER = "owner@logs.example"
INVITEE = "invitee@logs.example"
STRANGER = "nobody-here@logs.example"
RESET_TOKEN = "RESETSENTINELtoken0123456789abcdefghijklmnopqrstu"
INVITE_TOKEN = "INVITESENTINELtoken0123456789abcdefghijklmnopqrst"


# ---------------------------------------------------------------------------
# a blank key is unset, for every provider setting
# ---------------------------------------------------------------------------


class TestABlankKeyIsNoKey:
    @pytest.mark.parametrize("name", PROVIDER_SETTINGS)
    def test_blank_or_whitespace_in_the_environment_is_unset(
        self, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for blank in ("", "   "):
            monkeypatch.setenv(name.upper(), blank)
            assert getattr(Settings(_env_file=None), name) is None, (name, blank)

    @pytest.mark.parametrize("name", PROVIDER_SETTINGS)
    def test_a_real_value_is_kept(self, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(name.upper(), "set-to-something")
        value = getattr(Settings(_env_file=None), name)
        assert value is not None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        assert raw == "set-to-something"

    def test_every_provider_setting_is_covered(self) -> None:
        """A new provider key must join the one normaliser, not get its own reading."""
        fields = Settings.model_fields
        keyish = {
            name
            for name in fields
            if name.endswith(("_api_key", "_key", "_secret"))
            and name not in ("app_secret",)
        }
        assert keyish <= set(PROVIDER_SETTINGS), keyish - set(PROVIDER_SETTINGS)

    async def test_a_blank_resend_key_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing reaches Resend with an empty credential — not even a request."""
        monkeypatch.setenv("RESEND_API_KEY", "")
        settings = Settings(_env_file=None)
        assert settings.email_configured is False

        def no_client(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("an HTTP client was opened with no key configured")

        monkeypatch.setattr(email_service.httpx, "AsyncClient", no_client)
        await email_service.send_password_reset(
            OWNER, "http://localhost:3000/reset-password#x", user_id="user_x", settings=settings
        )


# ---------------------------------------------------------------------------
# no token, no link and no address in any log line
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mint known tokens, so their absence from the logs can be asserted."""
    monkeypatch.setattr(reset_service, "new_session_token", lambda: RESET_TOKEN)
    monkeypatch.setattr(invitation_service, "new_session_token", lambda: INVITE_TOKEN)


@pytest.fixture
def resend_configured(settings, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:  # noqa: ANN001
    """A key set, and Resend answered by a fake that records what it was sent."""
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "email_test"})

    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(email_service.httpx, "AsyncClient", factory)
    return sent


async def _drive_both_paths(client: AsyncClient) -> None:
    """Sign up, ask for a reset (a real address and an unknown one), invite a seat."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Log Test Agency", "fullName": "Owner", "email": OWNER,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    agency_id = resp.json()["agency"]["id"]
    for address in (OWNER, STRANGER):
        reset = await client.post(f"{BASE}/auth/reset-password/request", json={"email": address})
        assert reset.status_code == 200, reset.text
    invite = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": INVITEE, "role": "member"}
    )
    assert invite.status_code == 201, invite.text


def _assert_nothing_secret(everything: str, web: str) -> None:
    # The links as the code builds them — the web app's address, then the
    # token. (The API's own `/auth/reset-password/request` path is not one:
    # the test client logs every request it makes, and that path is public.)
    links = (f"{web.rstrip('/')}/reset-password#", f"{web.rstrip('/')}/invite#")
    for secret in (RESET_TOKEN, INVITE_TOKEN, *links, OWNER, INVITEE, STRANGER):
        assert secret not in everything, f"{secret!r} reached the logs"


class TestNothingSecretIsLogged:
    async def test_with_email_not_configured(
        self, client: AsyncClient, settings, sentinel_tokens, capsys, caplog  # noqa: ANN001
    ) -> None:
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            await _drive_both_paths(client)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        _assert_nothing_secret(everything, settings.public_web_base_url)
        # And the events that say what happened are there, by id.
        kinds = {e["event"] for e in events}
        assert "email.password_reset.not_sent" in kinds
        assert "email.invitation.not_sent" in kinds
        assert "password_reset.request.no_account" in kinds
        assert all(e.get("user_id") for e in events if e["event"].startswith("email."))

    async def test_with_email_configured(
        self,
        client: AsyncClient,
        settings,  # noqa: ANN001
        sentinel_tokens,  # noqa: ANN001
        resend_configured,  # noqa: ANN001
        capsys,  # noqa: ANN001
        caplog,  # noqa: ANN001
    ) -> None:
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            await _drive_both_paths(client)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        _assert_nothing_secret(everything, settings.public_web_base_url)
        kinds = {e["event"] for e in events}
        assert {"email.password_reset.sent", "email.invitation.sent"} <= kinds
        # The links DID go out — to the inboxes, and only there.
        bodies = " ".join(message["text"] for message in resend_configured)
        assert f"/reset-password#{RESET_TOKEN}" in bodies
        assert f"/invite#{INVITE_TOKEN}" in bodies
        assert [m["to"] for m in resend_configured] == [[OWNER], [INVITEE]]


# ---------------------------------------------------------------------------
# httpx writes each request's whole URL, and the SerpApi key is in it — 2026-10-03
# ---------------------------------------------------------------------------

KEY_SENTINEL = "SERPKEYSENTINEL-0123456789abcdef"
# Literal, not `logs.QUIET_LOGGERS`: a name dropped from that tuple must fail these tests
# and not quietly shrink them.
HTTP_LOGGERS = ("httpx", "httpcore")
ANTHROPIC_LOGGER = "anthropic"
ALL_QUIET = (*HTTP_LOGGERS, ANTHROPIC_LOGGER)
# The loggers httpcore actually writes through: children of `httpcore`.
HTTPCORE_CHILDREN = (
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)
# What the SDK's request options carry: a prompt, here the text of a client's site.
PROMPT_SENTINEL = "PROMPTSENTINEL the page text of a client site"


@pytest.fixture
def unconfigured_loggers() -> Iterator[None]:
    """`httpx`, `httpcore` and `anthropic` as a process nothing has configured has them.

    That is no level of their own. Earlier tests have usually pinned them already (every
    `create_app` does), so this puts them back, and puts back whatever it found. `setLevel`,
    never the attribute: `setLevel` is what clears the logging module's cache of which
    levels are enabled.
    """
    found = {name: logging.getLogger(name).level for name in ALL_QUIET}
    for name in found:
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield
    for name, level in found.items():
        logging.getLogger(name).setLevel(level)


async def _request_through_httpx() -> None:
    """One request through a real httpx client, answered by a fake.

    The key is a query parameter, as SerpApi's is, so a line that names the request names it.
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get(
            "https://serpapi.com/search.json", params={"api_key": KEY_SENTINEL, "q": "x"}
        )


@pytest.mark.usefixtures("unconfigured_loggers")
class TestHttpxDoesNotWriteTheKey:
    """httpx logs `HTTP Request: GET <the whole URL> "HTTP/1.1 200 OK"` at INFO.

    Nothing in the app switches stdlib logging on, so the line is dropped. These pin that it
    stays dropped when something does: `configure_logging` gives `httpx` and `httpcore` a
    level of their own, and a logger with a level of its own does not follow the root.
    """

    async def test_left_alone_httpx_does_write_the_key_at_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The hazard, shown, so the tests below cannot pass for want of a line to catch."""
        caplog.set_level(logging.INFO)

        await _request_through_httpx()

        assert KEY_SENTINEL in caplog.text, (
            "httpx no longer writes the request URL at INFO: the pin in logs.py may be retired"
        )

    @pytest.mark.parametrize("root_level", [logging.INFO, logging.DEBUG], ids=["INFO", "DEBUG"])
    @pytest.mark.parametrize("root_opened", ["after", "before"])
    async def test_with_logging_configured_as_the_app_does_no_record_names_the_key(
        self, settings: Settings, caplog: pytest.LogCaptureFixture, root_level: int,
        root_opened: str,
    ) -> None:
        """Whichever order the root logger is opened and the app's logging is configured in."""
        if root_opened == "before":
            caplog.set_level(root_level)
        configure_logging(settings)
        if root_opened == "after":
            caplog.set_level(root_level)

        await _request_through_httpx()

        # `caplog` is on the root logger, so it sees a record from any logger, whatever its name.
        named_it = [
            record.name for record in caplog.records if KEY_SENTINEL in record.getMessage()
        ]
        assert named_it == []
        assert KEY_SENTINEL not in caplog.text

    @pytest.mark.parametrize("name", HTTP_LOGGERS)
    def test_configure_logging_pins_the_logger_to_warning_on_the_logger_itself(
        self, name: str, settings: Settings
    ) -> None:
        logger = logging.getLogger(name)
        assert logger.level == logging.NOTSET, "the fixture should have cleared it"
        root_level = logging.getLogger().level

        configure_logging(settings)
        configure_logging(settings)

        # Its own level, not the one it would inherit; and the root is not touched.
        assert logger.level == logging.WARNING
        assert logging.getLogger().level == root_level

    def test_the_loggers_httpcore_writes_through_are_quiet_under_a_debug_root(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Its trace prints the host, the port and every response header, at DEBUG."""
        configure_logging(settings)
        caplog.set_level(logging.DEBUG)

        for name in HTTPCORE_CHILDREN:
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
        # A real problem is still written.
        assert logging.getLogger("httpcore.connection").isEnabledFor(logging.WARNING)

    def test_create_app_leaves_them_pinned(self, settings: Settings) -> None:
        create_app(settings)

        assert [logging.getLogger(name).level for name in HTTP_LOGGERS] == [logging.WARNING] * 2


@pytest.mark.usefixtures("unconfigured_loggers")
class TestTheAnthropicSdkDoesNotWriteThePrompt:
    """At DEBUG the SDK's own logger prints each request's options, the prompt in them.

    `Request options: {… 'json_data': {'messages': [{'role': 'user', 'content': …}]}}`: the
    text of a client's site and, for sentiment, the engine's answer. The SDK raises that
    logger itself when `ANTHROPIC_LOG` is set (`anthropic/_utils/_logs.py`, as the package
    is imported), so "nothing switches logging on" is not the protection. The pin is.
    """

    def test_the_sdk_raising_its_logger_does_not_survive_configure_logging(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The logger the SDK writes the options through is a child of `anthropic`.
        sdk = logging.getLogger("anthropic._base_client")
        options = {"json_data": {"messages": [{"role": "user", "content": PROMPT_SENTINEL}]}}
        # What `ANTHROPIC_LOG=debug` does as the package is imported, before `create_app`
        # runs: its own logger, and httpx's, to DEBUG.
        logging.getLogger("anthropic").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        caplog.set_level(logging.DEBUG)

        sdk.debug("Request options: %s", options)
        assert PROMPT_SENTINEL in caplog.text, "a raised SDK logger writes nothing: no premise"

        caplog.clear()
        configure_logging(settings)
        sdk.debug("Request options: %s", options)
        sdk.warning("a real problem")

        assert PROMPT_SENTINEL not in caplog.text
        # Warnings and errors from the SDK are still written.
        assert "a real problem" in caplog.text
        assert logging.getLogger("anthropic").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_configure_logging_pins_the_anthropic_logger_to_warning_on_the_logger_itself(
        self, settings: Settings
    ) -> None:
        logger = logging.getLogger(ANTHROPIC_LOGGER)
        assert logger.level == logging.NOTSET, "the fixture should have cleared it"
        root_level = logging.getLogger().level

        configure_logging(settings)
        configure_logging(settings)

        assert logger.level == logging.WARNING
        assert logging.getLogger().level == root_level

    def test_create_app_leaves_it_pinned(self, settings: Settings) -> None:
        create_app(settings)

        assert logging.getLogger(ANTHROPIC_LOGGER).level == logging.WARNING


def _fresh_process(code: str, tmp_path: Path) -> dict[str, Any]:
    """Run `code` in a fresh interpreter with `ANTHROPIC_LOG` set; read the JSON it prints last."""
    env = {
        **os.environ,
        "ANTHROPIC_LOG": "info",
        # Not a deployed environment's settings, whatever shell this is run from.
        "ENVIRONMENT": "test",
        "PYTHONPATH": str(Path(avp_api.__file__).resolve().parents[1]),
    }
    env.pop("LOG_LEVEL", None)
    # From an empty directory, so that no developer's `.env` is read.
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    seen: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return seen


# The levels of the three loggers, as an expression for the fresh interpreter to print.
_LEVELS = "{n: logging.getLogger(n).level for n in ('httpx', 'httpcore', 'anthropic')}"


class TestThePinsComeLastInTheRealImportOrder:
    """Not simulated: a fresh process, with `ANTHROPIC_LOG` set, importing the app.

    `configure_logging` is the last word only if it runs after the SDK's import. `main.py`
    imports everything and then runs `create_app()`; this is what holds that. It would not
    hold if the SDK were imported lazily, after `create_app()` had pinned the loggers: the
    SDK would raise them again on its first import.
    """

    def test_the_sdk_raises_its_loggers_as_it_is_imported(self, tmp_path: Path) -> None:
        """The premise: with `ANTHROPIC_LOG` set there is something for the pin to undo."""
        seen = _fresh_process(
            f"import json, logging, anthropic\nprint(json.dumps({_LEVELS}))", tmp_path
        )

        assert seen["anthropic"] == logging.INFO
        assert seen["httpx"] == logging.INFO

    def test_importing_the_app_imports_the_sdk_and_leaves_all_three_at_warning(
        self, tmp_path: Path
    ) -> None:
        # Only the app is imported: if the SDK is among its imports it is already in
        # `sys.modules` by the time `import avp_api.main` returns, `create_app()` included.
        seen = _fresh_process(
            "import json, logging, sys\n"
            "import avp_api.main\n"
            "print(json.dumps({"
            f"'sdk_imported': 'anthropic' in sys.modules, 'levels': {_LEVELS}}}))",
            tmp_path,
        )

        assert seen["sdk_imported"] is True, (
            "the app does not import the Anthropic SDK while it is imported: an import "
            "after create_app() would raise the pinned loggers again"
        )
        assert seen["levels"] == dict.fromkeys(ALL_QUIET, logging.WARNING)


# ---------------------------------------------------------------------------
# the scripts that call SerpApi for real, and the shell they are run from — 2026-10-03
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(avp_api.__file__).resolve().parents[2] / "scripts"
# The calls that reach SerpApi: the search itself, and the detections that run it.
SERPAPI_ENTRY_POINTS = frozenset({"search_many", "detect_for_client", "detect_from_facts"})
_FUNCTIONS = ast.FunctionDef | ast.AsyncFunctionDef


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _scripts_reaching_serpapi() -> list[str]:
    return [
        path.name
        for path in sorted(SCRIPTS_DIR.glob("*.py"))
        if _called_names(ast.parse(path.read_text(encoding="utf-8"))) & SERPAPI_ENTRY_POINTS
    ]


def _own_nodes(function: _FUNCTIONS) -> Iterator[ast.AST]:
    """Every node `function` runs itself: its statements, not the bodies of functions it defines."""
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _FUNCTIONS | ast.Lambda):
            pending.extend(ast.iter_child_nodes(node))


SERPAPI_SCRIPTS = _scripts_reaching_serpapi()


class TestTheScriptsThatCallSerpapiConfigureLogging:
    """A script has no `create_app`, so nothing pins httpx and the SDK quiet unless it asks.

    With `ANTHROPIC_LOG` exported in the shell, httpx would print each SerpApi request's
    URL, key included, to a terminal whose output is pasted into the build log.
    """

    def test_the_scripts_that_reach_serpapi_are_found(self) -> None:
        """So the test below cannot pass for want of a script to look at."""
        assert {"verify_competitors.py", "verify_e2e.py", "verify_scoring.py"} <= set(
            SERPAPI_SCRIPTS
        )

    @pytest.mark.parametrize("name", SERPAPI_SCRIPTS)
    def test_main_configures_logging_before_it_awaits_anything(self, name: str) -> None:
        tree = ast.parse((SCRIPTS_DIR / name).read_text(encoding="utf-8"))
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "avp_api.logs"
            and any(alias.name == "configure_logging" for alias in node.names)
            for node in ast.walk(tree)
        )
        assert imported, f"{name} does not import configure_logging from avp_api.logs"

        mains = [node for node in tree.body if isinstance(node, _FUNCTIONS) and node.name == "main"]
        assert len(mains) == 1, f"{name} has no single top-level main()"
        main = mains[0]

        # One of main's own statements, so that it always runs, and not inside a branch.
        configured = [
            statement.lineno
            for statement in main.body
            if isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "configure_logging"
        ]
        assert configured, f"{name}: main() does not call configure_logging(...) itself"

        first_io = min(
            (
                node.lineno
                for node in _own_nodes(main)
                if isinstance(node, ast.Await | ast.AsyncWith | ast.AsyncFor)
            ),
            default=None,
        )
        assert first_io is None or configured[0] < first_io, (
            f"{name}: configure_logging (line {configured[0]}) comes after main() "
            f"has already awaited something (line {first_io})"
        )
