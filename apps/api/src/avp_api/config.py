"""Application settings and secrets management.

Epic 1 scope item: "Environment/secrets management for API keys (SerpApi, LLM
providers, engine APIs)".

Rules this module enforces:
  * Secrets are `SecretStr`, so they cannot be printed, logged, or serialised
    into an error response by accident. `repr()` of a SecretStr is '**********'.
  * Nothing has a usable default in production. `DATABASE_URL` and the app
    secret must be supplied; a missing one fails at boot, loudly, rather than
    silently connecting somewhere unexpected.
  * Provider keys are OPTIONAL at this stage and validated at point of use.
    Epic 1 has no scan pipeline, so requiring a SerpApi key to boot the API
    would block local development on credentials nobody needs yet.
  * `.env` is read for local development only and is git-ignored. The committed
    file is `.env.example`, which contains names and shapes but never values.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------
    environment: Environment = "local"
    log_level: str = "INFO"
    api_base_path: str = "/api/v1"

    # --- datastores --------------------------------------------------------
    # asyncpg driver; alembic rewrites this to psycopg2-style sync when needed.
    database_url: str = Field(
        default="postgresql+asyncpg://avp:avp@127.0.0.1:5432/avp",
        description="SQLAlchemy async URL for Postgres.",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 5
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")

    # --- auth --------------------------------------------------------------
    app_secret: SecretStr = Field(
        default=SecretStr("dev-only-insecure-secret-change-me"),
        description="Signing secret for CSRF tokens and invitation links.",
    )
    session_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    session_cookie_name: str = "avp_session"
    session_cookie_secure: bool = False  # forced True in staging/production
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_domain: str | None = None

    # Argon2id parameters. Defaults follow OWASP's recommended second option
    # (19 MiB, t=2, p=1), which resists GPU attack far better than bcrypt.
    argon2_time_cost: int = 2
    argon2_memory_cost_kib: int = 19456
    argon2_parallelism: int = 1

    # --- CORS --------------------------------------------------------------
    # The web app runs on a different origin in development, and session
    # cookies require an explicit origin allow-list (credentials + wildcard is
    # rejected by browsers).
    # `NoDecode` is load-bearing: pydantic-settings JSON-decodes complex types
    # (list, dict) read from a dotenv file BEFORE field validators run, so a
    # plain `CORS_ALLOW_ORIGINS=http://localhost:3000` raises a parse error
    # rather than reaching `_split_origins` below. NoDecode hands the raw string
    # to the validator instead.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- public share links (Epic 9.8) -------------------------------------
    # Where the WEB app is reachable from outside. The API returns a complete,
    # copyable URL from POST /scans/{id}/share rather than a bare token,
    # because the operator's next action is to paste it into an email — and a
    # token alone forces every caller to reinvent the same string, which is
    # how a frontend and a backend end up disagreeing about the path.
    #
    # Deliberately NOT derived from the request's Host or Origin header: those
    # are attacker-controlled, and a share link built from a spoofed Host is a
    # phishing URL carrying a real token. Configuration, not reflection.
    public_web_base_url: str = "http://localhost:3000"

    # --- password reset (Epic 9.13) ----------------------------------------
    # One hour. Long enough to walk to a different device and find the email,
    # short enough that a link left in an inbox is not a standing key. The
    # token is single-use as well as short-lived; neither alone is enough.
    password_reset_ttl_seconds: int = 3600
    # The From address. Unused while RESEND_API_KEY is unset.
    email_from: str = "no-reply@localhost"

    # --- seats -------------------------------------------------------------
    default_seat_limit: int = 3
    # Seven days, deliberately NOT the reset token's hour (Epic 9.14).
    #
    # They are different credentials with different exposure. A reset link is a
    # recovery key for an account that may be under attack right now, so the
    # window is as short as a person can act in. An invitation is an onboarding
    # link for someone who is not expecting it, may be away, and has nothing to
    # recover — and the seat it points at is already being paid for, so a link
    # that dies over a long weekend costs the agency a seat and an operator a
    # second round trip. Still bounded rather than open-ended: an invitation
    # that never expires is a standing key to a seat.
    invitation_ttl_seconds: int = 60 * 60 * 24 * 7

    # --- third-party providers (§5.1) --------------------------------------
    # All optional here; the scan pipeline validates presence at point of use
    # from Epic 2 onward.
    serpapi_key: SecretStr | None = None
    # Outbound email (Epic 9.13). OPTIONAL, exactly like every key above it:
    # unset is a supported mode, not a misconfiguration. With no key the reset
    # email is logged instead of sent, and the endpoint's response is unchanged
    # either way — see services/email.py.
    resend_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None
    google_ai_api_key: SecretStr | None = None

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Allows CORS_ALLOW_ORIGINS="http://a.com,http://b.com" in a .env file.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _harden_deployed_environments(self) -> Settings:
        if self.environment in ("staging", "production"):
            if self.app_secret.get_secret_value().startswith("dev-only"):
                raise ValueError(
                    "APP_SECRET is still the development placeholder. "
                    "Set a real secret before deploying."
                )
            # A session cookie sent over plaintext HTTP is a stolen session.
            object.__setattr__(self, "session_cookie_secure", True)
        return self

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")

    def sync_database_url(self) -> str:
        """Alembic runs synchronously; translate the async driver out."""
        return self.database_url.replace("+asyncpg", "").replace("postgresql+psycopg", "postgresql")

    def provider_key(self, name: str) -> str:
        """Fetch a required provider key, raising a clear error when unset.

        Used from Epic 2 onward. Kept here so that the failure message names the
        environment variable an operator has to set, rather than surfacing as a
        401 from a vendor.
        """
        value: SecretStr | None = getattr(self, name, None)
        if value is None:
            raise RuntimeError(
                f"{name.upper()} is not configured. Add it to your .env "
                f"(see .env.example) or the deployment secret store."
            )
        return value.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Cache is cleared in tests."""
    return Settings()
