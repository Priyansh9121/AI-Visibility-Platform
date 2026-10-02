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

import logging
import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Every setting that names or unlocks an outside provider. ONE rule for all of
# them, in `_blank_provider_setting_is_unset` below: a present-but-empty value
# is unset. Until 2026-09-29 only the three Google settings had it, and a blank
# `RESEND_API_KEY=` — exactly what `.env.example` ships — counted as a key: the
# recipient, the subject and the reset link went to Resend with an empty bearer
# token, to be refused only after they had been sent.
PROVIDER_SETTINGS: tuple[str, ...] = (
    "serpapi_key",
    "resend_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "perplexity_api_key",
    "google_ai_api_key",
    "stripe_secret_key",
    "stripe_price_id",
    "stripe_webhook_secret",
    "google_oauth_client_id",
    "google_oauth_client_secret",
    "google_oauth_redirect_url",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------
    environment: Environment = "local"
    # Unset by default, and then decided by `environment` — INFO in staging and
    # production, DEBUG in local and test (`effective_log_level`). Until
    # 2026-09-29 this read "INFO" and nothing read it at all: structlog was never
    # configured, so DEBUG lines — two of them carrying model-written reasoning
    # about client sites — printed in production. `logs.configure_logging`
    # applies it once, at startup.
    log_level: LogLevel | None = None
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
    # Bounds on every Redis call, so a slow or unreachable Redis fails the
    # request fast (a 503) instead of hanging it. Redis is same-region and
    # answers in about a millisecond; 2 s is generous for a Lua rate-limit
    # script or a session read, yet well inside a browser's patience. Nothing
    # on the API pool blocks on purpose (no BLPOP, XREAD BLOCK or pub/sub), so
    # a read timeout cannot break a legitimate wait.
    redis_connect_timeout_seconds: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    # --- auth --------------------------------------------------------------
    app_secret: SecretStr = Field(
        default=SecretStr("dev-only-insecure-secret-change-me"),
        description="Signing secret for CSRF tokens and invitation links.",
    )
    session_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    # The browser reaches this API through the web app's own origin (the web app
    # serves `/api/v1` itself), so the session cookie is first-party: always
    # SameSite=Lax, never with a Domain. A stale SESSION_COOKIE_SAMESITE or
    # SESSION_COOKIE_DOMAIN left in a deployed environment is ignored
    # (`extra="ignore"`), not an error.
    # This is the BASE name. The cookie is sent as `session_cookie_key`: with the
    # `__Host-` prefix when Secure, so only this exact host can set or read it
    # (the browser demands Secure, Path=/ and no Domain for that prefix). The
    # prefix is added by code and never configured, hence the validator below.
    session_cookie_name: str = "avp_session"
    session_cookie_secure: bool = False  # forced True in staging/production

    # Argon2id parameters. Defaults follow OWASP's recommended second option
    # (19 MiB, t=2, p=1), which resists GPU attack far better than bcrypt.
    argon2_time_cost: int = 2
    argon2_memory_cost_kib: int = 19456
    argon2_parallelism: int = 1

    # Rate limits on the three unauthenticated credential routes, per address
    # AND per source IP, as fixed windows in Redis (services/rate_limit.py).
    # Sign-up and reset-request count every attempt; login counts failures only
    # (a success gives its attempt back). The check runs before any Argon2,
    # database write or email. Per email stops one account being ground down
    # from many IPs; per IP stops one source walking many accounts. Limits and
    # windows must be at least 1.
    # Login: guessing passwords is the attack, so the per-email budget is small.
    login_limit_per_email: PositiveInt = 10
    login_limit_per_ip: PositiveInt = 30
    login_window_seconds: PositiveInt = 15 * 60
    # Change-password re-verifies the current password for a signed-in caller,
    # so it is the same guessing attack with a stolen session cookie. Same
    # budget as login, counted on its own keys; failures only.
    change_password_limit_per_email: PositiveInt = 10
    change_password_limit_per_ip: PositiveInt = 30
    change_password_window_seconds: PositiveInt = 15 * 60
    # Sign-up creates an agency, a user and (later) a scan allowance: the abuse
    # is minting free accounts, and probing which addresses are registered (409).
    signup_limit_per_email: PositiveInt = 5
    signup_limit_per_ip: PositiveInt = 10
    signup_window_seconds: PositiveInt = 60 * 60
    # Reset-request sends email (a cost, and a mailbox someone else owns) and
    # must trip identically for every address, so it stays enumeration-safe.
    reset_limit_per_email: PositiveInt = 3
    reset_limit_per_ip: PositiveInt = 10
    reset_window_seconds: PositiveInt = 60 * 60
    # Which request header carries the real client IP, for the per-IP bucket.
    # Empty (the default): use the connection's client host, which uvicorn's
    # --proxy-headers already sets from the leftmost X-Forwarded-For entry.
    # Render sits behind Cloudflare, which sets CF-Connecting-IP; that header is
    # trustworthy only if every request really arrives through Cloudflare. The
    # operator must confirm that before setting CLIENT_IP_HEADER=CF-Connecting-IP.
    # NEVER set this to X-Forwarded-For: its first entry is client-controlled.
    # When set, the header's whole value must be one IP (IPv6 is keyed on its
    # /64, IPv4-mapped on the IPv4); if the header is absent or unparseable the
    # request lands in one shared bucket, never in the connection's host.
    client_ip_header: str = ""

    # --- CORS --------------------------------------------------------------
    # The web app no longer calls this API cross-origin: it goes through its own
    # origin, so CORS is not what lets it in. The list now matters as the Origin
    # check's allow-list for writes (`origin_check.py`): the browser still sends
    # the web app's origin on a same-origin POST, and the proxy passes it
    # through. CORS itself stays as it was: an explicit list, because credentials
    # plus a wildcard is rejected by browsers.
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
    # token alone forces every caller to reinvent the same string (`/share#<token>`,
    # a fragment, never a path), which is
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

    # --- the scan allowance (2026-09-28) ------------------------------------
    # Sign-ins the one-free-then-forty-a-month gate does not apply to: the
    # platform operator, so the founder can scan without paying the platform.
    # A listed address is exempt only if that user ALSO has a linked Google
    # identity — see services/scan_allowance.is_exempt for why a bare email
    # is not enough. Per user, not per agency.
    #
    # EMPTY BY DEFAULT, deliberately. A personal address does not belong in
    # source, and an empty default means a fresh environment — a new
    # deployment, a restored database — exempts nobody until somebody decides
    # it should. The live value is `SCAN_ALLOWANCE_EXEMPT_EMAILS`, a
    # comma-separated list, set on the Render API service.
    scan_allowance_exempt_emails: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )

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
    # unset is a supported mode, not a misconfiguration — and today's
    # production state (2026-09-29: no verified sending domain yet). With no
    # key nothing is sent and NOTHING that carries a token is logged; the
    # reset and invitation flows say so instead (`email_configured`).
    resend_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None
    google_ai_api_key: SecretStr | None = None

    # --- billing (Epic 9.15) -----------------------------------------------
    # Optional here, exactly like every provider key above, and for the same
    # reason: an API that refuses to boot without a payment processor is an API
    # nobody can run locally to work on anything else. Each is validated at
    # point of use with an error naming the variable, via `provider_key` below.
    #
    # THESE ARE TEST-MODE KEYS AND THE CODE DOES NOT KNOW THE DIFFERENCE.
    # There is deliberately no `stripe_live_mode` flag and no branch anywhere
    # that behaves differently on an `sk_live_` key. Going live is a key swap
    # made by the founder when Stripe's business verification is done — if that
    # switch required a code change, the code would be the thing standing
    # between a decision and its effect, and it would get made under pressure.
    stripe_secret_key: SecretStr | None = None
    # NOT a secret, and deliberately not wrapped in one. A Price id identifies a
    # published product; it is the sort of thing that appears in client-side
    # code in most Stripe integrations. Marking it `SecretStr` would say
    # something untrue about it, and `test_env_template.py` reads this file to
    # decide what a credential looks like.
    stripe_price_id: str | None = None
    # Produced by `stripe listen`, or by the dashboard for a deployed endpoint.
    # Absent by default, which is a supported state — see routers/billing.py for
    # what the webhook does when a request arrives and this is unset. It does
    # NOT fall back to trusting the payload.
    stripe_webhook_secret: SecretStr | None = None

    # --- Google sign-in (Epic 20) ------------------------------------------
    # All three optional, like every provider above: with any of them unset
    # the button's target answers 503 `google-sign-in-not-configured` and the
    # password flow is untouched. They come from a Google Cloud Console OAuth
    # client the founder creates; nothing here can invent them.
    #
    # The client id is NOT a secret — it is sent to the browser in the
    # authorization URL — and is deliberately a plain `str`, for the reason
    # `stripe_price_id` gives. The client secret is.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    # The EXACT redirect URL registered in the Console, e.g.
    # http://localhost:8000/api/v1/auth/google/callback. Configuration, never
    # derived from the request's Host — the same argument `public_web_base_url`
    # makes, and sharper here: a redirect URL built from a spoofed Host is a
    # code sent to an attacker. It cannot be finalised for production until
    # the deploy host is chosen (build-log Epic 18.2's open decision).
    google_oauth_redirect_url: str | None = None
    # How long the state (between /start and /callback) and the sign-up ticket
    # (between /callback and /complete) live. Ten minutes is the time it takes
    # a person to pick an account and type an agency name, not a standing key.
    google_sign_in_ttl_seconds: int = 600

    @field_validator(*PROVIDER_SETTINGS, mode="before")
    @classmethod
    def _blank_provider_setting_is_unset(cls, value: object) -> object:
        # A blank is unset, for every provider setting alike. `cp .env.example
        # .env` leaves `GOOGLE_OAUTH_CLIENT_ID=` present and empty, and until
        # 2026-09-21 that counted as configured (the button sent a browser to
        # Google with an empty client id); until 2026-09-29 the same held for
        # every other key here, so a blank one still sent content to its
        # provider with an empty credential. One rule, applied once.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _log_level_is_a_level_name(cls, value: object) -> object:
        # `LOG_LEVEL=` is unset; `info` is `INFO`. Anything else fails at boot
        # through the Literal, rather than as a crash on the first log line.
        if isinstance(value, str):
            return value.strip().upper() or None
        return value

    @property
    def effective_log_level(self) -> int:
        """`LOG_LEVEL` if set; otherwise INFO when deployed, DEBUG when not."""
        if self.log_level is not None:
            return logging.getLevelNamesMapping()[self.log_level]
        return logging.INFO if self.environment in ("staging", "production") else logging.DEBUG

    @property
    def email_configured(self) -> bool:
        """Whether outbound email can be sent at all — a fact about the deployment.

        Global by construction: it never depends on an address, so reporting it
        cannot tell anybody whether an account exists.
        """
        return self.resend_api_key is not None

    @property
    def google_sign_in_configured(self) -> bool:
        return (
            self.google_oauth_client_id is not None
            and self.google_oauth_client_secret is not None
            and self.google_oauth_redirect_url is not None
        )

    @field_validator("session_cookie_name")
    @classmethod
    def _plain_cookie_name(cls, value: str) -> str:
        # A cookie-name token: nothing a Set-Cookie header would split on, and no
        # `__` start, because `__Host-` is added by `session_cookie_key`.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value) or value.startswith("__"):
            raise ValueError(
                "SESSION_COOKIE_NAME must be letters, digits, '_' or '-' and must not "
                "start with '__' (the __Host- prefix is added by the code)."
            )
        return value

    @property
    def session_cookie_key(self) -> str:
        """The name the cookie travels under. `__Host-` needs Secure, so local
        http development, where Secure is off, uses the bare name."""
        if self.session_cookie_secure:
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    @property
    def google_flow_cookie_key(self) -> str:
        """The cookie that binds a Google sign-in to the browser that started
        it. Named like the session cookie: `__Host-` when Secure."""
        if self.session_cookie_secure:
            return "__Host-avp_google_flow"
        return "avp_google_flow"

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Allows CORS_ALLOW_ORIGINS="http://a.com,http://b.com" in a .env file.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("scan_allowance_exempt_emails", mode="before")
    @classmethod
    def _split_and_normalise_emails(cls, value: object) -> object:
        # Comma-separated in the environment; lower-cased and stripped so the
        # match is the same normalisation `services/auth.normalise_email`
        # applies to `User.email` — an exemption must not fail on a capital.
        items = value.split(",") if isinstance(value, str) else list(value or [])  # type: ignore[call-overload]
        return [str(item).strip().lower() for item in items if str(item).strip()]

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
            # Browsers reject a wildcard origin when credentials are allowed,
            # so "*" here does not open the API — it closes it to every real
            # frontend while looking permissive. Refuse it at boot rather than
            # discover it as a sign-in that never sticks. Epic 18.2.
            origins = self.cors_allow_origins
            if not origins or any(o.strip() == "*" for o in origins):
                raise ValueError(
                    "CORS_ALLOW_ORIGINS must name the deployed frontend's real origin(s); "
                    "a wildcard or an empty list is rejected in staging/production."
                )
        return self

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")

    def sync_database_url(self) -> str:
        """Alembic runs synchronously; translate the async driver out."""
        return self.database_url.replace("+asyncpg", "").replace("postgresql+psycopg", "postgresql")

    def provider_key(self, name: str) -> str:
        """Fetch a required provider setting, raising a clear error when unset.

        Used from Epic 2 onward. Kept here so that the failure message names the
        environment variable an operator has to set, rather than surfacing as a
        401 from a vendor.

        Epic 9.15 widened this to plain strings as well as `SecretStr`, for one
        setting: `STRIPE_PRICE_ID`. A Price id is not a credential and saying it
        is would be a lie told in a type. The alternative was a second accessor
        with a second copy of the message below, which is how two failure modes
        end up phrased differently and one of them ends up unhelpful. One
        accessor, one message, both kinds of setting.
        """
        value: SecretStr | str | None = getattr(self, name, None)
        if value is None:
            raise RuntimeError(
                f"{name.upper()} is not configured. Add it to your .env "
                f"(see .env.example) or the deployment secret store."
            )
        return value.get_secret_value() if isinstance(value, SecretStr) else value


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Cache is cleared in tests."""
    return Settings()
