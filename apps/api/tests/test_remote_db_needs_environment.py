"""A remote database with no stated ENVIRONMENT is refused at start (checklist item F5a).

`environment` defaults to "local". A deploy that forgot ENVIRONMENT would otherwise run as local:
docs served, DEBUG logs, the dev APP_SECRET accepted, the cookie not forced secure, CORS at the
localhost default. Naming the environment, even `local`, is the operator saying so.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from avp_api.config import Settings

REMOTE_HOST = "db-prod-7.internal.example.net"
REMOTE = f"postgresql+asyncpg://avp:s3cretpw@{REMOTE_HOST}:5432/avp"
REAL_SECRET = "a-real-secret-of-sufficient-length-set-by-the-host"
MESSAGE = (
    "ENVIRONMENT is not set and DATABASE_URL points at a remote host. "
    "Set ENVIRONMENT=production (or staging), or ENVIRONMENT=local for a remote development "
    "database."
)


def _build(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    """Settings from the process environment only, like a deploy (no `.env`, no kwargs)."""
    for name in ("ENVIRONMENT", "DATABASE_URL", "APP_SECRET", "CORS_ALLOW_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_unset_environment_with_a_remote_host_is_refused_without_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError) as caught:
        _build(monkeypatch, {"DATABASE_URL": REMOTE})
    text = str(caught.value)
    assert MESSAGE in text
    for leak in (REMOTE_HOST, "s3cretpw", "postgresql", "5432"):
        assert leak not in text


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "[::1]", "db.localhost", "LOCALHOST"],
)
def test_unset_environment_with_a_local_host_is_fine(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    settings = _build(monkeypatch, {"DATABASE_URL": f"postgresql+asyncpg://avp@{host}:5432/avp"})
    assert settings.environment == "local"


@pytest.mark.parametrize(
    "query",
    [f"host={REMOTE_HOST}", "host=localhost,db.example.com", f"host=%2Fvar%2Frun,{REMOTE_HOST}"],
)
def test_a_remote_host_in_the_query_is_remote(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """libpq and asyncpg read `host=` from the query, so a URL with no host part can be remote."""
    with pytest.raises(ValidationError) as caught:
        _build(monkeypatch, {"DATABASE_URL": f"postgresql+asyncpg://avp:s3cretpw@/avp?{query}"})
    assert MESSAGE in str(caught.value)
    assert REMOTE_HOST not in str(caught.value)


@pytest.mark.parametrize("query", ["host=%2Fvar%2Frun%2Fpostgresql", "host=localhost", ""])
def test_a_socket_or_loopback_in_the_query_is_local(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    settings = _build(monkeypatch, {"DATABASE_URL": f"postgresql+asyncpg://avp@/avp?{query}"})
    assert settings.environment == "local"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://avp@/avp?dsn=postgresql://db-prod-7.internal.example.net/avp",
        "postgresql+asyncpg://avp@/avp?#&host=db-prod-7.internal.example.net",
    ],
)
def test_a_server_named_by_dsn_or_after_a_hash_is_remote(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """Read as the engine reads it: asyncpg takes a `dsn=`; SQLAlchemy keeps `#` in a query."""
    with pytest.raises(ValidationError) as caught:
        _build(monkeypatch, {"DATABASE_URL": url})
    assert MESSAGE in str(caught.value)
    assert REMOTE_HOST not in str(caught.value)


def test_a_url_with_no_host_is_remote_when_pghost_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", REMOTE_HOST)
    with pytest.raises(ValidationError, match="ENVIRONMENT is not set"):
        _build(monkeypatch, {"DATABASE_URL": "postgresql+asyncpg://avp@/avp"})
    monkeypatch.delenv("PGHOST")
    settings = _build(monkeypatch, {"DATABASE_URL": "postgresql+asyncpg://avp@/avp"})
    assert settings.environment == "local"


@pytest.mark.parametrize("host", ["db", "unused", "localhost.example.com", "10.0.0.5"])
def test_other_hosts_are_remote_including_single_label_ones(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    with pytest.raises(ValidationError, match="ENVIRONMENT is not set"):
        _build(monkeypatch, {"DATABASE_URL": f"postgresql+asyncpg://avp@{host}/avp"})


def test_the_default_database_url_with_no_environment_is_fine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _build(monkeypatch, {}).environment == "local"


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production"])
def test_an_explicit_environment_allows_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    settings = _build(
        monkeypatch,
        {
            "ENVIRONMENT": environment,
            "DATABASE_URL": REMOTE,
            "APP_SECRET": REAL_SECRET,
            "CORS_ALLOW_ORIGINS": "https://app.example.com",
        },
    )
    assert settings.environment == environment


def test_an_explicit_local_is_taken_from_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Proves `model_fields_set` sees an env-sourced value, not only a constructor argument.
    settings = _build(monkeypatch, {"ENVIRONMENT": "local", "DATABASE_URL": REMOTE})
    assert "environment" in settings.model_fields_set


def test_staging_and_production_still_run_their_hardening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="development placeholder"):
        _build(
            monkeypatch,
            {
                "ENVIRONMENT": "production",
                "DATABASE_URL": REMOTE,
                "CORS_ALLOW_ORIGINS": "https://app.example.com",
            },
        )
