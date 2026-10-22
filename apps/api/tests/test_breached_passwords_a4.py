"""The breached-password check (hardening A4, the founder's D10; launch checklist 3.130, item 1).

Where a person SETS a password (sign-up, a password change, a reset, an invitation accept) and
`BREACHED_PASSWORD_CHECK_ENABLED` is on, the password is looked up in the Pwned Passwords range
service by the first five characters of its SHA-1 hash, and refused if it is listed. The check
ships OFF: the service is an outside party that `/privacy` does not name yet.

What is held here, and how:

1. THE SETTINGS. Off by default, a blank is off, a non-boolean stops the start naming the variable
   and not its value; the timeout defaults to 2 seconds with a range; the template ships it off.
2. THE LOOKUP. Against a fake of the service (an `httpx.MockTransport`; the suite refuses real
   connections, `test_outbound_guard.py`) that answers like the real one, with unrelated entries
   and padding: a listed password is refused with the founder's sentence on its field, an unlisted
   one is accepted; the request carries the five-character prefix and nothing else, with
   `Add-Padding` and an AVP User-Agent that is not the crawler's; padding is ignored; off, there is
   no request and no client. The whole call is bounded by the timeout setting, and the answer by
   the size setting, counted after decompression (the fix round, security review L1).
3. IT FAILS OPEN. A 5xx, a 429, a redirect, a page that is not a list, an empty answer, an answer
   over the size cap, a refused connection, a read that times out and a call that outlasts the
   deadline all accept the password and log `auth.breach_check_unavailable` with the error's class
   and nothing else. The captured
   logs (structlog, stdout and the standard library) hold no password, hash, prefix, suffix or
   address, even when the error's own text names them.
4. EVERY DOOR, through its real route. Listed: a 422 on the field the form already shows for a short
   password, nothing written, the link or invitation still usable. Unlisted: accepted after exactly
   one request. Unavailable: accepted and logged. Off: no request at all.
5. SIGN-UP IS NO EXISTENCE ORACLE. A new address and a taken one make the same one request, and a
   listed password gets the same 422 for both; with the check off neither makes a request.
6. NEVER AT SIGN-IN. An account whose password is on the list signs in with no request (D10: no
   forced rotation), and meets the check at its next change.
7. IT COMES LAST. Nothing a caller could be refused for more cheaply makes an outside request.
8. NO TRANSACTION OR CONNECTION IS HELD ACROSS THE LOOKUP at the reset and invitation doors, the
   two that read a link before they ask (the fix round, security review I7).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import time
import types
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import (
    BREACHED_PASSWORD_MAX_ANSWER_BYTES_RANGE,
    BREACHED_PASSWORD_TIMEOUT_RANGE,
    Settings,
)
from avp_api.models import Agency, Invitation, PasswordResetToken, User
from avp_api.security import token_digest
from avp_api.services import breached_passwords, rate_limit
from avp_api.services import invitations as invitation_service
from avp_api.services import password_reset as reset_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
SENTENCE = "This password has appeared in a data breach. Choose a different one."
# On the fake service's list, and not on it.
LISTED = "hunter2-hunter2-hunter2"
ALSO_LISTED = "letmein-letmein-letmein"
FRESH = "an-entirely-different-passphrase"
EMAIL = "door@a4b.example"
OWNER = "owner@a4b.example"
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"

Attempt = Callable[[str], Awaitable[Response]]


def _sha1(password: str) -> str:
    return hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()


# --- a fake of the range service -----------------------------------------------------------------


class RangeService:
    """The range service as the tests meet it: it records what it was asked and answers like the
    real one, with the listed passwords' endings for the prefix asked about, other people's
    entries, and padding (a count of 0). `respond` replaces the answer for a failure."""

    def __init__(self) -> None:
        self.listed: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.clients: list[dict[str, Any]] = []
        self._responder: Callable[[httpx.Request], Any] | None = None

    def lists(self, *passwords: str, times: int = 1_000) -> None:
        for password in passwords:
            self.listed[password] = times

    def respond(self, responder: Callable[[httpx.Request], Any]) -> None:
        self._responder = responder

    def handler(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        if self._responder is not None:
            return self._responder(request)
        return self.answer(request)

    def answer(self, request: httpx.Request) -> Response:
        """What the real service would say: the same for every caller, built from the prefix."""
        prefix = request.url.path.rsplit("/", 1)[-1]

        def entries(label: str, count: int, *, padding: bool) -> list[str]:
            return [
                f"{hashlib.sha256(f'{prefix}{label}{i}'.encode()).hexdigest().upper()[:35]}:"
                f"{0 if padding else i + 1}"
                for i in range(count)
            ]

        real = [
            f"{_sha1(password)[5:]}:{times}"
            for password, times in self.listed.items()
            if _sha1(password)[:5] == prefix
        ]
        lines = entries("other", 40, padding=False) + real + entries("pad", 25, padding=True)
        return Response(200, text="\r\n".join(lines) + "\r\n")


@pytest.fixture
def range_service(monkeypatch: pytest.MonkeyPatch) -> RangeService:
    """Every client the check builds talks to the fake, and its construction is recorded."""
    service = RangeService()
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        service.clients.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(service.handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(breached_passwords, "httpx", types.SimpleNamespace(AsyncClient=factory))
    return service


@pytest.fixture
def check_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "breached_password_check_enabled", True)


def _on(settings: Settings, **more: Any) -> Settings:
    return settings.model_copy(update={"breached_password_check_enabled": True, **more})


def _raising(error: type[Exception]) -> Callable[[httpx.Request], Any]:
    """A failure whose own text names the URL (the prefix), the address and the password."""

    def raise_it(request: httpx.Request) -> Any:
        raise error(f"cannot reach {request.url} for {EMAIL} about {LISTED}")

    return raise_it


# --- 1. the settings -----------------------------------------------------------------------------


class TestTheSettings:
    def _settings(self, **given: Any) -> Settings:
        return Settings(
            _env_file=None,
            environment="local",
            database_url="postgresql+asyncpg://x@h/d",
            redis_url="redis://x/0",
            **given,
        )

    def test_the_shipped_defaults_are_off_and_two_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fields = Settings.model_fields
        assert fields["breached_password_check_enabled"].default is False
        assert fields["breached_password_timeout_seconds"].default == 2.0
        monkeypatch.delenv("BREACHED_PASSWORD_CHECK_ENABLED", raising=False)
        monkeypatch.delenv("BREACHED_PASSWORD_TIMEOUT_SECONDS", raising=False)
        built = self._settings()
        assert built.breached_password_check_enabled is False
        assert built.breached_password_timeout_seconds == 2.0

    def test_the_suites_own_settings_have_it_off(self, settings: Settings) -> None:
        assert settings.breached_password_check_enabled is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_is_off(self, monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
        monkeypatch.setenv("BREACHED_PASSWORD_CHECK_ENABLED", blank)
        assert self._settings().breached_password_check_enabled is False

    @pytest.mark.parametrize("raw", ["true", "1", " TRUE "])
    def test_true_turns_it_on(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("BREACHED_PASSWORD_CHECK_ENABLED", raw)
        assert self._settings().breached_password_check_enabled is True

    def test_a_value_that_is_not_a_boolean_stops_the_start_and_prints_no_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = "LEAK-breach-qz7x"
        monkeypatch.setenv("BREACHED_PASSWORD_CHECK_ENABLED", sentinel)
        with pytest.raises(ValidationError) as caught:
            self._settings()
        message = str(caught.value)
        assert "breached_password_check_enabled" in message.lower()
        assert sentinel not in message and "input_value" not in message

    @pytest.mark.parametrize("seconds", [BREACHED_PASSWORD_TIMEOUT_RANGE[0], 2, 10])
    def test_a_timeout_inside_the_range_is_taken(self, seconds: float) -> None:
        assert self._settings(
            breached_password_timeout_seconds=seconds
        ).breached_password_timeout_seconds == float(seconds)

    @pytest.mark.parametrize("seconds", [0.09, 0, -1, 10.01, 2000, float("inf"), float("nan")])
    def test_a_timeout_outside_the_range_stops_the_start_naming_the_setting(
        self, seconds: float
    ) -> None:
        with pytest.raises(ValidationError, match="breached_password_timeout_seconds"):
            self._settings(breached_password_timeout_seconds=seconds)

    def test_the_range_is_a_tenth_of_a_second_to_ten(self) -> None:
        assert BREACHED_PASSWORD_TIMEOUT_RANGE == (0.1, 10.0)

    def test_the_answer_size_defaults_to_one_mebibyte_with_a_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BREACHED_PASSWORD_MAX_ANSWER_BYTES", raising=False)
        assert Settings.model_fields["breached_password_max_answer_bytes"].default == 1024 * 1024
        assert self._settings().breached_password_max_answer_bytes == 1024 * 1024
        assert BREACHED_PASSWORD_MAX_ANSWER_BYTES_RANGE == (64 * 1024, 16 * 1024 * 1024)

    @pytest.mark.parametrize("size", [64 * 1024, 1024 * 1024, 16 * 1024 * 1024])
    def test_an_answer_size_inside_the_range_is_taken(self, size: int) -> None:
        built = self._settings(breached_password_max_answer_bytes=size)
        assert built.breached_password_max_answer_bytes == size

    @pytest.mark.parametrize("size", [0, 1, 64 * 1024 - 1, 16 * 1024 * 1024 + 1, -5])
    def test_an_answer_size_outside_the_range_stops_the_start_naming_the_setting(
        self, size: int
    ) -> None:
        with pytest.raises(ValidationError, match="breached_password_max_answer_bytes"):
            self._settings(breached_password_max_answer_bytes=size)

    def test_a_padded_answer_fits_under_the_lowest_size_with_room(self) -> None:
        """The lowest the setting takes must still hold a real answer, or a slip of units would
        make the check fail open on every password without a word: 1,000 entries (the most the
        service pads to) of 35 characters, a colon, nine digits and a line break."""
        biggest_real_answer = 1_000 * (35 + 1 + 9 + 2)
        assert biggest_real_answer < BREACHED_PASSWORD_MAX_ANSWER_BYTES_RANGE[0]

    def test_the_template_lists_every_spelling_the_setting_reads_as_a_boolean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The template's comment says which values are a boolean: it must be what the code does."""
        spellings = ["true", "false", "1", "0", "yes", "no", "on", "off", "t", "f", "y", "n"]
        said = " ".join(
            line.lstrip("# ").strip()
            for line in TEMPLATE.read_text().splitlines()
            if line.startswith("#")
        )
        listed = said.split("Any of ")[1].split(" is read as a boolean")[0]
        assert [w.strip() for w in listed.replace(" or ", ", ").split(",")] == spellings
        for spelling in spellings:
            for written in (spelling, spelling.upper()):
                monkeypatch.setenv("BREACHED_PASSWORD_CHECK_ENABLED", written)
                assert self._settings().breached_password_check_enabled is (
                    spelling in ("true", "1", "yes", "on", "t", "y")
                ), written
        monkeypatch.setenv("BREACHED_PASSWORD_CHECK_ENABLED", "maybe")
        with pytest.raises(ValidationError, match="breached_password_check_enabled"):
            self._settings()

    def test_the_template_documents_the_answer_size(self) -> None:
        assert "# BREACHED_PASSWORD_MAX_ANSWER_BYTES=1048576" in TEMPLATE.read_text()

    def test_the_template_ships_it_off_and_says_so(self) -> None:
        lines = TEMPLATE.read_text().splitlines()
        switch = "BREACHED_PASSWORD_CHECK_ENABLED=false"
        at = next(i for i, line in enumerate(lines) if line.strip() == switch)
        comment: list[str] = []
        for line in reversed(lines[:at]):
            if not line.startswith("#"):
                break
            comment.append(line.lower())
        said = " ".join(comment)
        assert "off" in said and "privacy" in said

    def test_a_claim_on_a_link_lasts_as_long_as_the_lookup_while_the_check_is_on(
        self, settings: Settings
    ) -> None:
        """The lookup runs inside the claim a reset or an invitation holds, before the hash waits
        for its slot, so the claim's lifetime counts it; off, it does not."""
        off = rate_limit.redeem_claim_ttl_ms(settings)
        longer = settings.model_copy(update={"breached_password_timeout_seconds": 9.0})
        assert rate_limit.redeem_claim_ttl_ms(longer) == off
        on = _on(settings, breached_password_timeout_seconds=2.5)
        assert rate_limit.redeem_claim_ttl_ms(on) == off + 2500


