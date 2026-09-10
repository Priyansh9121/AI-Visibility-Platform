"""The deployment-time requirements infra/deploy/README.md lists, asserted.

Epic 18.2. The README has said since Epic 1 that the app "refuses to boot" in
staging/production on the dev placeholder secret and forces the secure cookie
flag. Nothing tested either; a doc's word is not a gate. These are the tests,
and the CI image job proves the refusal against the built container too.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from avp_api.config import Settings

DB = "postgresql+asyncpg://avp:avp@db.internal:5432/avp"
REAL_SECRET = "a-real-secret-of-sufficient-length-set-by-the-host"


def _deployed(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "database_url": DB,
        "redis_url": "redis://cache.internal:6379/0",
        "app_secret": REAL_SECRET,
        "cors_allow_origins": ["https://app.example.com"],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_dev_placeholder_secret_refuses_to_boot(environment: str) -> None:
    with pytest.raises(ValidationError, match="development placeholder"):
        _deployed(environment=environment, app_secret="dev-only-insecure-secret-change-me")


def test_a_real_secret_boots() -> None:
    assert _deployed().app_secret.get_secret_value() == REAL_SECRET


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_secure_cookie_flag_is_forced_on(environment: str) -> None:
    # Even when the host's env explicitly says false.
    settings = _deployed(environment=environment, session_cookie_secure=False)
    assert settings.session_cookie_secure is True


def test_local_and_test_keep_the_flag_they_were_given() -> None:
    s = Settings(_env_file=None, environment="local", database_url=DB, redis_url="redis://x/0",
                 app_secret="dev-only-insecure-secret-change-me", session_cookie_secure=False)
    assert s.session_cookie_secure is False


@pytest.mark.parametrize("origins", [["*"], [" * "], [], ["https://app.example.com", "*"]])
def test_a_wildcard_or_empty_cors_list_refuses_to_boot(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOW_ORIGINS"):
        _deployed(cors_allow_origins=origins)


def test_a_comma_separated_string_of_real_origins_is_accepted() -> None:
    s = _deployed(cors_allow_origins="https://app.example.com,https://www.example.com")
    assert s.cors_allow_origins == ["https://app.example.com", "https://www.example.com"]
