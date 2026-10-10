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
  * A failed start never prints a value. Pydantic's errors would echo their input,
    which for a model validator is the whole environment (`hide_input_in_errors`
    below), every message here names the variable and not what it held, and the
    two URLs that carry credentials are left out of `repr()`.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# The longest trial Stripe accepts (`subscription_data.trial_period_days` takes 1 to 730). The
# setting below bounds itself by it, so a value Stripe would refuse fails the start and not the
# first checkout after it.
MAX_STRIPE_TRIAL_DAYS = 730

# What a bad `FEEDBACK_EMAIL` stops the start with. It names the variable and never the value:
# see `_feedback_address_is_one_address_or_nothing` for why.
FEEDBACK_EMAIL_MESSAGE = (
    "FEEDBACK_EMAIL is not an email address. Set it to one address, such as "
    'name@example.com, or leave it empty to turn the "Send feedback" link off.'
)

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


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local_host(database_url: str) -> bool:
    """True when every server the URL can reach is loopback or a local socket; else remote.

    Parsed with SQLAlchemy's own `make_url`, so this reads the URL exactly as the engine will
    (a stdlib parser would end the query at `#`, where SQLAlchemy does not).
    - `localhost`, `127.0.0.1`, `::1` and any `*.localhost` name are local. A single-label name
      such as `db` is remote: in a compose network or on a platform it is another machine.
    - The hosts are the URL's own and any `host=` in its query (libpq and asyncpg read both, and
      `host=` may list several, comma-separated); a `host=` that is a path is a Unix-socket
      directory on this machine.
    - A `dsn=` in the query is remote: asyncpg takes the server from it.
    - A URL that names no host is a local socket, unless `PGHOST` is set, which asyncpg then
      connects to.
    - An unparsable URL is remote.
    """
    try:
        url = make_url(database_url)
    except (ArgumentError, ValueError):
        return False

    def values(key: str) -> list[str]:
        value = url.query.get(key)
        if value is None:
            return []
        return [value] if isinstance(value, str) else list(value)

    if values("dsn"):
        return False
    named = [url.host or ""] + [h for v in values("host") for h in v.split(",")]
    hosts = [h.strip().lower().rstrip(".") for h in named if h.strip()]
    if not hosts and os.environ.get("PGHOST"):
        return False
    for host in hosts:
        if host.startswith("/"):
            continue  # a socket directory: this machine
        if host not in _LOCAL_HOSTS and not host.endswith(".localhost"):
            return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Pydantic prints a failed validation as its message and `input_value=<the input>`. For
        # a model validator (`_harden_deployed_environments`) the input is the whole settings
        # dict: the raw environment strings, not masked, cut to 50 characters (the first 25 and
        # the last 24). So a refused production start wrote the tail of the last setting that was
        # set (a webhook secret, an address) to the deploy log, and a key pasted into the wrong
        # variable was printed whole by its own field's error. This leaves `input_value` and
        # `input_type` out of every error's text; each error still names the setting.
        # `ValidationError.errors()` and `.json()` still carry the input. Nothing catches a
        # Settings error, so nothing reads them: the failed start is the traceback.
        hide_input_in_errors=True,
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
    # `repr=False` on both URLs: they carry `user:password@host` on a deployed host, and `repr()`
    # of the settings masks a `SecretStr` but prints a plain `str` in full. Nothing prints the
    # object today; this keeps it so for the first debug line or traceback viewer that does.
    database_url: str = Field(
        default="postgresql+asyncpg://avp:avp@127.0.0.1:5432/avp",
        description="SQLAlchemy async URL for Postgres.",
        repr=False,
    )
    database_pool_size: int = 10
    database_max_overflow: int = 5
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False)
    # Bounds on every Redis call, so a slow or unreachable Redis fails the
    # request fast (a 503) instead of hanging it. Redis is same-region and
    # answers in about a millisecond; 2 s is generous for a Lua rate-limit
    # script or a session read, yet well inside a browser's patience. Nothing
    # on the API pool blocks on purpose (no BLPOP, XREAD BLOCK or pub/sub), so
    # a read timeout cannot break a legitimate wait.
    # `redis.memory_high` is logged when Redis's used memory is at or above this share of its
    # maxmemory (runbook: "memory above 50%" is the upgrade trigger). Never warns at maxmemory 0.
    redis_memory_warn_ratio: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)
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
    # How many Argon2 hashes or verifies may run at once, per process. Each holds
    # argon2_memory_cost_kib (~19 MiB) while it runs, so this bounds the memory
    # password work can take on a small instance (security.py: one executor of this size).
    argon2_max_concurrency: int = Field(default=2, ge=1, le=32)
    # How long a request may wait for one of those slots before it is answered 503. The wait
    # holds no database connection (authenticate ends its read transaction first).
    argon2_queue_timeout_seconds: float = Field(default=10.0, gt=0, le=120, allow_inf_nan=False)

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
    # Google sign-in start is unauthenticated and writes one Redis key per call, on the instance
    # that also holds every session and window. Per IP only (no email exists there), counted
    # before that key is written; over it the browser is sent back with `rate-limited`.
    google_start_limit_per_ip: PositiveInt = 30
    google_start_window_seconds: PositiveInt = 15 * 60
    # The two public share-link routes (`POST /reports/shared` and `/reports/shared/pdf`) need no
    # session, so they are limited per source IP only, as fixed windows in Redis, before the token
    # is looked up. The PDF is the dearer one (it is rendered), so its budget is smaller. On a Redis
    # error the JSON report FAILS OPEN (a warning is logged): a prospect opening a report has no
    # account and no other way in. The PDF FAILS CLOSED (503): it is the costly route, and during
    # an outage the prospect can still read the report and only loses the download.
    shared_report_limit_per_ip: PositiveInt = 60
    shared_pdf_limit_per_ip: PositiveInt = 10
    shared_window_seconds: PositiveInt = 10 * 60
    # How many report PDFs may be rendered at once, per process, and how long a request may wait for
    # one of those slots before it is answered 503. A render is CPU work done on a worker thread,
    # never on the event loop (routers/report.py), and the bound keeps a burst from filling the
    # thread pool or the memory of a small instance. The wait holds no database connection.
    # Default 1: the renderer is pure Python and holds the GIL, so a second thread only competes
    # with the event loop. Past this many times 4 requests in flight (rendering, waiting, or
    # still building the report) a PDF request is answered 503 at once, before any query.
    pdf_render_max_concurrency: int = Field(default=1, ge=1, le=16)
    pdf_render_queue_timeout_seconds: float = Field(default=20.0, gt=0, le=120, allow_inf_nan=False)
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

    # --- platform-wide daily spend caps (D2) ------------------------------
    # How many new scans AVP as a whole starts per UTC day, and how many of those may be by
    # agencies that are not paid up (`MAX_SIGNUP_FREE_SCANS_PER_DAY`). Read before any provider
    # call (`services/spend_caps`). Running scans finish. 0 REFUSES EVERY NEW SCAN: it is the
    # founder's spend kill switch. Applied whether or not billing is configured.
    max_scans_per_day_platform: int = Field(default=60, ge=0)
    max_signup_free_scans_per_day: int = Field(default=20, ge=0)

    # --- paid AI calls outside a scan (F3) --------------------------------
    # Ad hoc questions (`POST /clients/{id}/prompt-runs`), a scan's fixes written again
    # (`POST /scans/{id}/fixes`) and a site classified (`POST /clients`, `.../reclassify`) each
    # pay a provider and are not a scan. Per-caller limits first, taken atomically in Redis
    # before the call: runs per client per hour (the row count stays as a second check), one
    # regeneration of a scan's fixes per cooldown, classifications per agency per UTC day.
    # Then AVP-wide daily caps (`services/spend_caps`, same rules as the scan caps: 0 refuses
    # every new call of that kind, the spend kill switch). All independent of each other.
    prompt_runs_per_client_per_hour: int = Field(default=30, ge=0)
    fix_regenerate_cooldown_seconds: int = Field(default=3600, ge=1)
    classifications_per_agency_per_day: int = Field(default=20, ge=0)
    max_prompt_runs_per_day_platform: int = Field(default=300, ge=0)
    max_fix_runs_per_day_platform: int = Field(default=100, ge=0)
    max_classifications_per_day_platform: int = Field(default=200, ge=0)
    # The share of the platform's classifications agencies not paid up may take between them,
    # and each agency's own share of the day for questions and fixes (the classification one is
    # `classifications_per_agency_per_day`), so no one tenant or free sign-up wave spends a
    # day's units for everybody. Mirrors `max_signup_free_scans_per_day`.
    max_free_classifications_per_day_platform: int = Field(default=60, ge=0)
    # An agency not paid up gets a smaller own day than a paid one (a few sign-ups at
    # `classifications_per_agency_per_day` each would otherwise take the whole free share).
    classifications_per_unpaid_agency_per_day: int = Field(default=5, ge=0)
    prompt_runs_per_agency_per_day: int = Field(default=100, ge=0)
    fix_runs_per_agency_per_day: int = Field(default=30, ge=0)

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
    # The free trial — 2026-10-04, founder decision: "30-day free trial with a card at
    # checkout, charged on day 31 unless cancelled". This is its length in days.
    #
    # 0, the DEFAULT, is no trial, and then checkout is exactly what it was before this
    # setting existed. Above 0, a checkout for an agency that has never had a subscription
    # asks Stripe for a trial of this many days and for the card up front, and Stripe
    # charges at the trial's end unless the agency cancels first (`services/billing.py`,
    # `create_checkout_session`). One trial per agency, ever: an agency that has had any
    # subscription, or whose Stripe customer has, is sent to the plain checkout.
    #
    # The public pages read this number from the API (`GET /billing/plan`) and say "the
    # first N days after you subscribe are free" only when it is above 0, so the length is
    # stated in one place.
    # Bounded at boot by Stripe's own limit: 0 to 730. A blank value is 0, like every other
    # blank setting, and any other value that is not a whole number in range stops the API
    # from starting, where somebody sees it.
    stripe_trial_days: int = Field(default=0, ge=0, le=MAX_STRIPE_TRIAL_DAYS)

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

    # --- the feedback link (2026-10-06) -------------------------------------
    # Where the signed-in web app's "Send feedback" link writes to: ONE email address. Unset,
    # the DEFAULT, there is no link and nothing a user sees changes. NOT a secret, and
    # deliberately a plain `str`, for the reason `stripe_price_id` gives: it is shown to every
    # signed-in user (`GET /auth/me` carries it as `feedbackEmail`) and the web builds a
    # `mailto:` from it, with the SHAPE of the page in the subject and never an id, a token or
    # a name. Only the auth router's context responses read it, and no log line carries it.
    #
    # A blank is unset, like every other blank setting. Anything else must be one address, or
    # the API does not start (the validator below). Set it on the API service only; the web
    # has no variable for it.
    feedback_email: str | None = None

    # --- the report's CSV (2026-10-06) ---------------------------------------------------------
    # Whether the signed-in report offers "Download figures (CSV)", and the route
    # `GET /scans/{scanId}/report.csv` answers. Off, the DEFAULT, the route is a 404 that says
    # what an unknown path says, the report says the export is not available
    # (`csvExportAvailable: false`), the web draws no link, and nothing a user sees changes.
    # On, the file carries the figures the signed-in report already shows, built from the same
    # projection, for a finished scan only. A share link never has it, whatever this says. NOT a
    # secret, and read by no log line. Set it on the API service only; the web has no variable
    # for it.
    #
    # A blank is off, like every other blank setting. A value that is not a boolean stops the API
    # from starting, naming this setting (the validator below): a typo that quietly left it off
    # would be a switch somebody believes they set.
    report_csv_enabled: bool = False

    # --- email when a scan finishes (F1, 2026-10-06) --------------------------------------------
    # Whether the API emails the user who started a scan when it finishes
    # (`services/scan_emails.py`,
    # run every minute by `scan_email_watch.py`). Off, the DEFAULT, nothing runs, nothing is claimed
    # or logged, and nothing a user sees changes. On, it still sends nothing while email is not
    # configured (`scan_emails_available`). NOT a secret, and read by no log line. A blank is off;
    # a value that is not a boolean stops the start, naming this setting.
    email_scan_notifications: bool = False

    # --- automatic monthly rescans (F2, 2026-10-06) --------------------------------------------
    # Whether the API starts a client's scan by itself, every month, for a client an owner or
    # admin has switched "Rescan every month" on for. Off, the DEFAULT, no loop runs, the
    # setting routes answer as an unknown path does, the client screen draws no switch, and
    # nothing a user sees changes. On, a loop in the API wakes every 10 minutes and starts at
    # most one due scan through the same start path as the Run button (`services/scan_start.py`),
    # so the scan allowance, the one-open-scan rule and the reaper all apply. NOT a secret, and
    # read by no log line. Set it on the API service only; the web has no variable for it.
    #
    # A blank is off, like every other blank setting. A value that is not a boolean stops the API
    # from starting, naming this setting (the validator below).
    auto_rescan_enabled: bool = False

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

    @field_validator("stripe_trial_days", mode="before")
    @classmethod
    def _a_blank_trial_length_is_no_trial(cls, value: object) -> object:
        # `STRIPE_TRIAL_DAYS=` is what `cp .env.example .env` and a cleared Render variable
        # leave behind. It is no trial, the safe direction: a blank must never be a reason to
        # claim one. Whitespace around a number is not part of it.
        if isinstance(value, str):
            return value.strip() or 0
        return value

    @field_validator("report_csv_enabled", mode="before")
    @classmethod
    def _a_blank_csv_switch_is_off(cls, value: object) -> object:
        # `REPORT_CSV_ENABLED=` is what `cp .env.example .env` and a cleared Render variable leave
        # behind. It is off, the safe direction, and never a refusal to start. Anything else is
        # read as a boolean by pydantic (`true`, `1`, `no`, `off`...), whitespace around it aside.
        if isinstance(value, str):
            return value.strip() or False
        return value

    @field_validator("email_scan_notifications", mode="before")
    @classmethod
    def _a_blank_scan_email_switch_is_off(cls, value: object) -> object:
        # `EMAIL_SCAN_NOTIFICATIONS=` is what `cp .env.example .env` and a cleared Render variable
        # leave behind: off, never a refusal to start.
        if isinstance(value, str):
            return value.strip() or False
        return value

    @field_validator("auto_rescan_enabled", mode="before")
    @classmethod
    def _a_blank_rescan_switch_is_off(cls, value: object) -> object:
        # `AUTO_RESCAN_ENABLED=` is what `cp .env.example .env` and a cleared Render variable
        # leave behind. It is off, the safe direction, and never a refusal to start.
        if isinstance(value, str):
            return value.strip() or False
        return value

    @field_validator("feedback_email", mode="before")
    @classmethod
    def _feedback_address_is_one_address_or_nothing(cls, value: object) -> object:
        # Blank is unset: `FEEDBACK_EMAIL=` is what `cp .env.example .env` and a cleared Render
        # variable leave behind, and it must never become a link to nowhere. Anything else is
        # checked as ONE bare address (`validate_email` refuses "Name <a@b>", a list and a
        # `mailto:` prefix), without asking the network, and kept as it normalises.
        if value is None:
            return None
        if isinstance(value, str):
            address = value.strip()
            if not address:
                return None
            try:
                return validate_email(address, check_deliverability=False).normalized
            except EmailNotValidError:
                pass  # said below, once, and without the value
        # A RuntimeError that carries no value, and not a ValueError, on purpose. Pydantic wraps
        # a ValueError in a ValidationError that would print `input_value='…'` but for this
        # model's `hide_input_in_errors`, so a key pasted into this variable by mistake could
        # reach the deploy log by the failed start; this stays value-free on its own. It is
        # raised out here, not inside the `except`, so the library's own message, which can quote
        # characters of the address, is not chained to it. `provider_key` below is the precedent
        # for naming the variable in a RuntimeError.
        raise RuntimeError(FEEDBACK_EMAIL_MESSAGE)

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
    def scan_emails_available(self) -> bool:
        """Whether a scan-finished email can go out at all: the setting is on AND email is set up.

        A fact about the deployment, the same for every user, so it can be shown to a signed-in
        user (`MeOut.scanEmails.available`) without telling anybody anything about an account.
        """
        return self.email_scan_notifications and self.email_configured

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
    def _refuse_unset_environment_on_remote_database(self) -> Settings:
        # `environment` defaults to "local", so a deploy that forgot ENVIRONMENT would run with
        # docs served, DEBUG logs, the dev APP_SECRET accepted, the cookie not forced secure and
        # CORS at the localhost default, and `_harden_deployed_environments` would never run.
        # A remote database with no stated environment is that mistake. Naming the environment
        # (even `local`, for a remote development database) is the operator saying so.
        # The message carries no part of the URL.
        if "environment" not in self.model_fields_set and not _is_local_host(self.database_url):
            raise ValueError(
                "ENVIRONMENT is not set and DATABASE_URL points at a remote host. "
                "Set ENVIRONMENT=production (or staging), or ENVIRONMENT=local for a remote "
                "development database."
            )
        return self

    @model_validator(mode="after")
    def _free_scans_within_the_platform_cap(self) -> Settings:
        # The free cap is a share of the platform's: above it, unpaid traffic could again take
        # every `scans` unit of the day. A platform cap of 0 is the spend kill switch and is
        # always allowed.
        if (
            self.max_scans_per_day_platform > 0
            and self.max_signup_free_scans_per_day > self.max_scans_per_day_platform
        ):
            raise ValueError(
                "MAX_SIGNUP_FREE_SCANS_PER_DAY must not be above MAX_SCANS_PER_DAY_PLATFORM "
                "(set MAX_SCANS_PER_DAY_PLATFORM=0 to refuse every new scan)."
            )
        return self

    @model_validator(mode="after")
    def _free_classifications_within_the_platform_cap(self) -> Settings:
        # As for scans: above the platform's cap the free share could take every unit. A
        # platform cap of 0 is the kill switch and is always allowed.
        if (
            self.max_classifications_per_day_platform > 0
            and self.max_free_classifications_per_day_platform
            > self.max_classifications_per_day_platform
        ):
            raise ValueError(
                "MAX_FREE_CLASSIFICATIONS_PER_DAY_PLATFORM must not be above "
                "MAX_CLASSIFICATIONS_PER_DAY_PLATFORM (set the latter to 0 to refuse every "
                "new classification)."
            )
        return self

    @model_validator(mode="after")
    def _unpaid_day_within_the_paid_day(self) -> Settings:
        if self.classifications_per_unpaid_agency_per_day > self.classifications_per_agency_per_day:
            raise ValueError(
                "CLASSIFICATIONS_PER_UNPAID_AGENCY_PER_DAY must not be above "
                "CLASSIFICATIONS_PER_AGENCY_PER_DAY."
            )
        return self

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