# --- 2. the lookup -------------------------------------------------------------------------------


class TestTheAnswerIsReadStrictly:
    def test_the_hash_is_the_sha1_of_the_utf8_split_after_five(self) -> None:
        assert breached_passwords.hash_parts("password") == (
            "5BAA6",
            "1E4C9B93F3F0682250B6CF8331B7EE68FD8",
        )
        prefix, suffix = breached_passwords.hash_parts("pässwörd-日本語")
        assert prefix + suffix == hashlib.sha1("pässwörd-日本語".encode()).hexdigest().upper()

    def test_a_listed_ending_with_a_count_is_found(self) -> None:
        suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"
        answer = f"{'0' * 35}:4\r\n{suffix}:9545824\r\n{'F' * 35}:1\r\n"
        assert breached_passwords.is_listed(answer, suffix) is True

    def test_padding_with_a_count_of_zero_is_not_a_listing(self) -> None:
        suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"
        assert breached_passwords.is_listed(f"{suffix}:0\r\n{'A' * 35}:2\r\n", suffix) is False

    def test_a_password_seen_once_is_listed(self) -> None:
        suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"
        assert breached_passwords.is_listed(f"{suffix}:1\n", suffix) is True

    def test_the_case_of_the_answer_does_not_matter(self) -> None:
        suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"
        assert breached_passwords.is_listed(f"{suffix.lower()}:12\n", suffix) is True

    def test_an_ending_that_is_not_there_is_not_listed(self) -> None:
        assert breached_passwords.is_listed(f"{'A' * 35}:7\r\n", "B" * 35) is False

    @pytest.mark.parametrize(
        "answer",
        [
            "",
            "\r\n\r\n",
            "<html>Bad gateway</html>",
            f"{'A' * 35}:3\r\nnot an entry\r\n{'B' * 35}:1",
            f"{'A' * 34}:3",
            f"{'A' * 36}:3",
            f"{'A' * 35}:three",
            f"{'A' * 35}:",
            f"{'G' * 35}:3",
        ],
        ids=["empty", "blank lines", "a page", "one bad line", "short", "long", "no count",
             "empty count", "not hex"],
    )
    def test_an_answer_that_is_not_a_list_is_not_read_as_not_listed(self, answer: str) -> None:
        with pytest.raises(breached_passwords.UnexpectedAnswerError):
            breached_passwords.is_listed(answer, "B" * 35)


