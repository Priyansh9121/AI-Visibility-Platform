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
from typing import Any, get_args

import httpx
import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr
from stripe import _util as stripe_util

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
STRIPE_LOGGER = "stripe"
ALL_QUIET = (*HTTP_LOGGERS, ANTHROPIC_LOGGER, STRIPE_LOGGER)
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
    """`httpx`, `httpcore`, `anthropic` and `stripe` as a process nothing has configured has them.

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


# What one call through the Stripe SDK writes, as the SDK's own helpers write it. The URL, the
# request body (an agency's name, say) and Stripe's error message are what must not reach a log.
STRIPE_URL = "/v1/customers/cus_STRIPELOGURLSENTINEL"
STRIPE_BODY = "name=STRIPEBODYSENTINEL+Agency&email=person%40example.test"
STRIPE_ERROR = "No such customer: STRIPEERRORSENTINEL"
STRIPE_SENTINELS = ("STRIPELOGURLSENTINEL", "STRIPEBODYSENTINEL", "STRIPEERRORSENTINEL")


def _the_sdk_writes_one_call() -> None:
    stripe_util.log_info("Request to Stripe api", method="post", url=STRIPE_URL)
    stripe_util.log_debug("Post details", post_data=STRIPE_BODY, api_version="2026-08-26")
    stripe_util.log_debug("API response body", body=STRIPE_BODY)
    stripe_util.log_info("Stripe v1 API error received", error_message=STRIPE_ERROR)


@pytest.mark.usefixtures("unconfigured_loggers")
class TestTheStripeSdkDoesNotWriteItsRequests:
    """The SDK logs through `stripe`: INFO for each request's method and URL and for each error
    Stripe answers with (its message), DEBUG for the request body and the response body. A request
    body carries an agency's name; a response body, a customer's.

    Nothing in the app switches stdlib logging on, so the lines are dropped. These pin that they
    stay dropped when something does: `configure_logging` gives `stripe` a level of its own, and a
    logger with a level of its own does not follow the root. (The SDK can also PRINT the same lines
    to stderr, past every logger level: that is `STRIPE_LOG`, switched off at the entrypoint,
    below.)
    """

    def test_left_alone_the_sdk_does_write_them_at_info_and_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The hazard, shown, so the tests below cannot pass for want of a line to catch."""
        caplog.set_level(logging.DEBUG)

        _the_sdk_writes_one_call()

        for sentinel in STRIPE_SENTINELS:
            assert sentinel in caplog.text, (
                f"the Stripe SDK no longer writes {sentinel!r} through its logger: "
                "the pin in logs.py may be retired"
            )

    @pytest.mark.parametrize("root_level", [logging.INFO, logging.DEBUG], ids=["INFO", "DEBUG"])
    @pytest.mark.parametrize("root_opened", ["after", "before"])
    def test_with_logging_configured_as_the_app_does_none_of_it_is_written(
        self, settings: Settings, caplog: pytest.LogCaptureFixture, root_level: int,
        root_opened: str,
    ) -> None:
        """Whichever order the root logger is opened and the app's logging is configured in."""
        if root_opened == "before":
            caplog.set_level(root_level)
        configure_logging(settings)
        if root_opened == "after":
            caplog.set_level(root_level)

        _the_sdk_writes_one_call()

        for sentinel in STRIPE_SENTINELS:
            assert sentinel not in caplog.text, sentinel

    def test_a_real_problem_from_the_sdk_is_still_written(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        configure_logging(settings)
        caplog.set_level(logging.DEBUG)

        logging.getLogger("stripe").warning("a real problem")

        assert "a real problem" in caplog.text

    def test_configure_logging_pins_the_stripe_logger_to_warning_on_the_logger_itself(
        self, settings: Settings
    ) -> None:
        logger = logging.getLogger(STRIPE_LOGGER)
        assert logger.level == logging.NOTSET, "the fixture should have cleared it"
        root_level = logging.getLogger().level

        configure_logging(settings)
        configure_logging(settings)

        assert logger.level == logging.WARNING
        assert logging.getLogger().level == root_level

    def test_create_app_leaves_it_pinned(self, settings: Settings) -> None:
        create_app(settings)

        assert logging.getLogger(STRIPE_LOGGER).level == logging.WARNING

    def test_logs_py_names_it_among_the_quiet_loggers(self) -> None:
        """The literal list above is what these tests hold the module to, not the other way."""
        from avp_api.logs import QUIET_LOGGERS

        assert set(ALL_QUIET) <= set(QUIET_LOGGERS)


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


# The levels of the quiet loggers, as an expression for the fresh interpreter to print.
_LEVELS = (
    "{n: logging.getLogger(n).level for n in ('httpx', 'httpcore', 'anthropic', 'stripe')}"
)


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

    def test_importing_the_app_imports_the_sdk_and_leaves_every_quiet_logger_at_warning(
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
# The calls that reach SerpApi: the search itself, the account read (`quota_left`: free, and it
# carries the key in its URL as a search does), and the detections that run both.
SERPAPI_ENTRY_POINTS = frozenset(
    {"search_many", "quota_left", "detect_for_client", "detect_from_facts"}
)
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


# ---------------------------------------------------------------------------
# the Stripe SDK prints past every logger when STRIPE_LOG is set — 2026-10-04
# ---------------------------------------------------------------------------

ENTRYPOINT = Path(avp_api.__file__).resolve().parents[2] / "docker-entrypoint.sh"
SDK_LINE = "log_info('Request to Stripe api', method='get', url='/v1/prices/price_PRINTSENTINEL')"


def _python(code: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run `code` in a fresh interpreter, with `env` added, and keep its stderr."""
    full = {
        **os.environ,
        "PYTHONPATH": str(Path(avp_api.__file__).resolve().parents[1]),
        **env,
    }
    for name in ("STRIPE_LOG", "ENVIRONMENT"):
        if name not in env:
            full.pop(name, None)
    # From an empty directory, so that no developer's `.env` is read.
    return subprocess.run(
        [sys.executable, "-c", code],
        env=full, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )


class TestTheStripeSdkPrintsWhenToldTo:
    """`STRIPE_LOG=info` (or `debug`) makes the SDK PRINT each request's method and URL, and the
    message of each error Stripe answers with, to stderr. It prints; it does not log, so no logger
    level stops it, and the pin on the `stripe` logger above is not the protection. `debug` adds
    the request and response bodies. The variable is read once, as `stripe._util` is imported
    (`STRIPE_LOG = os.environ.get("STRIPE_LOG")`), so unsetting it in Python code that runs after
    the import changes nothing: it has to be gone before the process starts. That is what the
    entrypoint does.
    """

    @pytest.mark.parametrize("level", ["info", "debug"])
    def test_with_it_set_the_sdk_prints_the_request_to_stderr(
        self, tmp_path: Path, level: str
    ) -> None:
        """The hazard, shown, so the entrypoint tests below cannot pass for want of a line."""
        done = _python(
            f"from stripe._util import log_info\n{SDK_LINE}", tmp_path, STRIPE_LOG=level
        )

        assert done.returncode == 0, done.stderr[-1500:]
        assert "price_PRINTSENTINEL" in done.stderr

    def test_without_it_the_sdk_prints_nothing(self, tmp_path: Path) -> None:
        done = _python(f"from stripe._util import log_info\n{SDK_LINE}", tmp_path)

        assert done.returncode == 0, done.stderr[-1500:]
        assert "PRINTSENTINEL" not in done.stderr

    def test_it_is_read_once_at_import_so_unsetting_it_later_in_python_is_too_late(
        self, tmp_path: Path
    ) -> None:
        done = _python(
            "import os\n"
            "from stripe._util import log_info\n"
            "del os.environ['STRIPE_LOG']\n"
            f"{SDK_LINE}",
            tmp_path,
            STRIPE_LOG="info",
        )

        assert done.returncode == 0, done.stderr[-1500:]
        assert "price_PRINTSENTINEL" in done.stderr


class TestTheAppSwitchesTheSdkPrintingOffItself:
    """Without the entrypoint: a developer who exports `STRIPE_LOG` and runs `uvicorn` directly.

    `stripe._util` reads the variable into a module global as it is imported, and decides at each
    call whether to print (`_console_log_level`), so billing, the only module here that imports
    the SDK, clears that global and `stripe.log` as it is imported.
    """

    PRINT_ALL = (
        "from stripe._util import log_info, log_debug\n"
        f"{SDK_LINE}\n"
        "log_debug('Stripe API response', body='price_PRINTSENTINEL')\n"
    )

    @pytest.mark.parametrize("level", ["info", "debug"])
    def test_importing_billing_makes_the_sdk_print_nothing_whatever_the_variable_says(
        self, tmp_path: Path, level: str
    ) -> None:
        done = _python(
            f"import avp_api.services.billing\n{self.PRINT_ALL}", tmp_path, STRIPE_LOG=level
        )

        assert done.returncode == 0, done.stderr[-1500:]
        assert "PRINTSENTINEL" not in done.stderr

    def test_building_the_app_makes_the_sdk_print_nothing_with_debug_set(
        self, tmp_path: Path
    ) -> None:
        done = _python(
            f"from avp_api.main import create_app\ncreate_app()\n{self.PRINT_ALL}",
            tmp_path,
            STRIPE_LOG="debug",
        )

        assert done.returncode == 0, done.stderr[-1500:]
        assert "PRINTSENTINEL" not in done.stderr

    def test_a_module_level_stripe_log_setting_is_cleared_too(self, tmp_path: Path) -> None:
        done = _python(
            "import stripe\nstripe.log = 'debug'\nimport avp_api.services.billing\n"
            f"{self.PRINT_ALL}",
            tmp_path,
        )

        assert done.returncode == 0, done.stderr[-1500:]
        assert "PRINTSENTINEL" not in done.stderr


def _run_entrypoint(script: Path, tmp_path: Path, **env: str) -> dict[str, str]:
    """Run an entrypoint script with `alembic` and `uvicorn` replaced by stubs; what each saw.

    The stubs write their arguments, `STRIPE_LOG` and `PYDANTIC_SETTINGS_DEBUG` (each, or
    `<unset>`) and their place in the order to files, and `cd` is a no-op, since the script `cd`s
    into `/app`. The real script is what runs: its `set -euo pipefail`, its order, its `exec`.
    """
    bin_dir = tmp_path / "bin"
    out = tmp_path / "out"
    bin_dir.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    for name in ("alembic", "uvicorn"):
        stub = bin_dir / name
        stub.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\' "${{STRIPE_LOG-<unset>}}" > "$STUB_OUT/{name}.stripe_log"\n'
            "printf '%s' \"${PYDANTIC_SETTINGS_DEBUG-<unset>}\" "
            f'> "$STUB_OUT/{name}.pydantic_settings_debug"\n'
            f'printf \'%s\' "$*" > "$STUB_OUT/{name}.argv"\n'
            f'printf \'%s\\n\' {name} >> "$STUB_OUT/order"\n'
        )
        stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_OUT": str(out),
        "PORT": "9",
        "HOME": str(tmp_path),
        **env,
    }
    done = subprocess.run(
        ["bash", "-c", 'cd() { :; }; export -f cd; exec "$0"', str(script)],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
    return {path.name: path.read_text() for path in out.iterdir()}


class TestTheEntrypointSwitchesItOff:
    """The container's entrypoint unsets `STRIPE_LOG` before it starts anything, so a variable
    set on the host (a debugging session left on, a template that carried it) cannot make the API
    print every Stripe request, the request bodies and the answers to stderr, which Render keeps."""

    @pytest.mark.parametrize("level", ["info", "debug"])
    def test_neither_alembic_nor_uvicorn_is_given_the_variable(
        self, tmp_path: Path, level: str
    ) -> None:
        seen = _run_entrypoint(ENTRYPOINT, tmp_path, STRIPE_LOG=level)

        assert seen["alembic.stripe_log"] == "<unset>"
        assert seen["uvicorn.stripe_log"] == "<unset>"

    def test_it_is_the_real_script_that_ran_and_in_its_order(self, tmp_path: Path) -> None:
        seen = _run_entrypoint(ENTRYPOINT, tmp_path, STRIPE_LOG="info")

        assert seen["order"] == "alembic\nuvicorn\n"
        assert seen["alembic.argv"] == "upgrade head"
        assert seen["uvicorn.argv"].startswith("avp_api.main:app ")
        assert "--workers 1" in seen["uvicorn.argv"]

    def test_without_the_line_the_harness_does_see_it(self, tmp_path: Path) -> None:
        """A control inside the test: the same script minus the unset, so the check above is
        known to be able to tell."""
        lines = ENTRYPOINT.read_text().splitlines(keepends=True)
        kept = [line for line in lines if line.strip() != "unset STRIPE_LOG"]
        assert len(kept) == len(lines) - 1, "the script has no `unset STRIPE_LOG` line to remove"
        without = tmp_path / "docker-entrypoint.sh"
        without.write_text("".join(kept))
        without.chmod(0o755)

        seen = _run_entrypoint(without, tmp_path, STRIPE_LOG="debug")

        assert seen["alembic.stripe_log"] == "debug"
        assert seen["uvicorn.stripe_log"] == "debug"


# ---------------------------------------------------------------------------
# a failed start prints no setting's value — 2026-10-06
# ---------------------------------------------------------------------------
# Pydantic prints a failed validation as its message and `input_value=<the input>`. For a MODEL
# validator (`_harden_deployed_environments`) the input is the whole settings dict: the raw
# environment strings, unmasked, cut to 50 characters (the first 25 and the last 24). A refused
# production start therefore wrote the tail of the last setting that was set (a webhook secret, a
# session secret, an address) to the deploy log, which Render keeps, and a key pasted into the
# wrong variable was printed whole by its own field's error. `Settings` hides its input in errors.
#
# These start the real thing in a fresh interpreter, from an empty directory (no `.env`), whose
# environment is ONLY what the test sets, with a sentinel in the setting under test, and read all
# that it wrote. Each sentinel is 11 characters and ends its value, so that cut would show it whole.

SRC_DIR = Path(avp_api.__file__).resolve().parents[1]
START_SETTINGS = "from avp_api.config import get_settings\nget_settings()\n"
START_THE_APP = "import avp_api.main\n"
UVICORN_START = [sys.executable, "-m", "uvicorn", "avp_api.main:app", "--port", "0"]

# Found from the model, so that a setting added later is in every test below without being named.
SECRET_SETTINGS = tuple(
    name
    for name, field in Settings.model_fields.items()
    if SecretStr in (field.annotation, *get_args(field.annotation))
)
URL_SETTINGS = ("database_url", "redis_url")  # `user:password@host`: credentials on a host
ADDRESS_SETTINGS = ("feedback_email", "scan_allowance_exempt_emails")  # no address in a log
SENSITIVE_SETTINGS = (*SECRET_SETTINGS, *URL_SETTINGS, *ADDRESS_SETTINGS)

# A real key pasted into the wrong variable: the value of an unrelated setting, which its own
# field's error used to print back, whole.
PASTED = "LEAK-pasted.qz7x"
# Settings whose own validation can fail on a pasted value: an int, a Literal, a float, a
# `PositiveInt`, and the one with a validator of ours (the cookie name).
WRONG_PLACE = (
    "STRIPE_TRIAL_DAYS",
    "LOG_LEVEL",
    "ENVIRONMENT",
    "SESSION_COOKIE_NAME",
    "DATABASE_POOL_SIZE",
    "LOGIN_LIMIT_PER_EMAIL",
    "REDIS_SOCKET_TIMEOUT_SECONDS",
)


def _sentinel(name: str) -> str:
    """A marker no other setting's value contains."""
    return f"LEAK{list(Settings.model_fields).index(name):02d}-qz7x"


def _value(name: str) -> str:
    """The sentinel in the shape its setting has, ending in the sentinel wherever that allows."""
    mark = _sentinel(name)
    if name == "database_url":
        return f"postgresql+asyncpg://u:{mark}@h/d"
    if name == "redis_url":
        return f"redis://:{mark}@h/0"
    if name in ADDRESS_SETTINGS:
        return f"{mark}@s.example"
    return mark


def _all_set() -> dict[str, str]:
    """Every sensitive setting at once, each its own sentinel (the app secret is no placeholder)."""
    return {name.upper(): _value(name) for name in SENSITIVE_SETTINGS}


def _start(
    tmp_path: Path, env: dict[str, str], code: str = START_SETTINGS, argv: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Start `code` (or `argv`) in a fresh interpreter, from an empty directory, with ONLY `env`.

    Nothing of the developer's shell is inherited but `PATH`, so a key exported there can neither
    be printed by the start nor change how it ends.
    """
    full = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(SRC_DIR), **env}
    return subprocess.run(
        argv or [sys.executable, "-c", code],
        env=full, cwd=tmp_path, capture_output=True, text=True, timeout=90, check=False,
    )


def _assert_names_and_hides(
    done: subprocess.CompletedProcess[str], *, variable: str, sentinels: list[str]
) -> None:
    """The start failed, said which variable is wrong, and printed none of the values."""
    everything = done.stdout + done.stderr
    assert done.returncode != 0, "the start was expected to fail: " + everything[-1500:]
    assert variable.lower() in everything.lower(), (variable, everything[-1500:])
    # Case-folded: a validator may upper-case a value before it refuses it (`LOG_LEVEL`).
    leaked = [mark for mark in sentinels if mark.lower() in everything.lower()]
    assert not leaked, ("printed a value", leaked, everything[-1500:])
    # The marker of every value pydantic echoes, whatever it was cut to.
    assert "input_value" not in everything, everything[-1500:]


class TestTheTestsBelowCanTell:
    def test_every_secret_setting_is_in_it(self) -> None:
        """An empty parametrisation is skipped, not failed: so the list is held to its members."""
        assert {
            "app_secret", "serpapi_key", "resend_api_key", "anthropic_api_key", "openai_api_key",
            "perplexity_api_key", "google_ai_api_key", "stripe_secret_key",
            "stripe_webhook_secret", "google_oauth_client_secret",
        } <= set(SECRET_SETTINGS)
        assert set(URL_SETTINGS) | set(ADDRESS_SETTINGS) <= set(Settings.model_fields)

    def test_the_sentinels_are_distinct_and_close_enough_to_the_end_to_be_printed_whole(
        self,
    ) -> None:
        marks = [_sentinel(name) for name in SENSITIVE_SETTINGS]
        assert len(set(marks)) == len(marks)
        for name in SENSITIVE_SETTINGS:
            value = _value(name)
            # Of an input that ends in this value pydantic shows the last 22 characters of it.
            assert len(value) - value.index(_sentinel(name)) <= 22, name

    def test_with_the_flag_off_the_same_start_prints_the_tail_of_a_secret(
        self, tmp_path: Path
    ) -> None:
        """The hazard, shown, so that the tests below cannot pass for want of a failing start:
        the settings with the one flag turned off print the webhook secret's tail and the input.
        (As they are, the same start prints neither: the `stripe_webhook_secret` case below.)"""
        env = {
            "ENVIRONMENT": "production",
            "STRIPE_WEBHOOK_SECRET": _value("stripe_webhook_secret"),
        }
        unhidden = (
            "from pydantic_settings import SettingsConfigDict\n"
            "from avp_api.config import Settings\n"
            "class Unhidden(Settings):\n"
            "    model_config = SettingsConfigDict(hide_input_in_errors=False)\n"
            "Unhidden()\n"
        )

        shown = _start(tmp_path, env, unhidden)

        assert shown.returncode != 0
        assert _sentinel("stripe_webhook_secret") in shown.stderr
        assert "input_value" in shown.stderr


class TestAFailedStartPrintsNoValue:
    """Each case is one refused start. It names the variable that is wrong and prints no value."""

    @pytest.mark.parametrize("setting", SENSITIVE_SETTINGS)
    def test_a_refused_deployed_start_prints_no_setting(
        self, tmp_path: Path, setting: str
    ) -> None:
        """The model validator: `ENVIRONMENT=production` with the app secret left a placeholder,
        and one sensitive setting set, which is then the last the input holds."""
        env = {"ENVIRONMENT": "production", setting.upper(): _value(setting)}
        if setting == "app_secret":
            # The refusal is the secret itself, still a placeholder: it must not be printed back.
            env["APP_SECRET"] = f"dev-only-{_value(setting)}"

        done = _start(tmp_path, env)

        _assert_names_and_hides(done, variable="APP_SECRET", sentinels=[_sentinel(setting)])

    def test_a_refused_wildcard_origin_prints_no_setting(self, tmp_path: Path) -> None:
        env = {"ENVIRONMENT": "production", "CORS_ALLOW_ORIGINS": "*", **_all_set()}

        done = _start(tmp_path, env)

        _assert_names_and_hides(
            done,
            variable="CORS_ALLOW_ORIGINS",
            sentinels=[_sentinel(name) for name in SENSITIVE_SETTINGS],
        )

    @pytest.mark.parametrize("variable", WRONG_PLACE)
    def test_a_value_pasted_into_the_wrong_setting_is_not_printed_back(
        self, tmp_path: Path, variable: str
    ) -> None:
        """A field validator: its error printed the value it refused, up to 50 characters."""
        env = {"ENVIRONMENT": "production", **_all_set(), variable: PASTED}

        done = _start(tmp_path, env)

        _assert_names_and_hides(
            done,
            variable=variable,
            sentinels=[_sentinel(name) for name in SENSITIVE_SETTINGS] + [PASTED],
        )

    @pytest.mark.parametrize(
        "bad",
        [PASTED, f"Name <{PASTED}@s.example>", f"{PASTED}@s.example, other@s.example"],
        ids=["a pasted key", "a name and an address", "two addresses"],
    )
    def test_a_bad_feedback_address_names_the_variable_and_not_the_value(
        self, tmp_path: Path, bad: str
    ) -> None:
        env = {"ENVIRONMENT": "production", **_all_set(), "FEEDBACK_EMAIL": bad}
        others = [_sentinel(name) for name in SENSITIVE_SETTINGS if name != "feedback_email"]

        done = _start(tmp_path, env)

        _assert_names_and_hides(done, variable="FEEDBACK_EMAIL", sentinels=[*others, PASTED])

    def test_a_pasted_value_fails_the_same_way_when_the_app_is_imported(
        self, tmp_path: Path
    ) -> None:
        """`app = create_app()` runs as `avp_api.main` is imported, which is what uvicorn does."""
        env = {"ENVIRONMENT": "production", **_all_set(), "STRIPE_TRIAL_DAYS": PASTED}

        done = _start(tmp_path, env, START_THE_APP)

        _assert_names_and_hides(
            done,
            variable="STRIPE_TRIAL_DAYS",
            sentinels=[_sentinel(name) for name in SENSITIVE_SETTINGS] + [PASTED],
        )

    def test_the_real_start_through_uvicorn_prints_no_value(self, tmp_path: Path) -> None:
        """The container's own command, `uvicorn avp_api.main:app`: the traceback it ends in."""
        env = {"ENVIRONMENT": "production", "CORS_ALLOW_ORIGINS": "*", **_all_set()}

        done = _start(tmp_path, env, argv=UVICORN_START)

        _assert_names_and_hides(
            done,
            variable="CORS_ALLOW_ORIGINS",
            sentinels=[_sentinel(name) for name in SENSITIVE_SETTINGS],
        )


class TestSettingsIsTheOnlySettingsClass:
    def test_it_is_the_only_settings_class_and_it_hides_its_input(self) -> None:
        """If a second `BaseSettings` is ever added it prints its input unless it says not to:
        this fails until that class hides its input too, and is told to name it here."""
        found: list[str] = []
        for path in sorted(SRC_DIR.joinpath("avp_api").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ClassDef) and any(
                    ast.unparse(base) in ("BaseSettings", "Settings") for base in node.bases
                ):
                    found.append(f"{path.name}:{node.name}")

        assert found == ["config.py:Settings"], found
        assert Settings.model_config.get("hide_input_in_errors") is True


class TestTheSettingsObjectKeepsItsCredentialsOutOfItsRepr:
    """`repr()` of a `SecretStr` is masked and a plain `str` is printed whole: the database and
    Redis URLs carry `user:password@host` on Render. Nothing prints the object today."""

    @staticmethod
    def _settings() -> Settings:
        names = (*SECRET_SETTINGS, *URL_SETTINGS)
        return Settings(_env_file=None, **{name: _value(name) for name in names})

    def test_neither_repr_nor_str_prints_a_credential(self) -> None:
        settings = self._settings()

        for text in (repr(settings), str(settings)):
            printed = [n for n in (*SECRET_SETTINGS, *URL_SETTINGS) if _sentinel(n) in text]
            assert not printed, printed

    def test_the_two_urls_are_left_out_of_it_by_name(self) -> None:
        for name in URL_SETTINGS:
            assert Settings.model_fields[name].repr is False, name

    def test_the_code_that_connects_still_has_them(self) -> None:
        settings = self._settings()

        assert settings.database_url == _value("database_url")
        assert settings.redis_url == _value("redis_url")
        assert _sentinel("database_url") in settings.sync_database_url()


# ---------------------------------------------------------------------------
# pydantic-settings writes every source it reads when told to — 2026-10-06
# ---------------------------------------------------------------------------


class TestPydanticSettingsPrintsWhenToldTo:
    """`PYDANTIC_SETTINGS_DEBUG=1` makes pydantic-settings write each source's raw values (the
    environment, a `.env` file) to its own logger at DEBUG, unmasked: provider keys, the Stripe key,
    the session secret, both URLs. The logger prints where something has set the root logger to
    DEBUG. The variable is read at each construction, and the entrypoint unsets it, below."""

    DUMP = (
        "import logging, sys\n"
        "logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)\n"
        "from avp_api.config import Settings\n"
        "Settings()\n"
    )

    @pytest.mark.parametrize("value", ["1", "true"])
    def test_with_it_set_and_the_logger_at_debug_every_source_is_written(
        self, tmp_path: Path, value: str
    ) -> None:
        """The hazard, shown, so the entrypoint tests below cannot pass for want of a line."""
        env = {"PYDANTIC_SETTINGS_DEBUG": value, "SERPAPI_KEY": _value("serpapi_key")}

        done = _start(tmp_path, env, self.DUMP)

        assert done.returncode == 0, done.stderr[-1500:]
        assert _sentinel("serpapi_key") in done.stderr

    def test_without_it_nothing_is_written(self, tmp_path: Path) -> None:
        done = _start(tmp_path, {"SERPAPI_KEY": _value("serpapi_key")}, self.DUMP)

        assert done.returncode == 0, done.stderr[-1500:]
        assert _sentinel("serpapi_key") not in done.stderr


class TestTheEntrypointSwitchesPydanticSettingsDebuggingOff:
    """The container's entrypoint unsets `PYDANTIC_SETTINGS_DEBUG` before it starts anything, as
    it does `STRIPE_LOG`, so a variable left on the host cannot make the settings write every
    source."""

    @pytest.mark.parametrize("value", ["1", "true"])
    def test_neither_alembic_nor_uvicorn_is_given_the_variable(
        self, tmp_path: Path, value: str
    ) -> None:
        seen = _run_entrypoint(ENTRYPOINT, tmp_path, PYDANTIC_SETTINGS_DEBUG=value)

        assert seen["alembic.pydantic_settings_debug"] == "<unset>"
        assert seen["uvicorn.pydantic_settings_debug"] == "<unset>"

    def test_without_the_line_the_harness_does_see_it(self, tmp_path: Path) -> None:
        """A control inside the test: the same script minus the unset, so the check above is
        known to be able to tell."""
        lines = ENTRYPOINT.read_text().splitlines(keepends=True)
        kept = [line for line in lines if line.strip() != "unset PYDANTIC_SETTINGS_DEBUG"]
        assert len(kept) == len(lines) - 1, "the script has no such `unset` line to remove"
        without = tmp_path / "docker-entrypoint.sh"
        without.write_text("".join(kept))
        without.chmod(0o755)

        seen = _run_entrypoint(without, tmp_path, PYDANTIC_SETTINGS_DEBUG="1")

        assert seen["alembic.pydantic_settings_debug"] == "1"
        assert seen["uvicorn.pydantic_settings_debug"] == "1"
