"""The deployment-time requirements infra/deploy/README.md lists, asserted.

Epic 18.2. The README has said since Epic 1 that the app "refuses to boot" in
staging/production on the dev placeholder secret and forces the secure cookie
flag. Nothing tested either; a doc's word is not a gate. These are the tests,
and the CI image job proves the refusal against the built container too.

A3 part 4 (hardening audit row 26) adds the second thing a deployed secret must be: at least 32
bytes. Those tests are the last section before the documentation ones.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from avp_api.config import APP_SECRET_MIN_BYTES, APP_SECRET_TOO_SHORT_MESSAGE, Settings

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


# ---------------------------------------------------------------------------
# APP_SECRET is at least 32 bytes where it is deployed — A3 part 4 (audit row 26)
# ---------------------------------------------------------------------------
# Until then the only thing refused was a value that STARTS `dev-only`, so a one-letter secret
# booted. The floor is a module constant, not a setting (whoever can set APP_SECRET could lower
# it), counted in bytes, and applies to staging and production only.


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("secret", ["", "k", "k" * 31], ids=["empty", "one byte", "31 bytes"])
def test_a_secret_under_32_bytes_refuses_to_boot(environment: str, secret: str) -> None:
    with pytest.raises(ValidationError, match="APP_SECRET is shorter than the 32 bytes"):
        _deployed(environment=environment, app_secret=secret)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_secret_of_exactly_32_bytes_boots(environment: str) -> None:
    secret = "k" * 32
    assert _deployed(environment=environment, app_secret=secret).app_secret.get_secret_value() == (
        secret
    )


def test_the_floor_counts_bytes_and_not_characters() -> None:
    # Sixteen characters of two bytes each: 32 bytes, so it boots. A count of characters would
    # refuse it.
    two_byte = "é" * 16
    assert (len(two_byte), len(two_byte.encode("utf-8"))) == (16, 32)
    assert _deployed(app_secret=two_byte).app_secret.get_secret_value() == two_byte
    # The same number of characters, one byte short.
    one_short = "é" * 15 + "k"
    assert (len(one_short), len(one_short.encode("utf-8"))) == (16, 31)
    with pytest.raises(ValidationError, match="APP_SECRET is shorter than the 32 bytes"):
        _deployed(app_secret=one_short)


def test_the_floor_is_32_bytes_and_the_message_says_so() -> None:
    assert APP_SECRET_MIN_BYTES == 32
    assert "32 bytes" in APP_SECRET_TOO_SHORT_MESSAGE


@pytest.mark.parametrize("secret", ["dev-only", "dev-only-x"])
def test_a_placeholder_keeps_its_own_message_when_it_is_also_short(secret: str) -> None:
    # CI's image job greps the placeholder's message in a built container's log.
    with pytest.raises(ValidationError, match="development placeholder"):
        _deployed(app_secret=secret)


def test_the_refusal_names_the_variable_the_minimum_and_how_to_make_one() -> None:
    with pytest.raises(ValidationError) as caught:
        _deployed(app_secret="k" * 20)
    (error,) = caught.value.errors()
    assert error["msg"] == f"Value error, {APP_SECRET_TOO_SHORT_MESSAGE}"
    assert "APP_SECRET" in error["msg"]
    assert "32 bytes" in error["msg"]
    assert 'python -c "import secrets; print(secrets.token_urlsafe(48))"' in error["msg"]


def test_the_refusal_prints_neither_the_value_nor_its_length() -> None:
    secret = "leak-me-" * 3 + "abc"  # 27 bytes, and 27 is a number the message does not contain
    with pytest.raises(ValidationError) as caught:
        _deployed(app_secret=secret)
    (error,) = caught.value.errors()
    printed = str(caught.value)
    assert "leak-me-" not in printed
    assert "input_value" not in printed
    assert str(len(secret)) not in error["msg"]


def test_a_secret_that_is_not_valid_text_is_counted_and_not_refused_by_the_codec() -> None:
    # The OS hands over an environment variable's invalid bytes as lone surrogates, and `Settings`
    # accepts them. The strict UTF-8 codec refuses such a value with an error that names the
    # positions of the invalid characters and, for a single one, quotes it; the floor must answer
    # with its own message.
    invalid = "\udcff" * 5
    with pytest.raises(ValidationError) as caught:
        _deployed(app_secret=invalid)
    (error,) = caught.value.errors()
    assert error["msg"] == f"Value error, {APP_SECRET_TOO_SHORT_MESSAGE}"
    assert "codec" not in str(caught.value)


@pytest.mark.parametrize("environment", ["local", "test"])
def test_a_short_secret_is_still_accepted_in_local_and_test(environment: str) -> None:
    for secret in ("", "short"):
        s = Settings(_env_file=None, environment=environment, database_url=DB,
                     redis_url="redis://x/0", app_secret=secret)
        assert s.app_secret.get_secret_value() == secret


def test_no_environment_variable_lowers_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not a setting: a deployed secret's floor cannot be set lower by whoever sets the secret.
    for name in ("APP_SECRET_MIN_BYTES", "APP_SECRET_MIN_LENGTH", "APP_SECRET_MINIMUM_BYTES"):
        monkeypatch.setenv(name, "1")
    # And no setting sits beside it that a floor could be read from.
    assert [field for field in Settings.model_fields if field.startswith("app_secret")] == [
        "app_secret"
    ]
    with pytest.raises(ValidationError, match="APP_SECRET is shorter than the 32 bytes"):
        _deployed(app_secret="k" * 10)


# ---------------------------------------------------------------------------
# the API's documentation is closed where it is deployed — 2026-09-29
# ---------------------------------------------------------------------------

DOCS_PATHS = ("/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json")


async def _statuses(settings: Settings) -> dict[str, int]:
    """What each documentation path answers. No datastore is touched by any of them."""
    from httpx import ASGITransport, AsyncClient

    from avp_api.logs import configure_logging
    from avp_api.main import create_app

    try:
        app = create_app(settings)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return {path: (await client.get(path)).status_code for path in DOCS_PATHS}
    finally:
        # `create_app` configures logging globally; give the suite its own back.
        configure_logging(Settings(_env_file=None, environment="test"))


@pytest.mark.parametrize("environment", ["staging", "production"])
async def test_the_docs_and_the_schema_are_not_served_when_deployed(environment: str) -> None:
    statuses = await _statuses(_deployed(environment=environment))
    assert statuses == dict.fromkeys(DOCS_PATHS, 404)


@pytest.mark.parametrize("environment", ["local", "test"])
async def test_development_still_serves_the_docs_and_the_schema(environment: str) -> None:
    settings = Settings(_env_file=None, environment=environment, database_url=DB,
                        redis_url="redis://x/0")
    statuses = await _statuses(settings)
    assert statuses["/api/v1/docs"] == 200
    assert statuses["/api/v1/openapi.json"] == 200
    # ReDoc has never been served, in any environment.
    assert statuses["/api/v1/redoc"] == 404
