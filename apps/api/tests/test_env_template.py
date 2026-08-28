"""Guard: `.env.example` must never carry a real secret.

This exists because the mix-up has now happened twice — the two filenames
differ by one suffix, and an editor opens the committed one by default when the
gitignored one does not exist yet. Both times the key was caught before any
commit, but relying on that is relying on luck.

`.env.example` is committed; `.env` is not. A populated value in the template is
a secret heading for version control.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"

# Keys whose value must be empty in the committed template. Non-secret settings
# (ENVIRONMENT, LOG_LEVEL, ports, URLs) legitimately carry defaults.
SECRET_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "SERPAPI_KEY",
    "PERPLEXITY_API_KEY",
    "GOOGLE_AI_API_KEY",
    # Epic 9.15. STRIPE_PRICE_ID is deliberately NOT here: a Price id names a
    # published product and is not a credential, so requiring it to be blank
    # would be asserting something untrue about it.
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
}

# Shapes that are secrets regardless of which key they sit under.
SECRET_SHAPES = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),   # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{32,}"),          # OpenAI-style
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),      # Google
    re.compile(r"pplx-[A-Za-z0-9]{20,}"),        # Perplexity
    # Stripe uses underscores where the patterns above use hyphens, so none of
    # them would have matched a pasted Stripe key. Added with the keys, in the
    # same task, rather than left as a gap that only shows up on the day it
    # matters. Covers live and test secret keys and a webhook signing secret.
    re.compile(r"sk_(live|test)_[A-Za-z0-9]{20,}"),  # Stripe secret key
    re.compile(r"rk_(live|test)_[A-Za-z0-9]{20,}"),  # Stripe restricted key
    re.compile(r"whsec_[A-Za-z0-9]{20,}"),           # Stripe webhook secret
]


def _values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in TEMPLATE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_template_exists() -> None:
    assert TEMPLATE.exists(), "apps/api/.env.example is the committed template and must exist"


@pytest.mark.parametrize("key", sorted(SECRET_KEYS))
def test_secret_keys_are_empty_in_the_template(key: str) -> None:
    value = _values().get(key)
    assert value is not None, f"{key} is missing from .env.example"
    assert value == "", (
        f"{key} has a value in .env.example, which is COMMITTED. "
        f"Real values belong in apps/api/.env (gitignored). "
        f"If this was a paste into the wrong file, move it and rotate the key."
    )


def test_no_credential_shaped_string_anywhere_in_the_template() -> None:
    """Catches a secret pasted under a key not on the list above."""
    text = TEMPLATE.read_text()
    for pattern in SECRET_SHAPES:
        match = pattern.search(text)
        assert match is None, (
            f"apps/api/.env.example contains a credential-shaped string "
            f"({match.group()[:12]}…). That file is committed."
        )


def test_app_secret_is_the_obvious_placeholder() -> None:
    """APP_SECRET may carry a value, but it must be recognisably fake.

    config.py refuses to boot in staging/production while it still starts with
    'dev-only', so the placeholder is load-bearing rather than decorative.
    """
    value = _values().get("APP_SECRET", "")
    assert value.startswith("dev-only"), (
        "APP_SECRET in .env.example must remain the 'dev-only…' placeholder — "
        "config.py refuses to boot in a deployed environment while it is unchanged."
    )