class TestTheLookup:
    async def test_a_listed_password_is_refused_with_the_sentence_on_the_field(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        range_service.lists(LISTED)
        with pytest.raises(breached_passwords.BreachedPasswordError) as caught:
            await breached_passwords.refuse_if_breached(
                LISTED, _on(settings), field="newPassword"
            )
        problem = caught.value.to_dict("/x")
        assert problem["status"] == 422
        assert problem["type"] == "/problems/validation-failed"
        assert problem["detail"] == "One or more fields were invalid."
        assert problem["errors"] == [
            {"field": "newPassword", "message": SENTENCE, "type": "value_error"}
        ]
        assert breached_passwords.BREACHED_PASSWORD_MESSAGE == SENTENCE
        assert LISTED not in json.dumps(problem) and _sha1(LISTED) not in json.dumps(problem)

    async def test_an_unlisted_password_is_accepted(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        range_service.lists(LISTED)
        assert (
            await breached_passwords.refuse_if_breached(FRESH, _on(settings), field="password")
            is None
        )
        assert len(range_service.requests) == 1

    async def test_the_request_carries_the_prefix_and_nothing_else(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        range_service.lists(LISTED)
        with pytest.raises(breached_passwords.BreachedPasswordError):
            await breached_passwords.refuse_if_breached(LISTED, _on(settings), field="password")

        (request,) = range_service.requests
        prefix, suffix = _sha1(LISTED)[:5], _sha1(LISTED)[5:]
        assert len(prefix) == 5 and prefix == prefix.upper()
        assert request.method == "GET"
        assert (request.url.scheme, request.url.host) == ("https", "api.pwnedpasswords.com")
        assert request.url.path == f"/range/{prefix}"
        assert request.url.query == b"" and request.content == b""
        assert request.headers["add-padding"] == "true"
        agent = request.headers["user-agent"]
        assert agent.startswith("AVP-API/") and "AVPScanBot" not in agent
        for header in ("authorization", "cookie", "x-api-key", "referer"):
            assert header not in request.headers
        wire = str(request.url) + str(dict(request.headers)) + request.content.decode()
        assert suffix not in wire and _sha1(LISTED) not in wire and LISTED not in wire

    async def test_the_client_is_built_with_the_timeout_setting_and_the_headers(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        await breached_passwords.refuse_if_breached(
            FRESH, _on(settings, breached_password_timeout_seconds=0.75), field="password"
        )
        (built,) = range_service.clients
        assert built["timeout"] == 0.75
        assert built["headers"] == {
            "User-Agent": breached_passwords.USER_AGENT,
            "Add-Padding": "true",
        }
        assert breached_passwords.USER_AGENT.startswith("AVP-API/")

    async def test_off_there_is_no_request_and_no_client(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        range_service.lists(LISTED)
        assert settings.breached_password_check_enabled is False
        assert (
            await breached_passwords.refuse_if_breached(LISTED, settings, field="password")
            is None
        )
        assert range_service.requests == [] and range_service.clients == []

    @pytest.mark.parametrize(
        ("responder", "error"),
        [
            pytest.param(lambda r: Response(500, text="oops"), "HTTPStatusError", id="a 500"),
            pytest.param(lambda r: Response(503), "HTTPStatusError", id="a 503"),
            pytest.param(
                lambda r: Response(429, headers={"Retry-After": "3"}),
                "HTTPStatusError",
                id="a 429",
            ),
            pytest.param(
                lambda r: Response(302, headers={"Location": "/elsewhere"}),
                "HTTPStatusError",
                id="a redirect",
            ),
            pytest.param(
                lambda r: Response(200, text="<html>Service unavailable</html>"),
                "UnexpectedAnswerError",
                id="a page",
            ),
            pytest.param(
                lambda r: Response(200, text=""), "UnexpectedAnswerError", id="an empty answer"
            ),
            pytest.param(
                lambda r: Response(
                    200, text=f"{'A' * 35}:3\r\nnot-an-entry\r\n{'B' * 35}:1"
                ),
                "UnexpectedAnswerError",
                id="a list with a bad line",
            ),
            pytest.param(_raising(httpx.ConnectError), "ConnectError", id="a refused connection"),
            pytest.param(_raising(httpx.ReadTimeout), "ReadTimeout", id="a read that times out"),
        ],
    )
    async def test_any_failure_accepts_the_password_and_logs_the_class_only(
        self,
        responder: Callable[[httpx.Request], Any],
        error: str,
        range_service: RangeService,
        settings: Settings,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("DEBUG")
        range_service.lists(LISTED)
        range_service.respond(responder)
        with structlog.testing.capture_logs() as events:
            result = await breached_passwords.refuse_if_breached(
                LISTED, _on(settings), field="password"
            )
        assert result is None  # the password is accepted: it fails open
        assert events == [
            {"event": "auth.breach_check_unavailable", "log_level": "warning", "error": error}
        ]
        prefix, suffix = _sha1(LISTED)[:5], _sha1(LISTED)[5:]
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        for secret in (LISTED, _sha1(LISTED), prefix, suffix, EMAIL, "api.pwnedpasswords.com"):
            assert secret not in everything, secret

    async def test_a_call_that_outlasts_the_deadline_is_cut_off_and_accepted(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        """httpx bounds each step; the setting bounds the call, so a service that answers a
        little at a time is still cut off at the timeout."""

        async def hang(request: httpx.Request) -> Response:
            await asyncio.sleep(30)
            return Response(200, text="")

        range_service.respond(hang)
        started = time.monotonic()
        with structlog.testing.capture_logs() as events:
            await breached_passwords.refuse_if_breached(
                LISTED, _on(settings, breached_password_timeout_seconds=0.1), field="password"
            )
        assert time.monotonic() - started < 3
        assert [e["error"] for e in events] == ["TimeoutError"]


class TestTheAnswerIsBounded:
    """The security review's L1: the range answer is read as a stream and dropped, with its
    connection, once the bytes after decompression pass `BREACHED_PASSWORD_MAX_ANSWER_BYTES`."""

    CAP = BREACHED_PASSWORD_MAX_ANSWER_BYTES_RANGE[0]  # the least the setting takes: small bodies
    LINE = b"A" * 35 + b":1\r\n"  # 39 bytes of well-formed padding-free noise

    def _real_line(self) -> bytes:
        return f"{_sha1(LISTED)[5:]}:9999\r\n".encode()

    def _body(self, *, lines: int, real: str | None = None) -> bytes:
        """Well-formed lines, with the listed one at the `first` or `last` place, or not at all."""
        noise = self.LINE * lines
        if real == "first":
            return self._real_line() + noise
        if real == "last":
            return noise + self._real_line()
        return noise

    def _settings(self, settings: Settings, **more: Any) -> Settings:
        return _on(settings, breached_password_max_answer_bytes=self.CAP, **more)

    async def test_an_answer_just_under_the_cap_is_read_to_its_last_line(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        lines = (self.CAP - len(self._real_line())) // len(self.LINE)
        body = self._body(lines=lines, real="last")
        assert self.CAP - len(self.LINE) < len(body) <= self.CAP
        range_service.respond(lambda request: Response(200, content=body))

        # Listed on the very last line, so the whole of it was read.
        with pytest.raises(breached_passwords.BreachedPasswordError):
            await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )

    async def test_an_answer_just_over_the_cap_is_dropped_and_the_password_accepted(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        lines = (self.CAP - len(self._real_line())) // len(self.LINE) + 1
        body = self._body(lines=lines, real="first")
        assert self.CAP < len(body) <= self.CAP + len(self.LINE)
        range_service.respond(lambda request: Response(200, content=body))

        with structlog.testing.capture_logs() as events:
            # The listed line is the first one, and is not acted on: an answer past the cap is
            # not an answer.
            result = await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )

        assert result is None
        assert events == [
            {
                "event": "auth.breach_check_unavailable",
                "log_level": "warning",
                "error": "UnexpectedAnswerError",
            }
        ]

    async def test_a_compressed_answer_is_counted_after_it_is_decompressed(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        """Small on the wire and far over the cap once inflated: the size that counts is the
        decoded one. A compressed answer under the cap is read, so decoding itself works."""
        gzip_headers = {"Content-Encoding": "gzip"}

        inflated = self._body(lines=(self.CAP * 8) // len(self.LINE), real="first")
        packed = gzip.compress(inflated)
        assert len(packed) < self.CAP < len(inflated), "the body must be small only when packed"
        range_service.respond(
            lambda request: Response(200, content=packed, headers=gzip_headers)
        )
        with structlog.testing.capture_logs() as events:
            result = await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )
        assert result is None
        assert [e["error"] for e in events] == ["UnexpectedAnswerError"]

        small = gzip.compress(self._body(lines=200, real="last"))
        range_service.respond(lambda request: Response(200, content=small, headers=gzip_headers))
        with pytest.raises(breached_passwords.BreachedPasswordError):
            await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )

    async def test_the_read_stops_at_the_cap_and_goes_no_further(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        pulled = 0
        chunk = self.LINE * 400  # 15,600 bytes

        async def endless() -> Any:
            nonlocal pulled
            for _ in range(1_000):
                pulled += 1
                yield chunk

        range_service.respond(lambda request: Response(200, content=endless()))
        with structlog.testing.capture_logs() as events:
            await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )

        assert [e["error"] for e in events] == ["UnexpectedAnswerError"]
        # The cap is passed by the fifth chunk (4 x 15,600 is under 65,536 and 5 x is over): the
        # other 995 were never asked for.
        assert 1 <= pulled <= 6, pulled

    async def test_a_status_that_is_not_a_success_is_found_before_the_body_is_read(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        pulled = 0

        async def body() -> Any:
            nonlocal pulled
            pulled += 1
            yield self.LINE

        range_service.respond(lambda request: Response(503, content=body()))
        with structlog.testing.capture_logs() as events:
            await breached_passwords.refuse_if_breached(
                LISTED, self._settings(settings), field="password"
            )

        assert [e["error"] for e in events] == ["HTTPStatusError"]
        assert pulled == 0, "the body of a refusal was read"

    async def test_the_one_deadline_covers_a_body_that_stalls(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        async def stalls() -> Any:
            yield self.LINE
            await asyncio.sleep(30)
            yield self.LINE

        range_service.respond(lambda request: Response(200, content=stalls()))
        started = time.monotonic()
        with structlog.testing.capture_logs() as events:
            await breached_passwords.refuse_if_breached(
                LISTED,
                self._settings(settings, breached_password_timeout_seconds=0.1),
                field="password",
            )

        assert time.monotonic() - started < 3
        assert [e["error"] for e in events] == ["TimeoutError"]

    async def test_the_default_cap_reads_a_full_size_padded_answer_with_room_to_spare(
        self, range_service: RangeService, settings: Settings
    ) -> None:
        """1,000 entries of the largest shape (nine-digit counts): the most the service pads to."""
        entries = [f"{i:035X}:{999_999_999 - i}" for i in range(999)]
        entries.insert(500, f"{_sha1(LISTED)[5:]}:123456789")
        body = ("\r\n".join(entries) + "\r\n").encode()
        assert 45_000 < len(body) < settings.breached_password_max_answer_bytes // 10
        range_service.respond(lambda request: Response(200, content=body))

        with pytest.raises(breached_passwords.BreachedPasswordError):
            await breached_passwords.refuse_if_breached(LISTED, _on(settings), field="password")


# --- 3. every door, through its real route -------------------------------------------------------


def _factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _sign_up(client: AsyncClient, email: str, password: str = PASSWORD) -> Response:
    return await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "A4b Agency",
            "fullName": "Owner",
            "email": email,
            "password": password,
        },
    )


async def _owner(client: AsyncClient, email: str = EMAIL) -> dict[str, Any]:
    resp = await _sign_up(client, email)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _mint_reset(engine: AsyncEngine, email: str, raw: str, *, expired: bool = False) -> None:
    async with _factory(engine)() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        lifetime = timedelta(hours=-1) if expired else timedelta(hours=1)
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user.id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + lifetime,
            )
        )
        await s.commit()


async def _door_sign_up(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    async def attempt(password: str) -> Response:
        return await _sign_up(client, EMAIL, password)

    return attempt, EMAIL


async def _door_change(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    await _owner(client)

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": password},
        )

    return attempt, EMAIL


async def _door_reset(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    await _owner(client)
    raw = "a4b-reset-link-" + "r" * 24
    await _mint_reset(engine, EMAIL, raw)

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": password}
        )

    return attempt, EMAIL


async def _invite(client: AsyncClient, engine: AsyncEngine, raw: str) -> str:
    """An owner with a pending invitation for `EMAIL` whose link is `raw`; the agency's id."""
    agency_id = (await _owner(client, OWNER))["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": EMAIL, "role": "member"}
    )
    assert invited.status_code == 201, invited.text
    async with _factory(engine)() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == EMAIL,
                    Invitation.accepted_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(raw)
        await s.commit()
    return agency_id


async def _door_invitation(client: AsyncClient, engine: AsyncEngine) -> tuple[Attempt, str]:
    raw = "a4b-invitation-" + "i" * 24
    await _invite(client, engine, raw)

    async def attempt(password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": raw, "fullName": "Invited Person", "password": password},
        )

    return attempt, EMAIL


# door -> (how to set it up, the field its refusals name, the status of a success)
DOORS: dict[str, tuple[Callable[..., Awaitable[tuple[Attempt, str]]], str, int]] = {
    "sign-up": (_door_sign_up, "password", 201),
    "change": (_door_change, "newPassword", 200),
    "reset": (_door_reset, "newPassword", 200),
    "invitation": (_door_invitation, "password", 200),
}
DOOR_NAMES = list(DOORS)


async def _snapshot(engine: AsyncEngine) -> dict[str, Any]:
    """What a refusal must leave exactly as it was: the people and their hashes, the agencies,
    and whether each link and invitation has been used."""
    async with _factory(engine)() as s:
        users = (
            await s.execute(
                select(User.email, User.password_hash, User.status).order_by(User.email)
            )
        ).all()
        return {
            "users": [tuple(row) for row in users],
            "agencies": (await s.execute(select(func.count()).select_from(Agency))).scalar_one(),
            "links": list((await s.execute(select(PasswordResetToken.used_at))).scalars()),
            "invitations": list((await s.execute(select(Invitation.accepted_at))).scalars()),
        }


async def _status_of_sign_in(client: AsyncClient, email: str, password: str) -> int:
    await client.post(f"{BASE}/auth/logout")
    resp = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    return resp.status_code


@pytest.mark.parametrize("door", DOOR_NAMES)
class TestEveryDoor:
    async def test_a_listed_password_is_refused_on_the_field_and_nothing_is_written(
        self,
        door: str,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        setup, field, success = DOORS[door]
        range_service.lists(LISTED)
        attempt, email = await setup(client, engine)
        range_service.requests.clear()  # set-up signs an owner up, which asks too
        before = await _snapshot(engine)

        refused = await attempt(LISTED)

        assert refused.status_code == 422, refused.text
        assert refused.headers["content-type"].startswith("application/problem+json")
        doc = refused.json()
        assert doc["type"] == "/problems/validation-failed"
        assert doc["detail"] == "One or more fields were invalid."
        assert doc["errors"] == [{"field": field, "message": SENTENCE, "type": "value_error"}]
        assert LISTED not in refused.text and _sha1(LISTED) not in refused.text
        assert len(range_service.requests) == 1
        assert await _snapshot(engine) == before, "a refused password changed something"

        # The field is the one the form already shows a short password on.
        short = await attempt("a1b2c3d4e5f")
        assert short.status_code == 422
        assert short.json()["errors"][0]["field"] == field
        assert len(range_service.requests) == 1, "a password the schema refuses made a request"

        # The link, invitation or account is still good: another password goes through.
        assert (await attempt(FRESH)).status_code == success
        assert len(range_service.requests) == 2
        assert await _status_of_sign_in(client, email, FRESH) == 200

    async def test_an_unlisted_password_is_accepted_after_exactly_one_request(
        self,
        door: str,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        setup, _field, success = DOORS[door]
        range_service.lists(LISTED)
        attempt, _email = await setup(client, engine)
        range_service.requests.clear()  # set-up signs an owner up, which asks too
        assert (await attempt(FRESH)).status_code == success
        (request,) = range_service.requests
        assert request.url.path == f"/range/{_sha1(FRESH)[:5]}"

    async def test_a_failed_lookup_accepts_the_password_and_logs_it_once(
        self,
        door: str,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        setup, _field, success = DOORS[door]
        range_service.lists(LISTED)
        attempt, email = await setup(client, engine)
        range_service.respond(lambda request: Response(503))

        with structlog.testing.capture_logs() as events:
            accepted = await attempt(LISTED)

        assert accepted.status_code == success, accepted.text
        unavailable = [e for e in events if e["event"] == "auth.breach_check_unavailable"]
        assert unavailable == [
            {"event": "auth.breach_check_unavailable", "log_level": "warning",
             "error": "HTTPStatusError"}
        ]
        assert await _status_of_sign_in(client, email, LISTED) == 200

    async def test_with_the_check_off_there_is_no_request_at_all(
        self,
        door: str,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        settings: Settings,
    ) -> None:
        setup, _field, success = DOORS[door]
        range_service.lists(LISTED)
        assert settings.breached_password_check_enabled is False
        attempt, _email = await setup(client, engine)

        assert (await attempt(LISTED)).status_code == success
        assert range_service.requests == [] and range_service.clients == []


# --- 4. sign-up is no existence oracle -----------------------------------------------------------


class TestSignUpIsNoExistenceOracle:
    async def test_a_new_address_and_a_taken_one_make_the_same_one_request(
        self,
        client: AsyncClient,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        seen: list[tuple[int, list[httpx.Request]]] = []
        for email in ("first@oracle.example", "first@oracle.example", "second@oracle.example"):
            range_service.requests.clear()
            resp = await _sign_up(client, email, FRESH)
            seen.append((resp.status_code, list(range_service.requests)))

        assert [status for status, _ in seen] == [201, 409, 201]
        for _status, requests in seen:
            assert len(requests) == 1
        shapes = {
            (str(r.url), tuple(sorted(r.headers.items())), r.content)
            for _status, requests in seen
            for r in requests
            # `host`, `user-agent` and the rest are equal too; only the address differs, and it is
            # not in a request at all.
        }
        assert len(shapes) == 1, "a new and a taken address asked different things"

    async def test_a_listed_password_gets_the_same_refusal_for_a_taken_address(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        range_service.lists(LISTED)
        assert (await _sign_up(client, "taken@oracle.example", FRESH)).status_code == 201
        range_service.requests.clear()
        before = await _snapshot(engine)

        taken = await _sign_up(client, "taken@oracle.example", LISTED)
        new = await _sign_up(client, "new@oracle.example", LISTED)

        assert (taken.status_code, new.status_code) == (422, 422)
        assert taken.content == new.content, "the refusal depends on the address"
        assert new.json()["errors"] == [
            {"field": "password", "message": SENTENCE, "type": "value_error"}
        ]
        assert len(range_service.requests) == 2
        assert await _snapshot(engine) == before

    async def test_with_the_check_off_neither_makes_a_request(
        self, client: AsyncClient, range_service: RangeService
    ) -> None:
        assert (await _sign_up(client, "first@oracle.example", FRESH)).status_code == 201
        assert (await _sign_up(client, "first@oracle.example", FRESH)).status_code == 409
        assert (await _sign_up(client, "second@oracle.example", FRESH)).status_code == 201
        assert range_service.requests == [] and range_service.clients == []


# --- 5. never at sign-in -------------------------------------------------------------------------


class TestSignInNeverAsks:
    async def test_a_listed_password_still_signs_in_and_meets_the_check_at_its_next_change(
        self,
        client: AsyncClient,
        range_service: RangeService,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        range_service.lists(LISTED, ALSO_LISTED)
        # Made while the check was off, with a password that is on the list.
        assert (await _sign_up(client, EMAIL, LISTED)).status_code == 201
        assert range_service.requests == []
        monkeypatch.setattr(settings, "breached_password_check_enabled", True)

        # Signing in, with the right password and with a wrong one, asks nothing.
        assert await _status_of_sign_in(client, EMAIL, LISTED) == 200
        assert await _status_of_sign_in(client, EMAIL, "not-the-password-at-all") == 401
        # Nor does the rewrite of an older hash, which hashes the password again.
        monkeypatch.setattr(settings, "argon2_time_cost", 2)
        monkeypatch.setattr(settings, "argon2_memory_cost_kib", 16)
        assert await _status_of_sign_in(client, EMAIL, LISTED) == 200
        assert range_service.requests == [] and range_service.clients == []

        # The rule applies on the next change: a listed password is refused, an unlisted one is not.
        change = {"currentPassword": LISTED}
        refused = await client.post(
            f"{BASE}/auth/change-password", json={**change, "newPassword": ALSO_LISTED}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["errors"][0]["message"] == SENTENCE
        accepted = await client.post(
            f"{BASE}/auth/change-password", json={**change, "newPassword": FRESH}
        )
        assert accepted.status_code == 200, accepted.text
        assert len(range_service.requests) == 2


# --- 6. it comes last ----------------------------------------------------------------------------


class TestItComesLast:
    async def test_a_wrong_current_password_and_an_unchanged_one_make_no_request(
        self, client: AsyncClient, range_service: RangeService, check_on: None
    ) -> None:
        await _owner(client)
        range_service.requests.clear()  # the owner's own sign-up asked
        range_service.lists(PASSWORD, LISTED)

        wrong = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": "not-the-current-one-at-all", "newPassword": LISTED},
        )
        assert wrong.status_code == 401
        same = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": PASSWORD},
        )
        assert same.status_code == 422
        assert same.json()["errors"][0]["message"] == (
            "That is already your password. Choose a different one."
        )
        assert range_service.requests == []

    async def test_a_password_the_schema_refuses_makes_no_request(
        self, client: AsyncClient, range_service: RangeService, check_on: None
    ) -> None:
        range_service.lists("a1b2c3d4e5f")
        short = await _sign_up(client, EMAIL, "a1b2c3d4e5f")
        repetitive = await _sign_up(client, EMAIL, "aaaaaaaaaaaa")
        assert (short.status_code, repetitive.status_code) == (422, 422)
        assert range_service.requests == []

    async def test_a_reset_link_that_is_not_good_makes_no_request(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        await _owner(client)
        range_service.requests.clear()  # the owner's own sign-up asked
        range_service.lists(LISTED)
        await _mint_reset(engine, EMAIL, "a4b-expired-link-" + "e" * 24, expired=True)
        await _mint_reset(engine, EMAIL, "a4b-good-link-" + "g" * 24)

        for raw in ("a4b-unknown-link-" + "u" * 24, "a4b-expired-link-" + "e" * 24):
            refused = await client.post(
                f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": LISTED}
            )
            assert refused.status_code == 400, refused.text
        assert range_service.requests == []

        # Used once, it is refused again without a request.
        good = "a4b-good-link-" + "g" * 24
        used = await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": good, "newPassword": FRESH}
        )
        assert used.status_code == 200, used.text
        assert len(range_service.requests) == 1
        again = await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": good, "newPassword": LISTED}
        )
        assert again.status_code == 400
        assert len(range_service.requests) == 1

    async def test_an_invitation_that_is_not_good_makes_no_request(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        raw = "a4b-invitation-" + "i" * 24
        await _invite(client, engine, raw)
        range_service.requests.clear()  # the owner's own sign-up asked
        range_service.lists(LISTED)

        unknown = await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": "a4b-unknown-" + "u" * 24, "fullName": "Nobody", "password": LISTED},
        )
        assert unknown.status_code == 400, unknown.text
        assert range_service.requests == []

        accepted = await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": raw, "fullName": "Invited Person", "password": FRESH},
        )
        assert accepted.status_code == 200, accepted.text
        again = await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": raw, "fullName": "Invited Person", "password": LISTED},
        )
        assert again.status_code == 400
        assert len(range_service.requests) == 1


# --- 6b. no transaction is held across the lookup ------------------------------------------------


class TestNoTransactionIsHeldAcrossTheLookup:
    """The security review's I7. A reset and an invitation accept look at the link first, which
    begins a transaction and checks out a connection; they end it (`session.commit()`) BEFORE the
    wait for the outside service, so a slow service holds no pooled connection. Moving the call
    above that commit would hold the first look's transaction for as long as the lookup takes
    (up to the timeout), and no other test would notice. The two other doors ask before they have
    read anything, or after a commit of their own, and "it comes last" catches a move there.

    The service layer is called directly with a session of the test's own, because the route's
    session is not the test's to look at. The fake service notes, at the moment it is asked,
    whether that session is in a transaction and how many connections the pool has out."""

    @staticmethod
    def _observe(
        range_service: RangeService, session: Any, engine: AsyncEngine
    ) -> list[tuple[bool, int]]:
        seen: list[tuple[bool, int]] = []

        def asked(request: httpx.Request) -> Response:
            seen.append((session.in_transaction(), engine.pool.checkedout()))  # type: ignore[attr-defined]
            return range_service.answer(request)

        range_service.respond(asked)
        return seen

    async def test_confirm_reset_asks_with_its_transaction_ended(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        await _owner(client)
        raw = "a4b-transaction-reset-" + "r" * 24
        await _mint_reset(engine, EMAIL, raw)
        range_service.requests.clear()  # the owner's own sign-up asked

        async with _factory(engine)() as session:
            seen = self._observe(range_service, session, engine)
            user = await reset_service.confirm_reset(
                session,
                redis=redis_module.get_redis(settings),
                token=raw,
                new_password=FRESH,
                settings=settings,
            )
            await session.commit()

        assert user.email == EMAIL
        assert seen == [(False, 0)], "the lookup ran inside a transaction, or with a connection out"

    async def test_accept_invitation_asks_with_its_transaction_ended(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        range_service: RangeService,
        check_on: None,
    ) -> None:
        raw = "a4b-transaction-invitation-" + "i" * 24
        await _invite(client, engine, raw)
        range_service.requests.clear()  # the owner's own sign-up asked

        async with _factory(engine)() as session:
            seen = self._observe(range_service, session, engine)
            user = await invitation_service.accept_invitation(
                session,
                redis=redis_module.get_redis(settings),
                token=raw,
                full_name="Invited Person",
                password=FRESH,
                settings=settings,
            )
            await session.commit()

        assert user.email == EMAIL
        assert seen == [(False, 0)], "the lookup ran inside a transaction, or with a connection out"


# --- 7. what the logs hold -----------------------------------------------------------------------


class TestNothingSecretIsLogged:
    async def test_a_failed_lookup_through_the_app_logs_the_class_and_nothing_of_the_request(
        self,
        client: AsyncClient,
        range_service: RangeService,
        check_on: None,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("DEBUG")
        range_service.lists(LISTED)
        range_service.respond(_raising(httpx.ConnectError))

        with structlog.testing.capture_logs() as events:
            resp = await _sign_up(client, EMAIL, LISTED)

        assert resp.status_code == 201, resp.text
        assert [e for e in events if e["event"] == "auth.breach_check_unavailable"] == [
            {"event": "auth.breach_check_unavailable", "log_level": "warning",
             "error": "ConnectError"}
        ]
        prefix, suffix = _sha1(LISTED)[:5], _sha1(LISTED)[5:]
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        for secret in (LISTED, _sha1(LISTED), prefix, suffix, EMAIL, "pwnedpasswords"):
            assert secret not in everything, secret
        # httpx writes a request's whole URL at INFO, and the URL carries the prefix:
        # `logs.configure_logging` keeps it quiet, and this is the call that would be heard.
        assert "HTTP Request" not in caplog.text

    async def test_a_refusal_and_an_acceptance_log_nothing_of_the_request(
        self,
        client: AsyncClient,
        range_service: RangeService,
        check_on: None,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("DEBUG")
        range_service.lists(LISTED)

        with structlog.testing.capture_logs() as events:
            refused = await _sign_up(client, EMAIL, LISTED)
            accepted = await _sign_up(client, EMAIL, FRESH)

        assert (refused.status_code, accepted.status_code) == (422, 201)
        assert not [e for e in events if "breach" in e["event"]], events
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        for secret in (LISTED, FRESH, _sha1(LISTED), _sha1(LISTED)[:5], _sha1(FRESH)[:5], EMAIL):
            assert secret not in everything, secret
        assert "HTTP Request" not in caplog.text
