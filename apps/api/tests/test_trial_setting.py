"""`STRIPE_TRIAL_DAYS` — how long the free trial is, in days — 2026-10-04.

The founder decided a free trial at checkout: the card taken, charged on day 31 unless the
agency cancels. This setting is its length. Zero, the default, is no trial, and with zero
checkout is exactly what it was before the setting existed (`test_billing.py` holds that).

It is read at boot like every other setting, so a value Stripe would refuse fails the start,
where somebody sees it, and not the first checkout after it, where an agency does. Stripe
takes `trial_period_days` from 1 to 730; zero here means "send none".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from avp_api.config import Settings

DB = "postgresql+asyncpg://avp:avp@db.internal:5432/avp"
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"


def _settings() -> Settings:
    """Settings as the app builds them: from the environment, reading no dotenv file."""
    return Settings(_env_file=None, environment="local", database_url=DB, redis_url="redis://x/0")


def test_the_default_is_no_trial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_TRIAL_DAYS", raising=False)
    assert _settings().stripe_trial_days == 0


@pytest.mark.parametrize(
    ("raw", "days"),
    [("0", 0), ("1", 1), ("14", 14), ("30", 30), ("730", 730), (" 30 ", 30)],
)
def test_a_number_of_days_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, days: int
) -> None:
    monkeypatch.setenv("STRIPE_TRIAL_DAYS", raw)
    assert _settings().stripe_trial_days == days


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_is_no_trial_like_every_other_blank_setting(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """`cp .env.example .env` and clearing the value in Render both leave `NAME=` behind.

    That is "unset" for every provider setting and for `LOG_LEVEL`, and here it is no
    trial, the safe direction: a blank must never be a reason to claim one.
    """
    monkeypatch.setenv("STRIPE_TRIAL_DAYS", blank)
    assert _settings().stripe_trial_days == 0


@pytest.mark.parametrize(
    "bad",
    ["-1", "731", "999999", "abc", "30.5", "thirty", "true", "30 days"],
)
def test_a_value_that_is_not_zero_to_730_whole_days_fails_at_boot(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("STRIPE_TRIAL_DAYS", bad)
    with pytest.raises(ValidationError, match="stripe_trial_days"):
        _settings()


def test_the_bounds_are_stripes() -> None:
    """730 is the longest trial Stripe accepts, and one the shortest: both ends are inside."""
    for days in (0, 1, 730):
        assert Settings(_env_file=None, stripe_trial_days=days).stripe_trial_days == days
    for days in (-1, 731):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, stripe_trial_days=days)


def test_the_template_documents_it_and_ships_it_off() -> None:
    """A deployment that copies `.env.example` gets no trial until somebody decides one."""
    lines = TEMPLATE.read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == "STRIPE_TRIAL_DAYS=0")
    # The comment block just above it says what it is, and that 0 means none.
    comment: list[str] = []
    for line in reversed(lines[:at]):
        if not line.startswith("#"):
            break
        comment.append(line.lower())
    said = " ".join(comment)
    assert "trial" in said
    assert "0" in said and "no trial" in said
