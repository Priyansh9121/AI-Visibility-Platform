"""Logging, configured once at startup — 2026-09-29.

Until this date structlog was never configured, so every DEBUG line printed in
production, and uvicorn's access log printed share-link tokens and Google
callback codes in full. `avp_api/logs.py` fixes both; these pin how.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
import structlog

from avp_api.config import Settings
from avp_api.logs import RedactSecretPaths, configure_logging, redact_path
from avp_api.main import create_app

SECRET = "a-real-shaped-secret-0123456789abcdef"


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, app_secret=SECRET, **overrides)


@pytest.fixture
def restore_logging(settings) -> Iterator[None]:  # noqa: ANN001
    """Put the suite's own configuration back after a test changes the global one."""
    yield
    configure_logging(settings)


class TestTheLevel:
    @pytest.mark.parametrize(
        ("environment", "level"),
        [("local", "DEBUG"), ("test", "DEBUG"), ("staging", "INFO"), ("production", "INFO")],
    )
    def test_is_decided_by_the_environment_when_unset(self, environment: str, level: str) -> None:
        settings = _settings(environment=environment)
        assert logging.getLevelName(settings.effective_log_level) == level

    @pytest.mark.parametrize(
        ("environment", "log_level", "level"),
        [
            ("production", "debug", "DEBUG"),
            ("local", "WARNING", "WARNING"),
            ("staging", "", "INFO"),
        ],
    )
    def test_log_level_wins_when_set_and_blank_is_unset(
        self, environment: str, log_level: str, level: str
    ) -> None:
        settings = _settings(environment=environment, log_level=log_level)
        assert logging.getLevelName(settings.effective_log_level) == level

    def test_a_misspelt_level_fails_at_boot_not_on_the_first_log_line(self) -> None:
        with pytest.raises(ValueError):
            _settings(log_level="verbose")

    def test_production_does_not_print_the_model_reasoning(self, restore_logging) -> None:  # noqa: ANN001
        """The two DEBUG lines that carry model-written reasoning about client sites."""
        configure_logging(_settings(environment="production"))
        with structlog.testing.capture_logs() as events:
            structlog.get_logger("avp_api.services.classify").debug(
                "classify.result", rationale="model prose about a client site"
            )
            structlog.get_logger("avp_api.services.fix_generator").debug(
                "fixes.reasoning", priority_reason="model prose"
            )
            structlog.get_logger("avp_api.services.classify").info("classify.done")
        assert [e["event"] for e in events] == ["classify.done"]

    def test_development_still_prints_them(self, restore_logging) -> None:  # noqa: ANN001
        configure_logging(_settings(environment="local"))
        with structlog.testing.capture_logs() as events:
            structlog.get_logger("avp_api.services.classify").debug("classify.result")
        assert [e["event"] for e in events] == ["classify.result"]


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped exactly as uvicorn's access logger makes one."""
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.9:51234", "GET", path, "1.1", 200), None,
    )


class TestTheAccessLog:
    @pytest.mark.parametrize(
        ("path", "logged"),
        [
            ("/api/v1/reports/Tk-abc_123XYZ", "/api/v1/reports/…"),
            ("/api/v1/reports/Tk-abc_123XYZ.pdf", "/api/v1/reports/….pdf"),
            ("/api/v1/reports/Tk-abc_123XYZ?download=1", "/api/v1/reports/…"),
            # The two body-token routes' names are not a licence to carry a token elsewhere.
            ("/api/v1/reports/shared?token=Tk-abc_123XYZ", "/api/v1/reports/…"),
            ("/api/v1/reports/shared/Tk-abc_123XYZ", "/api/v1/reports/…"),
            # uvicorn logs the unquoted path, so an encoded slash prints as a slash.
            ("/api/v1/reports/shared%2FTk-abc_123XYZ", "/api/v1/reports/…"),
            # A token that merely starts with "shared" is still a token.
            ("/api/v1/reports/sharedTk-abc_123XYZ", "/api/v1/reports/…"),
            ("/api/v1/reports/shared.pdf", "/api/v1/reports/….pdf"),
            (
                "/api/v1/auth/google/callback?state=S3cr3t&code=4/0Ab-code",
                "/api/v1/auth/google/callback",
            ),
            (
                "/api/v1/auth/google/callback?error=access_denied&state=S3cr3t",
                "/api/v1/auth/google/callback",
            ),
            ("/api/v1/auth/google/pending?ticket=T1ck3t", "/api/v1/auth/google/pending"),
            ("/api/v1/auth/google/pending", "/api/v1/auth/google/pending"),
        ],
    )
    def test_redacts_every_secret_a_path_can_carry(self, path: str, logged: str) -> None:
        record = _access_record(path)
        assert RedactSecretPaths().filter(record) is True
        message = record.getMessage()
        assert f'"GET {logged} HTTP/1.1" 200' in message
        for secret in ("Tk-abc_123XYZ", "S3cr3t", "4/0Ab-code", "T1ck3t"):
            assert secret not in message
        # Who asked, and how it went, stay: that is what the access log is for.
        assert message.startswith("203.0.113.9:51234 - ")

    @pytest.mark.parametrize(
        "path", ["/api/v1/reports/shared", "/api/v1/reports/shared/pdf"]
    )
    def test_the_body_token_routes_are_not_rewritten(self, path: str) -> None:
        """They carry no token in the path, so there is nothing to hide."""
        record = _access_record(path)
        assert RedactSecretPaths().filter(record) is True
        assert f'"GET {path} HTTP/1.1" 200' in record.getMessage()

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/health",
            "/api/v1/scans/scan_01ABC/report",
            "/api/v1/scans/scan_01ABC/report.pdf",
            "/api/v1/clients?limit=25&cursor=abc",
            "/api/v1/auth/google/start",
        ],
    )
    def test_leaves_every_other_path_alone(self, path: str) -> None:
        assert redact_path(path) == path

    def test_passes_a_record_it_does_not_recognise_through_untouched(self) -> None:
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain", None, None)
        assert RedactSecretPaths().filter(record) is True
        assert record.getMessage() == "plain"

    def test_create_app_installs_it_on_uvicorns_access_logger_once(self, settings) -> None:  # noqa: ANN001
        access = logging.getLogger("uvicorn.access")
        create_app(settings)
        create_app(settings)
        assert sum(isinstance(f, RedactSecretPaths) for f in access.filters) == 1
